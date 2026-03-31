"""HummusLink - Cross-platform sync bridge between Windows and iPhone.

Entry point: starts the FastAPI server, system tray, clipboard monitor,
and mDNS service discovery.
"""

import asyncio
import logging
import os
import signal
import sys
import threading

import uvicorn

from config import (
    APP_NAME,
    APP_VERSION,
    SERVER_HOST,
    SERVER_PORT,
    STORAGE_DIR,
)
from server.app import create_app
from server.clipboard import ClipboardMonitor
from server.discovery import ServiceDiscovery
from server.file_manager import FileManager
from server.pairing import PairingManager
from server.tray import TrayApp
from server.websocket_handler import ConnectionManager

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def print_banner(local_ip: str):
    """Print the startup banner."""
    print()
    print("=" * 56)
    print(f"  {APP_NAME} v{APP_VERSION}")
    print(f"  By Hummus Development LLC")
    print("=" * 56)
    print()
    print(f"  Server running at:")
    print(f"    http://{local_ip}:{SERVER_PORT}")
    print()
    print(f"  To connect your iPhone:")
    print(f"    1. Open Safari on your iPhone")
    print(f"    2. Go to http://{local_ip}:{SERVER_PORT}")
    print(f"    3. Tap Share > Add to Home Screen")
    print()
    print(f"  Or scan the QR code at:")
    print(f"    http://{local_ip}:{SERVER_PORT}/api/qr")
    print()
    print(f"  Files stored in: {STORAGE_DIR}")
    print("=" * 56)
    print()


def main():
    """Main entry point."""
    # Initialize components
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

    # Storage
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Storage directory: {STORAGE_DIR}")

    # Core components
    connection_manager = ConnectionManager()
    file_manager = FileManager(STORAGE_DIR)
    clipboard_monitor = ClipboardMonitor(connection_manager)

    # Discovery
    discovery = ServiceDiscovery(SERVER_PORT)
    local_ip = discovery.get_local_ip()

    # Pairing
    pairing_manager = PairingManager(local_ip, SERVER_PORT)

    # Create FastAPI app
    app = create_app(
        manager=connection_manager,
        clipboard_monitor=clipboard_monitor,
        file_manager=file_manager,
        pairing_manager=pairing_manager,
    )

    # Shutdown flag
    shutdown_event = threading.Event()

    def on_quit():
        """Handle quit from system tray."""
        shutdown_event.set()
        os.kill(os.getpid(), signal.SIGINT)

    # System tray (runs in a separate thread)
    server_url = f"http://{local_ip}:{SERVER_PORT}"
    tray = TrayApp(server_url, on_quit, manager=connection_manager)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    tray_thread.start()
    logger.info("System tray started")

    # Register mDNS
    try:
        discovery.register()
    except Exception as e:
        logger.warning(f"mDNS registration failed (non-fatal): {e}")

    # Startup event to launch clipboard monitor
    @app.on_event("startup")
    async def on_startup():
        asyncio.create_task(clipboard_monitor.start())
        logger.info("Clipboard monitor started as background task")

    @app.on_event("shutdown")
    async def on_shutdown():
        clipboard_monitor.stop()
        try:
            discovery.unregister()
        except Exception:
            pass
        logger.info("Shutdown complete")

    # Print banner
    print_banner(local_ip)

    # Run the server
    config = uvicorn.Config(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        clipboard_monitor.stop()
        try:
            discovery.unregister()
        except Exception:
            pass


if __name__ == "__main__":
    main()
