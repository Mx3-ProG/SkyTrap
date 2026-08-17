import subprocess

from skytrap.core.context import WorkspaceContext
from skytrap.tools.git import review_diff


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=True)


def test_review_diff_shows_content_for_new_untracked_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new_file.py").write_text("print('hi')\n")

    result = review_diff(_workspace(tmp_path), ["new_file.py"])

    assert result.success
    assert "NEW FILE: new_file.py" in result.output
    assert "print('hi')" in result.output


def test_review_diff_shows_diff_for_modified_tracked_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "existing.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "existing.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    (tmp_path / "existing.py").write_text("a = 2\n")

    result = review_diff(_workspace(tmp_path), ["existing.py"])

    assert result.success
    assert "-a = 1" in result.output
    assert "+a = 2" in result.output


def test_review_diff_no_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "existing.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "existing.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    result = review_diff(_workspace(tmp_path), ["existing.py"])

    assert result.success
    assert result.output == "No differences."
