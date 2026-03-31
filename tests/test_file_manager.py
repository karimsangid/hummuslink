"""Tests for the file manager."""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path


class TestFileManager(unittest.TestCase):
    """Test file storage, retrieval, and cleanup."""

    def setUp(self):
        from server.file_manager import FileManager

        self.tmp_dir = Path(tempfile.mkdtemp())
        self.fm = FileManager(self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_save_file(self):
        """Saving a file should return metadata and create the file on disk."""
        meta = self._run(self.fm.save_file("test.txt", b"hello world", "phone1"))
        self.assertIn("file_id", meta)
        self.assertEqual(meta["filename"], "test.txt")
        self.assertEqual(meta["size"], 11)
        self.assertEqual(meta["from_device"], "phone1")
        self.assertTrue(meta["url"].startswith("/api/files/"))

        # File should exist on disk
        path = self.fm.get_file_path(meta["file_id"])
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b"hello world")

    def test_list_files(self):
        """list_files should return saved files sorted by time."""
        self._run(self.fm.save_file("a.txt", b"aaa", "d1"))
        self._run(self.fm.save_file("b.txt", b"bbb", "d2"))
        files = self.fm.list_files()
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["filename"], "b.txt")

    def test_delete_file(self):
        """Deleting a file should remove it from disk and metadata."""
        meta = self._run(self.fm.save_file("del.txt", b"delete me", "d1"))
        file_id = meta["file_id"]
        path = self.fm.get_file_path(file_id)
        self.assertTrue(path.exists())

        result = self.fm.delete_file(file_id)
        self.assertTrue(result)
        self.assertIsNone(self.fm.get_file_path(file_id))
        self.assertFalse(path.exists())

    def test_delete_nonexistent(self):
        """Deleting a nonexistent file should return False."""
        self.assertFalse(self.fm.delete_file("nonexistent"))

    def test_sanitize_filename(self):
        """Filenames should be sanitized."""
        from server.file_manager import FileManager

        self.assertEqual(FileManager._sanitize_filename("hello.txt"), "hello.txt")
        self.assertEqual(FileManager._sanitize_filename("../etc/passwd"), "file_..._etc_passwd")
        self.assertEqual(FileManager._sanitize_filename(".hidden"), "file_.hidden")
        self.assertIn("_", FileManager._sanitize_filename("path/to/file.txt"))

    def test_get_storage_usage(self):
        """Storage usage should reflect saved files."""
        self._run(self.fm.save_file("a.txt", b"x" * 1000, "d1"))
        usage = self.fm.get_storage_usage()
        self.assertEqual(usage["file_count"], 1)
        self.assertGreaterEqual(usage["total_bytes"], 1000)

    def test_list_files_limit(self):
        """list_files should respect the limit parameter."""
        for i in range(10):
            self._run(self.fm.save_file(f"f{i}.txt", b"data", "d1"))
        files = self.fm.list_files(limit=3)
        self.assertEqual(len(files), 3)

    def test_cleanup_old_files(self):
        """cleanup_old_files removes files older than max_age."""
        meta = self._run(self.fm.save_file("old.txt", b"old", "d1"))
        # Manually backdate the uploaded_at
        from datetime import datetime, timedelta, timezone

        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        self.fm.files[meta["file_id"]]["uploaded_at"] = old_time

        self.fm.cleanup_old_files(max_age_hours=24)
        self.assertEqual(len(self.fm.files), 0)


if __name__ == "__main__":
    unittest.main()
