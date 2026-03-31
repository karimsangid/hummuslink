"""FastAPI application setup for HummusLink."""

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import APP_NAME, APP_VERSION
from server.routes import init_routes, router
from server.websocket_handler import ConnectionManager, websocket_endpoint

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


def create_app(
    manager: ConnectionManager,
    clipboard_monitor,
    file_manager,
    pairing_manager,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(title=APP_NAME, version=APP_VERSION)

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
            websocket, device_id, manager, clipboard_monitor, file_manager
        )

    # Mount static files for frontend
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    # Serve index.html at root
    @app.get("/")
    async def serve_index():
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path), media_type="text/html")
        return {"message": f"{APP_NAME} server is running. Frontend not found."}

    # Serve other frontend files (manifest.json, sw.js, etc.)
    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(str(FRONTEND_DIR / "manifest.json"), media_type="application/json")

    @app.get("/sw.js")
    async def serve_sw():
        return FileResponse(str(FRONTEND_DIR / "sw.js"), media_type="application/javascript")

    @app.get("/styles.css")
    async def serve_styles():
        return FileResponse(str(FRONTEND_DIR / "styles.css"), media_type="text/css")

    @app.get("/app.js")
    async def serve_app_js():
        return FileResponse(str(FRONTEND_DIR / "app.js"), media_type="application/javascript")

    logger.info(f"{APP_NAME} FastAPI app created")
    return app
