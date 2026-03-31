"""File storage, upload, and download handling for HummusLink."""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import FILE_CLEANUP_MAX_AGE_HOURS

logger = logging.getLogger(__name__)


class FileManager:
    """Handles file storage, upload, and download."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.files: dict[str, dict] = {}
        self._scan_existing_files()

    def _scan_existing_files(self):
        """Scan storage directory for existing files on startup."""
        for path in self.storage_dir.iterdir():
            if path.is_file():
                name = path.name
                # Files are stored as file_id_filename
                parts = name.split("_", 1)
                if len(parts) == 2:
                    file_id, original_name = parts
                    self.files[file_id] = {
                        "file_id": file_id,
                        "filename": original_name,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "uploaded_at": datetime.fromtimestamp(
                            path.stat().st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "from_device": "unknown",
                        "url": f"/api/files/{file_id}",
                    }

    async def save_file(
        self, filename: str, content: bytes, from_device: str = "unknown"
    ) -> dict:
        """Save an uploaded file and return file metadata with download URL."""
        file_id = uuid.uuid4().hex[:12]
        safe_filename = self._sanitize_filename(filename)
        stored_name = f"{file_id}_{safe_filename}"
        file_path = self.storage_dir / stored_name

        file_path.write_bytes(content)

        metadata = {
            "file_id": file_id,
            "filename": safe_filename,
            "path": str(file_path),
            "size": len(content),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "from_device": from_device,
            "url": f"/api/files/{file_id}",
        }
        self.files[file_id] = metadata

        logger.info(
            f"File saved: {safe_filename} ({len(content)} bytes) from {from_device}"
        )
        return metadata

    def get_file_path(self, file_id: str) -> Path | None:
        """Get the path to a stored file for download."""
        meta = self.files.get(file_id)
        if meta:
            path = Path(meta["path"])
            if path.exists():
                return path
        return None

    def get_file_metadata(self, file_id: str) -> dict | None:
        """Get metadata for a stored file."""
        return self.files.get(file_id)

    def list_files(self, limit: int = 50) -> list[dict]:
        """List recent files, sorted by upload time descending."""
        files = sorted(
            self.files.values(), key=lambda f: f["uploaded_at"], reverse=True
        )
        return [
            {
                "file_id": f["file_id"],
                "filename": f["filename"],
                "size": f["size"],
                "uploaded_at": f["uploaded_at"],
                "from_device": f["from_device"],
                "url": f["url"],
            }
            for f in files[:limit]
        ]

    def delete_file(self, file_id: str) -> bool:
        """Delete a file by ID."""
        meta = self.files.pop(file_id, None)
        if meta:
            path = Path(meta["path"])
            if path.exists():
                path.unlink()
                logger.info(f"File deleted: {meta['filename']}")
            return True
        return False

    def cleanup_old_files(self, max_age_hours: int = FILE_CLEANUP_MAX_AGE_HOURS):
        """Remove files older than max_age_hours."""
        now = datetime.now(timezone.utc)
        to_delete = []
        for file_id, meta in self.files.items():
            uploaded_at = datetime.fromisoformat(meta["uploaded_at"])
            age_hours = (now - uploaded_at).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_delete.append(file_id)

        for file_id in to_delete:
            self.delete_file(file_id)

        if to_delete:
            logger.info(f"Cleaned up {len(to_delete)} old files")

    def get_storage_usage(self) -> dict:
        """Get total storage usage."""
        total = sum(
            Path(m["path"]).stat().st_size
            for m in self.files.values()
            if Path(m["path"]).exists()
        )
        return {
            "total_bytes": total,
            "total_mb": round(total / (1024 * 1024), 2),
            "file_count": len(self.files),
        }

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize a filename, removing path separators and null bytes."""
        sanitized = filename.replace("/", "_").replace("\\", "_").replace("\0", "")
        if not sanitized or sanitized.startswith("."):
            sanitized = "file_" + sanitized
        return sanitized[:255]
