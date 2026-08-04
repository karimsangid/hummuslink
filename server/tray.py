"""System tray icon and menu for HummusLink on Windows."""

import logging
import os
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import APP_NAME, BRAND_BG, BRAND_GOLD, BRAND_CREAM, STORAGE_DIR

logger = logging.getLogger(__name__)


class TrayApp:
    """System tray icon for HummusLink."""

    def __init__(self, server_url: str, on_quit: callable, manager=None, on_open_drop=None):
        self.server_url = server_url
        self.on_quit = on_quit
        self.manager = manager
        self.on_open_drop = on_open_drop
        self.icon = None

    def create_icon(self) -> Image.Image:
        """Create a 64x64 brand-coloured icon (warm-dark + heritage gold 'H')."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background circle - warm-dark with gold ring
        draw.ellipse(
            [2, 2, size - 3, size - 3],
            fill=BRAND_BG,
            outline=BRAND_GOLD,
            width=3,
        )

        # Draw "H" in heritage gold
        try:
            font = ImageFont.truetype("arial.ttf", 32)
        except (OSError, IOError):
            font = ImageFont.load_default()

        text = "H"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2
        y = (size - text_h) // 2 - 2
        draw.text((x, y), text, fill=BRAND_GOLD, font=font)

        return img

    def build_menu(self) -> pystray.Menu:
        """Build the system tray context menu."""
        items = [
            pystray.MenuItem(
                "Open Dashboard",
                self._open_dashboard,
                default=True,
            ),
            pystray.MenuItem(
                "Show QR Code",
                self._show_qr,
            ),
        ]
        if self.on_open_drop:
            items.append(
                pystray.MenuItem("Drop Files Here...", self._open_drop)
            )
        items.append(
            pystray.MenuItem("Open Storage Folder", self._open_storage)
        )
        items.extend([
            pystray.MenuItem(
                lambda item: f"Connected Devices: {self._device_count()}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        ])
        return pystray.Menu(*items)

    def _open_dashboard(self):
        webbrowser.open(self.server_url)

    def _show_qr(self):
        webbrowser.open(f"{self.server_url}/api/qr")

    def _open_drop(self):
        if self.on_open_drop:
            # Tk must run on main thread on Windows; spawn a fresh thread that
            # owns its own Tk root.
            threading.Thread(target=self.on_open_drop, daemon=True).start()

    def _open_storage(self):
        try:
            os.startfile(str(STORAGE_DIR))
        except OSError as e:
            logger.warning(f"open storage folder failed: {e}")

    def notify(self, title: str, message: str):
        """Native Windows toast. Safe to call from any thread."""
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.warning(f"tray notify failed: {e}")

    def _device_count(self) -> int:
        if self.manager:
            return self.manager.device_count
        return 0

    def _quit(self):
        logger.info("Quit requested from system tray")
        if self.icon:
            self.icon.stop()
        if self.on_quit:
            self.on_quit()

    def run(self):
        """Start the system tray icon. Blocking - run in a thread."""
        self.icon = pystray.Icon(
            APP_NAME,
            self.create_icon(),
            APP_NAME,
            menu=self.build_menu(),
        )
        logger.info("System tray icon started")
        self.icon.run()
