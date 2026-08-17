import subprocess
from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import IGNORED_DIRS, resolve_in_workspace

MAX_RESULT_LINES = 200


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Search the codebase for a text pattern using ripgrep. Returns each match as "
        "'file:line:content'. "
        'Arguments: {"query": "<pattern>", "path": "<optional relative path to search, '
        'default the whole workspace>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        query = arguments.get("query")
        if not query:
            return ToolResult(success=False, output="Missing required argument 'query'")

        path_arg = arguments.get("path", ".")
        ok, resolved = resolve_in_workspace(workspace, path_arg)
        if not ok:
            return ToolResult(success=False, output=resolved)

        target = Path(resolved)
        if not target.exists():
            return ToolResult(success=False, output=f"Path not found: {path_arg}")

        command = ["rg", "--line-number", "--no-heading", "--color=never"]
        for ignored in IGNORED_DIRS:
            command += ["--glob", f"!{ignored}/**"]
        command += [query, str(target.relative_to(workspace.path))]

        try:
            result = subprocess.run(
                command,
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            return ToolResult(success=False, output="ripgrep ('rg') is not installed")
        except subprocess.SubprocessError as exc:
            return ToolResult(success=False, output=f"Search failed: {exc}")

        # rg exits 1 when there are simply no matches — that's a successful, empty search.
        if result.returncode not in (0, 1):
            return ToolResult(success=False, output=f"ripgrep error: {result.stderr.strip()}")

        lines = result.stdout.splitlines()
        if not lines:
            return ToolResult(success=True, output="No matches found.")

        truncated = lines[:MAX_RESULT_LINES]
        output = "\n".join(truncated)
        if len(lines) > MAX_RESULT_LINES:
            output += f"\n... ({len(lines) - MAX_RESULT_LINES} more matches truncated)"
        return ToolResult(success=True, output=output)
