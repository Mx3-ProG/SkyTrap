import typer

from skytrap.core.context import detect_workspace
from skytrap.models.ollama import OllamaProvider
from skytrap.ui.terminal import print_banner, run_chat_loop

app = typer.Typer(add_completion=False, invoke_without_command=True)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()

    print_banner(model, workspace)
    run_chat_loop(model)
