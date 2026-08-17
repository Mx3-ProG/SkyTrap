import typer

from skytrap.core.agent import run_agent_turn
from skytrap.core.context import detect_workspace
from skytrap.core.roles import run_architect
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.tests import RunTestsTool
from skytrap.ui.terminal import (
    confirm_shell,
    confirm_write,
    console,
    print_banner,
    print_plan,
    run_chat_loop,
)

app = typer.Typer(add_completion=False, invoke_without_command=True)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()
    tools = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
        WriteFileTool(confirm=confirm_write),
        ShellTool(confirm=confirm_shell),
        RunTestsTool(),
    ]
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
