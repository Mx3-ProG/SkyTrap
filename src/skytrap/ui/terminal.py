from typing import Callable, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text

from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider

console = Console()

ChatMode = Literal["normal", "plan", "auto"]
MODE_CYCLE: tuple[ChatMode, ...] = ("normal", "plan", "auto")
MODE_STYLES: dict[ChatMode, str] = {"normal": "cyan", "plan": "yellow", "auto": "green"}
MODE_DESCRIPTIONS: dict[ChatMode, str] = {
    "normal": "full tools, each write_file/shell asks for confirmation",
    "plan": "read-only — Architect analyzes and plans, nothing can be changed",
    "auto": "full tools, writes/commands run without asking (still shown)",
}


class ChatState:
    """Tracks the current chat mode. Cycling it (via the Shift+Tab / Ctrl+Shift+Tab
    key binding) only updates this — it never triggers a model call by itself; the
    new mode only takes effect on the next message the user sends.
    """

    def __init__(self) -> None:
        self.mode: ChatMode = "normal"

    def cycle_mode(self) -> None:
        current_index = MODE_CYCLE.index(self.mode)
        self.mode = MODE_CYCLE[(current_index + 1) % len(MODE_CYCLE)]


def make_mode_aware_confirm(base_confirm: Callable[[str], bool], state: ChatState) -> Callable[[str], bool]:
    """Wraps an existing confirm_* function so that in "auto" mode it still shows the
    preview (transparency — you see what happened) but skips the y/n prompt. In
    "normal"/"plan" mode it behaves exactly as before. The underlying Tool objects and
    their base confirm callbacks are untouched; only the wrapper is mode-aware.
    """

    def wrapped(preview: str) -> bool:
        if state.mode == "auto":
            console.print(
                Panel(Text(preview), title="Auto-approved", border_style="green", padding=(1, 2))
            )
            return True
        return base_confirm(preview)

    return wrapped


def confirm_write(preview: str) -> bool:
    """Shows a diff/new-file preview and asks the user to approve a write_file call."""
    syntax = Syntax(preview, "diff", theme="ansi_dark", word_wrap=True) if preview.startswith(
        ("---", "+++", "@@")
    ) else Text(preview)
    console.print(
        Panel(syntax, title="Proposed write", border_style="yellow", padding=(1, 2))
    )
    return Confirm.ask("Apply this write?", default=False)


def confirm_shell(preview: str) -> bool:
    """Shows the pending shell command and asks the user to approve running it."""
    console.print(
        Panel(Text(preview), title="Run shell command?", border_style="yellow", padding=(1, 2))
    )
    return Confirm.ask("Run this command?", default=False)


def confirm_start_process(preview: str) -> bool:
    """Shows the pending background command and asks the user to approve starting it."""
    console.print(
        Panel(
            Text(preview),
            title="Start background process?",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    return Confirm.ask("Start this process?", default=False)


def confirm_stop_process(preview: str) -> bool:
    """Shows the process about to be stopped and asks the user to approve it."""
    console.print(
        Panel(Text(preview), title="Stop background process?", border_style="yellow", padding=(1, 2))
    )
    return Confirm.ask("Stop this process?", default=False)


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
    console.print(
        "[dim]Shift+Tab cycles mode: normal -> plan -> auto. "
        "Ctrl+C interrupts a running turn.[/dim]\n"
    )


DEFAULT_PLAN_NOTE = (
    "Read-only analysis — nothing was changed. Run `skytrap` to have SkyTrap implement it."
)


def print_plan(plan_text: str, note: str = DEFAULT_PLAN_NOTE) -> None:
    console.print(
        Panel(Text(plan_text), title="Architect plan", border_style="cyan", padding=(1, 2))
    )
    if note:
        console.print(f"[dim]{note}[/dim]")


def confirm_implement_plan() -> bool:
    return Confirm.ask("Implement this plan?", default=False)


def print_developer_summary(summary: str) -> None:
    console.print(
        Panel(Text(summary), title="Developer summary", border_style="green", padding=(1, 2))
    )


def print_test_result(output: str, success: bool) -> None:
    style = "green" if success else "red"
    title = "Tests passed" if success else "Tests failed"
    console.print(Panel(Text(output), title=title, border_style=style, padding=(1, 2)))


def print_diff_summary(diff_text: str) -> None:
    syntax = (
        Syntax(diff_text, "diff", theme="ansi_dark", word_wrap=True)
        if diff_text.startswith(("---", "+++", "@@"))
        else Text(diff_text)
    )
    console.print(Panel(syntax, title="Diff", border_style="cyan", padding=(1, 2)))


def print_review(review_text: str) -> None:
    console.print(
        Panel(Text(review_text), title="Reviewer", border_style="magenta", padding=(1, 2))
    )


def _build_key_bindings(state: ChatState) -> KeyBindings:
    bindings = KeyBindings()

    def announce_mode_change() -> None:
        style = MODE_STYLES[state.mode]
        console.print(
            f"\n[dim]Mode ->[/dim] [bold {style}]{state.mode}[/bold {style}] "
            f"[dim]({MODE_DESCRIPTIONS[state.mode]})[/dim]"
        )

    def cycle(_event) -> None:
        state.cycle_mode()
        # Printing from a key binding must go through run_in_terminal, otherwise it
        # corrupts the active prompt's display instead of appearing cleanly above it.
        run_in_terminal(announce_mode_change)

    # "Ctrl+Shift+Tab" isn't a key prompt_toolkit's ANSI parser can represent at all —
    # verified directly against prompt_toolkit.key_binding.key_bindings._parse_key,
    # which raises ValueError for "c-s-tab"/"c-tab" (most terminals don't send a
    # distinguishable escape sequence for it). Only Shift+Tab (Keys.BackTab, "s-tab")
    # is actually parseable — which is exactly what Claude Code itself binds this to.
    bindings.add("s-tab")(cycle)

    return bindings


def run_chat_loop(respond: Callable[[str, ChatState], None], state: ChatState | None = None) -> None:
    state = state or ChatState()
    session: PromptSession = PromptSession(key_bindings=_build_key_bindings(state))

    console.print()
    while True:
        try:
            user_input = session.prompt(f"SkyTrap [{state.mode}] > ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        stripped = user_input.strip()
        if not stripped:
            continue
        if stripped.lower() in {"exit", "quit"}:
            break

        try:
            respond(stripped, state)
        except KeyboardInterrupt:
            # Ctrl+C during "thinking"/tool execution — abort this turn, not the whole
            # session. Python delivers the interrupt wherever the call currently is
            # (including inside a blocking httpx request), so no threading/cancellation
            # machinery is needed for this to work.
            console.print("[yellow]Interrupted.[/yellow]")
        except Exception as exc:  # noqa: BLE001 - surface any backend failure to the user
            console.print(f"[bold red]Error:[/bold red] {exc}")

        console.print()
