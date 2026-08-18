from fastapi.testclient import TestClient

from skytrap.server.app import create_app
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings


def _client(tmp_path):
    store = AuthStore(db_path=tmp_path / "auth-test.db")
    app = create_app(settings=Settings(secret_key="test-secret"), auth_store=store)
    return TestClient(app, base_url="https://testserver"), store


def test_cors_allows_the_vite_dev_origin(tmp_path):
    client, store = _client(tmp_path)
    response = client.options(
        "/auth/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    store.close()


def test_cors_rejects_an_arbitrary_origin(tmp_path):
    client, store = _client(tmp_path)
    response = client.options(
        "/auth/me",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers
    store.close()


def test_app_starts_without_a_built_frontend(tmp_path):
    # frontend/dist doesn't exist in this test environment — create_app() must
    # not fail to mount it, and API routes must still work normally.
    client, store = _client(tmp_path)
    response = client.get("/auth/me")
    assert response.status_code == 401  # not authenticated, but the route exists
    store.close()
