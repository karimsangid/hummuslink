"""System tray icon and menu for HummusLink on Windows."""

import logging
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import APP_NAME

logger = logging.getLogger(__name__)


class TrayApp:
    """System tray icon for HummusLink."""

    def __init__(self, server_url: str, on_quit: callable, manager=None):
        self.server_url = server_url
        self.on_quit = on_quit
        self.manager = manager
        self.icon = None

    def create_icon(self) -> Image.Image:
        """Create a simple 64x64 icon with an 'H' and link motif using PIL."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background circle - dark with red accent
        draw.ellipse([2, 2, size - 3, size - 3], fill="#1a1a2e", outline="#ff4444", width=3)

        # Draw "H" in center
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
        draw.text((x, y), text, fill="#ff4444", font=font)

        return img

    def build_menu(self) -> pystray.Menu:
        """Build the system tray context menu."""
        return pystray.Menu(
            pystray.MenuItem(
                "Open Dashboard",
                self._open_dashboard,
                default=True,
            ),
            pystray.MenuItem(
                "Show QR Code",
                self._show_qr,
            ),
            pystray.MenuItem(
                lambda item: f"Connected Devices: {self._device_count()}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Quit",
                self._quit,
            ),
        )

    def _open_dashboard(self):
        """Open the web dashboard in the default browser."""
        webbrowser.open(self.server_url)

    def _show_qr(self):
        """Open the QR code page in the browser."""
        webbrowser.open(f"{self.server_url}/api/qr")

    def _device_count(self) -> int:
        """Get the number of connected devices."""
        if self.manager:
            return self.manager.device_count
        return 0

    def _quit(self):
        """Quit the application."""
        logger.info("Quit requested from system tray")
        if self.icon:
            self.icon.stop()
        if self.on_quit:
            self.on_quit()

    def run(self):
        """Start the system tray icon. This is blocking -- run in a thread."""
        self.icon = pystray.Icon(
            APP_NAME,
            self.create_icon(),
            APP_NAME,
            menu=self.build_menu(),
        )
        logger.info("System tray icon started")
        self.icon.run()
