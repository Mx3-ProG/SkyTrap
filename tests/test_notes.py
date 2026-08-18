import pytest

from skytrap.core.context import WorkspaceContext
from skytrap.memory.sqlite import SqliteMemory
from skytrap.tools.notes import GetPastNotesTool


@pytest.fixture
def memory(tmp_path):
    mem = SqliteMemory(db_path=tmp_path / "notes-test.db")
    yield mem
    mem.close()


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_record_and_list_notes_newest_first(memory):
    session_id = memory.start_session("/workspace/a")
    memory.record_note(session_id, "/workspace/a", "First note")
    memory.record_note(session_id, "/workspace/a", "Second note")

    notes = memory.list_notes("/workspace/a")

    assert [n.summary for n in notes] == ["Second note", "First note"]


def test_list_notes_filters_by_workspace(memory):
    session_id = memory.start_session("/workspace/a")
    memory.record_note(session_id, "/workspace/a", "Note for A")
    memory.record_note(session_id, "/workspace/b", "Note for B")

    notes_a = memory.list_notes("/workspace/a")

    assert len(notes_a) == 1
    assert notes_a[0].summary == "Note for A"


def test_search_notes_matches_substring(memory):
    session_id = memory.start_session("/workspace/a")
    memory.record_note(session_id, "/workspace/a", "Fixed the auth bug in login.py")
    memory.record_note(session_id, "/workspace/a", "Added a new CSS lint tool")

    results = memory.search_notes("/workspace/a", "auth")

    assert len(results) == 1
    assert "auth" in results[0].summary


def test_get_past_notes_tool_empty(tmp_path, memory):
    tool = GetPastNotesTool(memory=memory)
    result = tool.execute(_workspace(tmp_path), {"mode": "recent"})
    assert result.success
    assert "No past notes" in result.output


def test_get_past_notes_tool_recent(tmp_path, memory):
    resolved = tmp_path.resolve()
    session_id = memory.start_session(str(resolved))
    memory.record_note(session_id, str(resolved), "Did the thing")

    tool = GetPastNotesTool(memory=memory)
    result = tool.execute(_workspace(resolved), {"mode": "recent"})

    assert result.success
    assert "Did the thing" in result.output


def test_get_past_notes_tool_search_requires_query(tmp_path, memory):
    tool = GetPastNotesTool(memory=memory)
    result = tool.execute(_workspace(tmp_path), {"mode": "search"})
    assert not result.success
    assert "query" in result.output


def test_get_past_notes_tool_unknown_mode(tmp_path, memory):
    tool = GetPastNotesTool(memory=memory)
    result = tool.execute(_workspace(tmp_path), {"mode": "bogus"})
    assert not result.success
