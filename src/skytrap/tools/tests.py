import json
import shlex
import subprocess

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 4000


def _detect_command(workspace: WorkspaceContext) -> str | None:
    root = workspace.path

    if (root / "pyproject.toml").exists():
        return "uv run pytest"
    if (root / "pytest.ini").exists() or (root / "setup.cfg").exists():
        return "pytest"

    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if "test" in data.get("scripts", {}):
            return "npm test"

    return None


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Run the project's test suite. Auto-detects pytest (pyproject.toml/pytest.ini/"
        "setup.cfg) or `npm test` (package.json with a 'test' script). Always safe to "
        "run without confirmation — it only reads and executes tests, never mutates "
        "the workspace. "
        'Arguments: {"command": "<optional explicit test command, overrides detection>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        command = arguments.get("command") or _detect_command(workspace)
        if not command:
            return ToolResult(
                success=False,
                output=(
                    "Could not detect a test runner (no pyproject.toml/pytest.ini/"
                    "setup.cfg and no package.json with a 'test' script). Pass an "
                    "explicit 'command' argument."
                ),
            )

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
                success=False, output=f"Tests timed out after {TIMEOUT_SECONDS}s: {command}"
            )
        except subprocess.SubprocessError as exc:
            return ToolResult(success=False, output=f"Failed to run tests: {exc}")

        parts = [f"command: {command}", f"exit code: {result.returncode}"]
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()[-MAX_OUTPUT_CHARS:]}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()[-MAX_OUTPUT_CHARS:]}")

        return ToolResult(success=result.returncode == 0, output="\n\n".join(parts))
