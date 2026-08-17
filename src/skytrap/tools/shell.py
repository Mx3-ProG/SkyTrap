import re
import shlex
import subprocess
from typing import Callable, Literal

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 60

FORBIDDEN_PATTERNS = [
    r"\bsudo\b",
    r"\brm\s+-rf\s+/(\s|$)",
    r"\bdiskutil\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]

SAFE_PREFIXES = [
    ("ls",),
    ("pwd",),
    ("cat",),
    ("find",),
    ("grep",),
    ("rg",),
    ("wc",),
    ("head",),
    ("tail",),
    ("which",),
    ("echo",),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "branch"),
    ("git", "show"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("yarn", "test"),
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
]

CONFIRM_PREFIXES = [
    ("npm", "install"),
    ("npm", "ci"),
    ("npm", "run"),
    ("yarn", "install"),
    ("pip", "install"),
    ("uv", "pip", "install"),
    ("git", "commit"),
    ("git", "checkout"),
    ("git", "reset"),
    ("git", "push"),
    ("git", "merge"),
    ("git", "rebase"),
    ("git", "add"),
    ("rm",),
    ("mv",),
    ("python",),
    ("python3",),
    ("node",),
]

Classification = Literal["SAFE", "CONFIRM", "FORBIDDEN"]


def classify_command(command: str) -> Classification:
    if any(re.search(pattern, command) for pattern in FORBIDDEN_PATTERNS):
        return "FORBIDDEN"

    try:
        tokens = tuple(shlex.split(command))
    except ValueError:
        return "CONFIRM"  # unparsable quoting — don't guess, ask the user

    for prefix in SAFE_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return "SAFE"

    for prefix in CONFIRM_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return "CONFIRM"

    # Unknown command: default to the safer choice, not silent execution.
    return "CONFIRM"


class ShellTool(Tool):
    name = "shell"
    description = (
        "Run a shell command inside the workspace root. Commands are classified "
        "SAFE (runs immediately), CONFIRM (asks the user first), or FORBIDDEN "
        "(refused). Shell pipes/redirects are not supported — one plain command only. "
        'Arguments: {"command": "<command>"}'
    )

    def __init__(self, confirm: Callable[[str], bool]):
        self._confirm = confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        command = arguments.get("command")
        if not command:
            return ToolResult(success=False, output="Missing required argument 'command'")

        classification = classify_command(command)
        if classification == "FORBIDDEN":
            return ToolResult(success=False, output=f"Command blocked (forbidden): {command}")

        if classification == "CONFIRM":
            preview = f"Workspace: {workspace.path}\nCommand:   {command}"
            if not self._confirm(preview):
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
