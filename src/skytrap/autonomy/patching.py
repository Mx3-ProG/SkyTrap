from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import ToolResult
from skytrap.tools.filesystem import resolve_in_workspace


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatchBackup:
    backup_id: str
    path: Path
    before: str
    before_hash: str
    after_hash: str


class PatchEngine:
    """Conflict-aware targeted replacement with in-memory, per-run rollback."""

    def __init__(self) -> None:
        self._backups: dict[str, PatchBackup] = {}

    def apply_replacement(
        self,
        workspace: WorkspaceContext,
        path: str,
        expected: str,
        replacement: str,
        *,
        expected_hash: str | None = None,
    ) -> ToolResult:
        ok, resolved = resolve_in_workspace(workspace, path)
        if not ok:
            return ToolResult(success=False, output=resolved)
        target = Path(resolved)
        if not target.is_file():
            return ToolResult(success=False, output=f"File not found: {path}")
        try:
            before = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(success=False, output=f"Cannot patch {path}: {exc}")
        before_hash = _digest(before)
        if expected_hash is not None and before_hash != expected_hash:
            return ToolResult(success=False, status="failed", output=f"Patch conflict: {path} changed since inspection")
        occurrences = before.count(expected)
        if occurrences != 1:
            return ToolResult(
                success=False,
                output=f"Patch conflict: expected exactly one match in {path}, found {occurrences}",
                metadata={"matches": occurrences, "before_hash": before_hash},
            )
        after = before.replace(expected, replacement, 1)
        backup_id = uuid4().hex
        after_hash = _digest(after)
        self._backups[backup_id] = PatchBackup(backup_id, target, before, before_hash, after_hash)
        try:
            target.write_text(after, encoding="utf-8")
        except OSError as exc:
            self._backups.pop(backup_id, None)
            return ToolResult(success=False, output=f"Could not write {path}: {exc}")
        return ToolResult(
            success=True,
            output=f"Patched {path}",
            metadata={"backup_id": backup_id, "before_hash": before_hash, "after_hash": after_hash},
        )

    def rollback(self, backup_id: str) -> ToolResult:
        backup = self._backups.get(backup_id)
        if backup is None:
            return ToolResult(success=False, output=f"Unknown patch backup: {backup_id}")
        try:
            current = backup.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(success=False, output=f"Cannot rollback patch: {exc}")
        if _digest(current) != backup.after_hash:
            return ToolResult(success=False, output="Rollback conflict: file changed after the patch")
        backup.path.write_text(backup.before, encoding="utf-8")
        self._backups.pop(backup_id, None)
        return ToolResult(success=True, output=f"Rolled back {backup.path.name}")
