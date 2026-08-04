"""Resumable chunked uploads for HummusLink.

Big videos from the phone die when iOS suspends the PWA mid-transfer; with
chunks the client just resumes from the last acknowledged chunk. Sessions
survive server restarts: each upload is a sparse .part file plus a sidecar
JSON describing what has landed.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from config import MAX_FILE_SIZE

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024  # what we tell clients to slice by
PART_MAX_AGE_HOURS = 48
_ID_HEX = set("0123456789abcdef")


class UploadSessions:
    """Disk-backed resumable upload sessions inside <storage>/.uploads/."""

    def __init__(self, storage_dir: Path):
        self.parts_dir = storage_dir / ".uploads"
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _paths(self, upload_id: str) -> tuple[Path, Path]:
        if len(upload_id) != 16 or not all(c in _ID_HEX for c in upload_id):
            raise ValueError("bad upload_id")
        return (
            self.parts_dir / f"{upload_id}.part",
            self.parts_dir / f"{upload_id}.json",
        )

    def _read_meta(self, meta_path: Path) -> dict | None:
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    async def init(self, filename: str, size: int, from_device: str) -> dict:
        if size <= 0 or size > MAX_FILE_SIZE:
            raise ValueError(f"size must be 1..{MAX_FILE_SIZE}")
        upload_id = uuid.uuid4().hex[:16]
        part, meta_path = self._paths(upload_id)
        meta = {
            "upload_id": upload_id,
            "filename": filename,
            "size": size,
            "chunk_size": CHUNK_SIZE,
            "total_chunks": (size + CHUNK_SIZE - 1) // CHUNK_SIZE,
            "received": [],
            "from_device": from_device,
            "updated_at": time.time(),
        }
        async with self._lock:
            # Pre-size the part file so chunk writes can seek anywhere.
            with open(part, "wb") as f:
                f.truncate(size)
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        logger.info(f"Resumable upload init: {filename} ({size} bytes) id={upload_id}")
        return meta

    async def status(self, upload_id: str) -> dict | None:
        part, meta_path = self._paths(upload_id)
        if not meta_path.exists() or not part.exists():
            return None
        return self._read_meta(meta_path)

    async def write_chunk(self, upload_id: str, index: int, data: bytes) -> dict:
        part, meta_path = self._paths(upload_id)
        async with self._lock:
            meta = self._read_meta(meta_path)
            if meta is None or not part.exists():
                raise KeyError("unknown upload")
            if index < 0 or index >= meta["total_chunks"]:
                raise ValueError("chunk index out of range")
            expected = (
                meta["size"] - index * meta["chunk_size"]
                if index == meta["total_chunks"] - 1
                else meta["chunk_size"]
            )
            if len(data) != expected:
                raise ValueError(f"chunk {index} should be {expected} bytes, got {len(data)}")

            def _write():
                with open(part, "r+b") as f:
                    f.seek(index * meta["chunk_size"])
                    f.write(data)

            await asyncio.to_thread(_write)
            if index not in meta["received"]:
                meta["received"].append(index)
            meta["updated_at"] = time.time()
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return meta

    async def complete(self, upload_id: str) -> tuple[dict, Path]:
        """Validate the session is fully received; return (meta, part_path).

        Caller moves the part into permanent storage and then calls discard().
        """
        part, meta_path = self._paths(upload_id)
        meta = self._read_meta(meta_path)
        if meta is None or not part.exists():
            raise KeyError("unknown upload")
        missing = meta["total_chunks"] - len(set(meta["received"]))
        if missing > 0:
            raise ValueError(f"{missing} chunks still missing")
        actual = part.stat().st_size
        if actual != meta["size"]:
            raise ValueError(f"size mismatch: expected {meta['size']}, on disk {actual}")
        return meta, part

    def discard(self, upload_id: str) -> None:
        try:
            part, meta_path = self._paths(upload_id)
        except ValueError:
            return
        for p in (part, meta_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def cleanup_stale(self, max_age_hours: int = PART_MAX_AGE_HOURS) -> int:
        """Drop abandoned sessions (no chunk activity for max_age_hours)."""
        cutoff = time.time() - max_age_hours * 3600
        removed = 0
        for meta_path in self.parts_dir.glob("*.json"):
            meta = self._read_meta(meta_path)
            stale = meta is None or meta.get("updated_at", 0) < cutoff
            if stale:
                upload_id = meta_path.stem
                self.discard(upload_id)
                removed += 1
        # Orphan .part files with no sidecar
        for part in self.parts_dir.glob("*.part"):
            if not part.with_suffix(".json").exists():
                try:
                    if part.stat().st_mtime < cutoff:
                        part.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            logger.info(f"Cleaned up {removed} stale upload sessions")
        return removed
