import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm
from rich.status import Status
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


@dataclass(frozen=True)
class TerminalCapabilities:
    unicode: bool
    color: bool
    # Whether a live-updating spinner/animation makes sense here at all: a real TTY,
    # not CI, not NO_COLOR-style scripted output. Defaults to True so existing call
    # sites (tests included) that only pass unicode/color keep their prior behavior.
    interactive: bool = True


@dataclass(frozen=True)
class CommandMapEntry:
    command: str
    description: str


UNICODE_SYMBOLS = {
    "explore": "◇",
    "discover": "◆",
    "modify": "⚒",
    "retry": "↻",
    "success": "✓",
    "error": "✗",
    "checkpoint": "✦",
    "warning": "⚠",
    "branch": "├─",
    "last": "╰─",
    "prompt": "❯",
    "treasure": "✦",
}

ASCII_SYMBOLS = {
    "explore": ">",
    "discover": "*",
    "modify": "+",
    "retry": "~",
    "success": "OK",
    "error": "X",
    "checkpoint": "*",
    "warning": "!",
    "branch": "|-",
    "last": "`-",
    "prompt": "$",
    "treasure": "*",
}


def detect_terminal_capabilities(target_console: Console | None = None) -> TerminalCapabilities:
    target = target_console or console
    encoding = (getattr(target.file, "encoding", None) or "").upper()
    unicode_supported = "UTF" in encoding
    color_supported = target.color_system is not None and "NO_COLOR" not in os.environ
    # Animations only make sense on a real interactive terminal: not CI, not a pipe,
    # not something explicitly asking for deterministic non-animated output.
    interactive = (
        target.is_terminal
        and "CI" not in os.environ
        and "SKYTRAP_NO_ANIMATION" not in os.environ
    )
    return TerminalCapabilities(unicode=unicode_supported, color=color_supported, interactive=interactive)


def _symbols(capabilities: TerminalCapabilities) -> dict[str, str]:
    return UNICODE_SYMBOLS if capabilities.unicode else ASCII_SYMBOLS


def _style(capabilities: TerminalCapabilities, style: str) -> str:
    return style if capabilities.color else "none"


def _panel_box(capabilities: TerminalCapabilities):
    return box.ROUNDED if capabilities.unicode else box.ASCII


def generate_command_map(root_command: Any, program: str = "skytrap") -> list[CommandMapEntry]:
    """Build the compact startup map from Click/Typer's real command tree.

    Small command groups are expanded automatically. Large groups remain a single
    discoverable entry, keeping the startup useful on short terminals.
    """
    entries = [CommandMapEntry(program, "Interactive Rabbit Hole session")]

    def usage(command: Any, prefix: str) -> str:
        parts = [prefix]
        for parameter in getattr(command, "params", []):
            if getattr(parameter, "param_type_name", "") != "argument":
                continue
            if not getattr(parameter, "required", False):
                continue
            name = str(getattr(parameter, "name", "value")).upper()
            parts.append(f'"{name}"' if name in {"GOAL", "TASK"} else name)
        return " ".join(parts)

    def add(command: Any, prefix: str) -> None:
        description = command.get_short_help_str(44) or "Open command group"
        entries.append(CommandMapEntry(usage(command, prefix), description))
        children = getattr(command, "commands", None)
        if children and len(children) <= 6:
            for name, child in children.items():
                add(child, f"{prefix} {name}")

    for name, command in getattr(root_command, "commands", {}).items():
        add(command, f"{program} {name}")
    return entries


def print_startup_dashboard(
    workspace: WorkspaceContext,
    model_name: str,
    ollama_online: bool,
    git_state: str,
    execution_mode: str,
    memory_state: str,
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
) -> None:
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    rabbit = "  /)/)\n ( •.•)  SKYTRAP\n / >◇   RABBIT HOLE" if caps.unicode else "  /)/)\n ( o.o)  SKYTRAP\n / >*   RABBIT HOLE"
    logo = Text(rabbit, style=_style(caps, "bold bright_white"))
    status = Table.grid(padding=(0, 1), expand=True)
    status.add_column(style=_style(caps, "dim"), no_wrap=True)
    status.add_column(ratio=1, overflow="fold")

    def row(label: str, value: str, value_style: str | None = None) -> None:
        status.add_row(label, Text(value, style=_style(caps, value_style or "white")))

    row("workspace", str(workspace.path), "cyan")
    row("model", model_name)
    row("ollama", "online" if ollama_online else "offline", "green" if ollama_online else "red")
    row("branch", workspace.branch or "none", "cyan" if workspace.is_git else "yellow")
    row("git", git_state, "green" if git_state == "clean" else "yellow")
    row("mode", execution_mode, "cyan")
    row("memory", memory_state, "green")
    slogan = Text("Descend. Explore. Bring back working code.", style=_style(caps, "dim italic"))
    target.print(
        Panel(
            Group(logo, Text(""), status, Text(""), slogan),
            border_style=_style(caps, "cyan"),
            box=_panel_box(caps),
            padding=(0, 1),
        )
    )


def print_command_map(
    entries: list[CommandMapEntry],
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
    compact: bool = False,
) -> None:
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    table = Table.grid(padding=(0, 2 if compact else 1), expand=True)
    if compact and target.width >= 72:
        table.add_column(ratio=1, overflow="fold")
        table.add_column(ratio=1, overflow="fold")
        split = (len(entries) + 1) // 2
        for index in range(split):
            left = Text(entries[index].command, style=_style(caps, "bold cyan"))
            right_index = index + split
            right = (
                Text(entries[right_index].command, style=_style(caps, "bold cyan"))
                if right_index < len(entries)
                else Text("")
            )
            table.add_row(left, right)
    else:
        table.add_column(ratio=2, overflow="fold")
        if not compact:
            table.add_column(ratio=3, overflow="fold")
        for entry in entries:
            row = [Text(entry.command, style=_style(caps, "bold cyan"))]
            if not compact:
                row.append(Text(entry.description, style=_style(caps, "dim")))
            table.add_row(*row)
    target.print(
        Panel(
            table,
            title="TREASURE MAP",
            title_align="left",
            border_style=_style(caps, "bright_black"),
            box=_panel_box(caps),
            padding=(0, 1),
        )
    )


def print_agent_event(
    event: dict,
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
) -> None:
    """Render one real AgentLoop event; state snapshots remain intentionally silent."""
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    symbol = _symbols(caps)
    kind = event.get("kind")
    if kind == "intent_interpreted":
        confidence = float(event.get("confidence", 0.0))
        target.print(
            Text(
                f"{symbol['discover']} intent understood · confidence {confidence:.0%} · risk {event.get('risk', 'unknown')}",
                style=_style(caps, "cyan"),
            )
        )
    elif kind == "working_assumption":
        target.print(
            Text(
                f"{symbol['explore']} {event.get('assumption', '')}",
                style=_style(caps, "yellow"),
            )
        )
    elif kind == "path_forks":
        print_path_forks(
            event.get("paths") or [],
            event.get("question") or "Which path did you mean?",
            target_console=target,
            capabilities=caps,
        )
    elif kind == "exploration_started":
        target.print(Text(f"{symbol['explore']} descending into {event.get('target', 'workspace')}...", style=_style(caps, "cyan")))
    elif kind == "plan_created":
        target.print(Text(f"{symbol['branch']} {symbol['discover']} mapped {event.get('steps', 0)} steps · {event.get('files', 0)} files", style=_style(caps, "cyan")))
    elif kind == "tool_result":
        tool = event.get("tool", "tool")
        arguments = event.get("arguments") or {}
        target_value = arguments.get("path") or arguments.get("query") or arguments.get("command") or ""
        mutating = tool in {"write_file", "patch_file", "delete_file"}
        mark = symbol["modify"] if mutating else symbol["discover"]
        style = "yellow" if mutating else "cyan"
        outcome = "done" if event.get("success") else "failed"
        target.print(Text(f"{symbol['branch']} {mark} {tool} {target_value} · {outcome}".rstrip(), style=_style(caps, style if event.get("success") else "red")))
    elif kind == "verification_started":
        target.print(Text(f"{symbol['branch']} {symbol['explore']} verifying the tunnels...", style=_style(caps, "cyan")))
    elif kind == "verification_stage":
        stage = event.get("stage", "check")
        if event.get("skipped"):
            label, style = "SKIP", "bright_black"
        elif event.get("success"):
            label, style = "PASS", "green"
        else:
            label, style = "FAIL", "red"
        target.print(Text(f"{symbol['branch']} {symbol['explore']} {stage} ........ {label}", style=_style(caps, style)))
    elif kind == "retry":
        target.print(Text(f"{symbol['branch']} {symbol['retry']} following another tunnel · revision {event.get('revision')}", style=_style(caps, "yellow")))
    elif kind == "checkpoint":
        mark = symbol["checkpoint"] if event.get("success") else symbol["error"]
        target.print(Text(f"{symbol['branch']} {mark} checkpoint {'sealed' if event.get('success') else 'failed'}", style=_style(caps, "green" if event.get("success") else "red")))
    elif kind == "task_stopped":
        target.print(Text(f"{symbol['last']} {symbol['warning']} descent stopped; state preserved", style=_style(caps, "yellow")))
    elif kind == "task_error":
        target.print(Text(f"{symbol['last']} {symbol['error']} {event.get('error', 'task failed')}", style=_style(caps, "red")))
    elif kind == "task_completed":
        print_treasure_found(target_console=target, capabilities=caps)


def print_treasure_found(
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
) -> None:
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    symbol = _symbols(caps)
    target.print(Text(f"{symbol['last']} {symbol['treasure']} TREASURE FOUND", style=_style(caps, "bold green")))


def print_path_forks(
    paths: list[str],
    question: str,
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
) -> None:
    """Render only the plausible paths supplied by the intent runtime."""
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    body = Text()
    for index, path in enumerate(paths[:3]):
        body.append(f"{chr(65 + index)}  ", style=_style(caps, "bold yellow"))
        body.append(str(path) + "\n", style=_style(caps, "white"))
    body.append(question, style=_style(caps, "bold white"))
    target.print(
        Panel(
            body,
            title="? THE PATH FORKS",
            title_align="left",
            border_style=_style(caps, "yellow"),
            box=_panel_box(caps),
            padding=(0, 1),
        )
    )


def print_risk_action(
    level: str,
    action: str,
    scope: str,
    *,
    target_console: Console | None = None,
    capabilities: TerminalCapabilities | None = None,
) -> None:
    target = target_console or console
    caps = capabilities or detect_terminal_capabilities(target)
    warning = _symbols(caps)["warning"]
    body = Text()
    body.append(f"{action}\n", style=_style(caps, "bold white"))
    body.append(f"Scope: {scope}", style=_style(caps, "red"))
    target.print(
        Panel(
            body,
            title=f"{warning} {level} RISK ACTION",
            title_align="left",
            border_style=_style(caps, "bold red"),
            box=_panel_box(caps),
            padding=(0, 1),
        )
    )


DIFF_CONTEXT_KEEP_EDGES = 2  # unchanged lines kept at the start/end of a collapsed run
DIFF_CONTEXT_COLLAPSE_ABOVE = 6  # a run of unchanged lines longer than this gets collapsed


def _split_diff_hunks(diff_text: str) -> list[tuple[str, list[str]]]:
    """Splits a unified diff (as produced by difflib.unified_diff) into
    (hunk_header, body_lines) pairs, dropping the leading `---`/`+++` file header
    lines — the panel title already names the file."""
    hunks: list[tuple[str, list[str]]] = []
    body: list[str] | None = None
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            body = []
            hunks.append((line, body))
            continue
        if body is not None:
            body.append(line)
    return hunks


def format_diff_body(
    diff_text: str,
    capabilities: TerminalCapabilities,
    *,
    full: bool = False,
) -> tuple[Text, int]:
    """Renders a unified diff as colored Rich Text: removed lines red, added lines
    green, hunk headers cyan, context lines default. Long runs of unchanged context
    lines within a hunk are collapsed to "... N unchanged lines hidden ..." unless
    `full` is set. Returns (rendered_text, total_hidden_line_count)."""
    body = Text()
    hunks = _split_diff_hunks(diff_text)
    total_hidden = 0

    if not hunks:
        stripped = diff_text.strip()
        style = _style(capabilities, "white" if stripped else "dim")
        body.append(stripped or "(no textual changes)", style=style)
        return body, 0

    for index, (header, lines) in enumerate(hunks):
        if index > 0:
            body.append("\n")
        body.append(header + "\n", style=_style(capabilities, "cyan"))

        if full or len(lines) <= DIFF_CONTEXT_COLLAPSE_ABOVE:
            display_lines = lines
        else:
            display_lines = []
            position = 0
            while position < len(lines):
                current = lines[position]
                if current.startswith(("+", "-")):
                    display_lines.append(current)
                    position += 1
                    continue
                end = position
                while end < len(lines) and not lines[end].startswith(("+", "-")):
                    end += 1
                run = lines[position:end]
                if len(run) <= 2 * DIFF_CONTEXT_KEEP_EDGES:
                    display_lines.extend(run)
                else:
                    hidden = len(run) - 2 * DIFF_CONTEXT_KEEP_EDGES
                    total_hidden += hidden
                    display_lines.extend(run[:DIFF_CONTEXT_KEEP_EDGES])
                    display_lines.append(f"\0HIDDEN\0{hidden}")
                    display_lines.extend(run[-DIFF_CONTEXT_KEEP_EDGES:])
                position = end

        for line in display_lines:
            if line.startswith("\0HIDDEN\0"):
                count = line.removeprefix("\0HIDDEN\0")
                body.append(
                    f"    ... {count} unchanged lines hidden ...\n",
                    style=_style(capabilities, "dim italic"),
                )
            elif line.startswith("+"):
                body.append(line + "\n", style=_style(capabilities, "green"))
            elif line.startswith("-"):
                body.append(line + "\n", style=_style(capabilities, "red"))
            else:
                body.append(line + "\n", style=_style(capabilities, "white"))

    return body, total_hidden


def render_diff_panel(
    path: str,
    diff_text: str,
    *,
    capabilities: TerminalCapabilities | None = None,
    full: bool = False,
    title_prefix: str = "PATCH",
) -> Panel:
    caps = capabilities or detect_terminal_capabilities()
    body, hidden = format_diff_body(diff_text, caps, full=full)
    if hidden and not full:
        body.append(
            f"\n(showing collapsed context — {hidden} unchanged lines hidden; pass --full-diff for the complete patch)",
            style=_style(caps, "dim"),
        )
    return Panel(
        body,
        title=f"{title_prefix} {path}",
        title_align="left",
        border_style=_style(caps, "bright_cyan"),
        box=_panel_box(caps),
        padding=(0, 1),
    )


ACTIVITY_LABELS: dict[str, str] = {
    "list_directory": "Exploring repository...",
    "search_code": "Searching code...",
    "write_file": "Applying patch...",
    "patch_file": "Applying patch...",
    "delete_file": "Applying patch...",
    "run_tests": "Running tests...",
    "git_diff": "Reviewing changes...",
    "git_status": "Reviewing changes...",
}

VERIFICATION_ACTIVITY_LABELS: dict[str, str] = {
    "lint": "Running lint...",
    "typecheck": "Running typecheck...",
    "test": "Running tests...",
    "build": "Building project...",
}


def activity_label_for_tool(tool_name: str, arguments: dict | None = None) -> str:
    """Maps a tool call to one of the human-facing activity descriptions from the
    Live Diff / Working Animation spec. Falls back to a generic "Running <tool>..."
    for tools with no dedicated phrasing, and never invents progress beyond what the
    tool call itself represents."""
    arguments = arguments or {}
    if tool_name == "read_file":
        path = arguments.get("path", "file")
        return f"Reading {path}..."
    return ACTIVITY_LABELS.get(tool_name, f"Running {tool_name}...")


class AgentRenderer:
    """Presentation layer for one autonomous/agentic run. Owns the working
    spinner, the permanent timeline, live diffs, the file counter, and the final
    summary — the runtime (AgentLoop, run_agent_turn, ...) only emits structured
    events; this class alone decides how (or whether, on a non-interactive/CI
    target) to render them. No event here is ever printed unless the runtime
    actually emitted it — no fabricated progress.
    """

    def __init__(
        self,
        target_console: Console | None = None,
        capabilities: TerminalCapabilities | None = None,
        full_diff: bool = False,
    ) -> None:
        self.console = target_console or console
        self.caps = capabilities or detect_terminal_capabilities(self.console)
        self.full_diff = full_diff

        self._status: Status | None = None
        self._ticker: threading.Thread | None = None
        self._stop_ticker: threading.Event | None = None
        self._activity_label: str | None = None
        self._activity_started_at: float = 0.0

        # Accumulated state used to build the final summary — every field here is
        # populated exclusively from real events, never guessed.
        self.files: dict[str, str] = {}
        self.additions = 0
        self.deletions = 0
        self.verification: dict[str, bool] = {}
        self.checkpoint_commit: str | None = None
        self.final_diff: str | None = None

    # -- working animation --------------------------------------------------

    def _activity_text(self) -> Text:
        elapsed = time.monotonic() - self._activity_started_at
        suffix = f" · {elapsed:.1f}s" if elapsed >= 3 else ""
        return Text(f"{self._activity_label}{suffix}", style=_style(self.caps, "cyan"))

    def _tick(self, stop: threading.Event) -> None:
        while not stop.wait(0.5):
            status = self._status
            if status is None:
                return
            try:
                status.update(self._activity_text())
            except Exception:  # noqa: BLE001 - a rendering hiccup must never crash the run
                return

    def start_activity(self, label: str) -> None:
        """Starts (or replaces) the current "what SkyTrap is doing right now"
        indicator. A spinner on an interactive TTY; a single deterministic log
        line everywhere else (CI, piped output, NO_COLOR-style scripts)."""
        self.finish_activity()
        self._activity_label = label
        self._activity_started_at = time.monotonic()
        if self.caps.interactive:
            self._status = self.console.status(self._activity_text(), spinner="dots")
            self._status.start()
            self._stop_ticker = threading.Event()
            self._ticker = threading.Thread(
                target=self._tick, args=(self._stop_ticker,), daemon=True
            )
            self._ticker.start()
        else:
            self.console.print(Text(label, style=_style(self.caps, "dim")))

    def update_activity(self, label: str) -> None:
        """Updates the label of the currently running activity without ending it
        (e.g. a long shell command's elapsed duration)."""
        if label == self._activity_label:
            return
        self._activity_label = label
        if self._status is not None:
            self._status.update(self._activity_text())
        elif not self.caps.interactive:
            self.console.print(Text(label, style=_style(self.caps, "dim")))

    def finish_activity(self) -> None:
        """Stops the spinner immediately — called before rendering any timeline
        line, and always on error, so nothing spins forever."""
        if self._stop_ticker is not None:
            self._stop_ticker.set()
            self._stop_ticker = None
        self._ticker = None
        if self._status is not None:
            self._status.stop()
            self._status = None
        self._activity_label = None

    # -- diffs ---------------------------------------------------------------

    def render_diff(self, path: str, diff_text: str, *, title_prefix: str = "PATCH") -> None:
        self.finish_activity()
        self.console.print(
            render_diff_panel(
                path,
                diff_text,
                capabilities=self.caps,
                full=self.full_diff,
                title_prefix=title_prefix,
            )
        )

    def render_file_counter(self) -> None:
        modified = sum(1 for kind in self.files.values() if kind == "modified")
        created = sum(1 for kind in self.files.values() if kind == "created")
        deleted = sum(1 for kind in self.files.values() if kind == "deleted")
        self.console.print(
            Text(
                f"FILES {modified} modified · {created} created · {deleted} deleted",
                style=_style(self.caps, "dim"),
            )
        )

    def _track_tool_result(self, event: dict) -> None:
        tool = event.get("tool")
        if tool not in {"write_file", "patch_file", "delete_file"} or not event.get("success"):
            return
        path = (event.get("arguments") or {}).get("path")
        if not path:
            return
        metadata = event.get("metadata") or {}
        if tool == "delete_file" or metadata.get("is_delete"):
            self.files[path] = "deleted"
        elif metadata.get("is_new_file"):
            self.files[path] = "created"
        else:
            self.files.setdefault(path, "modified")
        self.additions += metadata.get("added_lines", 0)
        self.deletions += metadata.get("removed_lines", 0)

    # -- tool results / risk prompts ------------------------------------------

    def render_tool_result(self, event: dict) -> None:
        self.finish_activity()
        print_agent_event(event, target_console=self.console, capabilities=self.caps)
        if event.get("kind") != "tool_result":
            return
        self._track_tool_result(event)
        metadata = event.get("metadata") or {}
        diff_text = metadata.get("diff")
        if diff_text and event.get("success") and event.get("tool") in {"write_file", "patch_file", "delete_file"}:
            path = (event.get("arguments") or {}).get("path", "?")
            title = "DELETE" if metadata.get("is_delete") else "PATCH"
            self.render_diff(path, diff_text, title_prefix=title)
            self.render_file_counter()

    def render_risk_prompt(self, level: str, action: str, scope: str) -> None:
        self.finish_activity()
        print_risk_action(level, action, scope, target_console=self.console, capabilities=self.caps)

    # -- summary ---------------------------------------------------------------

    def render_summary(self) -> None:
        self.finish_activity()
        modified = sum(1 for kind in self.files.values() if kind == "modified")
        created = sum(1 for kind in self.files.values() if kind == "created")
        deleted = sum(1 for kind in self.files.values() if kind == "deleted")

        lines = [
            f"{len(self.files)} files touched  ({modified} modified · {created} created · {deleted} deleted)",
            f"+{self.additions} additions",
            f"-{self.deletions} deletions",
        ]
        for stage in ("lint", "typecheck", "test", "build"):
            if stage in self.verification:
                label, style = ("PASS", "green") if self.verification[stage] else ("FAIL", "red")
                lines.append(f"{stage.capitalize():<13} {label}")
        if self.checkpoint_commit:
            lines.append(f"Checkpoint    {self.checkpoint_commit[:12]}")

        symbol = _symbols(self.caps)["treasure"]
        self.console.print(
            Panel(
                Text("\n".join(lines)),
                title=f"{symbol} TREASURE FOUND",
                title_align="left",
                border_style=_style(self.caps, "bold green"),
                box=_panel_box(self.caps),
                padding=(0, 1),
            )
        )
        if self.final_diff:
            self.render_diff("(all changes)", self.final_diff, title_prefix="FINAL DIFF")

    # -- event dispatch ----------------------------------------------------

    @staticmethod
    def _label_for_activity_event(event: dict) -> str:
        """Translates a structured "activity" event (runtime-emitted, contains no
        presentation text of its own) into one of the human-facing phrases from the
        Live Diff / Working Animation spec. An explicit "label" is honored as-is for
        callers (e.g. run_agent_turn's on_step bridge) that don't have a `phase`."""
        if event.get("label"):
            return str(event["label"])
        phase = event.get("phase")
        if phase == "planning":
            return "Planning next move..."
        if phase == "checkpoint":
            return "Creating checkpoint..."
        if phase == "verification":
            stage = event.get("stage", "")
            return VERIFICATION_ACTIVITY_LABELS.get(stage, f"Running {stage}...")
        if phase == "tool_call":
            return activity_label_for_tool(event.get("tool") or "", event.get("arguments"))
        return "Working..."

    def handle_event(self, event: dict) -> None:
        """Single entry point the runtime feeds every emitted event through. Real
        events only — see the AgentLoop/run_agent_turn emitters this is wired to."""
        kind = event.get("kind")

        if kind == "activity":
            self.start_activity(self._label_for_activity_event(event))
            return

        if kind == "verification_stage" and not event.get("skipped"):
            self.verification[event.get("stage", "")] = bool(event.get("success"))

        if kind == "checkpoint":
            metadata = event.get("metadata") or {}
            self.checkpoint_commit = metadata.get("checkpoint_commit")
            self.final_diff = metadata.get("diff") or self.final_diff

        if kind == "tool_result":
            self.render_tool_result(event)
            return

        if kind == "task_completed":
            # print_agent_event's own plain "TREASURE FOUND" line is superseded by
            # the richer summary panel built from accumulated real events.
            self.render_summary()
            return

        self.finish_activity()
        print_agent_event(event, target_console=self.console, capabilities=self.caps)


def build_rabbit_prompt(
    workspace: WorkspaceContext,
    state: "ChatState",
    capabilities: TerminalCapabilities | None = None,
) -> str:
    caps = capabilities or detect_terminal_capabilities()
    try:
        display_path = f"~/{workspace.path.relative_to(Path.home())}"
    except ValueError:
        display_path = str(workspace.path)
    return f"rabbit@skytrap {display_path} [{state.mode}] {_symbols(caps)['prompt']} "


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
    """Compatibility wrapper using the single Rabbit Hole command-map renderer."""
    print_command_map(
        [CommandMapEntry(f"skytrap {name}", help_text) for name, help_text in rows]
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


def run_chat_loop(
    respond: Callable[[str, ChatState], None],
    state: ChatState | None = None,
    workspace: WorkspaceContext | None = None,
) -> None:
    state = state or ChatState()
    workspace = workspace or WorkspaceContext(
        path=Path.cwd(), name=Path.cwd().name, is_git=False
    )

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
                user_input = session.prompt(build_rabbit_prompt(workspace, state))
            else:
                # console.input DOES parse Rich markup — "[normal]"/"[plan]"/"[auto]"
                # would otherwise be silently swallowed as an (invalid) style tag,
                # same class of bug fixed earlier for tool output containing "[...]".
                prompt = build_rabbit_prompt(workspace, state)
                user_input = console.input(prompt.replace("[", "\\["))
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
