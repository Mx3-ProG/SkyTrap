import os
import signal
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".skytrap" / "skytrap.db"
LOG_DIR = Path.home() / ".skytrap" / "logs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_path TEXT NOT NULL,
    command TEXT NOT NULL,
    pid INTEGER NOT NULL,
    log_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False

    # os.kill(pid, 0) still succeeds for a zombie (defunct, exited but unreaped)
    # process — its PID stays in the table until something waits on it. That's a real
    # state we hit in practice: SkyTrap isn't always the parent that can reap a given
    # PID (e.g. checking a process a *previous* `skytrap` invocation started), so we
    # can't rely on waitpid(). Check the actual state via `ps` instead.
    try:
        result = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # can't determine state — assume alive rather than falsely "stopped"

    state = result.stdout.strip()
    return bool(state) and not state.upper().startswith("Z")


@dataclass
class ProcessRecord:
    id: int
    workspace_path: str
    command: str
    pid: int
    log_path: str
    started_at: str
    stopped_at: str | None

    @property
    def running(self) -> bool:
        return self.stopped_at is None and is_running(self.pid)


def start_process(workspace_path: str, command_tokens: list[str]) -> ProcessRecord:
    """Launches command_tokens detached from this process (start_new_session=True), so
    it keeps running after `skytrap` exits — the whole point of this module. Output is
    redirected to a log file under ~/.skytrap/logs/ since there's no terminal for it to
    write to once detached.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started_at = _now()
    safe_stamp = started_at.replace(":", "-")
    # command_tokens[0] may be an absolute path (e.g. sys.executable) — take just the
    # basename so it can't inject path separators into the log filename.
    command_label = Path(command_tokens[0]).name
    log_path = LOG_DIR / f"{safe_stamp}-{command_label}.log"

    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command_tokens,
            cwd=workspace_path,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT INTO processes (workspace_path, command, pid, log_path, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (workspace_path, " ".join(command_tokens), process.pid, str(log_path), started_at),
        )
        conn.commit()
        return ProcessRecord(
            id=cursor.lastrowid,
            workspace_path=workspace_path,
            command=" ".join(command_tokens),
            pid=process.pid,
            log_path=str(log_path),
            started_at=started_at,
            stopped_at=None,
        )
    finally:
        conn.close()


def list_processes() -> list[ProcessRecord]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, workspace_path, command, pid, log_path, started_at, stopped_at "
            "FROM processes ORDER BY id DESC"
        ).fetchall()
        return [ProcessRecord(*row) for row in rows]
    finally:
        conn.close()


def get_process(process_id: int) -> ProcessRecord | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, workspace_path, command, pid, log_path, started_at, stopped_at "
            "FROM processes WHERE id = ?",
            (process_id,),
        ).fetchone()
        return ProcessRecord(*row) if row else None
    finally:
        conn.close()


def _mark_stopped(process_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE processes SET stopped_at = ? WHERE id = ? AND stopped_at IS NULL",
            (_now(), process_id),
        )
        conn.commit()
    finally:
        conn.close()


def stop_process(process_id: int) -> tuple[bool, str]:
    record = get_process(process_id)
    if record is None:
        return False, f"No tracked process with id {process_id}"

    if not is_running(record.pid):
        _mark_stopped(process_id)
        return False, f"Process #{process_id} (pid {record.pid}) was already not running"

    try:
        os.kill(record.pid, signal.SIGTERM)
    except ProcessLookupError:
        _mark_stopped(process_id)
        return False, f"Process #{process_id} (pid {record.pid}) was already not running"

    _mark_stopped(process_id)
    return True, f"Stopped process #{process_id} (pid {record.pid})"


def tail_log(record: ProcessRecord, lines: int = 50) -> str:
    path = Path(record.log_path)
    if not path.exists():
        return "(no log file)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read log: {exc})"
    log_lines = content.splitlines()
    return "\n".join(log_lines[-lines:]) or "(empty log)"
