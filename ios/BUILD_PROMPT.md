# HummusLink iOS — Build Prompt (Mac session, Xcode required)

This file is a self-contained build brief. Copy the section under **PROMPT** into a fresh coding session on the Mac (where Xcode is installed) and work through it.

The PC-side server is already shipped at `https://github.com/karimsangid/...` (private) and runs on Karim's PC at `100.89.111.87:8765` over Tailscale. It exposes a REST endpoint `POST /api/share-target` (multipart: `title`, `text`, `url`, `files`) that the iOS app's Share Extension targets directly. There is no need to reverse-engineer iMessage or run an iCloud bridge — the iOS app's only job is to be a polite Share Sheet target and a thin native client for the existing PWA features.

---

## PROMPT (copy below this line)

You are picking up the **HummusLink iOS** sub-project for Karim Sangid (Hummus Development LLC). HummusLink is a cross-platform sync bridge between Windows 11 and iPhone — the PC-side Python server already runs at `http://100.89.111.87:8765` over Tailscale and exposes a working REST/WebSocket API. Your job is to scaffold a native SwiftUI iOS app + a **Share Extension** so that any iOS app's Share Sheet (Messages, Safari, Photos, X, Mail) can hand content off to HummusLink in one tap.

### Repo layout

The project lives at `~/Projects/hummuslink/` (clone from `git@github.com:karimsangid/hummuslink.git` if missing). Create the iOS project under `~/Projects/hummuslink/ios/HummusLink.xcodeproj`. Match the pattern of Karim's other iOS apps (Hummus Pulse iOS, HummusTravel iOS) — those project memories describe their structure.

### Targets

1. **HummusLink** (iOS app, SwiftUI) — host app. Minimal UI:
   - Settings screen: paste Tailscale base URL (default `http://100.89.111.87:8765`) + paste shared secret token. Both stored in App Group UserDefaults (`group.com.hummusdev.hummuslink`) so the Share Extension can read them.
   - Recent transfers list (calls `GET /api/files`, renders thumbs from `thumb_url`).
   - Pair screen: opens an in-app `WKWebView` to `<base>/api/qr` so the user can re-scan to refresh the token.

2. **HummusLinkShare** (Share Extension target) — the actual unlock:
   - Reads `extensionContext.inputItems` for `NSURL`, `NSString`, `UIImage`, `NSItemProvider` of UTType.image / .movie / .fileURL.
   - Multipart-POSTs to `<base>/api/share-target` with fields `title` / `text` / `url` and any files attached as `files`.
   - Token goes in the `Authorization: Bearer <token>` header **and** as the `?token=` query param (server already accepts the latter; add a small Authorization header parse on the server side if you want belt-and-suspenders auth).
   - On success: 1.5-second confirmation toast then `extensionContext.completeRequest(returningItems: nil)`.

### Brand palette (Hummus Development LLC)

```swift
extension Color {
    static let hummusBg = Color(hex: "#0c0a07")      // warm-dark
    static let hummusGold = Color(hex: "#d9a14a")    // heritage gold
    static let hummusCream = Color(hex: "#f5ead6")   // cream
    static let hummusCardBg = Color(hex: "#1a140e")
    static let hummusBorder = Color(hex: "#2a2218")
    static let hummusCreamDim = Color(hex: "#bdb098")
}
```

App icon: warm-dark background (`#0c0a07`), heritage gold (`#d9a14a`) "H" centered. Generate at `1024x1024` and let Xcode slice.

### App Group

- Group identifier: `group.com.hummusdev.hummuslink`
- Both targets sign with the same Apple ID + group entitlement.
- Stored keys:
  - `baseURL` (String) — e.g. `http://100.89.111.87:8765`
  - `sharedSecret` (String) — captured from QR or pasted manually
  - `deviceId` (String) — randomly generated UUID, persisted

### Networking helper (shared between targets)

Create `Sources/HummusLinkCore/Client.swift`:

```swift
struct HLClient {
    let baseURL: URL
    let token: String
    let deviceId: String

    func postShareTarget(title: String?, text: String?, url: String?,
                         files: [(filename: String, data: Data, mime: String)]) async throws { ... }
    func listFiles(limit: Int = 50) async throws -> [HLFile]
    func thumbURL(fileId: String) -> URL { baseURL.appending(path: "/api/files/\(fileId)/thumb") }
}
```

Use `URLSession` with a `multipart/form-data` body builder. Default to `URLRequest(url:).timeoutInterval = 30`.

### Share Extension info.plist constraints

Restrict to the activations that make sense:

```
NSExtensionActivationRule:
    NSExtensionActivationSupportsImageWithMaxCount: 10
    NSExtensionActivationSupportsMovieWithMaxCount: 5
    NSExtensionActivationSupportsFileWithMaxCount: 10
    NSExtensionActivationSupportsText: true
    NSExtensionActivationSupportsWebURLWithMaxCount: 1
    NSExtensionActivationSupportsAttachmentsWithMaxCount: 10
```

### Acceptance test (run on real iPhone, not simulator — no Tailscale on sim)

1. From Messages: long-press a message → Share → HummusLink → confirm "Sent" toast → open `http://100.89.111.87:8765` on PC → see the text in the Activity feed.
2. From Photos: pick a HEIC photo → Share → HummusLink → server converts to JPEG → thumb appears in /api/files.
3. From Safari: Share a URL → HummusLink → URL appears in PC dashboard as text_share.
4. With the host app: tap a recent file's thumb → opens the JPEG full-screen.
5. Force-quit the host app, run the Share Extension cold — token + base URL must persist via App Group.

### Distribution

- Sign with Karim's free Apple ID (7-day cert, matches his Hummus Pulse iOS / HummusTravel iOS setup).
- Document the re-sign cadence in `ios/README.md`.
- DO NOT ship to App Store; this is a personal-use sideloaded app.

### Out of scope (for this session)

- iMessage protocol reverse-engineering. Don't touch IDS / APNs / pypush. The Share Extension is the only iMessage-content path — user invokes it manually via Share Sheet on the message bubble.
- Beeper / AirMessage style live mirroring.
- Any cloud relay. Tailscale + the PC-side server is the entire network model.

### Server endpoints to read first

Before writing the client, read these on the PC server (already deployed):

- `POST /api/share-target` — `multipart/form-data` with optional `title`, `text`, `url`, and 0..N `files` parts.
- `GET /api/files?limit=50` — JSON array of `{file_id, filename, size, mime, uploaded_at, from_device, url, thumb_url}`.
- `GET /api/files/{id}/thumb` — 256x256 JPEG.
- `WS /ws/{device_id}?token=<secret>&device_name=...&device_type=phone` — only needed if you want live updates in the host app; the Share Extension itself doesn't need WS.
- The shared secret is at `~/HummusLink/.shared_secret` on the PC, or echoed in the QR at `/api/qr` after the `?token=` param.

### Done when

- Both targets build clean for iOS 17 / Xcode 16+.
- Real-device sideload works.
- Acceptance tests 1–5 pass.
- App Group entitlement verified by force-quit test.
- `ios/README.md` documents Apple ID, App Group ID, signing cadence, and how to refresh the token.

Hand back: list of created files + paths + a one-paragraph "what's next" (e.g., live-updating recent transfers via WebSocket, push notifications via APNs).
