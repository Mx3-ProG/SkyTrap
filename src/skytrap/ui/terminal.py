from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider

console = Console()


def confirm_write(preview: str) -> bool:
    """Shows a diff/new-file preview and asks the user to approve a write_file call."""
    syntax = Syntax(preview, "diff", theme="ansi_dark", word_wrap=True) if preview.startswith(
        ("---", "+++", "@@")
    ) else preview
    console.print(
        Panel(syntax, title="Proposed write", border_style="yellow", padding=(1, 2))
    )
    return Confirm.ask("Apply this write?", default=False)


def print_banner(model: ModelProvider, workspace: WorkspaceContext) -> None:
    branch_display = workspace.branch if workspace.is_git else "no git repo"
    body = (
        f"[bold]Engine:[/bold]    {model.engine}\n"
        f"[bold]Model:[/bold]     {model.name}\n"
        f"[bold]Workspace:[/bold] {workspace.name}\n"
        f"[bold]Branch:[/bold]    {branch_display}\n"
        f"[bold]API Cost:[/bold]  €{model.cost_eur:.2f}"
    )
    console.print(
        Panel(body, title="SKYTRAP", title_align="center", border_style="cyan", padding=(1, 2))
    )


def run_chat_loop(respond: Callable[[str], str]) -> None:
    console.print()
    while True:
        try:
            user_input = console.input("[bold cyan]SkyTrap >[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        stripped = user_input.strip()
        if not stripped:
            continue
        if stripped.lower() in {"exit", "quit"}:
            break

        # Plain text, not a Live spinner: a tool call (e.g. write_file) may need to
        # prompt the user interactively mid-turn, which doesn't mix with Rich's Live display.
        console.print("[dim]thinking...[/dim]")
        try:
            reply = respond(stripped)
        except Exception as exc:  # noqa: BLE001 - surface any backend failure to the user
            console.print(f"[bold red]Error:[/bold red] {exc}")
            continue

        console.print(reply)
        console.print()
