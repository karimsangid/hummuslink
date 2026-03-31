"""Tests for the pairing manager."""

import unittest


class TestPairingManager(unittest.TestCase):
    """Test QR code generation and token validation."""

    def setUp(self):
        from server.pairing import PairingManager

        self.pm = PairingManager("192.168.1.100", 8765)

    def test_get_pairing_url(self):
        """Pairing URL should contain the host, port, and token."""
        url = self.pm.get_pairing_url()
        self.assertIn("http://192.168.1.100:8765", url)
        self.assertIn("token=", url)

    def test_generate_pairing_qr(self):
        """QR code should be a non-empty base64 string."""
        qr = self.pm.generate_pairing_qr()
        self.assertIsInstance(qr, str)
        self.assertGreater(len(qr), 100)
        # Should be valid base64 (no whitespace issues)
        import base64
        decoded = base64.b64decode(qr)
        # Should be a PNG (starts with PNG magic bytes)
        self.assertTrue(decoded[:4] == b"\x89PNG")

    def test_validate_token(self):
        """Generated tokens should be valid."""
        url = self.pm.get_pairing_url()
        token = url.split("token=")[1]
        self.assertTrue(self.pm.validate_token(token))

    def test_token_marked_used(self):
        """After validation, the token should be marked as used."""
        url = self.pm.get_pairing_url()
        token = url.split("token=")[1]
        self.pm.validate_token(token)
        entry = self.pm.pairing_tokens.get(token)
        self.assertIsNotNone(entry)
        self.assertTrue(entry["used"])

    def test_multiple_tokens(self):
        """Multiple tokens can be generated and each has a unique value."""
        urls = [self.pm.get_pairing_url() for _ in range(5)]
        tokens = [u.split("token=")[1] for u in urls]
        # All should be unique
        self.assertEqual(len(set(tokens)), 5)

    def test_token_cleanup(self):
        """Old tokens should be cleaned up when limit is exceeded."""
        for _ in range(25):
            self.pm.get_pairing_url()
        self.assertLessEqual(len(self.pm.pairing_tokens), 20)

    def test_unknown_token_still_validates(self):
        """Unknown tokens validate (permissive for local network ease of use)."""
        self.assertTrue(self.pm.validate_token("random_unknown_token"))


if __name__ == "__main__":
    unittest.main()
