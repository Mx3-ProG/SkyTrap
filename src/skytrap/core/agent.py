from typing import Callable

from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.state import TaskState
from skytrap.core.context import WorkspaceContext
from skytrap.core.intent import looks_like_consultant_refusal
from skytrap.core.project_inspection import inspect_project, resolve_commands
from skytrap.core.project_notes import load_recent_journal
# Re-exported for existing importers (e.g. skytrap.core.doctor) — the actual
# implementation lives in skytrap.core.protocol so that module (not this one)
# is the shared dependency both `run_agent_turn` here and `AgentLoop` in
# skytrap.autonomy.loop parse decisions through, avoiding a circular import
# now that this module also depends on skytrap.autonomy.executor.
from skytrap.core.protocol import Decision, _parse_decision  # noqa: F401
from skytrap.core.repo_map import build_repo_map
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool

MAX_STEPS = 5
MAX_INSTRUCTIONS_CHARS = 20_000
INSTRUCTIONS_FILENAME = "SKYTRAP.md"
MAX_EXECUTION_REJECTIONS = 2
MUTATING_TOOL_NAMES = {"write_file", "patch_file", "delete_file", "shell"}

EXECUTION_NUDGE = (
    "You are in execution mode. The user requested implementation, not advice. You "
    "have filesystem and command-execution tools listed above — use them now. Stop "
    "explaining and perform the next concrete step (create/edit a file, or run a "
    "command)."
)

SYSTEM_PROMPT_TEMPLATE = """You are SkyTrap, a local coding assistant running in a terminal.

Workspace root: {workspace_path}

Workspace file structure (for orientation — read_file/list_directory still show \
actual content, this is just a map):
{repo_map}

{language_summary}
Write idiomatic code for whichever language a file is in — never port patterns from \
one language into another (no JavaScript-style callbacks in Rust, no Java-style \
boilerplate in Go, etc). Use the check/build/test/format/lint commands listed above \
for the relevant language instead of inventing your own; if a project already has a \
build system (CMake, Make, an existing package.json script, ...) use it rather than \
a hand-written compile line. Call inspect_project if you need this for a part of the \
repository not covered above (e.g. a monorepo subdirectory in a different language).

Available tools:
{tools_description}

You must always respond with a single JSON object and nothing else — no markdown \
code fences, no text outside the JSON.

To call a tool, the "type" field is always the literal string "tool_call" — the tool's \
own name goes in the "tool" field, never in "type":
{{"type": "tool_call", "tool": "<tool_name>", "arguments": {{...}}}}
Example: {{"type": "tool_call", "tool": "read_file", "arguments": {{"path": "src/main.py"}}}}

To answer the user directly (no tool needed):
{{"type": "final", "message": "<your response>"}}

Only call a tool when you genuinely need information from the workspace to answer. \
For greetings or general questions, respond immediately with type "final".

You may only ever return ONE of these two JSON shapes — never invent a different \
"type" value, and never put more than one tool call in a single response. If a task \
needs several tools, call the first one now; you will be given its result and can \
call the next tool in your following response.

Some tools change the workspace (e.g. write_file). Ordinary changes apply immediately; \
only genuinely risky ones (secrets/credentials files, git reset/push/checkout, rm, mv) \
show the user a diff and ask for their confirmation automatically — this happens \
outside of you, as part of executing the tool. Never ask the user for confirmation \
yourself and never describe the pending change as a "final" message instead of \
calling the tool: if the user asked you to make a change, call the tool directly. You \
will be told in the next tool result whether the user approved it.

If a tool result is an error — including a failing test, lint, or build command — \
diagnose the cause yourself from the error text and try again with a fix in your next \
tool call. Do not stop to ask the user what to do. Never end a turn by only asking \
whether you should proceed when you already have enough information to act — call \
the tool.

A request to implement, program, build, create a project, or continue a previously \
agreed plan is an execution request, not a request for advice. Never respond with an \
explanation of how the change could be made, a code snippet with no corresponding \
write_file call, or a statement that the project is too large to build, when \
write_file/shell are in your tool list above — call them. A large task is not a \
reason to stop; break it into the next concrete file/command and keep going.
"""


def _load_project_instructions(workspace: WorkspaceContext) -> str | None:
    """Reads {workspace_root}/SKYTRAP.md if present — project-specific guidance the
    user wants applied every session, the same idea as a CLAUDE.md/AGENTS.md."""
    path = workspace.path / INSTRUCTIONS_FILENAME
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (UnicodeDecodeError, OSError):
        return None
    return content[:MAX_INSTRUCTIONS_CHARS] or None


def _build_language_summary(workspace: WorkspaceContext) -> str:
    """A compact per-language cheat sheet — real percentages/toolchain status from
    inspect_project(), not a description the model has to trust blindly."""
    profile = inspect_project(workspace)
    if not profile.languages:
        return "No recognized programming language detected in this workspace yet."

    lines = ["Languages detected in this workspace:"]
    for match in profile.languages[:5]:  # cap: monorepos can match many languages
        commands = resolve_commands(workspace, match)
        available = [
            exe for exe in match.profile.toolchain_executables if profile.toolchain.get(exe)
        ]
        lines.append(
            f"- {match.profile.name} ({match.percentage}% of source files"
            f"{', manifest found' if match.manifest_detected else ''})"
        )
        if available:
            lines.append(f"    installed toolchain: {', '.join(available)}")
        for label, values in (
            ("check", (commands.check_command,) if commands.check_command else ()),
            ("build", commands.build_commands),
            ("test", commands.test_commands),
            ("format", commands.format_commands),
            ("lint", commands.lint_commands),
        ):
            if values:
                lines.append(f"    {label}: {' | '.join(v for v in values if v)}")
    return "\n".join(lines)


def _build_system_prompt(
    workspace: WorkspaceContext, tools: list[Tool], role_prompt: str | None = None
) -> str:
    tools_description = "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        workspace_path=workspace.path,
        repo_map=build_repo_map(workspace),
        language_summary=_build_language_summary(workspace),
        tools_description=tools_description or "(none)",
    )

    if role_prompt:
        # Placed first, before anything else, for maximum salience — a role override
        # (e.g. Architect: read-only, plan-only) matters more than the generic framing.
        prompt = f"{role_prompt}\n\n{prompt}"

    instructions = _load_project_instructions(workspace)
    if instructions:
        # Placed right after the tool protocol, before the model starts reasoning about
        # the user's message, since small local models weight earlier content more
        # heavily than instructions appended at the very end of a long system prompt.
        marker = f"MANDATORY PROJECT RULES (from {INSTRUCTIONS_FILENAME} — override any of your default behavior):"
        prompt = prompt.replace(
            "Only call a tool when you genuinely need",
            f"{marker}\n{instructions}\n\nThese rules apply to every response you give in this "
            f"workspace, without exception.\n\nOnly call a tool when you genuinely need",
        )

    journal = load_recent_journal(workspace)
    if journal:
        # Placed after SKYTRAP.md (user-authored rules stay highest priority) but
        # still before the tool-use instructions, for the same salience reason.
        # This is the agent's own memory of past sessions on this project — how it
        # "picks the thread back up" instead of starting from zero every turn.
        journal_marker = (
            "CONTINUITY NOTES (from Skytrap/JOURNAL.md — your own summaries of past "
            "sessions on this project; use read_file on the full path for older history):"
        )
        prompt = prompt.replace(
            "Only call a tool when you genuinely need",
            f"{journal_marker}\n{journal}\n\nOnly call a tool when you genuinely need",
        )

    return prompt


def run_agent_turn(
    model: ModelProvider,
    executor: ToolExecutor,
    task: TaskState,
    memory: WorkingMemory,
    workspace: WorkspaceContext,
    history: list[dict],
    user_input: str,
    role_prompt: str | None = None,
    on_step: Callable[[dict], None] | None = None,
    max_steps: int = MAX_STEPS,
    require_execution_evidence: bool = False,
) -> str:
    """Runs one observe -> decide -> act -> observe loop until the model gives a
    final answer or `max_steps` is reached. `history` is mutated in place so the
    conversation carries over between turns. `role_prompt`, if given, overrides the
    generic assistant framing (e.g. a restricted Architect role). `on_step`, if
    given, is called after each tool result with {"tool", "arguments", "observation",
    "metadata", "success"} — used by the web server to stream progress over a
    WebSocket; the CLI passes None (no behavior change). `max_steps` defaults to
    MAX_STEPS (5), which suits the short read-only/no-tools roles (Architect,
    Reviewer, Summarizer); the Developer role — which routinely needs to read a few
    files, write/delete one or more, and run tests — is given a higher budget by
    its caller.

    Every tool call is routed through `executor` (a `skytrap.autonomy.executor.
    ToolExecutor`) — the exact same RiskEngine/ApprovalEngine/inspect-before-write
    policy the autonomous `skytrap agent run` uses. There is deliberately no
    second, parallel execution policy here: `tool.execute()` is never called
    directly. `task`/`memory` are the caller's session-scoped `TaskState`/
    `WorkingMemory` — carrying them across turns (not recreating them per call)
    is what lets the inspect-before-write guard remember a file read three turns
    ago, the same way it does within one autonomous task.

    `require_execution_evidence`, when True, refuses to accept a "final" answer
    that made zero mutating tool calls (write_file/patch_file/delete_file/shell)
    AND reads as consultant-style hedging (looks_like_consultant_refusal) — the
    caller has determined this turn is an implementation request, so a tool-free
    "here's how you could do it" is never a valid completion. A tool-free final
    that is NOT a hedge (e.g. "no code change is needed for this question") is
    still accepted — the guard targets the specific reported failure mode, not
    every answer that happens not to touch a file. Instead of returning, a nudge
    is appended and the loop continues, up to MAX_EXECUTION_REJECTIONS times;
    beyond that the model's own message is returned as-is (a real capability gap,
    not infinite retry).
    """
    tools = list(executor.tools.values())
    messages = [{"role": "system", "content": _build_system_prompt(workspace, tools, role_prompt)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})

    mutating_calls_made = 0
    rejections = 0

    for _ in range(max_steps):
        raw = model.chat(messages)
        decision = _parse_decision(raw)
        messages.append({"role": "assistant", "content": raw})

        if decision.type == "final":
            final_message = decision.message or raw
            needs_rejection = (
                require_execution_evidence
                and mutating_calls_made == 0
                and looks_like_consultant_refusal(final_message)
                and rejections < MAX_EXECUTION_REJECTIONS
            )
            if needs_rejection:
                rejections += 1
                messages.append({"role": "system", "content": EXECUTION_NUDGE})
                continue
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": final_message})
            return final_message

        result = executor.execute(task, memory, workspace, decision.tool or "", decision.arguments)
        observation = result.output if result.success else f"ERROR: {result.output}"
        if result.success and decision.tool in MUTATING_TOOL_NAMES:
            mutating_calls_made += 1

        if on_step is not None:
            on_step(
                {
                    "tool": decision.tool,
                    "arguments": decision.arguments,
                    "observation": observation,
                    "metadata": result.metadata,
                    "success": result.success,
                }
            )

        messages.append(
            {"role": "user", "content": f"Tool result for {decision.tool}:\n{observation}"}
        )

    timeout_message = "I couldn't complete this within the allowed number of steps."
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": timeout_message})
    return timeout_message
