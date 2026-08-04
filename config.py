"""HummusLink configuration."""

import os
import secrets
from pathlib import Path

APP_NAME = "HummusLink"
APP_VERSION = "0.5.0"
APP_AUTHOR = "Hummus Development LLC"
APP_CONTACT = "karimsangid@gmail.com"

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765

STORAGE_DIR = Path(os.path.expanduser("~/HummusLink"))
DB_PATH = STORAGE_DIR / "hummuslink.db"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB (streamed to disk, never held in RAM)
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB streaming chunks

CLIPBOARD_POLL_INTERVAL = 0.5  # seconds (fallback when event API unavailable)
CLIPBOARD_HISTORY_SIZE = 50

PAIRING_TOKEN_LENGTH = 32

HEARTBEAT_INTERVAL = 15  # seconds

FILE_CLEANUP_MAX_AGE_HOURS = 24 * 7  # one week

# ----- Brand palette (Hummus Development LLC) -----
BRAND_BG = "#0c0a07"
BRAND_GOLD = "#d9a14a"
BRAND_GOLD_HOVER = "#e8b35d"
BRAND_CREAM = "#f5ead6"
BRAND_CREAM_DIM = "#bdb098"
BRAND_BORDER = "#2a2218"
BRAND_CARD = "#1a140e"

# ----- Shared secret for websocket pairing -----
# Persisted to disk on first run; env var override wins if set.
SHARED_SECRET_FILE = STORAGE_DIR / ".shared_secret"


def _resolve_shared_secret() -> str:
    env_val = os.environ.get("HUMMUSLINK_SHARED_SECRET", "").strip()
    if env_val:
        return env_val
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if SHARED_SECRET_FILE.exists():
        val = SHARED_SECRET_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val
    new_secret = secrets.token_urlsafe(PAIRING_TOKEN_LENGTH)
    SHARED_SECRET_FILE.write_text(new_secret, encoding="utf-8")
    try:
        os.chmod(SHARED_SECRET_FILE, 0o600)
    except OSError:
        pass
    return new_secret


SHARED_SECRET = _resolve_shared_secret()
