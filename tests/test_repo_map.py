from skytrap.core.context import WorkspaceContext
from skytrap.core.repo_map import build_repo_map


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_lists_files_and_dirs(tmp_path):
    (tmp_path / "README.md").write_text("hi")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass")

    result = build_repo_map(_workspace(tmp_path))

    assert "README.md" in result
    assert "src/" in result
    assert "main.py" in result


def test_ignores_pycache_and_node_modules(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "mod.pyc").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("")
    (tmp_path / "real.py").write_text("pass")

    result = build_repo_map(_workspace(tmp_path))

    assert "real.py" in result
    assert "__pycache__" not in result
    assert "node_modules" not in result
    assert "pkg.js" not in result


def test_empty_workspace(tmp_path):
    assert build_repo_map(_workspace(tmp_path)) == "(empty workspace)"


def test_truncates_past_max_entries(tmp_path):
    for i in range(20):
        (tmp_path / f"file_{i}.txt").write_text("")

    result = build_repo_map(_workspace(tmp_path), max_entries=5)

    assert "truncated" in result
    assert len(result.splitlines()) == 6  # 5 entries + truncation notice
