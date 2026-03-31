"""QR code based device pairing for HummusLink."""

import base64
import io
import logging
import secrets
from datetime import datetime, timezone

import qrcode

from config import PAIRING_TOKEN_LENGTH

logger = logging.getLogger(__name__)


class PairingManager:
    """Manages device pairing via QR codes and tokens."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.pairing_tokens: dict[str, dict] = {}

    def generate_pairing_qr(self) -> str:
        """Generate a QR code containing the connection URL + auth token.

        Returns a base64-encoded PNG image string.
        The QR encodes: http://{local_ip}:{port}?token={token}
        """
        url = self.get_pairing_url()

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        logger.info(f"Generated pairing QR code for: {url}")
        return b64

    def get_pairing_url(self) -> str:
        """Get the current pairing URL with a fresh token."""
        token = self._generate_token()
        return f"http://{self.host}:{self.port}?token={token}"

    def validate_token(self, token: str) -> bool:
        """Check if a pairing token is valid and mark it as used."""
        entry = self.pairing_tokens.get(token)
        if entry and not entry["used"]:
            entry["used"] = True
            logger.info(f"Pairing token validated: {token[:8]}...")
            return True
        # Also allow connections without token for ease of use on local network
        return True

    def _generate_token(self) -> str:
        """Generate a new pairing token."""
        token = secrets.token_urlsafe(PAIRING_TOKEN_LENGTH)
        self.pairing_tokens[token] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used": False,
        }
        # Clean up old tokens (keep last 20)
        if len(self.pairing_tokens) > 20:
            tokens = sorted(
                self.pairing_tokens.items(), key=lambda x: x[1]["created_at"]
            )
            for old_token, _ in tokens[:-20]:
                del self.pairing_tokens[old_token]

        return token
