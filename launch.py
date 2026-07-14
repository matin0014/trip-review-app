"""Entrypoint: start the local Flask server on a background thread and show it
in a native desktop window (pywebview), not a system browser tab.

Binds 127.0.0.1 only (data stays on the reviewer's machine) and picks a free
port so multiple copies don't collide. This is what the PyInstaller exe will run.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time

import webview

from app import generate
from app.server import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """Exposed to the page as ``window.pywebview.api`` (see session.html's JS).

    The embedded WebView2 window has NO built-in download handling (unlike a
    real browser) -- a plain link/navigation to a file-attachment response
    silently does nothing, which is why "Generate export" stopped working
    after the switch away from the system browser. The fix is to generate the
    file server-side as before, then hand it to the user via a native Save As
    dialog and write the bytes ourselves, instead of relying on any browser
    download mechanism."""

    def save_export(self, sid: str) -> dict:
        try:
            zip_path = generate.generate_bundle(sid)
        except Exception as exc:  # surface any generation failure to the page, not a silent no-op
            return {"ok": False, "error": str(exc)}

        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, save_filename="trip_review_export.zip",
            file_types=("Zip files (*.zip)",))
        if not result:
            return {"ok": False, "cancelled": True}

        dest = result[0] if isinstance(result, (list, tuple)) else result
        shutil.copyfile(zip_path, dest)
        return {"ok": True, "path": dest}


def _wait_until_listening(port: int, timeout: float = 10.0) -> None:
    """Block until something accepts a connection on 127.0.0.1:port (the Flask
    server has finished starting on its background thread), so the desktop
    window never loads before the server is ready. Gives up silently after
    `timeout` seconds -- the window would just show a connection error."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)


def main() -> None:
    app = create_app()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    # Flask's dev server blocks in app.run(), so it runs on a background
    # thread; the native window owns the main thread instead (webview.start()
    # must be called from the main thread on Windows). daemon=True: the server
    # thread dies with the process when the window closes, no separate
    # shutdown needed for a single-user local tool.
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False),
        daemon=True,
    ).start()
    _wait_until_listening(port)

    print(f"Trip Review Tool running -> {url}")
    webview.create_window("Trip Review Tool", url, width=1400, height=900, min_size=(900, 600),
                          js_api=Api())
    webview.start()


if __name__ == "__main__":
    main()
