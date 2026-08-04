"""Phone push notifications via notif-hub (self-hosted on :8090).

Same channel chickpea-check uses, so pushes land on Karim's phone through
the existing notif-hub subscription. Fire-and-forget: a dead notif-hub must
never slow down or fail a transfer.
"""

import asyncio
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

NOTIF_HUB_BASE = "http://127.0.0.1:8090"
PROJECT_SLUG = "hummuslink"

# Devices whose events should NOT push to the phone — they originate there.
_PHONE_ORIGIN_PREFIXES = ("phone", "ios-shortcut", "share-target")

# Set by main.py to TrayApp.notify — fires a native Windows toast.
pc_notify_hook = None


def is_phone_origin(from_device: str | None) -> bool:
    return bool(from_device) and from_device.startswith(_PHONE_ORIGIN_PREFIXES)


def _post(title: str, body: str) -> None:
    payload = {
        "project": PROJECT_SLUG,
        "title": title,
        "body": body,
        "priority": "normal",
    }
    req = urllib.request.Request(
        f"{NOTIF_HUB_BASE}/notify",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if not (200 <= resp.status < 300):
                logger.warning(f"notif-hub push HTTP {resp.status} for '{title}'")
    except Exception as e:
        logger.warning(f"notif-hub push failed for '{title}': {e}")


def push(title: str, body: str = "") -> None:
    """Schedule a push without blocking the caller. Safe from async context."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_post, title, body))
    except RuntimeError:
        # No running loop (sync context, e.g. drop window thread)
        import threading

        threading.Thread(target=_post, args=(title, body), daemon=True).start()


def _fmt_size(size: int) -> str:
    mb = size / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{size // 1024} KB"


def _toast(title: str, body: str) -> None:
    """Native Windows toast via the tray icon. Best-effort."""
    hook = pc_notify_hook
    if hook is None:
        return
    try:
        hook(title, body)
    except Exception as e:
        logger.warning(f"PC toast failed for '{title}': {e}")


def push_file(filename: str, size: int, from_device: str | None) -> None:
    """Notify the receiving side: phone-origin files toast the PC,
    everything else pushes to the phone via notif-hub."""
    if is_phone_origin(from_device):
        _toast("HummusLink", f"File from phone: {filename} ({_fmt_size(size)})")
        return
    push(f"File ready: {filename}", f"{_fmt_size(size)} from {from_device or 'unknown'}")


def push_text(content: str, from_device: str | None) -> None:
    preview = content if len(content) <= 120 else content[:117] + "..."
    if is_phone_origin(from_device):
        _toast("HummusLink", f"Text from phone: {preview}")
        return
    push("Text from PC", preview)
