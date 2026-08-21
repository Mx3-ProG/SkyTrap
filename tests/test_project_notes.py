from skytrap.core.context import WorkspaceContext
from skytrap.core.project_notes import append_journal_entry, journal_path, load_recent_journal


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_load_recent_journal_absent(tmp_path):
    assert load_recent_journal(_workspace(tmp_path)) is None


def test_append_journal_entry_creates_skytrap_dir(tmp_path):
    workspace = _workspace(tmp_path)
    append_journal_entry(workspace, "Add a widget", "Created widget.py implementing Widget.")

    path = journal_path(workspace)
    assert path.is_file()
    assert path.parent.name == "Skytrap"

    content = path.read_text()
    assert "Add a widget" in content
    assert "Created widget.py implementing Widget." in content


def test_append_journal_entry_is_append_only(tmp_path):
    workspace = _workspace(tmp_path)
    append_journal_entry(workspace, "First task", "Did the first thing.")
    append_journal_entry(workspace, "Second task", "Did the second thing.")

    content = journal_path(workspace).read_text()
    assert "Did the first thing." in content
    assert "Did the second thing." in content
    assert content.index("Did the first thing.") < content.index("Did the second thing.")


def test_load_recent_journal_returns_tail(tmp_path):
    workspace = _workspace(tmp_path)
    append_journal_entry(workspace, "Old task", "old" * 10)
    append_journal_entry(workspace, "Recent task", "recent-marker")

    journal = load_recent_journal(workspace, max_chars=50)
    assert "recent-marker" in journal
