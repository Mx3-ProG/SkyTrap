import hashlib
import secrets

OTP_LENGTH = 6


def generate_code() -> str:
    """A 6-digit numeric code — the standard OTP UX convention. Uses secrets, not
    random, since this gates account access."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


def hash_code(code: str) -> str:
    """OTP codes are short-lived and rate-limited, but still hashed at rest rather
    than stored in plaintext — cheap to do, no reason not to."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()
