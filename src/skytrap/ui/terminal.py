import sys
from typing import Callable, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import ProjectProfile
from skytrap.models.base import ModelProvider

console = Console()

ChatMode = Literal["normal", "plan", "auto"]
MODE_CYCLE: tuple[ChatMode, ...] = ("normal", "plan", "auto")
MODE_STYLES: dict[ChatMode, str] = {"normal": "cyan", "plan": "yellow", "auto": "green"}
MODE_DESCRIPTIONS: dict[ChatMode, str] = {
    "normal": "safe actions run immediately; installs/scripts ask first; destructive actions (rm, git reset/push, secrets) always ask",
    "plan": "read-only — Architect analyzes and plans, nothing can be changed",
    "auto": "safe + medium-risk actions run immediately; destructive actions (rm, git reset/push, secrets) still always ask",
}

SUCCESS = "✓"
ERROR = "✗"
WARNING = "⚠"
ACTION = "●"


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


def confirm_delete(preview: str) -> bool:
    """Shows a file-deletion preview and asks the user to approve a delete_file call."""
    console.print(
        Panel(Text(preview), title="Proposed deletion", border_style="red", padding=(1, 2))
    )
    return Confirm.ask("Delete this file?", default=False)


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


def print_commands(rows: list[tuple[str, str]]) -> None:
    """Lists every registered CLI subcommand (name + one-line help), for `skytrap
    commands` — the plain `skytrap --help` Typer already provides, rendered to match
    the rest of SkyTrap's terminal output instead of Click's default formatting."""
    table = Table(title="SkyTrap commands", border_style="cyan", show_lines=False)
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Description", style="white")
    for name, help_text in rows:
        table.add_row(f"skytrap {name}", help_text)
    console.print(table)
    console.print(
        "[dim]Run `skytrap` with no command to start the interactive chat. "
        "`skytrap <command> --help` shows full details for one command.[/dim]"
    )


def print_project_detected(profile: ProjectProfile) -> None:
    """The item-20-style "PROJECT DETECTED" panel — languages by real file-count
    share and which of their toolchains are actually on PATH, not a guess."""
    if not profile.languages:
        console.print("[dim]No recognized language detected in this workspace.[/dim]")
        return

    lines = ["[bold]Languages[/bold]"]
    for match in profile.languages:
        marker = " [dim](manifest)[/dim]" if match.manifest_detected else ""
        lines.append(f"  {match.profile.name:<12} {match.percentage:>5.1f}%{marker}")

    relevant_tools = sorted(
        {exe for m in profile.languages for exe in m.profile.toolchain_executables}
    )
    if relevant_tools:
        lines.append("")
        lines.append("[bold]Toolchain[/bold]")
        for name in relevant_tools:
            found = profile.toolchain.get(name)
            mark = f"[green]{SUCCESS}[/green]" if found else f"[red]{ERROR}[/red]"
            lines.append(f"  {mark} {name}")

    console.print(Panel("\n".join(lines), title="Project detected", border_style="cyan", padding=(1, 2)))


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


def log_step(message: str, index: int | None = None, total: int | None = None) -> None:
    """Marks a real phase transition (Architect analyzing, Developer implementing,
    running tests, ...) — every call here corresponds to an operation actually about
    to happen, never a fabricated progress indicator."""
    prefix = f"[{index}/{total}] " if index is not None and total is not None else f"{ACTION} "
    console.print(f"[cyan]{prefix}{message}[/cyan]")


_FILE_ACTION_STYLES = {"A": "green", "M": "yellow", "D": "red"}


def log_file(path: str, action: Literal["A", "M", "D"]) -> None:
    """Announces a file that was actually created/modified/deleted on disk."""
    style = _FILE_ACTION_STYLES[action]
    console.print(f"[{style}]{action}[/{style}]  {path}")


def print_task_report(
    created: list[str], modified: list[str], test_success: bool | None, summary: str
) -> None:
    """Terse end-of-task report: what was actually created/modified and whether
    validation actually ran and passed — no line here is printed unless the
    corresponding operation genuinely happened."""
    lines = [f"[bold green]{SUCCESS} TASK COMPLETED[/bold green]", ""]
    if created:
        lines.append("[bold]Created:[/bold]")
        lines.extend(f"  [green]A[/green] {path}" for path in created)
    if modified:
        lines.append("[bold]Modified:[/bold]")
        lines.extend(f"  [yellow]M[/yellow] {path}" for path in modified)
    if test_success is not None:
        style, mark, label = ("green", SUCCESS, "Tests passed") if test_success else ("red", ERROR, "Tests failed")
        lines.append(f"[{style}]{mark} {label}[/{style}]")
    lines.append("")
    lines.append(summary)
    console.print(Panel("\n".join(lines), title="Result", border_style="cyan", padding=(1, 2)))


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

    # prompt_toolkit's non-tty fallback reads the ENTIRE stdin stream on its first
    # .prompt() call rather than one line at a time — confirmed directly: a second,
    # unrelated read (Rich's Confirm.ask(), used by every write_file/shell/docx
    # confirmation) then hits an immediate EOFError because the pipe is already
    # drained. Shift+Tab is meaningless without a real interactive terminal anyway
    # (no human pressing keys), so when stdin isn't a tty — piped input, scripts,
    # CI — fall back to plain console.input(), which reads one line at a time and
    # doesn't fight with Confirm.ask() over the same stream.
    use_prompt_toolkit = sys.stdin.isatty()
    session: PromptSession | None = (
        PromptSession(key_bindings=_build_key_bindings(state)) if use_prompt_toolkit else None
    )

    console.print()
    while True:
        try:
            if session is not None:
                # plain string, no markup parsing — prompt_toolkit doesn't interpret
                # Rich's "[...]" syntax, so no escaping needed here.
                user_input = session.prompt(f"SkyTrap [{state.mode}] > ")
            else:
                # console.input DOES parse Rich markup — "[normal]"/"[plan]"/"[auto]"
                # would otherwise be silently swallowed as an (invalid) style tag,
                # same class of bug fixed earlier for tool output containing "[...]".
                user_input = console.input(f"SkyTrap \\[{state.mode}] > ")
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
