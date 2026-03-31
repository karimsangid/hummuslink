# HummusLink

A cross-platform sync bridge between Windows 11 and iPhone. File transfer, clipboard sync, text/link sharing, and photo transfer -- like Apple's ecosystem but for Windows + iPhone.

Built by **Hummus Development LLC** (Karim Sangid).

## Features

- **Clipboard Sync** -- Copy on your PC, paste on your phone (and vice versa). Automatic, real-time.
- **File Transfer** -- Upload files or photos from your iPhone to your PC, and download files from your PC.
- **Text/Link Sharing** -- Send any text or URL between devices instantly.
- **Photo Capture** -- Take a photo on your iPhone and send it straight to your PC.
- **System Tray** -- Runs quietly in the Windows system tray with quick access to the dashboard and QR pairing.
- **QR Code Pairing** -- Scan a QR code with your iPhone to connect instantly.
- **PWA** -- Install on your iPhone home screen for a native app experience.
- **Local Only** -- Everything stays on your local WiFi network. No cloud, no accounts, no tracking.

## How It Works

```
[iPhone Safari PWA] <--WebSocket (local WiFi)--> [Windows Python Server + System Tray]
```

Both devices are on the same WiFi network. The PC runs a Python server (FastAPI + WebSocket) with a system tray icon. The iPhone connects via a Progressive Web App served directly by the PC.

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
python main.py
```

The server will start and display your local IP address. Then on your iPhone:

1. Open **Safari** (must be Safari for PWA support)
2. Navigate to `http://<your-pc-ip>:8765`
3. Tap the **Share** button, then **Add to Home Screen**
4. Open HummusLink from your home screen

You can also scan the QR code displayed at `http://<your-pc-ip>:8765/api/qr`.

## Tech Stack

- **Server**: Python 3.10+, FastAPI, uvicorn, WebSockets
- **System Tray**: pystray + Pillow
- **Clipboard**: pyperclip
- **Discovery**: zeroconf (mDNS)
- **Pairing**: qrcode
- **Frontend**: Vanilla HTML/CSS/JavaScript (no build step, no npm)
- **PWA**: Service Worker, Web App Manifest, iOS meta tags

## Project Structure

```
hummuslink/
├── main.py                  # Entry point
├── config.py                # Configuration
├── server/
│   ├── app.py               # FastAPI app setup
│   ├── routes.py            # REST API endpoints
│   ├── websocket_handler.py # WebSocket connection manager
│   ├── clipboard.py         # Clipboard monitoring
│   ├── file_manager.py      # File storage
│   ├── discovery.py         # mDNS service registration
│   ├── pairing.py           # QR code pairing
│   └── tray.py              # System tray icon
├── frontend/
│   ├── index.html           # PWA shell
│   ├── manifest.json        # PWA manifest
│   ├── sw.js                # Service worker
│   ├── app.js               # Application JavaScript
│   └── styles.css           # Styles
└── tests/
    ├── test_clipboard.py
    ├── test_file_manager.py
    └── test_pairing.py
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | PWA frontend |
| GET | `/api/status` | Server status |
| GET | `/api/qr` | Pairing QR code page |
| GET | `/api/pairing-url` | Connection URL as JSON |
| POST | `/api/files/upload` | Upload a file |
| GET | `/api/files/{id}` | Download a file |
| GET | `/api/files` | List recent files |
| DELETE | `/api/files/{id}` | Delete a file |
| GET | `/api/devices` | Connected devices |
| GET | `/api/clipboard` | Current clipboard + history |
| WS | `/ws/{device_id}` | WebSocket connection |

## License

MIT License

---

*By Hummus Development LLC*
