# HummusLink

A cross-platform sync bridge between Windows 11 and iPhone — file transfer, clipboard sync (text + images), text/link sharing, and OS share-sheet integration. Like Apple's ecosystem features, but for Windows + iPhone, with no cloud and no third-party servers.

Built by **Hummus Development LLC** (Karim Sangid).

## Features

- **Clipboard sync** — copy on PC → paste on phone (and vice versa). Event-driven on Windows (zero CPU when idle); falls back to polling elsewhere. Image clipboard supported on PC → broadcast as files.
- **File transfer** — upload files or photos from iPhone to PC (streaming, up to 500 MB), download files from PC. HEIC photos auto-convert to JPEG. Image thumbnails generated lazily.
- **Text/link sharing** — send any text or URL between devices instantly.
- **Web Share Target** — Android PWA + future native iOS Share Extension can post to `/api/share-target` so the OS share sheet routes content into HummusLink.
- **System tray** — quiet Windows tray icon: dashboard, QR pairing, and a drag-and-drop floating window for desktop file transfers.
- **QR pairing** — scan once with iPhone camera; URL embeds a persisted shared secret which the websocket validates on every connect.
- **PWA** — installable on iPhone home screen, looks native.
- **Persistent state** — clipboard history, file metadata, and devices survive Scheduled Task restarts (SQLite at `~/HummusLink/hummuslink.db`).

## Network model

HummusLink is **Tailscale-first**, not "same WiFi only". The PC binds `0.0.0.0:8765` and is reached by your iPhone (also on your tailnet) via the PC's Tailscale IP — currently `100.89.111.87:8765`. Same WiFi works as a fallback.

```
[iPhone Safari PWA]  <-- WebSocket / HTTPS over Tailscale -->  [Windows server + tray]
                                                               port 8765
```

Why Tailscale: stable IP across networks, no port forwarding, no exposing the server to the public internet, end-to-end encryption between your devices.

## Quickstart

```bash
pip install -r requirements.txt
python main.py
```

The server prints its Tailscale IP (or LAN IP if you're not on a tailnet) and the QR pairing URL. On your iPhone:

1. Open Safari
2. Open `http://100.89.111.87:8765/api/qr` and scan/tap the QR
3. Tap **Share → Add to Home Screen**

The QR URL embeds the shared secret as `?token=...`. The PWA captures it once on first load and reuses it for every reconnect.

## Pairing secret

On first run a 32-byte URL-safe secret is generated and stored at `~/HummusLink/.shared_secret` (mode 0600). All websocket connections must echo it back as `?token=...`. Override with `HUMMUSLINK_SHARED_SECRET` env var if you want to set a fixed value.

## Uptime: Scheduled Task `HummusLink`

Registered as a Windows Scheduled Task (AtLogOn) that launches `run-service.bat` → `pythonw.exe main.py`. Re-register / reset:

```powershell
powershell -ExecutionPolicy Bypass -File install-task.ps1
```

Logs append to `%USERPROFILE%\.hummuslink\hummuslink.log`.

⚠️ **pythonw resolution gotcha**: the install script hardcodes `C:\Users\reach\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe`. Don't `Get-Command pythonw.exe` — that resolves to the WindowsApps shim which exits 0x1 silently with no logs.

## Tech stack

- **Server** — Python 3.10+, FastAPI, uvicorn, WebSockets, SQLite, aiofiles, Pillow + pillow-heif
- **Windows native** — pywin32 (event-driven clipboard listener), pyperclip (fallback), pystray + Pillow (tray icon)
- **Discovery** — zeroconf mDNS
- **Frontend** — Vanilla HTML/CSS/JS PWA (no build step), Service Worker, manifest with `share_target`

## API surface

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | PWA shell |
| GET | `/api/status` | Server status + connected device count |
| GET | `/api/qr` | Pairing QR page (HTML, embeds secret) |
| GET | `/api/pairing-url` | Pairing URL as JSON |
| POST | `/api/files/upload` | Streaming multipart upload |
| GET | `/api/files/{id}` | Download with proper Content-Type |
| GET | `/api/files/{id}/thumb` | 256×256 JPEG thumbnail (images only) |
| GET | `/api/files` | Recent file list |
| DELETE | `/api/files/{id}` | Delete a file |
| GET | `/api/devices` | Currently connected devices |
| GET | `/api/clipboard` | Current clipboard + history |
| POST | `/api/clipboard` | Push clipboard from device (HTTP path) |
| DELETE | `/api/clipboard` | Clear clipboard history |
| GET | `/api/storage` | Storage usage |
| GET | `/api/icon/{size}` | Brand-coloured PNG icon (16–1024 px) |
| POST | `/api/share-target` | OS Share Sheet target (multipart: `title`, `text`, `url`, `files`) |
| WS | `/ws/{device_id}?token=...` | WebSocket (token required) |

## Project layout

```
hummuslink/
├── main.py                   # Entry point + lifespan + DB wiring
├── config.py                 # Configuration + brand palette + shared-secret bootstrap
├── server/
│   ├── app.py                # FastAPI app factory
│   ├── routes.py             # REST API + share-target + thumbnails
│   ├── websocket_handler.py  # WebSocket connection manager (token-validated)
│   ├── clipboard.py          # Event-driven (Windows) clipboard monitor + image sync
│   ├── file_manager.py       # Streaming uploads, HEIC convert, thumbs, path-traversal safe
│   ├── db.py                 # SQLite persistence (clipboard / files / devices)
│   ├── discovery.py          # mDNS service registration
│   ├── pairing.py            # Shared-secret pairing + QR generation
│   ├── tray.py               # Windows system tray
│   └── drop_window.py        # Tk drag-and-drop floating window
├── frontend/
│   ├── index.html            # PWA shell
│   ├── manifest.json         # PWA manifest with share_target
│   ├── sw.js                 # Service worker
│   ├── app.js                # Application JS (token-aware, thumbs, share-target toast)
│   └── styles.css            # Hummus brand palette
└── ios/                      # (Pending) Native SwiftUI app + Share Extension
```

## License

MIT — by Hummus Development LLC.
