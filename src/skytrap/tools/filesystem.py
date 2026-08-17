import difflib
from pathlib import Path
from typing import Callable

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult

MAX_READ_BYTES = 200_000

IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


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


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "List the immediate contents (files and subdirectories) of a directory. "
        'Arguments: {"path": "<path relative to the workspace root, "." for the root>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        path_arg = arguments.get("path", ".")

        ok, resolved = resolve_in_workspace(workspace, path_arg)
        if not ok:
            return ToolResult(success=False, output=resolved)

        dir_path = Path(resolved)
        if not dir_path.exists():
            return ToolResult(success=False, output=f"Directory not found: {path_arg}")
        if not dir_path.is_dir():
            return ToolResult(success=False, output=f"Not a directory: {path_arg}")

        entries = []
        for entry in sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
            if entry.name in IGNORED_DIRS:
                continue
            entries.append(f"{entry.name}/" if entry.is_dir() else entry.name)

        if not entries:
            return ToolResult(success=True, output="(empty directory)")
        return ToolResult(success=True, output="\n".join(entries))


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or overwrite an existing one with new content. The user is "
        "shown a diff and must confirm before anything is actually written to disk. "
        'Arguments: {"path": "<path relative to workspace root>", "content": "<full new file content>"}'
    )

    def __init__(self, confirm: Callable[[str], bool]):
        """`confirm` is given a human-readable preview (diff or new-file content) and
        must return True to proceed with the write, False to abort it."""
        self._confirm = confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        path_arg = arguments.get("path")
        content = arguments.get("content")
        if not path_arg:
            return ToolResult(success=False, output="Missing required argument 'path'")
        if content is None:
            return ToolResult(success=False, output="Missing required argument 'content'")

        ok, resolved = resolve_in_workspace(workspace, path_arg)
        if not ok:
            return ToolResult(success=False, output=resolved)

        file_path = Path(resolved)
        if file_path.exists() and not file_path.is_file():
            return ToolResult(
                success=False, output=f"'{path_arg}' exists and is not a regular file"
            )

        preview = self._build_preview(path_arg, file_path, content)
        if not self._confirm(preview):
            return ToolResult(success=False, output="User declined the write; file not modified.")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ToolResult(success=True, output=f"Wrote {len(content)} characters to {path_arg}")

    @staticmethod
    def _build_preview(path_arg: str, file_path: Path, new_content: str) -> str:
        if not file_path.exists():
            return f"NEW FILE: {path_arg}\n\n{new_content}"

        try:
            old_content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return f"Overwriting binary/unreadable file: {path_arg}\n\n{new_content}"

        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{path_arg}",
                tofile=f"b/{path_arg}",
                lineterm="",
            )
        )
        return "\n".join(diff_lines) or "(no textual changes)"
