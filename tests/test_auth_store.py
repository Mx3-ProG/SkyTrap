import time

import pytest

from skytrap.server.auth import store as store_module
from skytrap.server.auth.jwt import sign_access_token, verify_access_token
from skytrap.server.auth.store import AuthStore


@pytest.fixture
def auth_store(tmp_path):
    store = AuthStore(db_path=tmp_path / "auth-test.db")
    yield store
    store.close()


def test_create_user_and_lookup(auth_store):
    user = auth_store.create_user("me@example.com", "correct horse battery staple")
    assert user.id > 0
    assert auth_store.get_user_by_email("me@example.com").id == user.id
    assert auth_store.get_user_by_email("nobody@example.com") is None


def test_create_user_refuses_a_second_account(auth_store):
    auth_store.create_user("me@example.com", "password1")
    with pytest.raises(ValueError):
        auth_store.create_user("someone-else@example.com", "password2")


def test_verify_password(auth_store):
    user = auth_store.create_user("me@example.com", "correct horse battery staple")
    assert auth_store.verify_password(user, "correct horse battery staple")
    assert not auth_store.verify_password(user, "wrong password")


def test_otp_generate_and_verify_once(auth_store):
    user = auth_store.create_user("me@example.com", "password")
    code = auth_store.create_otp(user.id)
    assert code is not None
    assert len(code) == 6
    assert code.isdigit()

    assert auth_store.verify_otp(user.id, code)
    # a consumed code cannot be reused
    assert not auth_store.verify_otp(user.id, code)


def test_otp_wrong_code_fails(auth_store):
    user = auth_store.create_user("me@example.com", "password")
    auth_store.create_otp(user.id)
    assert not auth_store.verify_otp(user.id, "000000")


def test_otp_resend_cooldown(auth_store):
    user = auth_store.create_user("me@example.com", "password")
    first = auth_store.create_otp(user.id)
    assert first is not None
    second = auth_store.create_otp(user.id)
    assert second is None  # inside the 60s cooldown


def test_otp_lockout_after_max_attempts(auth_store, monkeypatch):
    monkeypatch.setattr(store_module, "OTP_MAX_ATTEMPTS", 3)
    user = auth_store.create_user("me@example.com", "password")
    code = auth_store.create_otp(user.id)

    for _ in range(3):
        assert not auth_store.verify_otp(user.id, "000000")

    # even the correct code is now rejected — attempt budget exhausted
    assert not auth_store.verify_otp(user.id, code)


def test_otp_expired_code_fails(auth_store):
    user = auth_store.create_user("me@example.com", "password")
    code = auth_store.create_otp(user.id)
    # simulate expiry directly rather than sleeping 5 real minutes in a test
    auth_store._conn.execute(
        "UPDATE otp_codes SET expires_at = '2000-01-01T00:00:00+00:00' WHERE user_id = ?",
        (user.id,),
    )
    auth_store._conn.commit()
    assert not auth_store.verify_otp(user.id, code)


def test_login_rate_limiting(auth_store, monkeypatch):
    monkeypatch.setattr(store_module, "LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 3)
    assert not auth_store.is_rate_limited("1.2.3.4")
    for _ in range(3):
        auth_store.record_login_attempt("1.2.3.4", "me@example.com", "password", False)
    assert auth_store.is_rate_limited("1.2.3.4")
    assert not auth_store.is_rate_limited("9.9.9.9")  # different IP unaffected


def test_refresh_token_rotation(auth_store):
    user = auth_store.create_user("me@example.com", "password")
    token = auth_store.create_refresh_token(user.id)

    result = auth_store.rotate_refresh_token(token)
    assert result is not None
    rotated_user_id, new_token = result
    assert rotated_user_id == user.id
    assert new_token != token

    # the old token was revoked by rotation — cannot be reused
    assert auth_store.rotate_refresh_token(token) is None


def test_refresh_token_unknown_is_rejected(auth_store):
    assert auth_store.rotate_refresh_token("not-a-real-token") is None


def test_jwt_sign_and_verify_roundtrip():
    token = sign_access_token(42, secret_key="test-secret")
    assert verify_access_token(token, secret_key="test-secret") == 42


def test_jwt_wrong_secret_rejected():
    token = sign_access_token(42, secret_key="test-secret")
    assert verify_access_token(token, secret_key="wrong-secret") is None


def test_jwt_garbage_token_rejected():
    assert verify_access_token("not.a.jwt", secret_key="test-secret") is None


def test_jwt_expired_token_rejected(monkeypatch):
    import skytrap.server.auth.jwt as jwt_module

    monkeypatch.setattr(jwt_module, "ACCESS_TOKEN_TTL_SECONDS", -1)
    token = sign_access_token(42, secret_key="test-secret")
    time.sleep(0.01)
    assert verify_access_token(token, secret_key="test-secret") is None
