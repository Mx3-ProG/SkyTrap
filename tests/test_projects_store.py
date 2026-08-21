import pytest

from skytrap.core.projects import ProjectRegistrationError, ProjectStore


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(db_path=tmp_path / "projects.db")
    yield s
    s.close()


def test_register_and_list(store, tmp_path):
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()

    project = store.register("My App", str(project_dir))

    assert project.name == "My App"
    assert project.path == str(project_dir.resolve())
    assert [p.id for p in store.list()] == [project.id]


def test_register_rejects_missing_directory(store, tmp_path):
    with pytest.raises(ProjectRegistrationError):
        store.register("Ghost", str(tmp_path / "does-not-exist"))


def test_register_rejects_duplicate_path(store, tmp_path):
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    store.register("My App", str(project_dir))

    with pytest.raises(ProjectRegistrationError):
        store.register("My App Again", str(project_dir))


def test_get_returns_none_for_unknown_id(store):
    assert store.get(9999) is None


def test_remove(store, tmp_path):
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    project = store.register("My App", str(project_dir))

    assert store.remove(project.id) is True
    assert store.get(project.id) is None
    assert store.remove(project.id) is False
