import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".skytrap" / "skytrap.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_path TEXT NOT NULL,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    workspace_path TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Note:
    id: int
    session_id: int
    workspace_path: str
    summary: str
    tags: str | None
    created_at: str


class SqliteMemory:
    """Persists sessions and messages across runs of `skytrap`, one row per turn.
    Long-term memory (project instructions, lessons, mistakes) will build on top of
    this later — for now it's just a durable log.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def start_session(self, workspace_path: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO sessions (workspace_path, started_at) VALUES (?, ?)",
            (workspace_path, _now()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def record_message(self, session_id: int, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, _now()),
        )
        self._conn.commit()

    def record_note(
        self, session_id: int, workspace_path: str, summary: str, tags: str | None = None
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO notes (session_id, workspace_path, summary, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, workspace_path, summary, tags, _now()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def list_notes(self, workspace_path: str, limit: int = 10) -> list[Note]:
        rows = self._conn.execute(
            "SELECT id, session_id, workspace_path, summary, tags, created_at FROM notes "
            "WHERE workspace_path = ? ORDER BY id DESC LIMIT ?",
            (workspace_path, limit),
        ).fetchall()
        return [Note(*row) for row in rows]

    def search_notes(self, workspace_path: str, query: str, limit: int = 10) -> list[Note]:
        rows = self._conn.execute(
            "SELECT id, session_id, workspace_path, summary, tags, created_at FROM notes "
            "WHERE workspace_path = ? AND summary LIKE ? ORDER BY id DESC LIMIT ?",
            (workspace_path, f"%{query}%", limit),
        ).fetchall()
        return [Note(*row) for row in rows]

    @property
    def connection(self) -> sqlite3.Connection:
        """Exposes the underlying connection so co-located stores (e.g.
        `skytrap.intelligence.repository_memory.RepositoryMemoryStore`) can
        share the same sqlite database/file instead of opening a second one."""
        return self._conn

    def close(self) -> None:
        self._conn.close()
