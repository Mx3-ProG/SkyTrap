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
    chat() — so tests exercise the real Architect -> confirm plan -> Developer ->
    tests -> Summarizer pipeline (run_server_task) without depending on a real
    local Ollama model. The same instance is shared across every role in the
    pipeline, so `decisions` is one flat script covering all of them in call order.
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
            _final("1. Use write_file to create hello.txt with the requested content."),  # Architect
            _write_call("hello.txt", "hi from the agent\n"),  # Developer
            _final("done, file written"),  # Developer final summary
            _final("Wrote hello.txt for the first time."),  # Summarizer
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

            # 1. Architect's plan is proposed first — nothing happens until approved.
            plan_confirm = ws.receive_json()
            assert plan_confirm["type"] == "confirm_request"
            assert plan_confirm["kind"] == "plan"
            assert "hello.txt" in plan_confirm["preview"]
            ws.send_json({"type": "confirm_response", "id": plan_confirm["id"], "answer": True})

            # 2. hello.txt is an ordinary (SAFE-classified) path, so write_file writes
            # immediately with no confirmation round trip of its own.
            write_progress = ws.receive_json()
            assert write_progress["type"] == "turn_progress"
            assert write_progress["turn_id"] == turn_id
            assert write_progress["tool"] == "write_file"

            tests_progress = ws.receive_json()
            assert tests_progress["type"] == "turn_progress"
            assert tests_progress["tool"] == "run_tests"

            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"
            assert complete["turn_id"] == turn_id
            assert complete["status"] == "done"
            assert complete["result"] == "Developer summary:\ndone, file written"

    assert target_file.read_text() == "hi from the agent\n"

    journal = target_workspace / "Skytrap" / "JOURNAL.md"
    assert journal.is_file()
    assert "Wrote hello.txt for the first time." in journal.read_text()


def test_declined_plan_skips_everything(app_and_store, email_sender, tmp_path):
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()
    target_file = target_workspace / "hello.txt"

    app.state.model_provider = FakeModelProvider(
        [_final("1. Use write_file to create hello.txt.")]  # Architect only
    )

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "write hello.txt", "workspace": str(target_workspace)}
            )
            turn_id = response.json()["turn_id"]

            plan_confirm = ws.receive_json()
            assert plan_confirm["kind"] == "plan"
            ws.send_json({"type": "confirm_response", "id": plan_confirm["id"], "answer": False})

            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"
            assert complete["turn_id"] == turn_id
            assert complete["status"] == "done"
            assert complete["result"] == "Plan declined — no changes made."

    assert not target_file.exists()
    assert not (target_workspace / "Skytrap").exists()


def test_declined_write_confirmation_skips_write_but_turn_still_completes(
    app_and_store, email_sender, tmp_path
):
    # A sensitive path (.env) so write_file is DESTRUCTIVE-classified and still asks
    # for its own confirmation — an ordinary path is SAFE and would never ask at all.
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()
    target_file = target_workspace / ".env"

    app.state.model_provider = FakeModelProvider(
        [
            _final("1. Use write_file to create .env."),  # Architect
            _write_call(".env", "should not be written\n"),  # Developer
            _final("ok, skipped"),  # Developer final summary
        ]
    )

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "write .env", "workspace": str(target_workspace)}
            )
            turn_id = response.json()["turn_id"]

            plan_confirm = ws.receive_json()
            ws.send_json({"type": "confirm_response", "id": plan_confirm["id"], "answer": True})

            write_confirm = ws.receive_json()
            assert write_confirm["kind"] == "write"
            ws.send_json({"type": "confirm_response", "id": write_confirm["id"], "answer": False})

            ws.receive_json()  # turn_progress for the declined write_file call
            ws.receive_json()  # turn_progress for run_tests

            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"
            assert complete["turn_id"] == turn_id
            assert complete["status"] == "done"

    assert not target_file.exists()
    # No file was actually touched, so no journal entry is written for this no-op.
    assert not (target_workspace / "Skytrap").exists()


def test_get_turn_reflects_status_over_time(app_and_store, email_sender, tmp_path):
    app, store = app_and_store
    target_workspace = tmp_path / "workspace"
    target_workspace.mkdir()

    app.state.model_provider = FakeModelProvider(
        [
            _final("No changes needed — this can be answered directly."),  # Architect
            _final("Answered directly, nothing to write."),  # Developer final summary
        ]
    )

    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)

        with _websocket_connect(client, "/ws") as ws:
            response = client.post(
                "/turns", json={"task": "just answer", "workspace": str(target_workspace)}
            )
            turn_id = response.json()["turn_id"]

            plan_confirm = ws.receive_json()
            ws.send_json({"type": "confirm_response", "id": plan_confirm["id"], "answer": True})

            ws.receive_json()  # turn_progress for run_tests
            complete = ws.receive_json()
            assert complete["type"] == "turn_complete"

        final_status = client.get(f"/turns/{turn_id}").json()
        assert final_status == {
            "turn_id": turn_id,
            "status": "done",
            "result": "Developer summary:\nAnswered directly, nothing to write.",
            "error": None,
        }


def test_get_unknown_turn_returns_404(app_and_store, email_sender):
    app, store = app_and_store
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, email_sender)
        response = client.get("/turns/does-not-exist")
        assert response.status_code == 404
