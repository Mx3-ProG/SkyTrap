"""Item 14 — RepositoryMemory: a compact, persisted summary of what SkyTrap
already knows about a repository, fingerprinted to the Git HEAD (+dirty
flag) it was computed against. A stale fingerprint means the repo changed
since — callers must treat it as invalidated and recompute rather than trust
possibly-outdated architecture/convention claims.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from skytrap.intelligence.snapshot import RepositorySnapshot
from skytrap.intelligence.symbols import SymbolIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS repository_memory (
    workspace_path TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RepositoryMemory(BaseModel):
    workspace_path: str
    fingerprint: str
    branch: str | None = None
    git_commit: str | None = None
    architecture: str = ""
    conventions: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    important_decisions: list[str] = Field(default_factory=list)
    critical_areas: list[str] = Field(default_factory=list)
    incidents: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    # Item 3 — CONSUME REPOSITORY MEMORY. The compact, serialized Tree-sitter
    # output (path -> ParsedFile.model_dump()) for every indexed file — this is
    # what lets a fingerprint-matching task skip re-parsing the whole repository
    # (SymbolIndex.restore_from) instead of only reusing prose strings.
    parsed_files: dict[str, dict] = Field(default_factory=dict)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RepositorySnapshot,
        *,
        decisions: list[str] | None = None,
        symbol_index: SymbolIndex | None = None,
    ) -> "RepositoryMemory":
        return cls(
            workspace_path=snapshot.root,
            fingerprint=snapshot.fingerprint,
            branch=snapshot.git.branch,
            git_commit=snapshot.git.head,
            architecture=(
                f"{', '.join(snapshot.languages) or 'unknown language(s)'}; "
                f"{', '.join(snapshot.frameworks) or 'no framework detected'}; "
                f"build via {', '.join(snapshot.build_system) or 'unknown'}"
            ),
            conventions=snapshot.conventions.guidance(),
            components=snapshot.entrypoints,
            important_decisions=decisions or [],
            critical_areas=snapshot.manifests,
            commands=[],
            relations=[],
            parsed_files=(
                {
                    path: symbol_index.parsed_file(path).model_dump(mode="json")
                    for path in symbol_index.files()
                }
                if symbol_index is not None
                else {}
            ),
        )


class RepositoryMemoryStore:
    """Reuses the same sqlite database as SqliteMemory (`~/.skytrap/skytrap.db`
    by default) — a `RepositoryMemory` is small, workspace-scoped, and durable
    across separate `skytrap` invocations, exactly like session notes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def save(self, memory: RepositoryMemory) -> None:
        self._conn.execute(
            "INSERT INTO repository_memory (workspace_path, fingerprint, data, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(workspace_path) DO UPDATE SET fingerprint=excluded.fingerprint, "
            "data=excluded.data, updated_at=excluded.updated_at",
            (
                memory.workspace_path,
                memory.fingerprint,
                memory.model_dump_json(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def load(self, workspace_path: str) -> RepositoryMemory | None:
        row = self._conn.execute(
            "SELECT data FROM repository_memory WHERE workspace_path = ?", (workspace_path,)
        ).fetchone()
        if row is None:
            return None
        return RepositoryMemory.model_validate(json.loads(row[0]))

    def load_if_current(self, workspace_path: str, fingerprint: str) -> RepositoryMemory | None:
        """Returns the stored memory only if it matches the given fingerprint —
        otherwise the repo has changed and cached architecture/convention
        claims must be treated as invalidated (recomputed by the caller)."""
        memory = self.load(workspace_path)
        if memory is None or memory.fingerprint != fingerprint:
            return None
        return memory
