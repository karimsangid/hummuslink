"""Floating Tk window for drag-and-drop file uploads from the PC desktop.

Opened from the tray menu. Drop files (or click to browse) → uploaded via
FileManager and broadcast as file_ready.
"""

import asyncio
import logging
import shutil
import threading
from pathlib import Path

from config import BRAND_BG, BRAND_BORDER, BRAND_CARD, BRAND_CREAM, BRAND_GOLD

logger = logging.getLogger(__name__)


def open_drop_window(file_manager, manager, loop: asyncio.AbstractEventLoop):
    """Open a small drop-zone window. Runs in its own thread, owns its own Tk root."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        logger.warning("tkinter unavailable; drop window disabled")
        return

    # tkinterdnd2 is optional — without it we still show a click-to-browse window.
    dnd_ok = False
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

        root = TkinterDnD.Tk()
        dnd_ok = True
    except ImportError:
        root = tk.Tk()

    root.title("HummusLink — Drop Files")
    root.geometry("420x260+200+200")
    root.configure(bg=BRAND_BG)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    container = tk.Frame(root, bg=BRAND_BG, padx=20, pady=20)
    container.pack(fill="both", expand=True)

    title = tk.Label(
        container,
        text="HummusLink",
        bg=BRAND_BG,
        fg=BRAND_GOLD,
        font=("Segoe UI", 16, "bold"),
    )
    title.pack(anchor="w")

    drop_zone = tk.Label(
        container,
        text=(
            "Drop files here\nor click to browse"
            if dnd_ok
            else "Click to browse files\n(install tkinterdnd2 for drag-and-drop)"
        ),
        bg=BRAND_CARD,
        fg=BRAND_CREAM,
        font=("Segoe UI", 12),
        relief="flat",
        bd=2,
        highlightthickness=2,
        highlightbackground=BRAND_BORDER,
        cursor="hand2",
    )
    drop_zone.pack(fill="both", expand=True, pady=(12, 8))

    status = tk.Label(
        container,
        text="Ready.",
        bg=BRAND_BG,
        fg=BRAND_CREAM,
        font=("Segoe UI", 9),
    )
    status.pack(anchor="w")

    def upload_path(p: Path):
        # Stream-copy on disk — never load the whole file into RAM
        # (a dropped 2GB video used to allocate 2GB here).
        try:
            file_id, target, safe_filename = file_manager.begin_save(
                p.name, from_device="pc-drop"
            )
            status.config(text=f"Copying: {p.name}...")
            status.update_idletasks()
            shutil.copyfile(p, target)
        except Exception as e:
            status.config(text=f"Copy failed: {p.name} ({e})")
            return

        async def finish():
            meta = await file_manager.commit_save(
                file_id, target, safe_filename, "pc-drop"
            )
            await manager.broadcast(
                {
                    "type": "file_ready",
                    "filename": meta["filename"],
                    "size": meta["size"],
                    "url": meta["url"],
                    "thumb_url": meta.get("thumb_url"),
                    "from": "pc",
                    "file_id": meta["file_id"],
                    "timestamp": meta["uploaded_at"],
                }
            )

        # Commit + broadcast on the asyncio loop.
        asyncio.run_coroutine_threadsafe(finish(), loop)
        status.config(text=f"Uploaded: {p.name}")

    def on_click(_event=None):
        paths = filedialog.askopenfilenames(parent=root, title="Pick files to send")
        for p in paths:
            upload_path(Path(p))

    drop_zone.bind("<Button-1>", on_click)

    if dnd_ok:
        from tkinterdnd2 import DND_FILES  # type: ignore

        def on_drop(event):
            # event.data is a TCL-style list; tkinterdnd2 provides splitlist
            try:
                paths = root.tk.splitlist(event.data)
            except Exception:
                paths = [event.data]
            for p in paths:
                upload_path(Path(p))

        drop_zone.drop_target_register(DND_FILES)
        drop_zone.dnd_bind("<<Drop>>", on_drop)

    root.mainloop()
