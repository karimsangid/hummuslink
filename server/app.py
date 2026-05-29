"""FastAPI application setup for HummusLink."""

import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import APP_NAME, APP_VERSION, SHARED_SECRET
from server.routes import init_routes, router
from server.websocket_handler import ConnectionManager, websocket_endpoint

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Sent on the HTML/JS/CSS so iOS home-screen PWAs never pin a stale copy —
# without this, edits to the frontend don't surface until the app is deleted
# and re-added. The files are tiny and local, so revalidating every load is free.
NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

# Files whose content drives the frontend "version" (and the cache-busting
# query string stamped onto asset URLs). Bumps automatically on any edit.
_VERSIONED_FILES = ("index.html", "app.js", "styles.css")


def frontend_version() -> str:
    """Short content hash of the frontend assets — changes only when they do."""
    h = hashlib.sha1()
    for name in _VERSIONED_FILES:
        p = FRONTEND_DIR / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def create_app(
    manager: ConnectionManager,
    clipboard_monitor,
    file_manager,
    pairing_manager,
    lifespan=None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    # CORS middleware - allow all origins for local network use
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize route dependencies
    init_routes(manager, clipboard_monitor, file_manager, pairing_manager)

    # Include REST API routes
    app.include_router(router)

    # WebSocket endpoint
    @app.websocket("/ws/{device_id}")
    async def ws_endpoint(websocket: WebSocket, device_id: str):
        await websocket_endpoint(
            websocket,
            device_id,
            manager,
            clipboard_monitor,
            file_manager,
            pairing_manager,
        )

    # Mount static files for frontend
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    # Serve index.html at root, with the shared pairing token bootstrapped
    # into localStorage so the WS handshake succeeds even when the user
    # hits the bare URL (i.e. without scanning the QR pairing link).
    # Anyone able to GET / is already inside the Tailscale-only network,
    # so the token is no weaker than the existing access boundary.
    @app.get("/", response_class=HTMLResponse)
    async def serve_index():
        index_path = FRONTEND_DIR / "index.html"
        if not index_path.exists():
            return HTMLResponse(f"{APP_NAME} server is running. Frontend not found.")
        html = index_path.read_text(encoding="utf-8")
        # Version-stamp the asset URLs so a fresh launch always pulls the
        # current JS/CSS instead of an iOS-cached copy.
        ver = frontend_version()
        html = html.replace('href="/styles.css"', f'href="/styles.css?v={ver}"')
        html = html.replace('src="/app.js"', f'src="/app.js?v={ver}"')
        bootstrap = (
            "<script>try{if(!localStorage.getItem('hummuslink_token'))"
            f"localStorage.setItem('hummuslink_token',{SHARED_SECRET!r});"
            "}catch(e){}</script>"
        )
        if "</head>" in html:
            html = html.replace("</head>", bootstrap + "</head>", 1)
        else:
            html = bootstrap + html
        return HTMLResponse(html, headers=NO_STORE)

    # Lightweight version probe — the PWA polls this on foreground and reloads
    # itself when the hash changes, so frontend updates self-deploy.
    @app.get("/api/version")
    async def get_version():
        return JSONResponse({"version": frontend_version()}, headers=NO_STORE)

    # Serve other frontend files (manifest.json, sw.js, etc.)
    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(str(FRONTEND_DIR / "manifest.json"), media_type="application/json")

    @app.get("/sw.js")
    async def serve_sw():
        return FileResponse(str(FRONTEND_DIR / "sw.js"), media_type="application/javascript", headers=NO_STORE)

    @app.get("/styles.css")
    async def serve_styles():
        return FileResponse(str(FRONTEND_DIR / "styles.css"), media_type="text/css", headers=NO_STORE)

    @app.get("/app.js")
    async def serve_app_js():
        return FileResponse(str(FRONTEND_DIR / "app.js"), media_type="application/javascript", headers=NO_STORE)

    logger.info(f"{APP_NAME} FastAPI app created")
    return app
