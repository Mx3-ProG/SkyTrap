import shlex
import subprocess

from skytrap.core.context import WorkspaceContext
from skytrap.core.language_detection import detect_languages
from skytrap.core.project_inspection import resolve_commands
from skytrap.tools.base import Tool, ToolResult

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 4000


def _detect_command(workspace: WorkspaceContext) -> str | None:
    """Picks the test command from the highest-confidence detected language (manifest
    match beats bare file-extension count) via the LanguageProfile registry —
    project-appropriate for Python/JS/TS/Rust/Go/C/C++/C#/Ruby instead of a
    hardcoded pytest-or-npm guess. Falls through languages in detection order until
    one actually has a test command to offer (e.g. a Rust workspace with no test
    files yet has nothing to run)."""
    for match in detect_languages(workspace):
        for command in resolve_commands(workspace, match).test_commands:
            if command:
                return command
    return None


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Run the project's test suite. Auto-detects the right command from the "
        "workspace's language/build files (pytest, npm/pnpm/yarn/bun test, cargo "
        "test, go test, dotnet test, rspec/rails test, ctest/make test — whichever "
        "the detected language and its manifest indicate). Always safe to run "
        "without confirmation — it only reads and executes tests, never mutates "
        "the workspace. "
        'Arguments: {"command": "<optional explicit test command, overrides detection>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        command = arguments.get("command") or _detect_command(workspace)
        if not command:
            return ToolResult(
                success=False,
                output=(
                    "Could not detect a test runner for any language found in this "
                    "workspace. Pass an explicit 'command' argument."
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
