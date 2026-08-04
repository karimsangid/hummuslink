"""REST API routes for HummusLink."""

import logging
import mimetypes
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from urllib.parse import quote

import aiofiles
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageFont
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from server import file_manager as fm_mod
from server.uploads import UploadSessions

from config import (
    APP_NAME,
    APP_VERSION,
    BRAND_BG,
    BRAND_BORDER,
    BRAND_CARD,
    BRAND_CREAM,
    BRAND_CREAM_DIM,
    BRAND_GOLD,
    MAX_FILE_SIZE,
    UPLOAD_CHUNK_SIZE,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Set during init_routes()
manager = None
clipboard_monitor = None
file_manager = None
pairing_manager = None
upload_sessions = None


def init_routes(mgr, clip, fm, pm):
    global manager, clipboard_monitor, file_manager, pairing_manager, upload_sessions
    manager = mgr
    clipboard_monitor = clip
    file_manager = fm
    pairing_manager = pm
    upload_sessions = UploadSessions(fm.storage_dir)


async def _broadcast_file_ready(meta: dict, from_device: str):
    if not manager:
        return
    await manager.broadcast(
        {
            "type": "file_ready",
            "filename": meta["filename"],
            "size": meta["size"],
            "url": meta["url"],
            "thumb_url": meta.get("thumb_url"),
            "from": from_device,
            "file_id": meta["file_id"],
            "timestamp": meta["uploaded_at"],
        }
    )


@router.get("/api/status")
async def get_status():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
        "connected_devices": manager.device_count if manager else 0,
    }


@router.get("/api/qr")
async def get_qr_code():
    if not pairing_manager:
        return JSONResponse({"error": "Pairing not available"}, status_code=503)

    qr_b64 = pairing_manager.generate_pairing_qr()
    url = pairing_manager.get_pairing_url()

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>HummusLink — Pair Device</title>
    <style>
        body {{
            background: {BRAND_BG};
            color: {BRAND_CREAM};
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        h1 {{ color: {BRAND_GOLD}; margin-bottom: 10px; letter-spacing: -0.5px; }}
        p {{ color: {BRAND_CREAM_DIM}; margin-bottom: 30px; text-align: center; }}
        img {{ border-radius: 12px; background: {BRAND_CREAM}; padding: 16px; }}
        .url {{
            color: {BRAND_GOLD};
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 13px;
            margin-top: 20px;
            background: {BRAND_CARD};
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid {BRAND_BORDER};
            max-width: 90%;
            word-break: break-all;
            text-align: center;
        }}
        .footer {{
            color: {BRAND_CREAM_DIM};
            font-size: 12px;
            margin-top: 32px;
        }}
    </style>
</head>
<body>
    <h1>HummusLink</h1>
    <p>Scan this code with your iPhone camera<br>or open the URL below in Safari</p>
    <img src="data:image/png;base64,{qr_b64}" alt="QR Code" width="280" height="280">
    <div class="url">{url}</div>
    <div class="footer">By Hummus Development LLC</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/pairing-url")
async def get_pairing_url():
    if not pairing_manager:
        return JSONResponse({"error": "Pairing not available"}, status_code=503)
    return {"url": pairing_manager.get_pairing_url()}


# ---------- file uploads (streaming) ----------


@router.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    from_device: str = Query(default="unknown"),
):
    """Stream upload to disk so 500MB videos don't blow out RAM."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    file_id, target, safe_filename = file_manager.begin_save(
        file.filename or "unnamed", from_device=from_device
    )
    logger.info(f"Upload started: {safe_filename} from {from_device}")

    written = 0
    try:
        async with aiofiles.open(target, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    await out.close()
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    logger.warning(
                        f"Upload rejected (too large): {safe_filename} from "
                        f"{from_device} exceeded {MAX_FILE_SIZE} bytes"
                    )
                    return JSONResponse(
                        {
                            "error": (
                                f"File too large. Maximum size is "
                                f"{MAX_FILE_SIZE // (1024*1024)}MB"
                            )
                        },
                        status_code=413,
                    )
                await out.write(chunk)
    except Exception as e:
        logger.error(f"upload stream failed: {e}")
        try:
            target.unlink()
        except OSError:
            pass
        return JSONResponse({"error": "Upload failed"}, status_code=500)

    meta = await file_manager.commit_save(file_id, target, safe_filename, from_device)
    await _broadcast_file_ready(meta, from_device)
    return meta


# ---------- resumable chunked uploads ----------


@router.post("/api/upload/init")
async def upload_init(payload: dict):
    """Start a resumable upload. Client slices the file by chunk_size."""
    if not upload_sessions:
        return JSONResponse({"error": "Not ready"}, status_code=503)
    filename = (payload or {}).get("filename") or "unnamed"
    size = int((payload or {}).get("size") or 0)
    from_device = (payload or {}).get("from_device") or "unknown"
    try:
        meta = await upload_sessions.init(filename, size, from_device)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {
        "upload_id": meta["upload_id"],
        "chunk_size": meta["chunk_size"],
        "total_chunks": meta["total_chunks"],
    }


@router.get("/api/upload/status/{upload_id}")
async def upload_status(upload_id: str):
    """What the server already has — lets a client resume after dying mid-send."""
    if not upload_sessions:
        return JSONResponse({"error": "Not ready"}, status_code=503)
    try:
        meta = await upload_sessions.status(upload_id)
    except ValueError:
        return JSONResponse({"error": "Bad upload id"}, status_code=400)
    if meta is None:
        return JSONResponse({"error": "Unknown upload"}, status_code=404)
    return {
        "upload_id": meta["upload_id"],
        "chunk_size": meta["chunk_size"],
        "total_chunks": meta["total_chunks"],
        "received": sorted(set(meta["received"])),
    }


@router.post("/api/upload/chunk/{upload_id}/{index}")
async def upload_chunk(upload_id: str, index: int, request: Request):
    if not upload_sessions:
        return JSONResponse({"error": "Not ready"}, status_code=503)
    data = await request.body()
    try:
        meta = await upload_sessions.write_chunk(upload_id, index, data)
    except KeyError:
        return JSONResponse({"error": "Unknown upload"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"received": len(set(meta["received"])), "total": meta["total_chunks"]}


@router.post("/api/upload/complete/{upload_id}")
async def upload_complete(upload_id: str):
    if not upload_sessions or not file_manager:
        return JSONResponse({"error": "Not ready"}, status_code=503)
    try:
        meta, part = await upload_sessions.complete(upload_id)
    except KeyError:
        return JSONResponse({"error": "Unknown upload"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    file_meta = await file_manager.commit_part(
        meta["filename"], part, meta["from_device"]
    )
    upload_sessions.discard(upload_id)
    await _broadcast_file_ready(file_meta, meta["from_device"])
    return file_meta


# ---------- iOS Shortcut upload (raw body, no multipart) ----------


@router.post("/api/shortcut/upload")
async def shortcut_upload(request: Request, filename: str = Query(default="")):
    """Receive a file or text from an iOS Shortcut ('Get Contents of URL').

    The Shortcut sends the shared item as the raw request body. Text and URLs
    become a text_share + PC clipboard; anything else is stored as a file.
    """
    if not file_manager or not manager:
        return JSONResponse({"error": "Server not ready"}, status_code=503)

    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()

    if ctype in ("text/plain", "application/json") or ctype.startswith("text/"):
        body = await request.body()
        content = body.decode("utf-8", errors="replace").strip()
        if not content:
            return JSONResponse({"error": "Empty body"}, status_code=400)
        if clipboard_monitor:
            try:
                await clipboard_monitor.set_clipboard(content)
            except Exception as e:
                logger.warning(f"shortcut text clipboard push failed: {e}")
        file_manager.db.add_feed("text", content, "ios-shortcut")
        await manager.broadcast(
            {
                "type": "text_share",
                "content": content,
                "from": "ios-shortcut",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"status": "ok", "kind": "text"}

    if not filename:
        ext = mimetypes.guess_extension(ctype) or ".bin"
        filename = f"shortcut-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}"

    file_id, target, safe_filename = file_manager.begin_save(
        filename, from_device="ios-shortcut"
    )
    logger.info(f"Shortcut upload started: {safe_filename} ({ctype})")
    written = 0
    try:
        async with aiofiles.open(target, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    raise ValueError("too large")
                await out.write(chunk)
    except ValueError:
        try:
            target.unlink()
        except OSError:
            pass
        logger.warning(f"Shortcut upload rejected (too large): {safe_filename}")
        return JSONResponse(
            {"error": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"},
            status_code=413,
        )
    except Exception as e:
        logger.error(f"shortcut upload stream failed: {e}")
        try:
            target.unlink()
        except OSError:
            pass
        return JSONResponse({"error": "Upload failed"}, status_code=500)

    if written == 0:
        try:
            target.unlink()
        except OSError:
            pass
        return JSONResponse({"error": "Empty body"}, status_code=400)

    meta = await file_manager.commit_save(file_id, target, safe_filename, "ios-shortcut")
    await _broadcast_file_ready(meta, "ios-shortcut")
    return {"status": "ok", "kind": "file", **meta}


# ---------- zip download (must register before /api/files/{file_id}) ----------


@router.get("/api/files/zip")
async def zip_files(ids: str = Query(...)):
    """Bundle selected files into a zip. Temp zip is deleted after the response."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    entries = []
    for fid in [s for s in ids.split(",") if s][:100]:
        path = file_manager.get_file_path(fid)
        if not path:
            continue
        meta = file_manager.get_file_metadata(fid) or {}
        entries.append((path, meta.get("filename") or path.name))
    if not entries:
        return JSONResponse({"error": "No files found"}, status_code=404)

    tmp = tempfile.NamedTemporaryFile(prefix="hummuslink_", suffix=".zip", delete=False)
    tmp.close()

    def build():
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
            used = set()
            for path, name in entries:
                arcname, n = name, 1
                while arcname in used:
                    arcname = f"{n}_{name}"
                    n += 1
                used.add(arcname)
                z.write(path, arcname)

    await run_in_threadpool(build)
    return FileResponse(
        tmp.name,
        filename="hummuslink-files.zip",
        media_type="application/zip",
        background=BackgroundTask(os.unlink, tmp.name),
    )


def _inline_ok(mime: str) -> bool:
    """Types we're willing to serve with `Content-Disposition: inline`.

    Deliberately narrow. Anything the browser could execute in our own origin —
    text/html, image/svg+xml, JS — stays `attachment` so a shared file can never
    turn into stored XSS against the dashboard. Everything gets `nosniff`.
    """
    if mime in ("application/pdf", "text/plain"):
        return True
    if mime == "image/svg+xml":
        return False
    return mime.startswith(("image/", "video/", "audio/"))


@router.get("/api/files/{file_id}")
async def download_file(file_id: str, inline: int = Query(default=0)):
    """Serve a file by ID.

    Default is `attachment` (the Save/download path). `?inline=1` flips to
    `inline` for safelisted types — Starlette stamps `attachment` whenever a
    `filename=` is passed, and no browser will render an `attachment` response
    inside an `<iframe>`, which is what made PDF preview come up blank.
    """
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    path = file_manager.get_file_path(file_id)
    if not path:
        return JSONResponse({"error": "File not found"}, status_code=404)

    meta = file_manager.get_file_metadata(file_id) or {}
    filename = meta.get("filename") or path.name
    mime = meta.get("mime") or "application/octet-stream"

    headers = {"X-Content-Type-Options": "nosniff"}
    if inline and _inline_ok(mime):
        # Build the header by hand instead of passing filename= (which forces
        # attachment). RFC 5987 form so non-ASCII names survive.
        ascii_name = filename.encode("ascii", "ignore").decode() or "file"
        quoted = quote(filename)
        headers["Content-Disposition"] = (
            f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
        )
        return FileResponse(path=str(path), media_type=mime, headers=headers)

    return FileResponse(
        path=str(path), filename=filename, media_type=mime, headers=headers
    )


@router.get("/api/files/{file_id}/pdf")
async def pdf_info(file_id: str):
    """Page count + page dimensions for the in-app PDF viewer."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    info = await run_in_threadpool(file_manager.pdf_info, file_id)
    if info is None:
        # `renderer` tells the client whether PyMuPDF is missing (fall back to the
        # raw-file view) or the file itself just isn't a readable PDF.
        return JSONResponse(
            {"error": "Not a readable PDF", "renderer": fm_mod.PDF_OK},
            status_code=404,
        )
    return info


@router.get("/api/files/{file_id}/pdf/{page}")
async def pdf_page(file_id: str, page: int, w: int = Query(default=1400)):
    """Render one PDF page to JPEG. Cached on disk; width snaps to a fixed ladder."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    out = await run_in_threadpool(file_manager.get_pdf_page_path, file_id, page, w)
    if not out:
        return JSONResponse({"error": "Page not available"}, status_code=404)
    # Immutable: a given (file_id, page, width) render never changes.
    return FileResponse(
        path=str(out),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/files/{file_id}/text")
async def text_preview(file_id: str):
    """Head of a text file for the inline text viewer."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    data = await run_in_threadpool(file_manager.read_text_preview, file_id)
    if data is None:
        return JSONResponse({"error": "Not previewable as text"}, status_code=404)
    return data


@router.get("/api/files/{file_id}/thumb")
async def get_thumbnail(file_id: str):
    """Return a 256x256 JPEG thumbnail. 404 if not an image."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    thumb = file_manager.get_thumb_path(file_id)
    if not thumb:
        return JSONResponse({"error": "No thumbnail"}, status_code=404)
    return FileResponse(path=str(thumb), media_type="image/jpeg")


@router.get("/api/files")
async def list_files(limit: int = Query(default=50, le=200)):
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    return {"files": file_manager.list_files(limit=limit)}


@router.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    if file_manager.delete_file(file_id):
        return {"status": "deleted", "file_id": file_id}
    return JSONResponse({"error": "File not found"}, status_code=404)


@router.post("/api/files/{file_id}/pin")
async def pin_file(file_id: str, payload: dict):
    """Pinned files survive the periodic cleanup."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    pinned = bool((payload or {}).get("pinned", True))
    if file_manager.set_pinned(file_id, pinned):
        return {"status": "ok", "file_id": file_id, "pinned": pinned}
    return JSONResponse({"error": "File not found"}, status_code=404)


@router.post("/api/files/{file_id}/reveal")
async def reveal_file(file_id: str):
    """Open Windows Explorer with the file selected. The file already lives on
    the PC's disk — no point re-downloading it from a browser on the same box."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    path = file_manager.get_file_path(file_id)
    if not path:
        return JSONResponse({"error": "File not found"}, status_code=404)
    try:
        subprocess.Popen(["explorer", f"/select,{path}"])
    except OSError as e:
        logger.warning(f"reveal failed for {file_id}: {e}")
        return JSONResponse({"error": "Could not open Explorer"}, status_code=500)
    return {"status": "ok"}


@router.post("/api/files/clear-unpinned")
async def clear_unpinned():
    """Delete every unpinned file. One-tap storage cleanup from Settings."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    deleted = 0
    for r in file_manager.list_files(limit=100000):
        if r.get("pinned"):
            continue
        if file_manager.delete_file(r["file_id"]):
            deleted += 1
    logger.info(f"clear-unpinned removed {deleted} files")
    return {"status": "ok", "deleted": deleted}


@router.get("/api/feed")
async def get_feed(limit: int = Query(default=50, le=200)):
    """Unified activity feed (text shares + files), persisted server-side so
    every device sees the same history."""
    if not file_manager:
        return {"feed": []}
    items = []
    for r in file_manager.db.list_feed(limit=limit):
        item = {
            "kind": r["kind"],
            "content": r["content"],
            "from_device": r.get("from_device"),
            "timestamp": r["timestamp"],
        }
        if r["kind"] == "file" and r.get("extra"):
            item["file_id"] = r["extra"]
            item["url"] = f"/api/files/{r['extra']}"
            item["available"] = file_manager.get_file_path(r["extra"]) is not None
        items.append(item)
    return {"feed": items}


@router.get("/api/devices")
async def list_devices():
    if not manager:
        return {"devices": []}
    return {"devices": manager.get_connected_devices()}


@router.get("/api/clipboard")
async def get_clipboard():
    if not clipboard_monitor:
        return {"content": "", "history": []}
    return {
        "content": clipboard_monitor.get_current(),
        "history": clipboard_monitor.get_history(),
    }


@router.post("/api/clipboard")
async def post_clipboard(payload: dict):
    """Accept clipboard content from a device (HTTP path; complements WS clipboard_sync)."""
    if not clipboard_monitor:
        return JSONResponse({"error": "Clipboard not available"}, status_code=503)
    content = (payload or {}).get("content", "")
    if not content:
        return JSONResponse({"error": "Empty content"}, status_code=400)
    await clipboard_monitor.set_clipboard(content)
    if manager:
        await manager.broadcast(
            {
                "type": "clipboard_sync",
                "content": content,
                "from": "http",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    return {"status": "ok"}


@router.delete("/api/clipboard")
async def clear_clipboard():
    if not clipboard_monitor:
        return JSONResponse({"error": "Clipboard not available"}, status_code=503)
    clipboard_monitor.clear_history()
    return {"status": "cleared"}


@router.get("/api/storage")
async def get_storage():
    if not file_manager:
        return {"total_bytes": 0, "total_mb": 0, "file_count": 0}
    return file_manager.get_storage_usage()


# ---------- Web Share Target ----------


@router.post("/api/share-target")
async def share_target(
    request: Request,
    title: Optional[str] = Form(default=None),
    text: Optional[str] = Form(default=None),
    url: Optional[str] = Form(default=None),
    files: list[UploadFile] = File(default=[]),
):
    """Receive a shared payload from the OS share sheet (Android PWA + native iOS share-ext).

    Browsers reach this via POST multipart from the manifest's share_target action.
    On success we 303 the user back to the app root so the PWA UI keeps focus.
    """
    if not file_manager or not manager:
        return JSONResponse({"error": "Server not ready"}, status_code=503)

    pieces = [s for s in (title, text, url) if s]
    combined_text = "\n".join(pieces).strip()

    received: list[dict] = []

    if combined_text:
        file_manager.db.add_feed("text", combined_text, "share-target")
        await manager.broadcast(
            {
                "type": "text_share",
                "content": combined_text,
                "from": "share-target",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        received.append({"kind": "text", "content": combined_text})

    for f in files or []:
        try:
            content = await f.read()
        except Exception:
            continue
        if not content:
            continue
        if len(content) > MAX_FILE_SIZE:
            continue
        meta = file_manager.save_bytes(
            f.filename or "shared", content, from_device="share-target"
        )
        await manager.broadcast(
            {
                "type": "file_ready",
                "filename": meta["filename"],
                "size": meta["size"],
                "url": meta["url"],
                "thumb_url": meta.get("thumb_url"),
                "from": "share-target",
                "file_id": meta["file_id"],
                "timestamp": meta["uploaded_at"],
            }
        )
        received.append({"kind": "file", "url": meta["url"]})

    # If invoked from a browser share sheet, send the user back to the app.
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse("/?shared=1", status_code=303)
    return {"status": "ok", "received": received}


# ---------- PWA icons ----------


@router.get("/api/icon/{size}")
async def get_icon(size: int):
    """Generate a brand-coloured PNG icon for the PWA manifest."""
    size = min(max(size, 16), 1024)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = max(size // 16, 2)
    draw.ellipse(
        [margin, margin, size - margin - 1, size - margin - 1],
        fill=BRAND_BG,
        outline=BRAND_GOLD,
        width=max(size // 20, 2),
    )

    font_size = int(size * 0.5)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()

    text = "H"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2
    y = (size - text_h) // 2 - size // 16
    draw.text((x, y), text, fill=BRAND_GOLD, font=font)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")
