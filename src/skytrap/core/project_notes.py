from datetime import datetime, timezone
from pathlib import Path

from skytrap.core.context import WorkspaceContext

SKYTRAP_DIR_NAME = "Skytrap"
JOURNAL_FILENAME = "JOURNAL.md"
MAX_TASK_HEADER_CHARS = 80


def journal_path(workspace: WorkspaceContext) -> Path:
    return workspace.path / SKYTRAP_DIR_NAME / JOURNAL_FILENAME


def append_journal_entry(workspace: WorkspaceContext, task: str, note: str) -> None:
    """Appends a dated entry to {workspace}/Skytrap/JOURNAL.md, creating the
    directory and file on first use — this is the only place the `Skytrap/`
    folder gets created, no separate init step needed. Append-only: past entries
    are never rewritten, so the file itself is the project's visible history."""
    path = journal_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    header = task.strip().replace("\n", " ")[:MAX_TASK_HEADER_CHARS]
    entry = f"## {timestamp} — {header}\n{note.strip()}\n\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def load_recent_journal(workspace: WorkspaceContext, max_chars: int = 6_000) -> str | None:
    """Reads the TAIL of the journal (most recent entries matter most for
    continuity), or None if it doesn't exist yet / is empty / unreadable —
    mirrors the defensive style of _load_project_instructions() in core/agent.py."""
    path = journal_path(workspace)
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (UnicodeDecodeError, OSError):
        return None
    if not content:
        return None
    return content[-max_chars:]
