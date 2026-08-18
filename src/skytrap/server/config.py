import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

SECRET_KEY_PATH = Path.home() / ".skytrap" / "server_secret_key"


def _load_or_generate_secret_key() -> str:
    """Uses SKYTRAP_SECRET_KEY if set. Otherwise generates one on first run and
    persists it to ~/.skytrap/server_secret_key (owner-only permissions) so tokens
    survive a server restart, rather than requiring manual setup before this
    milestone can be tested at all."""
    env_key = os.environ.get("SKYTRAP_SECRET_KEY")
    if env_key:
        return env_key

    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()

    key = secrets.token_urlsafe(32)
    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_KEY_PATH.write_text(key, encoding="utf-8")
    SECRET_KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only
    return key


@dataclass
class Settings:
    secret_key: str
    host: str = "127.0.0.1"
    port: int = 8000


def load_settings() -> Settings:
    return Settings(
        secret_key=_load_or_generate_secret_key(),
        host=os.environ.get("SKYTRAP_HOST", "127.0.0.1"),
        port=int(os.environ.get("SKYTRAP_PORT", "8000")),
    )
