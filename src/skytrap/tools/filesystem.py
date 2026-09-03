import difflib
from pathlib import Path
from typing import Callable

from skytrap.core.context import WorkspaceContext
from skytrap.core.tool_safety import classify_path
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
        "Create a new file or overwrite an existing one with new content. Ordinary "
        "source files are written immediately, no confirmation needed. Paths that look "
        "like secrets/credentials (.env, *secret*, *credential*, *.pem, keys) show a "
        "diff and require the user's confirmation first. "
        'Arguments: {"path": "<path relative to workspace root>", "content": "<full new file content>"}'
    )

    def __init__(self, confirm: Callable[[str], bool], on_write: Callable[[str], None] | None = None):
        """`confirm` is given a human-readable preview (diff or new-file content) and
        must return True to proceed with the write, False to abort it. `on_write`, if
        given, is called with the relative path after a successful write — used to
        scope a diff summary to only what was actually touched, rather than the whole
        working tree (which may have unrelated pre-existing uncommitted changes)."""
        self._confirm = confirm
        self._on_write = on_write

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

        is_new_file, diff_text, added, removed = self._compute_diff(path_arg, file_path, content)

        if classify_path(path_arg) == "DESTRUCTIVE":
            preview = self._build_preview(path_arg, is_new_file, diff_text, content)
            if not self._confirm(preview):
                return ToolResult(
                    success=False, output="User declined the write; file not modified."
                )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        if self._on_write:
            self._on_write(path_arg)
        return ToolResult(
            success=True,
            output=f"Wrote {len(content)} characters to {path_arg}",
            metadata={
                "diff": diff_text,
                "is_new_file": is_new_file,
                "added_lines": added,
                "removed_lines": removed,
            },
        )

    @staticmethod
    def _compute_diff(
        path_arg: str, file_path: Path, new_content: str
    ) -> tuple[bool, str, int, int]:
        """Computes a unified diff (and +/- line counts) between what's on disk and
        `new_content`, unconditionally — used both for the DESTRUCTIVE-path
        confirmation preview and for live diff rendering of every write, regardless
        of confirmation tier. Returns (is_new_file, diff_text, added, removed)."""
        if not file_path.exists():
            added = len(new_content.splitlines())
            diff_lines = list(
                difflib.unified_diff(
                    [],
                    new_content.splitlines(),
                    fromfile="/dev/null",
                    tofile=f"b/{path_arg}",
                    lineterm="",
                )
            )
            return True, "\n".join(diff_lines), added, 0

        try:
            old_content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return False, "(binary/unreadable file — no diff available)", 0, 0

        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{path_arg}",
                tofile=f"b/{path_arg}",
                lineterm="",
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return False, "\n".join(diff_lines), added, removed

    @staticmethod
    def _build_preview(path_arg: str, is_new_file: bool, diff_text: str, new_content: str) -> str:
        if is_new_file:
            return f"NEW FILE: {path_arg}\n\n{new_content}"
        return diff_text or "(no textual changes)"


MAX_DELETE_PREVIEW_CHARS = 2_000


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete an existing file. Ordinary files are deleted immediately (git-"
        "recoverable, no confirmation needed). Paths that look like secrets/"
        "credentials (.env, *secret*, *credential*, *.pem, keys) show a preview and "
        "require the user's confirmation first. Directories cannot be deleted with "
        'this tool. Arguments: {"path": "<path relative to the workspace root>"}'
    )

    def __init__(self, confirm: Callable[[str], bool], on_delete: Callable[[str], None] | None = None):
        """`confirm` is given a human-readable preview of what's about to be deleted
        and must return True to proceed, False to abort — same contract as
        WriteFileTool's `confirm`. `on_delete`, if given, is called with the relative
        path after a successful deletion — same purpose as WriteFileTool's `on_write`."""
        self._confirm = confirm
        self._on_delete = on_delete

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
            return ToolResult(
                success=False, output=f"'{path_arg}' is not a regular file — refusing to delete"
            )

        diff_text, removed = self._compute_diff(path_arg, file_path)

        if classify_path(path_arg) == "DESTRUCTIVE":
            preview = self._build_preview(path_arg, file_path)
            if not self._confirm(preview):
                return ToolResult(
                    success=False, output="User declined the deletion; file not removed."
                )

        file_path.unlink()
        if self._on_delete:
            self._on_delete(path_arg)
        return ToolResult(
            success=True,
            output=f"Deleted {path_arg}",
            metadata={
                "diff": diff_text,
                "is_new_file": False,
                "is_delete": True,
                "added_lines": 0,
                "removed_lines": removed,
            },
        )

    @staticmethod
    def _compute_diff(path_arg: str, file_path: Path) -> tuple[str, int]:
        """Unified diff representing the whole file going away — same shape as
        WriteFileTool's diff, so the renderer can treat both uniformly."""
        try:
            old_content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return "(binary/unreadable file — no diff available)", 0
        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(),
                [],
                fromfile=f"a/{path_arg}",
                tofile="/dev/null",
                lineterm="",
            )
        )
        return "\n".join(diff_lines), len(old_content.splitlines())

    @staticmethod
    def _build_preview(path_arg: str, file_path: Path) -> str:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return f"DELETE FILE: {path_arg} (binary/unreadable content)"
        truncated = content[:MAX_DELETE_PREVIEW_CHARS]
        if len(content) > MAX_DELETE_PREVIEW_CHARS:
            truncated += "\n... (truncated)"
        return f"DELETE FILE: {path_arg}\n\n{truncated}"
