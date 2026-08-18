import pytest
from fastapi.testclient import TestClient

from skytrap.server.app import create_app
from skytrap.server.auth import store as store_module
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings

TEST_EMAIL = "me@example.com"
TEST_PASSWORD = "correct horse battery staple"


class FakeEmailSender:
    """Captures sent codes for tests instead of printing to the console — the raw
    OTP code is never stored anywhere retrievable (only its hash), so tests need
    the sender to hand it back, same as a real user would read it from their inbox.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    @property
    def last_code(self) -> str:
        # bodies look like "Your login code is: 123456"
        return self.sent[-1][2].rsplit(":", 1)[-1].strip()


@pytest.fixture
def email_sender():
    return FakeEmailSender()


@pytest.fixture
def client(tmp_path, email_sender):
    store = AuthStore(db_path=tmp_path / "auth-test.db")
    store.create_user(TEST_EMAIL, TEST_PASSWORD)

    app = create_app(settings=Settings(secret_key="test-secret-key-for-tests-only"), auth_store=store)
    app.state.email_sender = email_sender  # override the real ConsoleEmailSender

    # base_url must be https:// — the auth cookies are set with Secure=True (correct
    # for production, which sits behind Tailscale HTTPS), and httpx's TestClient
    # genuinely enforces RFC 6265 Secure-cookie rules based on the request scheme
    # (default base_url is "http://testserver", not a real "localhost", so it
    # doesn't get any special exemption) — using https:// here exercises the exact
    # same cookie code path production will use, rather than weakening the cookie
    # flags just to make the test client happy.
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client

    store.close()


def _login_and_get_code(client: TestClient, email_sender: FakeEmailSender, password: str = TEST_PASSWORD):
    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": password})
    return response, email_sender.last_code if email_sender.sent else None


def test_full_login_flow_reaches_protected_route(client, email_sender):
    login_response, code = _login_and_get_code(client, email_sender)
    assert login_response.status_code == 200
    assert code is not None and len(code) == 6

    verify_response = client.post("/auth/otp/verify", json={"email": TEST_EMAIL, "code": code})
    assert verify_response.status_code == 200
    assert "skytrap_access" in verify_response.cookies
    assert "skytrap_refresh" in verify_response.cookies

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"email": TEST_EMAIL}


def test_wrong_password_rejected_and_no_otp_sent(client, email_sender):
    response, _ = _login_and_get_code(client, email_sender, password="wrong password")
    assert response.status_code == 401
    assert email_sender.sent == []  # no OTP issued for a failed password check


def test_wrong_otp_code_rejected(client, email_sender):
    _login_and_get_code(client, email_sender)
    response = client.post("/auth/otp/verify", json={"email": TEST_EMAIL, "code": "000000"})
    assert response.status_code == 401


def test_me_without_cookie_is_unauthorized(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_login_rate_limited_after_too_many_attempts(client, email_sender, monkeypatch):
    monkeypatch.setattr(store_module, "LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 3)
    for _ in range(3):
        client.post("/auth/login", json={"email": TEST_EMAIL, "password": "wrong"})

    response, _ = _login_and_get_code(client, email_sender)  # correct password this time
    assert response.status_code == 429


def test_otp_resend_cooldown_returns_429(client, email_sender):
    _login_and_get_code(client, email_sender)
    response = client.post("/auth/otp/resend", json={"email": TEST_EMAIL})
    assert response.status_code == 429


def test_refresh_rotates_tokens(client, email_sender):
    _, code = _login_and_get_code(client, email_sender)
    verify_response = client.post("/auth/otp/verify", json={"email": TEST_EMAIL, "code": code})
    old_refresh = verify_response.cookies["skytrap_refresh"]

    refresh_response = client.post("/auth/refresh")
    assert refresh_response.status_code == 200
    new_refresh = refresh_response.cookies["skytrap_refresh"]
    assert new_refresh != old_refresh

    # /me still works after refresh, with the new access token cookie
    me_response = client.get("/auth/me")
    assert me_response.status_code == 200


def test_logout_revokes_refresh_token(client, email_sender):
    _, code = _login_and_get_code(client, email_sender)
    client.post("/auth/otp/verify", json={"email": TEST_EMAIL, "code": code})

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    # the refresh token that was just revoked can no longer be used
    refresh_response = client.post("/auth/refresh")
    assert refresh_response.status_code == 401
