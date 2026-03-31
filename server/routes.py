"""REST API routes for HummusLink."""

import logging
from io import BytesIO

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageFont

from config import APP_NAME, APP_VERSION, MAX_FILE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter()

# These are set during app initialization in app.py
manager = None
clipboard_monitor = None
file_manager = None
pairing_manager = None


def init_routes(mgr, clip, fm, pm):
    """Initialize route dependencies."""
    global manager, clipboard_monitor, file_manager, pairing_manager
    manager = mgr
    clipboard_monitor = clip
    file_manager = fm
    pairing_manager = pm


@router.get("/api/status")
async def get_status():
    """Server status and connected devices count."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
        "connected_devices": manager.device_count if manager else 0,
    }


@router.get("/api/qr")
async def get_qr_code():
    """Get pairing QR code as an HTML page with the QR image."""
    if not pairing_manager:
        return JSONResponse({"error": "Pairing not available"}, status_code=503)

    qr_b64 = pairing_manager.generate_pairing_qr()
    url = pairing_manager.get_pairing_url()

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>HummusLink - Pair Device</title>
    <style>
        body {{
            background: #0a0a0f;
            color: #fff;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        h1 {{ color: #ff4444; margin-bottom: 10px; }}
        p {{ color: #888; margin-bottom: 30px; text-align: center; }}
        img {{ border-radius: 12px; background: #fff; padding: 16px; }}
        .url {{ color: #ff4444; font-family: monospace; font-size: 14px; margin-top: 20px;
                background: #1a1a2e; padding: 12px 20px; border-radius: 8px; }}
    </style>
</head>
<body>
    <h1>HummusLink</h1>
    <p>Scan this QR code with your iPhone camera<br>or open the URL below in Safari</p>
    <img src="data:image/png;base64,{qr_b64}" alt="QR Code" width="280" height="280">
    <div class="url">{url}</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/pairing-url")
async def get_pairing_url():
    """Get the connection URL as JSON."""
    if not pairing_manager:
        return JSONResponse({"error": "Pairing not available"}, status_code=503)
    return {"url": pairing_manager.get_pairing_url()}


@router.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    from_device: str = Query(default="unknown"),
):
    """Upload a file (multipart form data)."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        return JSONResponse(
            {"error": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"},
            status_code=413,
        )

    metadata = await file_manager.save_file(
        filename=file.filename or "unnamed",
        content=content,
        from_device=from_device,
    )

    # Broadcast file_ready to all connected devices
    if manager:
        await manager.broadcast(
            {
                "type": "file_ready",
                "filename": metadata["filename"],
                "size": metadata["size"],
                "url": metadata["url"],
                "from": from_device,
                "file_id": metadata["file_id"],
                "timestamp": metadata["uploaded_at"],
            }
        )

    return metadata


@router.get("/api/files/{file_id}")
async def download_file(file_id: str):
    """Download a file by ID."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    path = file_manager.get_file_path(file_id)
    if not path:
        return JSONResponse({"error": "File not found"}, status_code=404)

    meta = file_manager.get_file_metadata(file_id)
    filename = meta["filename"] if meta else path.name

    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/api/files")
async def list_files(limit: int = Query(default=50, le=200)):
    """List recent files."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)
    return {"files": file_manager.list_files(limit=limit)}


@router.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    """Delete a file by ID."""
    if not file_manager:
        return JSONResponse({"error": "File manager not available"}, status_code=503)

    if file_manager.delete_file(file_id):
        return {"status": "deleted", "file_id": file_id}
    return JSONResponse({"error": "File not found"}, status_code=404)


@router.get("/api/devices")
async def list_devices():
    """List connected devices."""
    if not manager:
        return {"devices": []}
    return {"devices": manager.get_connected_devices()}


@router.get("/api/clipboard")
async def get_clipboard():
    """Get current clipboard content and history."""
    if not clipboard_monitor:
        return {"content": "", "history": []}
    return {
        "content": clipboard_monitor.get_current(),
        "history": clipboard_monitor.get_history(),
    }


@router.get("/api/storage")
async def get_storage():
    """Get storage usage information."""
    if not file_manager:
        return {"total_bytes": 0, "total_mb": 0, "file_count": 0}
    return file_manager.get_storage_usage()


@router.get("/api/icon/{size}")
async def get_icon(size: int):
    """Generate a PNG icon of the specified size for PWA manifest."""
    size = min(max(size, 16), 1024)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    margin = max(size // 16, 2)
    draw.ellipse(
        [margin, margin, size - margin - 1, size - margin - 1],
        fill="#1a1a2e",
        outline="#ff4444",
        width=max(size // 20, 2),
    )

    # Draw "H"
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
    draw.text((x, y), text, fill="#ff4444", font=font)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")
