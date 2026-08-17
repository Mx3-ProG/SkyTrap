from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult

MAX_READ_BYTES = 200_000


def resolve_in_workspace(workspace: WorkspaceContext, relative_path: str) -> tuple[bool, str]:
    """Resolves a path against the workspace root, refusing anything that escapes it.

    Returns (ok, resolved_path_or_error_message).
    """
    resolved = (workspace.path / relative_path).resolve()
    try:
        resolved.relative_to(workspace.path)
    except ValueError:
        return False, f"Access denied: '{relative_path}' is outside the workspace"
    return True, str(resolved)


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the full text content of a file. "
        'Arguments: {"path": "<path relative to the workspace root>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        path_arg = arguments.get("path")
        if not path_arg:
            return ToolResult(success=False, output="Missing required argument 'path'")

        ok, resolved = resolve_in_workspace(workspace, path_arg)
        if not ok:
            return ToolResult(success=False, output=resolved)

        file_path = Path(resolved)
        if not file_path.exists():
            return ToolResult(success=False, output=f"File not found: {path_arg}")
        if not file_path.is_file():
            return ToolResult(success=False, output=f"Not a file: {path_arg}")
        if file_path.stat().st_size > MAX_READ_BYTES:
            return ToolResult(
                success=False,
                output=f"File too large to read (> {MAX_READ_BYTES} bytes): {path_arg}",
            )

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return ToolResult(success=False, output=f"Cannot read binary file: {path_arg}")

        return ToolResult(success=True, output=content)
