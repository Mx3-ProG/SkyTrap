import shlex
import subprocess
from typing import Callable

from skytrap.core.context import WorkspaceContext
from skytrap.core.tool_safety import classify_command  # noqa: F401 - re-exported for callers/tests
from skytrap.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 60


class ShellTool(Tool):
    name = "shell"
    description = (
        "Run a shell command inside the workspace root. Commands are classified "
        "SAFE (runs immediately), CONFIRM (asks first, auto-approved in auto mode), "
        "DESTRUCTIVE (always asks, e.g. git reset/push/checkout, rm, mv), or FORBIDDEN "
        "(refused). Shell pipes/redirects are not supported — one plain command only. "
        'Arguments: {"command": "<command>"}'
    )

    def __init__(self, confirm: Callable[[str], bool], confirm_destructive: Callable[[str], bool] | None = None):
        """`confirm` is used for CONFIRM-tier commands (mode-aware — may be
        auto-approved). `confirm_destructive`, if given, is used for DESTRUCTIVE-tier
        commands instead and should always ask regardless of mode; defaults to
        `confirm` when not given (so existing single-callback callers keep working)."""
        self._confirm = confirm
        self._confirm_destructive = confirm_destructive or confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        command = arguments.get("command")
        if not command:
            return ToolResult(success=False, output="Missing required argument 'command'")

        classification = classify_command(command)
        if classification == "FORBIDDEN":
            return ToolResult(success=False, output=f"Command blocked (forbidden): {command}")

        if classification in ("CONFIRM", "DESTRUCTIVE"):
            preview = f"Workspace: {workspace.path}\nCommand:   {command}"
            confirm = self._confirm_destructive if classification == "DESTRUCTIVE" else self._confirm
            if not confirm(preview):
                return ToolResult(success=False, output="User declined to run this command.")

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return ToolResult(success=False, output=f"Could not parse command: {exc}")

        try:
            result = subprocess.run(
                tokens,
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return ToolResult(success=False, output=f"Command not found: {tokens[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False, output=f"Command timed out after {TIMEOUT_SECONDS}s: {command}"
            )
        except subprocess.SubprocessError as exc:
            return ToolResult(success=False, output=f"Command failed: {exc}")

        parts = [f"exit code: {result.returncode}"]
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()}")

        return ToolResult(success=result.returncode == 0, output="\n\n".join(parts))
