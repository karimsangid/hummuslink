"""QR code based pairing for HummusLink.

Single shared secret (persisted under STORAGE_DIR/.shared_secret on first run, or
overridden via env HUMMUSLINK_SHARED_SECRET). The secret is embedded in the
pairing URL; clients must echo it back on websocket connect.
"""

import base64
import hmac
import io
import logging

import qrcode

from config import SHARED_SECRET

logger = logging.getLogger(__name__)


class PairingManager:
    """Generates pairing URLs/QR codes and validates the shared secret."""

    def __init__(self, host: str, port: int, secret: str = SHARED_SECRET):
        self.host = host
        self.port = port
        self.secret = secret

    def generate_pairing_qr(self) -> str:
        """Return a base64 PNG QR encoding the pairing URL."""
        url = self.get_pairing_url()
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0c0a07", back_color="#f5ead6")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        logger.info(f"Generated pairing QR for: {url[:60]}...")
        return b64

    def get_pairing_url(self) -> str:
        """Return the pairing URL with the shared secret as query param."""
        return f"http://{self.host}:{self.port}?token={self.secret}"

    def validate_token(self, token: str | None) -> bool:
        """Constant-time compare against the shared secret."""
        if not token:
            return False
        try:
            return hmac.compare_digest(token, self.secret)
        except Exception:
            return False
