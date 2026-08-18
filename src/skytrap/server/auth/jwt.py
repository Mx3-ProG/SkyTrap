import time

import jwt

ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 15 * 60


def sign_access_token(user_id: int, secret_key: str) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + ACCESS_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def verify_access_token(token: str, secret_key: str) -> int | None:
    """Returns the user id if the token is valid and unexpired, else None — never
    raises, so callers can treat any failure uniformly as "not authenticated"."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        return None
