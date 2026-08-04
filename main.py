"""HummusLink - Cross-platform sync bridge between Windows and iPhone.

Entry point: starts the FastAPI server, system tray, clipboard monitor,
mDNS service discovery, and SQLite persistence.
"""

import asyncio
import logging
import os
import signal
import threading
from contextlib import asynccontextmanager

import uvicorn

from config import (
    APP_NAME,
    APP_VERSION,
    DB_PATH,
    SERVER_HOST,
    SERVER_PORT,
    SHARED_SECRET,
    STORAGE_DIR,
)
from server.app import create_app
from server.clipboard import ClipboardMonitor
from server.db import DB
from server.discovery import ServiceDiscovery
from server.file_manager import FileManager
from server.pairing import PairingManager
from server.tray import TrayApp
from server.websocket_handler import ConnectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def print_banner(local_ip: str):
    print()
    print("=" * 56)
    print(f"  {APP_NAME} v{APP_VERSION}")
    print(f"  By Hummus Development LLC")
    print("=" * 56)
    print()
    print(f"  Server running at:")
    print(f"    http://{local_ip}:{SERVER_PORT}")
    print()
    print(f"  Shared secret (first 8 chars): {SHARED_SECRET[:8]}...")
    print(f"  (full secret persisted at {STORAGE_DIR / '.shared_secret'})")
    print()
    print(f"  To connect your iPhone:")
    print(f"    1. Open Safari on your iPhone")
    print(f"    2. Open the QR page at http://{local_ip}:{SERVER_PORT}/api/qr")
    print(f"    3. Scan it (URL embeds the shared secret)")
    print()
    print(f"  Files stored in: {STORAGE_DIR}")
    print(f"  DB:              {DB_PATH}")
    print("=" * 56)
    print()


def main():
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Persistent state
    db = DB(DB_PATH)

    # Core components
    connection_manager = ConnectionManager(db=db)
    file_manager = FileManager(STORAGE_DIR, db=db)

    # Discovery (need IP before pairing manager)
    discovery = ServiceDiscovery(SERVER_PORT)
    local_ip = discovery.get_local_ip()

    # Pairing
    pairing_manager = PairingManager(local_ip, SERVER_PORT)

    # Clipboard
    clipboard_monitor = ClipboardMonitor(connection_manager, db, file_manager)

    # Periodic maintenance: expire old unpinned files + stale upload parts.
    async def maintenance_loop():
        from server.routes import upload_sessions

        while True:
            try:
                await asyncio.to_thread(file_manager.cleanup_old_files)
                if upload_sessions is not None:
                    await asyncio.to_thread(upload_sessions.cleanup_stale)
            except Exception as e:
                logger.warning(f"maintenance pass failed: {e}")
            await asyncio.sleep(6 * 3600)

    # Lifespan: launch + tear down background tasks tied to the app lifetime.
    @asynccontextmanager
    async def lifespan(app):
        cb_task = asyncio.create_task(clipboard_monitor.start())
        maint_task = asyncio.create_task(maintenance_loop())
        try:
            discovery.register()
        except Exception as e:
            logger.warning(f"mDNS registration failed (non-fatal): {e}")
        logger.info("Lifespan ready")
        try:
            yield
        finally:
            clipboard_monitor.stop()
            for t in (cb_task, maint_task):
                t.cancel()
            try:
                await cb_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                discovery.unregister()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
            logger.info("Lifespan shutdown complete")

    # FastAPI app
    app = create_app(
        manager=connection_manager,
        clipboard_monitor=clipboard_monitor,
        file_manager=file_manager,
        pairing_manager=pairing_manager,
        lifespan=lifespan,
    )

    # Tray (separate thread — needs a running event loop reference for drop window)
    server_url = f"http://{local_ip}:{SERVER_PORT}"
    main_loop_holder = {"loop": None}

    def on_quit():
        os.kill(os.getpid(), signal.SIGINT)

    def open_drop():
        loop = main_loop_holder.get("loop")
        if loop is None:
            logger.warning("Drop window: event loop not yet running")
            return
        try:
            from server.drop_window import open_drop_window

            open_drop_window(file_manager, connection_manager, loop)
        except Exception as e:
            logger.error(f"open_drop failed: {e}")

    tray = TrayApp(server_url, on_quit, manager=connection_manager, on_open_drop=open_drop)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    tray_thread.start()
    logger.info("System tray started")

    # Phone-origin events fire native Windows toasts through the tray icon.
    from server import notify

    notify.pc_notify_hook = tray.notify

    print_banner(local_ip)

    # Capture the running loop once uvicorn starts so the tray thread can bounce
    # work onto it.
    class _LoopCaptureServer(uvicorn.Server):
        async def serve(self, sockets=None):
            main_loop_holder["loop"] = asyncio.get_running_loop()
            await super().serve(sockets=sockets)

    config = uvicorn.Config(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
        access_log=False,
    )
    server = _LoopCaptureServer(config)

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
