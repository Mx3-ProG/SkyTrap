import json
import re

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.core.protocol import Decision
from skytrap.core.repo_map import build_repo_map
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool

MAX_STEPS = 5
MAX_INSTRUCTIONS_CHARS = 20_000
INSTRUCTIONS_FILENAME = "SKYTRAP.md"

SYSTEM_PROMPT_TEMPLATE = """You are SkyTrap, a local coding assistant running in a terminal.

Workspace root: {workspace_path}

Workspace file structure (for orientation — read_file/list_directory still show \
actual content, this is just a map):
{repo_map}

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

Some tools change the workspace (e.g. write_file). SkyTrap always shows the user a \
diff and asks for their confirmation automatically before any such change is applied \
— this happens outside of you, as part of executing the tool. Never ask the user for \
confirmation yourself and never describe the pending change as a "final" message \
instead of calling the tool: if the user asked you to make a change, call the tool \
directly. You will be told in the next tool result whether the user approved it.
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


def _build_system_prompt(
    workspace: WorkspaceContext, tools: list[Tool], role_prompt: str | None = None
) -> str:
    tools_description = "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        workspace_path=workspace.path,
        repo_map=build_repo_map(workspace),
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

    return prompt


def _repair_final_message(text: str) -> str | None:
    """Best-effort recovery for a `{"type": "final", "message": "..."}` response whose
    message contains raw, unescaped double quotes (e.g. the model quoting a code
    snippet like {"path": ...} inline) — this breaks JSON string boundaries in a way
    strict/non-strict json.loads can't recover from, since the quotes are genuinely
    ambiguous to a real parser. Only handles the "final" shape; a malformed tool_call
    is deliberately left to fail rather than risk executing a guessed-at repair.
    """
    if not re.search(r'"type"\s*:\s*"final"', text):
        return None

    message_key = re.search(r'"message"\s*:\s*"', text)
    if not message_key:
        return None

    quote_start = message_key.end()
    quote_end = text.rfind('"')
    if quote_end <= quote_start:
        return None

    raw_message = text[quote_start:quote_end]
    return raw_message.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def _parse_decision(raw: str) -> Decision:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
        text = text.strip()

    for strict in (True, False):
        # strict=False tolerates literal control characters (raw newlines, tabs) inside
        # JSON strings — models routinely emit multi-line "message" values that way
        # instead of escaping them as \n, which strict JSON rejects.
        try:
            data = json.loads(text, strict=strict)
            return Decision.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            continue

    repaired = _repair_final_message(text)
    if repaired is not None:
        return Decision(type="final", message=repaired)

    # Model didn't follow the protocol at all — treat its raw text as the final answer
    # rather than failing the turn outright.
    return Decision(type="final", message=raw)


def run_agent_turn(
    model: ModelProvider,
    tools: list[Tool],
    workspace: WorkspaceContext,
    history: list[dict],
    user_input: str,
    role_prompt: str | None = None,
) -> str:
    """Runs one observe -> decide -> act -> observe loop until the model gives a
    final answer or MAX_STEPS is reached. `history` is mutated in place so the
    conversation carries over between turns. `role_prompt`, if given, overrides the
    generic assistant framing (e.g. a restricted Architect role).
    """
    tools_by_name = {tool.name: tool for tool in tools}
    messages = [{"role": "system", "content": _build_system_prompt(workspace, tools, role_prompt)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})

    for _ in range(MAX_STEPS):
        raw = model.chat(messages)
        decision = _parse_decision(raw)
        messages.append({"role": "assistant", "content": raw})

        if decision.type == "final":
            final_message = decision.message or raw
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": final_message})
            return final_message

        tool = tools_by_name.get(decision.tool or "")
        if tool is None:
            observation = f"ERROR: unknown tool '{decision.tool}'"
        else:
            result = tool.execute(workspace, decision.arguments)
            observation = result.output if result.success else f"ERROR: {result.output}"

        messages.append(
            {"role": "user", "content": f"Tool result for {decision.tool}:\n{observation}"}
        )

    timeout_message = "I couldn't complete this within the allowed number of steps."
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": timeout_message})
    return timeout_message
