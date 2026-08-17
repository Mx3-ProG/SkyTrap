from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider
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

You only have read-only tools available (no write_file, no shell, no run_tests) — use \
them to check the actual code before proposing steps; do not guess at file contents or \
structure you haven't actually looked at.

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


def run_architect(model: ModelProvider, workspace: WorkspaceContext, task: str) -> str:
    """One-shot, read-only analysis: produces an implementation plan without touching
    the workspace. Each call is stateless (no shared history) since this is meant to be
    invoked explicitly per task, not as part of an ongoing conversation.
    """
    read_only_tools = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
    ]
    history: list[dict] = []
    return run_agent_turn(
        model, read_only_tools, workspace, history, task, role_prompt=ARCHITECT_ROLE_PROMPT
    )
