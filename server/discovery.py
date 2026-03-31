"""mDNS/Zeroconf service registration for local network discovery."""

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

from config import APP_NAME

logger = logging.getLogger(__name__)


class ServiceDiscovery:
    """Registers an mDNS service so the phone can discover the PC on the network."""

    def __init__(self, port: int):
        self.port = port
        self.zeroconf: Zeroconf | None = None
        self.info: ServiceInfo | None = None

    def register(self):
        """Register _hummuslink._tcp.local. service on the network."""
        local_ip = self.get_local_ip()
        logger.info(f"Local IP address: {local_ip}")

        self.info = ServiceInfo(
            "_hummuslink._tcp.local.",
            f"{APP_NAME}._hummuslink._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties={
                "version": "0.1.0",
                "name": APP_NAME,
            },
            server=f"{APP_NAME}.local.",
        )

        self.zeroconf = Zeroconf()
        self.zeroconf.register_service(self.info)
        logger.info(
            f"mDNS service registered: {APP_NAME}._hummuslink._tcp.local. on port {self.port}"
        )

    def unregister(self):
        """Unregister the service on shutdown."""
        if self.zeroconf and self.info:
            self.zeroconf.unregister_service(self.info)
            self.zeroconf.close()
            logger.info("mDNS service unregistered")

    @staticmethod
    def get_local_ip() -> str:
        """Get the local WiFi IP address of this machine.

        Uses the socket trick of connecting to an external address to determine
        which local interface would be used for outbound traffic.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            # Connect to a public DNS to determine our local IP
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback: try to get any non-loopback address
            hostname = socket.gethostname()
            try:
                return socket.gethostbyname(hostname)
            except Exception:
                return "127.0.0.1"
