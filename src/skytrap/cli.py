import typer

from skytrap.core.agent import run_agent_turn
from skytrap.core.context import detect_workspace
from skytrap.models.ollama import OllamaProvider
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.ui.terminal import confirm_shell, confirm_write, print_banner, run_chat_loop

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
    ]
    history: list[dict] = []

    def respond(user_input: str) -> str:
        return run_agent_turn(model, tools, workspace, history, user_input)

    print_banner(model, workspace)
    run_chat_loop(respond)
