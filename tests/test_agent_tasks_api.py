import json
import subprocess

from fastapi.testclient import TestClient

from skytrap.models.base import ModelProvider
from skytrap.server.app import create_app
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings


class EmailSender:
    def __init__(self):
        self.code = ""

    def send(self, to: str, subject: str, body: str) -> None:
        self.code = body.rsplit(":", 1)[-1].strip()


class Model(ModelProvider):
    name = "scripted"
    engine = "LOCAL"

    def __init__(self):
        self.responses = [
            {
                "summary": "fix",
                "steps": [
                    {
                        "id": "one",
                        "description": "patch",
                        "files": ["app.py"],
                        "commands": ["python3 -m unittest"],
                        "risks": [],
                        "success_criteria": ["tests pass"],
                    }
                ],
                "files": ["app.py"],
                "tests": ["python3 -m unittest"],
                "commands": ["python3 -m unittest"],
                "risks": [],
                "success_criteria": ["tests pass"],
            },
            {
                "type": "tool_call",
                "tool": "patch_file",
                "arguments": {
                    "path": "app.py",
                    "expected": "value = 0",
                    "replacement": "value = 1",
                },
            },
            {"type": "final", "message": "fixed"},
        ]

    def chat(self, messages: list[dict]) -> str:
        return json.dumps(self.responses.pop(0))


def test_server_agent_endpoint_runs_new_autonomous_lifecycle(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 0\n")
    (repo / "test_app.py").write_text(
        "import unittest\nimport app\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(app.value, 1)\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "app.py", "test_app.py"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        cwd=repo,
        check=True,
    )
    auth = AuthStore(tmp_path / "skytrap.db")
    auth.create_user("owner@example.com", "correct horse battery staple")
    app = create_app(
        settings=Settings(secret_key="test-secret-key-for-tests-only"),
        auth_store=auth,
        model_provider=Model(),
    )
    sender = EmailSender()
    app.state.email_sender = sender

    try:
        with TestClient(app, base_url="https://testserver") as client:
            client.post(
                "/auth/login",
                json={"email": "owner@example.com", "password": "correct horse battery staple"},
            )
            client.post(
                "/auth/otp/verify",
                json={"email": "owner@example.com", "code": sender.code},
            )
            with client.websocket_connect("/ws", cookies=dict(client.cookies)) as websocket:
                response = client.post(
                    "/agent/tasks",
                    json={"workspace": str(repo), "goal": "set value to one"},
                )
                assert response.status_code == 200
                task_id = response.json()["task_id"]
                while True:
                    event = websocket.receive_json()
                    if event["type"] == "agent_task_complete":
                        break

                assert event["task"]["task_id"] == task_id
                assert event["task"]["status"] == "completed"
                status = client.get(f"/agent/tasks/{task_id}")
                assert status.status_code == 200
                assert status.json()["checkpoint_commit"]
    finally:
        auth.close()

    assert (repo / "app.py").read_text() == "value = 1\n"
