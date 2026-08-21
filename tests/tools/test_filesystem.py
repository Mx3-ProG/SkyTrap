from skytrap.core.context import WorkspaceContext
from skytrap.tools.filesystem import DeleteFileTool, WriteFileTool, resolve_in_workspace


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_resolve_in_workspace_rejects_escape(tmp_path):
    ok, message = resolve_in_workspace(_workspace(tmp_path), "../outside.txt")
    assert ok is False
    assert "outside the workspace" in message


def test_delete_file_removes_when_confirmed(tmp_path):
    target = tmp_path / "doomed.txt"
    target.write_text("bye")

    tool = DeleteFileTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {"path": "doomed.txt"})

    assert result.success is True
    assert not target.exists()


def test_delete_file_ordinary_path_is_safe_and_never_confirms(tmp_path):
    target = tmp_path / "doomed.txt"
    target.write_text("bye")

    tool = DeleteFileTool(confirm=lambda preview: (_ for _ in ()).throw(AssertionError("should not confirm")))
    result = tool.execute(_workspace(tmp_path), {"path": "doomed.txt"})

    assert result.success is True
    assert not target.exists()


def test_delete_file_sensitive_path_declined_leaves_file_intact(tmp_path):
    target = tmp_path / ".env"
    target.write_text("still here")

    tool = DeleteFileTool(confirm=lambda preview: False)
    result = tool.execute(_workspace(tmp_path), {"path": ".env"})

    assert result.success is False
    assert target.exists()
    assert target.read_text() == "still here"


def test_delete_file_sensitive_path_preview_includes_content(tmp_path):
    target = tmp_path / ".env"
    target.write_text("SECRET=hello world")
    previews: list[str] = []

    def capture(preview: str) -> bool:
        previews.append(preview)
        return True

    DeleteFileTool(confirm=capture).execute(_workspace(tmp_path), {"path": ".env"})

    assert previews[0].startswith("DELETE FILE: .env")
    assert "SECRET=hello world" in previews[0]


def test_delete_file_missing_file_errors(tmp_path):
    tool = DeleteFileTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {"path": "nope.txt"})

    assert result.success is False
    assert "not found" in result.output.lower()


def test_delete_file_refuses_directory(tmp_path):
    (tmp_path / "a_dir").mkdir()

    tool = DeleteFileTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {"path": "a_dir"})

    assert result.success is False
    assert (tmp_path / "a_dir").exists()


def test_delete_file_rejects_path_escaping_workspace(tmp_path):
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("do not touch")
    try:
        tool = DeleteFileTool(confirm=lambda preview: True)
        result = tool.execute(_workspace(tmp_path), {"path": "../outside-target.txt"})

        assert result.success is False
        assert outside.exists()
    finally:
        outside.unlink()


def test_delete_file_missing_path_argument(tmp_path):
    tool = DeleteFileTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {})

    assert result.success is False
    assert "Missing required argument" in result.output


def test_write_file_ordinary_path_is_safe_and_never_confirms(tmp_path):
    tool = WriteFileTool(confirm=lambda preview: (_ for _ in ()).throw(AssertionError("should not confirm")))
    result = tool.execute(_workspace(tmp_path), {"path": "component.py", "content": "x = 1\n"})

    assert result.success is True
    assert (tmp_path / "component.py").read_text() == "x = 1\n"


def test_write_file_sensitive_path_still_confirms(tmp_path):
    calls = []

    def capture(preview: str) -> bool:
        calls.append(preview)
        return True

    tool = WriteFileTool(confirm=capture)
    result = tool.execute(_workspace(tmp_path), {"path": ".env", "content": "SECRET=1\n"})

    assert result.success is True
    assert len(calls) == 1


def test_write_file_sensitive_path_declined_leaves_nothing_written(tmp_path):
    tool = WriteFileTool(confirm=lambda preview: False)
    result = tool.execute(_workspace(tmp_path), {"path": ".env", "content": "SECRET=1\n"})

    assert result.success is False
    assert not (tmp_path / ".env").exists()
