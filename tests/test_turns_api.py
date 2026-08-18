import json

import pytest
from fastapi.testclient import TestClient

from skytrap.models.base import ModelProvider
from skytrap.server.app import create_app
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings

EMAIL = "owner@example.com"
PASSWORD = "correct horse battery staple"


class FakeEmailSender:
    """Same pattern as test_server_auth.py — captures the OTP so tests can log in
    without a real email backend."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    @property
    def last_code(self) -> str:
        return self.sent[-1][2].rsplit(":", 1)[-1].strip()


class FakeModelProvider(ModelProvider):
    """Replays a scripted sequence of raw JSON decision strings — one per call to
    chat() — so tests exercise the real run_agent_turn/tool-execution/confirmation
    machinery without depending on a real local Ollama model.
    """

    name = "fake"
    engine = "LOCAL"

    def __init__(self, decisions: list[str]) -> None:
        self._decisions = list(decisions)
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        decision = self._decisions[self.calls]
        self.calls += 1
        return decision


def _write_call(path: str, content: str) -> str:
    return json.dumps(
        {"type": "tool_call", "tool": "write_file", "arguments": {"path": path, "content": content}}
    )


def _final(message: str) -> str:
    return json.dumps({"type": "final", "message": message})


@pytest.fixture
def email_sender():
    return FakeEmailSender()


@pytest.fixture
def app_and_store(tmp_path, email_sender):
    store = AuthStore(db_path=tmp_path / "auth-test.db")
    store.create_user(EMAIL, PASSWORD)
    app = create_app(settings=Settings(secret_key="test-secret-key-for-tests-only"), auth_store=store)
    app.state.email_sender = email_sender
    yield app, store
    store.close()


def _login(client: TestClient, email_sender: FakeEmailSender) -> None:
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    code = email_sender.last_code
    response = client.post("/auth/otp/verify", json={"email": EMAIL, "code": code})
    assert response.status_code == 200


def _websocket_connect(client: TestClient, path: str):
    # websocket_connect() builds its handshake URL as ws://testserver (never
    # wss://) regardless of the client's https:// base_url, so httpx's cookie jar
    # withholds our Secure=True auth cookies from it unless passed explicitly here
    # — a TestClient scheme quirk, not something a real wss:// client needs.
    return client.websocket_connect(path, cookies=dict(client.cookies))


def test_post_turns_without_active_websocket_returns_409(app_and_store, email_sender):
    app, store = app_and_store
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        response = client.post("/turns", json={"task": "do something", "workspace": "."})
        assert response.status_code == 409


def test_full_turn_with_confirmation_writes_real_file(app_and_store, email_sender, tmp_path):
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()
    target_file = target_workspace / "hello.txt"

    app.state.model_provider = FakeModelProvider(
        [
            _write_call("hello.txt", "hi from the agent\n"),
            _final("done, file written"),
        ]
    )

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "write hello.txt", "workspace": str(target_workspace)}
            )
            assert response.status_code == 200
            turn_id = response.json()["turn_id"]

            # confirm_request arrives while write_file's execute() is still blocked
            # on the confirmation — turn_progress for that same tool call is only
            # emitted afterward, once execute() returns and on_step fires.
            confirm_request = ws.receive_json()
            assert confirm_request["type"] == "confirm_request"
            assert confirm_request["kind"] == "write"

            ws.send_json({"type": "confirm_response", "id": confirm_request["id"], "answer": True})

            progress = ws.receive_json()
            assert progress["type"] == "turn_progress"
            assert progress["turn_id"] == turn_id
            assert progress["tool"] == "write_file"

            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"
            assert complete["turn_id"] == turn_id
            assert complete["status"] == "done"
            assert complete["result"] == "done, file written"

    assert target_file.read_text() == "hi from the agent\n"


def test_declined_confirmation_skips_write_but_turn_still_completes(app_and_store, email_sender, tmp_path):
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()
    target_file = target_workspace / "hello.txt"

    app.state.model_provider = FakeModelProvider(
        [
            _write_call("hello.txt", "should not be written\n"),
            _final("ok, skipped"),
        ]
    )

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "write hello.txt", "workspace": str(target_workspace)}
            )
            turn_id = response.json()["turn_id"]

            confirm_request = ws.receive_json()
            ws.send_json({"type": "confirm_response", "id": confirm_request["id"], "answer": False})

            ws.receive_json()  # turn_progress
            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"
            assert complete["status"] == "done"

    assert not target_file.exists()


def test_get_turn_reflects_status_over_time(app_and_store, email_sender, tmp_path):
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()

    app.state.model_provider = FakeModelProvider([_final("no tools needed")])

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "just answer", "workspace": str(target_workspace)}
            )
            turn_id = response.json()["turn_id"]

            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"

        final_status = client.get(f"/turns/{turn_id}").json()
        assert final_status == {
            "turn_id": turn_id,
            "status": "done",
            "result": "no tools needed",
            "error": None,
        }


def test_get_unknown_turn_returns_404(app_and_store, email_sender):
    app, store = app_and_store
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        response = client.get("/turns/does-not-exist")
        assert response.status_code == 404
