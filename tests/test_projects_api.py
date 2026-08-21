import subprocess

import pytest
from fastapi.testclient import TestClient

from skytrap.core.projects import ProjectStore
from skytrap.server.app import create_app
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings

EMAIL = "owner@example.com"
PASSWORD = "correct horse battery staple"


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    @property
    def last_code(self) -> str:
        return self.sent[-1][2].rsplit(":", 1)[-1].strip()


@pytest.fixture
def email_sender():
    return FakeEmailSender()


@pytest.fixture
def app_and_stores(tmp_path, email_sender):
    auth_store = AuthStore(db_path=tmp_path / "auth-test.db")
    auth_store.create_user(EMAIL, PASSWORD)
    project_store = ProjectStore(db_path=tmp_path / "projects-test.db")
    app = create_app(
        settings=Settings(secret_key="test-secret-key-for-tests-only"),
        auth_store=auth_store,
        project_store=project_store,
    )
    app.state.email_sender = email_sender
    yield app, auth_store, project_store
    auth_store.close()
    project_store.close()


def _login(client: TestClient, email_sender: FakeEmailSender) -> None:
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    code = email_sender.last_code
    response = client.post("/auth/otp/verify", json={"email": EMAIL, "code": code})
    assert response.status_code == 200


def test_projects_require_authentication(app_and_stores):
    app, _, _ = app_and_stores
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/projects")
        assert response.status_code == 401


def test_register_list_get_remove_project(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        register = client.post("/projects", json={"name": "My App", "path": str(project_dir)})
        assert register.status_code == 200
        project_id = register.json()["id"]

        listed = client.get("/projects").json()
        assert len(listed) == 1
        assert listed[0]["name"] == "My App"

        detail = client.get(f"/projects/{project_id}")
        assert detail.status_code == 200

        removed = client.delete(f"/projects/{project_id}")
        assert removed.status_code == 200
        assert client.get(f"/projects/{project_id}").status_code == 404


def test_register_project_rejects_missing_directory(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        response = client.post(
            "/projects", json={"name": "Ghost", "path": str(tmp_path / "nope")}
        )
        assert response.status_code == 400


def test_list_and_read_and_write_files(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("print('hi')\n")

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        files = client.get(f"/projects/{project_id}/files", params={"path": "."})
        assert files.status_code == 200
        assert {"name": "main.py", "is_dir": False} in files.json()

        content = client.get(f"/projects/{project_id}/files/content", params={"path": "main.py"})
        assert content.status_code == 200
        assert content.json()["content"] == "print('hi')\n"

        write = client.put(
            f"/projects/{project_id}/files/content",
            json={"path": "main.py", "content": "print('bye')\n"},
        )
        assert write.status_code == 200
        assert (project_dir / "main.py").read_text() == "print('bye')\n"


def test_write_file_rejects_path_traversal(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        response = client.put(
            f"/projects/{project_id}/files/content",
            json={"path": "../outside.txt", "content": "pwned"},
        )
        assert response.status_code == 400
        assert not (tmp_path / "outside.txt").exists()


def test_write_file_rejects_sensitive_path(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        response = client.put(
            f"/projects/{project_id}/files/content",
            json={"path": ".env", "content": "SECRET=1"},
        )
        assert response.status_code == 403
        assert not (project_dir / ".env").exists()


def test_git_status(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        response = client.get(f"/projects/{project_id}/git/status")
        assert response.status_code == 200
        assert response.json()["success"] is True


def test_run_command_executes_safe_command(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    (project_dir / "file.txt").write_text("hello\n")

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        response = client.post(f"/projects/{project_id}/run", json={"command": "ls"})
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert "file.txt" in response.json()["output"]


def test_run_command_refuses_destructive_command(app_and_stores, email_sender, tmp_path):
    app, _, _ = app_and_stores
    project_dir = tmp_path / "my-app"
    project_dir.mkdir()
    (project_dir / "file.txt").write_text("hello\n")

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        project_id = client.post("/projects", json={"name": "My App", "path": str(project_dir)}).json()["id"]

        response = client.post(f"/projects/{project_id}/run", json={"command": "rm file.txt"})
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert (project_dir / "file.txt").exists()
