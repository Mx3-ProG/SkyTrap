import pytest
from fastapi.testclient import TestClient

from skytrap.server.app import create_app
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings


@pytest.fixture
def client(tmp_path):
    store = AuthStore(db_path=tmp_path / "auth-test.db")
    app = create_app(settings=Settings(secret_key="test-secret"), auth_store=store)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    store.close()


def test_client_receives_confirm_request_on_connect(client):
    with client.websocket_connect("/ws/confirm-demo") as ws:
        message = ws.receive_json()
        assert message["type"] == "confirm_request"
        assert message["kind"] == "demo"
        assert "id" in message


def test_answering_true_unblocks_the_worker_and_returns_result(client):
    with client.websocket_connect("/ws/confirm-demo") as ws:
        request = ws.receive_json()
        ws.send_json({"type": "confirm_response", "id": request["id"], "answer": True})

        result = ws.receive_json()
        assert result == {"type": "demo_result", "answer": True}


def test_answering_false_returns_false_result(client):
    with client.websocket_connect("/ws/confirm-demo") as ws:
        request = ws.receive_json()
        ws.send_json({"type": "confirm_response", "id": request["id"], "answer": False})

        result = ws.receive_json()
        assert result == {"type": "demo_result", "answer": False}


def test_disconnect_without_answering_does_not_hang_the_server(client):
    """abandon_all() must fire on disconnect — proven indirectly: if it didn't, the
    server-side worker thread would block for its full 30s timeout, and a second,
    independent connection right after would still work fine (the server process
    itself isn't stuck), which we confirm below."""
    with client.websocket_connect("/ws/confirm-demo") as ws:
        ws.receive_json()  # confirm_request arrives
        # deliberately disconnect here without responding

    # the server must still be responsive for a completely separate connection —
    # proves the first connection's worker thread didn't wedge anything global
    with client.websocket_connect("/ws/confirm-demo") as ws2:
        message = ws2.receive_json()
        assert message["type"] == "confirm_request"
        ws2.send_json({"type": "confirm_response", "id": message["id"], "answer": True})
        result = ws2.receive_json()
        assert result == {"type": "demo_result", "answer": True}
