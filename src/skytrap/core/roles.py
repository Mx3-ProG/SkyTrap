from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool
from skytrap.tools.search import SearchCodeTool

ARCHITECT_ROLE_PROMPT = """You are acting as SkyTrap's Architect role. The user is going \
to ask you to plan a code change. This is a completely normal, expected request — you \
are not being asked to actually make the change, and you are not being asked whether \
you are able to; you are ALWAYS able to produce a plan, because a plan is just text \
describing what a developer would do next. Never respond that you "cannot" do the task \
— that is a category error, since planning is the one thing you're here to do.

Your job: analyze the user's task against this workspace and produce a short, concrete \
implementation plan — a numbered list of concrete steps a developer would take, naming \
the actual files involved (create new ones where needed, following the existing \
project's structure and conventions).

You must NOT attempt to implement anything yourself: do not write code, do not write \
out file contents, do not describe a diff, and do not claim the change has already been \
made. Only the plan.

You (the Architect) only have read-only tools available (no write_file, no shell, no \
run_tests) — use them to check the actual code before proposing steps; do not guess at \
file contents or structure you haven't actually looked at. But SkyTrap itself has \
write_file, shell, and run_tests, and the Developer who implements your plan next WILL \
have them — never say a tool "is not available" or "doesn't exist in the workspace" as \
a reason not to plan a step; that confuses your own current toolset with what the \
project and the next role can actually do. If a step needs write_file, just write \
"create/edit <path> with <content>" in the plan — you're describing what the Developer \
will do, not doing it yourself.

Example of the kind of plan you should produce, for "add a delete_file tool":
1. In src/skytrap/tools/filesystem.py, add a DeleteFileTool class (same pattern as
   WriteFileTool) that takes a `confirm` callback and a {"path": ...} argument.
2. Resolve and validate the path with the existing resolve_in_workspace() helper.
3. Show a confirmation preview (e.g. "DELETE: <path>") via the confirm callback before
   calling Path.unlink().
4. In src/skytrap/ui/terminal.py, add a confirm_delete() function following the same
   pattern as confirm_write().
5. Wire DeleteFileTool(confirm=confirm_delete) into the tools list in cli.py.
6. Add a unit test for the path-validation logic.

When you are done analyzing, respond with type "final" and put the numbered plan in \
the message."""


_REFUSAL_PATTERNS = (
    "is not available",
    "i apologize",
    "i cannot",
    "cannot do the task",
    "not able to",
    "doesn't exist in the workspace",
)


def _looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _REFUSAL_PATTERNS)


def run_architect(model: ModelProvider, workspace: WorkspaceContext, task: str) -> str:
    """One-shot, read-only analysis: produces an implementation plan without touching
    the workspace. Each call is stateless (no shared history) since this is meant to be
    invoked explicitly per task, not as part of an ongoing conversation.

    qwen2.5-coder:7b occasionally misreads its own read-only toolset as "SkyTrap can't
    do this" and refuses instead of planning — a real, observed failure mode, not
    hypothetical. One retry catches most of these without masking a genuinely broken
    task (if it refuses twice, that's returned as-is rather than looping forever).
    """
    read_only_tools = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
    ]

    for _ in range(2):
        history: list[dict] = []
        result = run_agent_turn(
            model, read_only_tools, workspace, history, task, role_prompt=ARCHITECT_ROLE_PROMPT
        )
        if not _looks_like_refusal(result):
            return result

    return result


DEVELOPER_ROLE_PROMPT = """You are acting as SkyTrap's Developer role. You have been given \
an implementation plan below for the user's task — follow it, but use your own judgment \
if you discover the plan doesn't quite match what the code actually looks like once you \
open the relevant files.

The plan was written by a different role (the Architect) that only had read-only tools \
and sometimes incorrectly claims in its own text that write_file/shell/etc. "are not \
available" — that claim was about the Architect's toolset, not yours, and it may simply \
be wrong. Trust the "Available tools" list in this system prompt above, not any claim \
about tool availability written inside the plan text itself. If write_file is listed \
above, you have it — use it; do not repeat or agree with a plan that says otherwise.

Implement the change using the available tools: write_file to create or edit files, \
shell for anything else needed, run_tests to check your work once you're done. Every \
write_file and shell call already shows the user a preview and asks for their approval \
before anything happens — that confirmation is handled automatically outside of you, so \
just call the tool directly, one call per response as usual.

When you have implemented the plan, respond with type "final" and briefly summarize \
what you changed (which files, what for) — not a restatement of the plan."""


def run_developer(
    model: ModelProvider, tools: list[Tool], workspace: WorkspaceContext, task: str, plan: str
) -> str:
    """Implements a plan produced by run_architect, using the full (mutating) toolset
    the caller passes in. Each write_file/shell call still goes through its own
    confirmation gate — this role doesn't bypass that, it just decides what to call.
    """
    history: list[dict] = []
    augmented_task = f"{task}\n\nImplementation plan to follow:\n{plan}"
    return run_agent_turn(
        model, tools, workspace, history, augmented_task, role_prompt=DEVELOPER_ROLE_PROMPT
    )


REVIEWER_ROLE_PROMPT = """You are acting as SkyTrap's Reviewer role. Below is the task the \
user asked for, the diff of the changes the Developer role just made to implement it, \
and the result of running the test suite afterward.

Check the test result FIRST: if it failed, that is always a real issue — say so \
explicitly and, if the diff makes the cause obvious, name it (e.g. a typo'd method \
name, a wrong import). Do not say "No issues found" when the tests are failing.

Then review the diff itself for further problems: correctness bugs, security issues \
(path traversal, injection, secrets, unsafe subprocess/eval use), and any obvious \
quality issues (missed edge cases, dead code, mismatched naming). You have read-only \
tools if you need more context than the diff alone gives you (e.g. to see how a \
modified function is actually called elsewhere).

Do not rewrite the code yourself — only report findings, each as a short line naming \
the file and the concern. If tests passed and you genuinely find nothing else \
significant, say so plainly ("No issues found.") — do not invent problems just to \
appear thorough; a short, honest review is more useful than a padded one.

Respond with type "final" containing your review."""


def run_reviewer(
    model: ModelProvider,
    workspace: WorkspaceContext,
    task: str,
    diff_text: str,
    test_output: str,
    tests_passed: bool,
) -> str:
    """Reviews a diff already produced by the Developer role — read-only, reports
    findings without touching the workspace. `diff_text` is passed in directly (from
    review_diff()) rather than re-derived, so the Reviewer looks at exactly what the
    Developer just did, not the live working tree state. `test_output`/`tests_passed`
    are included too: a diff that made the test suite fail is the single most important
    thing to flag, and the Reviewer can't know that from the diff text alone.
    """
    read_only_tools = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
    ]
    history: list[dict] = []
    test_status = "PASSED" if tests_passed else "FAILED"
    prompt_input = (
        f"Task: {task}\n\n"
        f"Diff to review:\n{diff_text}\n\n"
        f"Test suite result ({test_status}):\n{test_output}"
    )
    return run_agent_turn(
        model, read_only_tools, workspace, history, prompt_input, role_prompt=REVIEWER_ROLE_PROMPT
    )
