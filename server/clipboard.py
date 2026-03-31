"""Clipboard monitoring and synchronization for HummusLink."""

import asyncio
import logging

import pyperclip

from config import CLIPBOARD_POLL_INTERVAL, CLIPBOARD_HISTORY_SIZE

logger = logging.getLogger(__name__)


class ClipboardMonitor:
    """Watches the Windows clipboard for changes and broadcasts them."""

    def __init__(self, manager):
        self.manager = manager
        self.last_content: str = ""
        self.running: bool = False
        self.ignore_next: bool = False
        self.history: list[dict] = []

    async def start(self):
        """Poll clipboard every CLIPBOARD_POLL_INTERVAL seconds.

        If the content has changed, broadcast the new content to all devices.
        """
        self.running = True
        logger.info("Clipboard monitor started")

        # Get initial clipboard content
        try:
            self.last_content = pyperclip.paste() or ""
        except Exception:
            self.last_content = ""

        while self.running:
            try:
                await asyncio.sleep(CLIPBOARD_POLL_INTERVAL)
                current = pyperclip.paste() or ""

                if current != self.last_content and current.strip():
                    if self.ignore_next:
                        self.ignore_next = False
                        self.last_content = current
                        continue

                    self.last_content = current
                    self._add_to_history(current, "pc")

                    logger.debug(
                        f"Clipboard changed, broadcasting: {current[:50]}..."
                    )
                    await self.manager.broadcast(
                        {
                            "type": "clipboard_sync",
                            "content": current,
                            "from": "pc",
                        }
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Clipboard monitor error: {e}")
                await asyncio.sleep(1)

        logger.info("Clipboard monitor stopped")

    async def set_clipboard(self, content: str):
        """Set clipboard content from a remote device.

        Sets ignore_next to prevent re-broadcasting what we just received.
        """
        self.ignore_next = True
        try:
            pyperclip.copy(content)
            self.last_content = content
            self._add_to_history(content, "phone")
            logger.debug(f"Clipboard set from remote: {content[:50]}...")
        except Exception as e:
            logger.error(f"Failed to set clipboard: {e}")
            self.ignore_next = False

    def get_current(self) -> str:
        """Get the current clipboard content."""
        try:
            return pyperclip.paste() or ""
        except Exception:
            return self.last_content

    def get_history(self) -> list[dict]:
        """Return clipboard history."""
        return list(self.history)

    def _add_to_history(self, content: str, source: str):
        """Add an entry to clipboard history."""
        from datetime import datetime, timezone

        entry = {
            "content": content,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.history.insert(0, entry)
        if len(self.history) > CLIPBOARD_HISTORY_SIZE:
            self.history = self.history[:CLIPBOARD_HISTORY_SIZE]

    def stop(self):
        """Stop the clipboard monitor."""
        self.running = False
