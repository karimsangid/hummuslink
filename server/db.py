"""SQLite persistence for HummusLink (clipboard history, file metadata, devices)."""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS clipboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clipboard_ts ON clipboard(timestamp DESC);

CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT,
    from_device TEXT,
    uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_uploaded ON files(uploaded_at DESC);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    extra TEXT,
    from_device TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feed_ts ON feed(id DESC);
"""


class DB:
    """Thread-safe SQLite wrapper. One connection, lock-guarded writes."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        # Migration: pinned files survive cleanup (added v0.3.0)
        try:
            self._conn.execute("ALTER TABLE files ADD COLUMN pinned INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        logger.info(f"DB ready at {db_path}")

    def close(self):
        with self._lock:
            self._conn.close()

    # ----- clipboard history -----

    def add_clipboard(self, content: str, source: str, max_history: int):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO clipboard (content, source, timestamp) VALUES (?, ?, ?)",
                (content, source, ts),
            )
            # Trim to max_history
            self._conn.execute(
                """DELETE FROM clipboard WHERE id NOT IN (
                       SELECT id FROM clipboard ORDER BY id DESC LIMIT ?
                   )""",
                (max_history,),
            )
        return cur.lastrowid

    def get_clipboard_history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT content, source, timestamp FROM clipboard ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_clipboard(self):
        with self._lock:
            self._conn.execute("DELETE FROM clipboard")

    # ----- files -----

    def upsert_file(self, meta: dict):
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO files
                   (file_id, filename, path, size, mime, from_device, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    meta["file_id"],
                    meta["filename"],
                    meta["path"],
                    meta["size"],
                    meta.get("mime"),
                    meta.get("from_device"),
                    meta["uploaded_at"],
                ),
            )

    def delete_file(self, file_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))

    def get_file(self, file_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE file_id = ?", (file_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_files(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files ORDER BY uploaded_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_files(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files").fetchall()
        return {r["file_id"]: dict(r) for r in rows}

    def set_pinned(self, file_id: str, pinned: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE files SET pinned = ? WHERE file_id = ?",
                (1 if pinned else 0, file_id),
            )
        return cur.rowcount > 0

    # ----- feed -----

    def add_feed(self, kind: str, content: str, from_device: str | None, extra: str | None = None, max_items: int = 200):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO feed (kind, content, extra, from_device, timestamp) VALUES (?, ?, ?, ?, ?)",
                (kind, content, extra, from_device, ts),
            )
            self._conn.execute(
                """DELETE FROM feed WHERE id NOT IN (
                       SELECT id FROM feed ORDER BY id DESC LIMIT ?
                   )""",
                (max_items,),
            )

    def list_feed(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, content, extra, from_device, timestamp FROM feed ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----- devices -----

    def upsert_device(self, device_id: str, name: str, type_: str):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self._conn.execute(
                "SELECT first_seen FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE devices SET name=?, type=?, last_seen=? WHERE device_id=?",
                    (name, type_, ts, device_id),
                )
            else:
                self._conn.execute(
                    """INSERT INTO devices (device_id, name, type, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?)""",
                    (device_id, name, type_, ts, ts),
                )

    def list_devices(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]
