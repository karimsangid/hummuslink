"""Clipboard monitoring + sync for HummusLink.

Strategy:
- Event-driven on Windows via AddClipboardFormatListener (pywin32). Zero CPU when idle.
- Polling fallback on macOS / Linux (or if pywin32 missing).
- Watches both text and images. Image clipboard → saved as PNG via FileManager,
  broadcast as a file_ready message so the existing file pipeline handles it.
"""

import asyncio
import hashlib
import logging
import sys
import threading
from datetime import datetime, timezone

import pyperclip
from PIL import ImageGrab

from config import CLIPBOARD_POLL_INTERVAL, CLIPBOARD_HISTORY_SIZE

logger = logging.getLogger(__name__)


def _hash_image(im) -> str:
    return hashlib.sha1(im.tobytes()).hexdigest()


class ClipboardMonitor:
    """Watches the host clipboard for changes and broadcasts them."""

    def __init__(self, manager, db, file_manager):
        self.manager = manager
        self.db = db
        self.file_manager = file_manager
        self.last_text: str = ""
        self.last_image_hash: str = ""
        self.running: bool = False
        self.ignore_next: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._win_listener_thread: threading.Thread | None = None

    async def start(self):
        """Begin watching. Picks event-driven Windows path if available."""
        self.running = True
        self._loop = asyncio.get_running_loop()
        logger.info("Clipboard monitor starting")

        # Initial state
        try:
            self.last_text = pyperclip.paste() or ""
        except Exception:
            self.last_text = ""

        if sys.platform == "win32" and self._try_start_win_listener():
            logger.info("Clipboard monitor: Windows event listener active")
            # Listener thread does the work; we just block until stopped.
            while self.running:
                await asyncio.sleep(1)
        else:
            logger.info(
                f"Clipboard monitor: polling fallback every {CLIPBOARD_POLL_INTERVAL}s"
            )
            await self._poll_loop()

        logger.info("Clipboard monitor stopped")

    # ---------- Windows event-driven path ----------

    def _try_start_win_listener(self) -> bool:
        try:
            import win32api
            import win32con
            import win32gui
        except ImportError:
            logger.warning("pywin32 not available, falling back to polling")
            return False

        def listener():
            class_name = "HummusLinkClipboardListener"
            hinst = win32api.GetModuleHandle(None)
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = class_name
            wc.lpfnWndProc = self._make_wndproc()
            try:
                class_atom = win32gui.RegisterClass(wc)
            except Exception as e:
                logger.error(f"RegisterClass failed: {e}")
                return

            hwnd = win32gui.CreateWindowEx(
                0,
                class_atom,
                "HummusLinkClipboardListener",
                0,
                0,
                0,
                0,
                0,
                win32con.HWND_MESSAGE,
                0,
                hinst,
                None,
            )
            try:
                import ctypes
                ctypes.windll.user32.AddClipboardFormatListener(hwnd)
            except Exception as e:
                logger.error(f"AddClipboardFormatListener failed: {e}")
                return

            logger.info("Win32 clipboard listener pumping messages")
            while self.running:
                win32gui.PumpWaitingMessages()
                # Sleep briefly so we don't spin the CPU; PumpWaitingMessages
                # is non-blocking. WM_CLIPBOARDUPDATE will queue and drain
                # on the next pump.
                try:
                    import time
                    time.sleep(0.1)
                except Exception:
                    break

        t = threading.Thread(target=listener, daemon=True, name="HummusLinkClipboard")
        t.start()
        self._win_listener_thread = t
        return True

    def _make_wndproc(self):
        import win32con

        WM_CLIPBOARDUPDATE = 0x031D

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE and self.running:
                self._on_clipboard_event()
            elif msg == win32con.WM_DESTROY:
                import win32gui
                win32gui.PostQuitMessage(0)
                return 0
            try:
                import win32gui
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
            except Exception:
                return 0

        return wndproc

    def _on_clipboard_event(self):
        """Fired by Win32 listener — bounce work onto the asyncio loop."""
        if self.ignore_next:
            self.ignore_next = False
            try:
                self.last_text = pyperclip.paste() or ""
            except Exception:
                pass
            return
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._sync_clipboard(), loop)

    # ---------- polling fallback ----------

    async def _poll_loop(self):
        while self.running:
            try:
                await asyncio.sleep(CLIPBOARD_POLL_INTERVAL)
                await self._sync_clipboard()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Clipboard polling error: {e}")
                await asyncio.sleep(1)

    # ---------- shared core ----------

    async def _sync_clipboard(self):
        """Detect what's on the clipboard now and broadcast diffs (text or image)."""
        # Try image first; pyperclip's paste of an image cb returns the OS marker text
        # on some platforms, polluting last_text.
        image = None
        try:
            grabbed = ImageGrab.grabclipboard()
            if grabbed is not None and not isinstance(grabbed, list):
                image = grabbed
        except Exception:
            image = None

        if image is not None:
            try:
                h = _hash_image(image)
            except Exception:
                h = ""
            if h and h != self.last_image_hash:
                self.last_image_hash = h
                await self._broadcast_image(image)
            return

        # Text path
        try:
            current = pyperclip.paste() or ""
        except Exception:
            current = ""
        if current and current != self.last_text:
            self.last_text = current
            self._add_to_history(current, "pc")
            logger.debug(f"Clipboard text changed, broadcasting: {current[:50]}...")
            await self.manager.broadcast(
                {
                    "type": "clipboard_sync",
                    "content": current,
                    "from": "pc",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    async def _broadcast_image(self, image):
        """Save the image via FileManager and broadcast as file_ready."""
        from io import BytesIO

        buf = BytesIO()
        try:
            image.save(buf, format="PNG")
        except Exception as e:
            logger.warning(f"image clipboard save failed: {e}")
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        meta = self.file_manager.save_bytes(
            f"clipboard-{ts}.png", buf.getvalue(), from_device="pc-clipboard"
        )
        await self.manager.broadcast(
            {
                "type": "file_ready",
                "filename": meta["filename"],
                "size": meta["size"],
                "url": meta["url"],
                "thumb_url": meta.get("thumb_url"),
                "from": "pc",
                "file_id": meta["file_id"],
                "kind": "clipboard_image",
                "timestamp": meta["uploaded_at"],
            }
        )
        logger.info(f"Broadcast clipboard image as file {meta['file_id']}")

    # ---------- public surface ----------

    async def set_clipboard(self, content: str):
        """Set host clipboard from a remote device. Suppresses one self-event."""
        self.ignore_next = True
        try:
            pyperclip.copy(content)
            self.last_text = content
            self._add_to_history(content, "phone")
            logger.debug(f"Clipboard set from remote: {content[:50]}...")
        except Exception as e:
            logger.error(f"Failed to set clipboard: {e}")
            self.ignore_next = False

    def get_current(self) -> str:
        try:
            return pyperclip.paste() or ""
        except Exception:
            return self.last_text

    def get_history(self) -> list[dict]:
        return self.db.get_clipboard_history(limit=CLIPBOARD_HISTORY_SIZE)

    def clear_history(self):
        self.db.clear_clipboard()

    def _add_to_history(self, content: str, source: str):
        try:
            self.db.add_clipboard(content, source, CLIPBOARD_HISTORY_SIZE)
        except Exception as e:
            logger.warning(f"history persist failed: {e}")

    def stop(self):
        self.running = False
