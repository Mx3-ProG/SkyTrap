import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".skytrap" / "projects.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Project:
    id: int
    name: str
    path: str
    created_at: str


class ProjectRegistrationError(ValueError):
    pass


class ProjectStore:
    """Registers local directories as SkyTrap projects — the Workspace/Project
    hierarchy from the product spec starts here as the simplest real thing that
    could work: one flat table of {name, path}. The filesystem stays the source of
    truth for repository contents (per the "don't store source in the DB" rule) —
    this table only ever holds a name and a validated path.

    Held as a long-lived singleton in FastAPI's app.state and accessed from
    whichever worker thread handles a given request (same reasoning as
    AuthStore, server/auth/store.py): check_same_thread=False + an explicit lock.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def register(self, name: str, path: str) -> Project:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ProjectRegistrationError(f"Not a directory: {resolved}")

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO projects (name, path, created_at) VALUES (?, ?, ?)",
                    (name, str(resolved), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectRegistrationError(f"Already registered: {resolved}") from exc
            self._conn.commit()
            return Project(id=cursor.lastrowid, name=name, path=str(resolved), created_at=_now())

    def list(self) -> list[Project]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, path, created_at FROM projects ORDER BY id DESC"
            ).fetchall()
        return [Project(*row) for row in rows]

    def get(self, project_id: int) -> Project | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, path, created_at FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project(*row) if row else None

    def remove(self, project_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()
