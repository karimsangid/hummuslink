"""Tests for the clipboard monitor."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestClipboardMonitor(unittest.TestCase):
    """Test clipboard monitoring and echo prevention."""

    def setUp(self):
        from server.clipboard import ClipboardMonitor

        self.manager = MagicMock()
        self.manager.broadcast = AsyncMock()
        self.monitor = ClipboardMonitor(self.manager)

    def test_initial_state(self):
        """Monitor starts with empty state."""
        self.assertEqual(self.monitor.last_content, "")
        self.assertFalse(self.monitor.running)
        self.assertFalse(self.monitor.ignore_next)
        self.assertEqual(self.monitor.history, [])

    def test_set_clipboard_sets_ignore_flag(self):
        """Setting clipboard from remote should set ignore_next to prevent echo."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("server.clipboard.pyperclip") as mock_pyperclip:
                loop.run_until_complete(self.monitor.set_clipboard("hello from phone"))
                self.assertTrue(self.monitor.ignore_next)
                mock_pyperclip.copy.assert_called_once_with("hello from phone")
                self.assertEqual(self.monitor.last_content, "hello from phone")
        finally:
            loop.close()

    def test_set_clipboard_adds_to_history(self):
        """Setting clipboard should add entry to history."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("server.clipboard.pyperclip"):
                loop.run_until_complete(self.monitor.set_clipboard("test content"))
                self.assertEqual(len(self.monitor.history), 1)
                self.assertEqual(self.monitor.history[0]["content"], "test content")
                self.assertEqual(self.monitor.history[0]["source"], "phone")
        finally:
            loop.close()

    def test_set_clipboard_error_resets_ignore(self):
        """If setting clipboard fails, ignore_next should be reset."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("server.clipboard.pyperclip") as mock_pyperclip:
                mock_pyperclip.copy.side_effect = Exception("clipboard error")
                loop.run_until_complete(self.monitor.set_clipboard("fail"))
                self.assertFalse(self.monitor.ignore_next)
        finally:
            loop.close()

    def test_history_limit(self):
        """History should be limited to CLIPBOARD_HISTORY_SIZE entries."""
        for i in range(25):
            self.monitor._add_to_history(f"item {i}", "pc")
        self.assertLessEqual(len(self.monitor.history), 20)
        self.assertEqual(self.monitor.history[0]["content"], "item 24")

    def test_get_current(self):
        """get_current should call pyperclip.paste."""
        with patch("server.clipboard.pyperclip") as mock_pyperclip:
            mock_pyperclip.paste.return_value = "current text"
            result = self.monitor.get_current()
            self.assertEqual(result, "current text")

    def test_get_current_fallback(self):
        """get_current should fall back to last_content on error."""
        self.monitor.last_content = "fallback"
        with patch("server.clipboard.pyperclip") as mock_pyperclip:
            mock_pyperclip.paste.side_effect = Exception("fail")
            result = self.monitor.get_current()
            self.assertEqual(result, "fallback")

    def test_stop(self):
        """stop() should set running to False."""
        self.monitor.running = True
        self.monitor.stop()
        self.assertFalse(self.monitor.running)


if __name__ == "__main__":
    unittest.main()
