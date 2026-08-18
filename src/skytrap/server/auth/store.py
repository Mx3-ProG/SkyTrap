import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from skytrap.server.auth.models import SCHEMA, User
from skytrap.server.auth.otp import generate_code, hash_code

DB_PATH = Path.home() / ".skytrap" / "skytrap.db"

OTP_TTL_MINUTES = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_PER_WINDOW = 5
OTP_WINDOW_MINUTES = 15
OTP_MAX_ATTEMPTS = 5
REFRESH_TOKEN_TTL_DAYS = 30
LOGIN_RATE_LIMIT_WINDOW_MINUTES = 15
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10

_hasher = PasswordHasher()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class AuthStore:
    """Single-user auth store — same ~/.skytrap/skytrap.db file SqliteMemory already
    uses, own tables. All timestamps stored as ISO-8601 UTC strings, matching the
    convention already established in memory/sqlite.py and core/processes.py.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit lock: AuthStore is a long-lived
        # singleton held in FastAPI's app.state, and FastAPI dispatches sync route
        # handlers (every route in router.py) to a worker thread pool — a different
        # thread on every request. sqlite3 forbids cross-thread use of a connection
        # by default; check_same_thread=False lifts that, but the connection still
        # isn't safe for genuinely concurrent access, hence the lock around every
        # method below.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # RLock, not Lock: rotate_refresh_token() calls create_refresh_token() on
        # self while already holding the lock — a plain Lock would deadlock there.
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- users ----

    def create_user(self, email: str, password: str) -> User:
        """Refuses to create a second account — this is a single-user system by design."""
        with self._lock:
            existing = self._conn.execute("SELECT id FROM users LIMIT 1").fetchone()
            if existing is not None:
                raise ValueError("A user already exists — SkyTrap's web auth is single-user")

            password_hash = _hasher.hash(password)
            created_at = _iso(_now())
            cursor = self._conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, created_at),
            )
            self._conn.commit()
            return User(cursor.lastrowid, email, password_hash, created_at)

    def get_user_by_email(self, email: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (email,)
            ).fetchone()
            return User(*row) if row else None

    def get_user(self, user_id: int) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return User(*row) if row else None

    def verify_password(self, user: User, password: str) -> bool:
        try:
            _hasher.verify(user.password_hash, password)
        except VerifyMismatchError:
            return False
        return True

    # ---- OTP ----

    def create_otp(self, user_id: int, purpose: str = "login") -> str | None:
        """Returns the raw code to email, or None if the resend cooldown or the
        per-window issuance cap blocks a new one (caller should surface this as a 429)."""
        with self._lock:
            if not self._otp_issuance_allowed(user_id, purpose):
                return None

            code = generate_code()
            now = _now()
            self._conn.execute(
                "INSERT INTO otp_codes (user_id, code_hash, purpose, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    hash_code(code),
                    purpose,
                    _iso(now),
                    _iso(now + timedelta(minutes=OTP_TTL_MINUTES)),
                ),
            )
            self._conn.commit()
            return code

    def _otp_issuance_allowed(self, user_id: int, purpose: str) -> bool:
        # Internal helper, always called with self._lock already held — no locking
        # here to avoid a pointless (if harmless, RLock) re-acquire.
        row = self._conn.execute(
            "SELECT created_at FROM otp_codes WHERE user_id = ? AND purpose = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, purpose),
        ).fetchone()
        if row is not None:
            last_created = _parse(row[0])
            if _now() - last_created < timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS):
                return False

        window_start = _iso(_now() - timedelta(minutes=OTP_WINDOW_MINUTES))
        count = self._conn.execute(
            "SELECT COUNT(*) FROM otp_codes WHERE user_id = ? AND purpose = ? AND created_at >= ?",
            (user_id, purpose, window_start),
        ).fetchone()[0]
        return count < OTP_MAX_PER_WINDOW

    def verify_otp(self, user_id: int, code: str, purpose: str = "login") -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, code_hash, expires_at, consumed_at, attempt_count FROM otp_codes "
                "WHERE user_id = ? AND purpose = ? ORDER BY id DESC LIMIT 1",
                (user_id, purpose),
            ).fetchone()
            if row is None:
                return False
            otp_id, code_hash, expires_at, consumed_at, attempt_count = row

            if consumed_at is not None:
                return False
            if _now() > _parse(expires_at):
                return False
            if attempt_count >= OTP_MAX_ATTEMPTS:
                return False

            # Count this attempt before checking the code, so a brute-force loop
            # can't retry indefinitely by aborting before the increment lands.
            self._conn.execute(
                "UPDATE otp_codes SET attempt_count = attempt_count + 1 WHERE id = ?", (otp_id,)
            )
            self._conn.commit()

            if hash_code(code) != code_hash:
                return False

            self._conn.execute(
                "UPDATE otp_codes SET consumed_at = ? WHERE id = ?", (_iso(_now()), otp_id)
            )
            self._conn.commit()
            return True

    # ---- login attempts / rate limiting ----

    def record_login_attempt(self, ip: str, email: str | None, stage: str, success: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO login_attempts (ip, email, stage, success, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ip, email, stage, int(success), _iso(_now())),
            )
            self._conn.commit()

    def is_rate_limited(self, ip: str) -> bool:
        with self._lock:
            window_start = _iso(_now() - timedelta(minutes=LOGIN_RATE_LIMIT_WINDOW_MINUTES))
            count = self._conn.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND created_at >= ?",
                (ip, window_start),
            ).fetchone()[0]
            return count >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS

    # ---- refresh tokens ----

    def create_refresh_token(self, user_id: int) -> str:
        with self._lock:
            raw_token = secrets.token_urlsafe(32)
            now = _now()
            self._conn.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    user_id,
                    hash_code(raw_token),
                    _iso(now),
                    _iso(now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)),
                ),
            )
            self._conn.commit()
            return raw_token

    def rotate_refresh_token(self, raw_token: str) -> tuple[int, str] | None:
        """Verifies raw_token, revokes it, issues a fresh one — standard refresh
        rotation to limit the replay window if a token ever leaks. Returns
        (user_id, new_raw_token), or None if invalid/expired/already revoked."""
        with self._lock:
            token_hash = hash_code(raw_token)
            row = self._conn.execute(
                "SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            token_id, user_id, expires_at, revoked_at = row
            if revoked_at is not None:
                return None
            if _now() > _parse(expires_at):
                return None

            self._conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?", (_iso(_now()), token_id)
            )
            self._conn.commit()
            new_token = self.create_refresh_token(user_id)  # re-acquires the RLock safely
            return user_id, new_token

    def revoke_refresh_token(self, raw_token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (_iso(_now()), hash_code(raw_token)),
            )
            self._conn.commit()
