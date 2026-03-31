"""WebSocket connection manager and message routing for HummusLink."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from config import HEARTBEAT_INTERVAL

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and message routing between devices."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.paired_devices: dict[str, dict] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    async def connect(
        self, websocket: WebSocket, device_id: str, device_name: str, device_type: str
    ):
        """Accept a new WebSocket connection and register the device."""
        await websocket.accept()
        self.active_connections[device_id] = websocket
        self.paired_devices[device_id] = {
            "name": device_name,
            "type": device_type,
            "paired_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"Device connected: {device_name} ({device_id}) [{device_type}]")

        # Start heartbeat for this connection
        self._heartbeat_tasks[device_id] = asyncio.create_task(
            self._heartbeat(device_id)
        )

        # Notify all other devices
        await self.broadcast(
            {
                "type": "device_connected",
                "device_id": device_id,
                "device_name": device_name,
                "device_type": device_type,
            },
            exclude=device_id,
        )

    async def disconnect(self, device_id: str):
        """Remove a device connection."""
        if device_id in self._heartbeat_tasks:
            self._heartbeat_tasks[device_id].cancel()
            del self._heartbeat_tasks[device_id]

        self.active_connections.pop(device_id, None)
        device_info = self.paired_devices.pop(device_id, {})
        logger.info(
            f"Device disconnected: {device_info.get('name', 'unknown')} ({device_id})"
        )

        await self.broadcast(
            {"type": "device_disconnected", "device_id": device_id},
            exclude=device_id,
        )

    async def broadcast(self, message: dict, exclude: str | None = None):
        """Send a message to all connected devices, optionally excluding one."""
        payload = json.dumps(message)
        disconnected = []
        for device_id, ws in self.active_connections.items():
            if device_id == exclude:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(device_id)

        for device_id in disconnected:
            await self.disconnect(device_id)

    async def send_to_device(self, device_id: str, message: dict):
        """Send a message to a specific device."""
        ws = self.active_connections.get(device_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                await self.disconnect(device_id)

    async def _heartbeat(self, device_id: str):
        """Send periodic pings to keep the connection alive."""
        try:
            while device_id in self.active_connections:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                ws = self.active_connections.get(device_id)
                if ws:
                    try:
                        await ws.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        await self.disconnect(device_id)
                        break
        except asyncio.CancelledError:
            pass

    def get_connected_devices(self) -> list[dict]:
        """Return a list of currently connected devices."""
        devices = []
        for device_id, info in self.paired_devices.items():
            if device_id in self.active_connections:
                devices.append(
                    {
                        "device_id": device_id,
                        "name": info["name"],
                        "type": info["type"],
                        "paired_at": info["paired_at"],
                    }
                )
        return devices

    @property
    def device_count(self) -> int:
        return len(self.active_connections)


async def websocket_endpoint(
    websocket: WebSocket,
    device_id: str,
    manager: ConnectionManager,
    clipboard_monitor,
    file_manager,
):
    """Handle a WebSocket connection for a device."""
    device_name = websocket.query_params.get("device_name", "Unknown")
    device_type = websocket.query_params.get("device_type", "phone")

    await manager.connect(websocket, device_id, device_name, device_type)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "pong":
                # Heartbeat response, ignore
                continue

            elif msg_type == "ping":
                await manager.send_to_device(device_id, {"type": "pong"})

            elif msg_type == "clipboard_sync":
                content = data.get("content", "")
                if content and clipboard_monitor:
                    await clipboard_monitor.set_clipboard(content)
                await manager.broadcast(
                    {
                        "type": "clipboard_sync",
                        "content": content,
                        "from": device_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    exclude=device_id,
                )

            elif msg_type == "text_share":
                await manager.broadcast(
                    {
                        "type": "text_share",
                        "content": data.get("content", ""),
                        "from": device_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    exclude=device_id,
                )

            elif msg_type == "file_ready":
                await manager.broadcast(
                    {
                        "type": "file_ready",
                        "filename": data.get("filename", ""),
                        "size": data.get("size", 0),
                        "url": data.get("url", ""),
                        "from": device_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    exclude=device_id,
                )

            elif msg_type == "notification":
                await manager.broadcast(
                    {
                        "type": "notification",
                        "title": data.get("title", ""),
                        "body": data.get("body", ""),
                        "from": device_id,
                    },
                    exclude=device_id,
                )

    except WebSocketDisconnect:
        await manager.disconnect(device_id)
    except Exception as e:
        logger.error(f"WebSocket error for {device_id}: {e}")
        await manager.disconnect(device_id)
