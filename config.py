"""HummusLink configuration."""

import os
from pathlib import Path

APP_NAME = "HummusLink"
APP_VERSION = "0.1.0"
APP_AUTHOR = "Hummus Development LLC"
APP_CONTACT = "karimsangid@gmail.com"

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765

STORAGE_DIR = Path(os.path.expanduser("~/HummusLink"))
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB

CLIPBOARD_POLL_INTERVAL = 0.5  # seconds

PAIRING_TOKEN_LENGTH = 32

HEARTBEAT_INTERVAL = 15  # seconds

FILE_CLEANUP_MAX_AGE_HOURS = 24
CLIPBOARD_HISTORY_SIZE = 20
