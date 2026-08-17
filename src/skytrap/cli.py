import typer

from skytrap.core.agent import run_agent_turn
from skytrap.core.context import detect_workspace
from skytrap.core.roles import run_architect, run_developer
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool, review_diff
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.tests import RunTestsTool
from skytrap.ui.terminal import (
    confirm_implement_plan,
    confirm_shell,
    confirm_write,
    console,
    print_banner,
    print_developer_summary,
    print_diff_summary,
    print_plan,
    print_test_result,
    run_chat_loop,
)

app = typer.Typer(add_completion=False, invoke_without_command=True)


def _build_full_toolset(on_write=None) -> list[Tool]:
    """The complete, mutating toolset: everything a chat session or the Developer
    role can call. write_file and shell each keep their own confirmation gate.
    `on_write`, if given, is forwarded to WriteFileTool to track touched paths."""
    return [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
        WriteFileTool(confirm=confirm_write, on_write=on_write),
        ShellTool(confirm=confirm_shell),
        RunTestsTool(),
    ]


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()
    tools = _build_full_toolset()
    history: list[dict] = []
    memory = SqliteMemory()
    session_id = memory.start_session(str(workspace.path))

    def respond(user_input: str) -> str:
        reply = run_agent_turn(model, tools, workspace, history, user_input)
        memory.record_message(session_id, "user", user_input)
        memory.record_message(session_id, "assistant", reply)
        return reply

    print_banner(model, workspace)
    try:
        run_chat_loop(respond)
    finally:
        memory.close()


@app.command()
def plan(task: str) -> None:
    """Analyze TASK against this workspace and print an implementation plan.
    Read-only: the Architect role has no write_file/shell/run_tests access, so
    nothing in the workspace is changed."""
    workspace = detect_workspace()
    model = OllamaProvider()
    console.print("[dim]Architect is analyzing the workspace...[/dim]")
    result = run_architect(model, workspace, task)
    print_plan(result)


@app.command()
def build(task: str) -> None:
    """Plan TASK with the Architect, then — only if you approve the plan — implement
    it with the Developer role, run the test suite, and show a diff summary. Every
    write_file/shell call still asks for its own individual confirmation on top of
    approving the plan up front."""
    workspace = detect_workspace()
    model = OllamaProvider()

    console.print("[dim]Architect is analyzing the workspace...[/dim]")
    plan_text = run_architect(model, workspace, task)
    print_plan(plan_text, note="")

    if not confirm_implement_plan():
        console.print("[dim]Cancelled — nothing was changed.[/dim]")
        return

    touched_files: list[str] = []
    tools = _build_full_toolset(on_write=touched_files.append)
    console.print("[dim]Developer is implementing the plan...[/dim]")
    summary = run_developer(model, tools, workspace, task, plan_text)
    print_developer_summary(summary)

    console.print("[dim]Running the test suite...[/dim]")
    test_result = RunTestsTool().execute(workspace, {})
    print_test_result(test_result.output, test_result.success)

    if touched_files:
        # Scoped to what the Developer actually wrote, not the whole working tree —
        # otherwise pre-existing unrelated uncommitted changes would show up here too.
        diff_result = review_diff(workspace, touched_files)
        if diff_result.success:
            print_diff_summary(diff_result.output)
    else:
        console.print("[dim]No files were written.[/dim]")
