"""File storage, upload, and download handling for HummusLink."""

import logging
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

# Register HEIC opener with Pillow so iPhone uploads display as images.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_OK = True
except Exception as _e:  # pragma: no cover
    HEIC_OK = False

from config import FILE_CLEANUP_MAX_AGE_HOURS
from server import notify

logger = logging.getLogger(__name__)


# PyMuPDF powers the PDF quick-view: page-1 grid thumbnails and the rendered
# pages the viewer scrolls through. Optional — without it PDFs simply fall back
# to a generic icon and the raw-file link, exactly as before.
try:
    import pymupdf as fitz

    PDF_OK = True
except Exception:  # pragma: no cover - older wheels only expose `fitz`
    try:
        import fitz

        PDF_OK = True
    except Exception:
        PDF_OK = False

_UNSAFE_NAME_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
THUMB_SIZE = (256, 256)
HEIC_EXTS = {".heic", ".heif"}
FFMPEG = shutil.which("ffmpeg")

# Rendered-page cache bounds. Widths are snapped to this ladder so a pinching
# phone can't ask for 900 distinct sizes and fill the disk with near-duplicates.
PDF_PAGE_WIDTHS = (640, 1000, 1400, 2000)
PDF_MAX_PAGES = 2000
# Text quick-view: how much of a text file we send to the viewer.
TEXT_PREVIEW_BYTES = 256 * 1024
TEXT_PREVIEW_EXTS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log", ".yml", ".yaml",
    ".ini", ".cfg", ".conf", ".toml", ".xml", ".html", ".htm", ".css", ".js",
    ".ts", ".tsx", ".jsx", ".py", ".sh", ".bat", ".ps1", ".sql", ".rs", ".go",
    ".java", ".c", ".h", ".cpp", ".rb", ".php", ".swift", ".kt", ".env", ".srt",
}


def _is_pdf(mime: str | None) -> bool:
    return (mime or "") == "application/pdf"


def is_text_previewable(mime: str | None, filename: str | None) -> bool:
    """True when the file is plausibly human-readable text we can show inline."""
    m = mime or ""
    if m.startswith("text/") or m in ("application/json", "application/xml"):
        return True
    return Path(filename or "").suffix.lower() in TEXT_PREVIEW_EXTS


def _has_thumb(mime: str | None) -> bool:
    m = mime or ""
    if m.startswith("image/"):
        return True
    if PDF_OK and _is_pdf(m):
        return True
    return bool(FFMPEG) and m.startswith("video/")


class FileManager:
    """Handles file storage, upload, and download. DB-backed metadata."""

    def __init__(self, storage_dir: Path, db):
        self.storage_dir = storage_dir.resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_dir = self.storage_dir / ".thumbs"
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        # Rendered PDF pages live one folder per file so deleting the file can
        # drop the whole render cache in one shot.
        self.pdf_dir = self.thumb_dir / "pdf"
        self.db = db
        self._reconcile_with_disk()

    def _reconcile_with_disk(self):
        """Ensure DB knows about every file on disk (and forgets ones that vanished).

        Only `<12hex>_<filename>` entries are recognized — secret files, the SQLite
        DB, and other dotfiles are explicitly ignored.
        """
        on_disk: dict[str, Path] = {}
        for path in self.storage_dir.iterdir():
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.name.startswith("hummuslink.db"):
                continue
            parts = path.name.split("_", 1)
            if len(parts) != 2:
                continue
            file_id, _ = parts
            # file_id must be 12 lowercase hex chars (uuid4().hex[:12])
            if len(file_id) != 12 or not all(c in "0123456789abcdef" for c in file_id):
                continue
            on_disk[file_id] = path

        known = self.db.all_files()

        # Add files that exist on disk but not in DB (preserves history across upgrade)
        for file_id, path in on_disk.items():
            if file_id not in known:
                _, original = path.name.split("_", 1)
                self.db.upsert_file(
                    {
                        "file_id": file_id,
                        "filename": original,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "mime": _guess_mime(original),
                        "from_device": "unknown",
                        "uploaded_at": datetime.fromtimestamp(
                            path.stat().st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }
                )

        # Drop DB rows whose file is gone
        for file_id in list(known.keys()):
            if file_id not in on_disk:
                self.db.delete_file(file_id)

    # ----- save (streaming) -----

    def begin_save(self, filename: str, from_device: str = "unknown"):
        """Allocate a path for a new upload. Returns (file_id, target_path, safe_filename)."""
        file_id = uuid.uuid4().hex[:12]
        safe_filename = self._sanitize_filename(filename)
        stored_name = f"{file_id}_{safe_filename}"
        target = (self.storage_dir / stored_name).resolve()
        # Resolved path must remain inside storage_dir (defence in depth).
        if self.storage_dir not in target.parents:
            raise ValueError("Resolved path escapes storage directory")
        return file_id, target, safe_filename

    async def commit_save(
        self,
        file_id: str,
        target: Path,
        safe_filename: str,
        from_device: str,
    ) -> dict:
        """Finalize an upload after streaming is done. Converts HEIC→JPEG if needed."""
        # iPhone HEIC → convert to JPEG so PC viewers can read.
        if HEIC_OK and target.suffix.lower() in HEIC_EXTS:
            try:
                with Image.open(target) as im:
                    rgb = im.convert("RGB")
                    new_path = target.with_suffix(".jpg")
                    rgb.save(new_path, format="JPEG", quality=88)
                target.unlink()
                target = new_path
                safe_filename = (
                    safe_filename.rsplit(".", 1)[0] + ".jpg"
                    if "." in safe_filename
                    else safe_filename + ".jpg"
                )
                # Refresh stored name to match actual file on disk
                new_stored = f"{file_id}_{safe_filename}"
                rename_target = self.storage_dir / new_stored
                target.rename(rename_target)
                target = rename_target
                logger.info(f"Converted HEIC → JPEG for {file_id}")
            except Exception as e:
                logger.warning(f"HEIC conversion failed for {file_id}: {e}")

        size = target.stat().st_size
        meta = {
            "file_id": file_id,
            "filename": safe_filename,
            "path": str(target),
            "size": size,
            "mime": _guess_mime(safe_filename),
            "from_device": from_device,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.upsert_file(meta)
        self.db.add_feed("file", safe_filename, from_device, extra=file_id)
        notify.push_file(safe_filename, size, from_device)
        meta["url"] = f"/api/files/{file_id}"
        meta["thumb_url"] = (
            f"/api/files/{file_id}/thumb" if _has_thumb(meta["mime"]) else None
        )
        logger.info(
            f"File saved: {safe_filename} ({size} bytes, {meta['mime']}) from {from_device}"
        )
        return meta

    async def commit_part(self, filename: str, part_path: Path, from_device: str) -> dict:
        """Finalize a resumable upload: move the assembled part file into storage."""
        file_id, target, safe_filename = self.begin_save(filename, from_device)
        os.replace(part_path, target)
        return await self.commit_save(file_id, target, safe_filename, from_device)

    def save_bytes(self, filename: str, content: bytes, from_device: str = "unknown") -> dict:
        """Synchronous in-memory save (small files only — image clipboard, share-target text)."""
        file_id, target, safe_filename = self.begin_save(filename, from_device)
        target.write_bytes(content)

        if HEIC_OK and target.suffix.lower() in HEIC_EXTS:
            try:
                with Image.open(target) as im:
                    rgb = im.convert("RGB")
                    new_path = target.with_suffix(".jpg")
                    rgb.save(new_path, format="JPEG", quality=88)
                target.unlink()
                safe_filename = (
                    safe_filename.rsplit(".", 1)[0] + ".jpg"
                    if "." in safe_filename
                    else safe_filename + ".jpg"
                )
                new_stored = f"{file_id}_{safe_filename}"
                rename_target = self.storage_dir / new_stored
                new_path.rename(rename_target)
                target = rename_target
            except Exception as e:
                logger.warning(f"HEIC conversion failed for {file_id}: {e}")

        size = target.stat().st_size
        meta = {
            "file_id": file_id,
            "filename": safe_filename,
            "path": str(target),
            "size": size,
            "mime": _guess_mime(safe_filename),
            "from_device": from_device,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.upsert_file(meta)
        self.db.add_feed("file", safe_filename, from_device, extra=file_id)
        notify.push_file(safe_filename, size, from_device)
        meta["url"] = f"/api/files/{file_id}"
        meta["thumb_url"] = (
            f"/api/files/{file_id}/thumb" if _has_thumb(meta["mime"]) else None
        )
        return meta

    # ----- read -----

    def get_file_path(self, file_id: str) -> Path | None:
        meta = self.db.get_file(file_id)
        if not meta:
            return None
        path = Path(meta["path"])
        return path if path.exists() else None

    def get_file_metadata(self, file_id: str) -> dict | None:
        return self.db.get_file(file_id)

    def list_files(self, limit: int = 50) -> list[dict]:
        rows = self.db.list_files(limit=limit)
        return [
            {
                "file_id": r["file_id"],
                "filename": r["filename"],
                "size": r["size"],
                "mime": r.get("mime"),
                "uploaded_at": r["uploaded_at"],
                "from_device": r.get("from_device"),
                "pinned": bool(r.get("pinned")),
                "url": f"/api/files/{r['file_id']}",
                "thumb_url": (
                    f"/api/files/{r['file_id']}/thumb"
                    if _has_thumb(r.get("mime"))
                    else None
                ),
            }
            for r in rows
        ]

    def delete_file(self, file_id: str) -> bool:
        meta = self.db.get_file(file_id)
        if not meta:
            return False
        path = Path(meta["path"])
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.warning(f"unlink failed for {path}: {e}")
        # Also remove cached thumb if any
        thumb = self.thumb_dir / f"{file_id}.jpg"
        if thumb.exists():
            try:
                thumb.unlink()
            except OSError:
                pass
        # ...and any rendered PDF pages, which are far larger than the thumb.
        pages = self.pdf_dir / file_id
        if pages.is_dir():
            shutil.rmtree(pages, ignore_errors=True)
        self.db.delete_file(file_id)
        logger.info(f"File deleted: {meta['filename']}")
        return True

    # ----- thumbnails -----

    def get_thumb_path(self, file_id: str) -> Path | None:
        """Return path to a 256x256 JPEG thumbnail. Lazily generated, cached.

        Images via Pillow; videos via ffmpeg (first-second poster frame);
        PDFs via PyMuPDF (first page).
        """
        meta = self.db.get_file(file_id)
        if not meta:
            return None
        mime = meta.get("mime") or ""
        if not _has_thumb(mime):
            return None

        thumb = self.thumb_dir / f"{file_id}.jpg"
        if thumb.exists():
            return thumb

        src = Path(meta["path"])
        if not src.exists():
            return None

        if mime.startswith("video/"):
            return self._make_video_thumb(file_id, src, thumb)

        if _is_pdf(mime):
            return self._make_pdf_thumb(file_id, src, thumb)

        try:
            with Image.open(src) as im:
                im.thumbnail(THUMB_SIZE)
                im.convert("RGB").save(thumb, format="JPEG", quality=80)
            return thumb
        except Exception as e:
            logger.warning(f"thumbnail failed for {file_id}: {e}")
            return None

    @staticmethod
    def _make_video_thumb(file_id: str, src: Path, thumb: Path) -> Path | None:
        """Extract a poster frame with ffmpeg, scaled to fit THUMB_SIZE."""
        if not FFMPEG:
            return None
        cmd = [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            f"scale={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:force_original_aspect_ratio=decrease",
            str(thumb),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=20, creationflags=0x08000000
            )  # CREATE_NO_WINDOW — server runs under pythonw
            if result.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0:
                return thumb
            # Very short clips can have nothing at 0.5s — retry from the start.
            cmd[cmd.index("-ss") + 1] = "0"
            result = subprocess.run(
                cmd, capture_output=True, timeout=20, creationflags=0x08000000
            )
            if result.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0:
                return thumb
            logger.warning(
                f"video thumbnail failed for {file_id}: {result.stderr.decode(errors='replace')[:200]}"
            )
        except Exception as e:
            logger.warning(f"video thumbnail failed for {file_id}: {e}")
        try:
            if thumb.exists() and thumb.stat().st_size == 0:
                thumb.unlink()
        except OSError:
            pass
        return None

    @staticmethod
    def _make_pdf_thumb(file_id: str, src: Path, thumb: Path) -> Path | None:
        """Render page 1 of a PDF down to a grid thumbnail."""
        if not PDF_OK:
            return None
        try:
            with fitz.open(str(src)) as doc:
                if doc.page_count < 1:
                    return None
                page = doc.load_page(0)
                # Render at ~2x the tile so the downscale stays crisp on retina.
                scale = (THUMB_SIZE[0] * 2) / max(page.rect.width, 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                data = pix.tobytes("ppm")
            with Image.open(BytesIO(data)) as im:
                im.thumbnail(THUMB_SIZE)
                # PDFs are usually white-on-white; flatten onto white, not black.
                im.convert("RGB").save(thumb, format="JPEG", quality=82)
            return thumb
        except Exception as e:
            logger.warning(f"pdf thumbnail failed for {file_id}: {e}")
            return None

    # ----- PDF quick-view -----

    def pdf_info(self, file_id: str) -> dict | None:
        """Page count + per-page aspect ratios, so the viewer can size placeholders."""
        if not PDF_OK:
            return None
        meta = self.db.get_file(file_id)
        if not meta or not _is_pdf(meta.get("mime")):
            return None
        src = Path(meta["path"])
        if not src.exists():
            return None
        try:
            with fitz.open(str(src)) as doc:
                if doc.needs_pass:
                    return {
                        "file_id": file_id,
                        "filename": meta.get("filename"),
                        "encrypted": True,
                        "pages": 0,
                        "sizes": [],
                    }
                total = doc.page_count
                count = min(total, PDF_MAX_PAGES)
                sizes = []
                for i in range(count):
                    r = doc.load_page(i).rect
                    sizes.append(
                        {"w": round(r.width, 1), "h": round(r.height, 1)}
                    )
            return {
                "file_id": file_id,
                "filename": meta.get("filename"),
                "encrypted": False,
                "pages": count,
                "total_pages": total,
                "truncated": count < total,
                "sizes": sizes,
                "widths": list(PDF_PAGE_WIDTHS),
            }
        except Exception as e:
            logger.warning(f"pdf info failed for {file_id}: {e}")
            return None

    def get_pdf_page_path(self, file_id: str, page: int, width: int) -> Path | None:
        """Render (and cache) one PDF page as a JPEG at a snapped width.

        Cached under `.thumbs/pdf/<file_id>/<page>_<width>.jpg` so re-opening a
        document is instant and cleanup can drop the whole folder with the file.
        """
        if not PDF_OK:
            return None
        meta = self.db.get_file(file_id)
        if not meta or not _is_pdf(meta.get("mime")):
            return None
        src = Path(meta["path"])
        if not src.exists():
            return None

        # Snap to the ladder — an arbitrary ?w= must never mint a new cache entry.
        width = min(PDF_PAGE_WIDTHS, key=lambda w: abs(w - int(width or 0)))
        if page < 0 or page >= PDF_MAX_PAGES:
            return None

        out_dir = self.pdf_dir / file_id
        out = out_dir / f"{page}_{width}.jpg"
        if out.exists():
            return out

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            with fitz.open(str(src)) as doc:
                if doc.needs_pass or page >= doc.page_count:
                    return None
                pg = doc.load_page(page)
                scale = width / max(pg.rect.width, 1)
                pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                data = pix.tobytes("ppm")
            with Image.open(BytesIO(data)) as im:
                im.convert("RGB").save(out, format="JPEG", quality=84, optimize=True)
            return out
        except Exception as e:
            logger.warning(f"pdf page render failed for {file_id} p{page}: {e}")
            return None

    # ----- text quick-view -----

    def read_text_preview(self, file_id: str) -> dict | None:
        """Return the head of a text file, decoded defensively."""
        meta = self.db.get_file(file_id)
        if not meta:
            return None
        if not is_text_previewable(meta.get("mime"), meta.get("filename")):
            return None
        src = Path(meta["path"])
        if not src.exists():
            return None
        try:
            raw = src.read_bytes()[: TEXT_PREVIEW_BYTES + 1]
        except OSError as e:
            logger.warning(f"text preview read failed for {file_id}: {e}")
            return None
        truncated = len(raw) > TEXT_PREVIEW_BYTES
        raw = raw[:TEXT_PREVIEW_BYTES]
        # A NUL in the head means it isn't really text, whatever the extension says.
        if b"\x00" in raw[:8192]:
            return None
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        return {
            "file_id": file_id,
            "filename": meta.get("filename"),
            "text": text,
            "truncated": truncated,
            "size": meta.get("size"),
        }

    # ----- maintenance -----

    def set_pinned(self, file_id: str, pinned: bool) -> bool:
        return self.db.set_pinned(file_id, pinned)

    def cleanup_old_files(self, max_age_hours: int = FILE_CLEANUP_MAX_AGE_HOURS):
        now = datetime.now(timezone.utc)
        to_delete = []
        for r in self.db.list_files(limit=10000):
            if r.get("pinned"):
                continue
            uploaded_at = datetime.fromisoformat(r["uploaded_at"])
            age_hours = (now - uploaded_at).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_delete.append(r["file_id"])
        for fid in to_delete:
            self.delete_file(fid)
        if to_delete:
            logger.info(f"Cleaned up {len(to_delete)} old files")

    def get_storage_usage(self) -> dict:
        rows = self.db.list_files(limit=100000)
        total = 0
        for r in rows:
            try:
                total += int(r["size"])
            except Exception:
                continue
        return {
            "total_bytes": total,
            "total_mb": round(total / (1024 * 1024), 2),
            "file_count": len(rows),
        }

    # ----- helpers -----

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Strip path separators, parent traversal, control chars, and reserved names."""
        name = filename.strip().replace("\\", "/").split("/")[-1]
        # Drop any leading dots or `..` so path traversal can't ride along.
        while name.startswith(".") or name.startswith(".."):
            name = name.lstrip(".")
        name = _UNSAFE_NAME_CHARS.sub("_", name)
        if not name:
            name = "file"
        return name[:200]


def _guess_mime(filename: str) -> str:
    """Return best-guess MIME, defaulting to application/octet-stream."""
    # Force HEIC mapping for older mimetypes that don't know it.
    lower = filename.lower()
    if lower.endswith(".heic"):
        return "image/heic"
    if lower.endswith(".heif"):
        return "image/heif"
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"
