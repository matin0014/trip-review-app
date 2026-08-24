"""Entrypoint: start the local Flask server on a background thread and show it
in a native desktop window (pywebview), not a system browser tab.

Binds 127.0.0.1 only (data stays on the reviewer's machine) and picks a free
port so multiple copies don't collide. This is what the PyInstaller exe will run.

Startup is wrapped in `_fatal` so a broken install reports itself. The exe is a
console app, so an unhandled exception here used to flash a window and vanish
before anyone could read it: reviewers just saw "it doesn't launch".

Two install problems account for nearly all of these, and BOTH surface as an
error naming whichever bundled library happened to load first, never the actual
cause:
  * blocked files -- Windows marks anything extracted from a zip that came from
    a shared drive as untrusted, and .NET then refuses to load its assemblies
    ("Failed to resolve ... from Python.Runtime.dll");
  * a damaged copy -- this build is ~200 MB across ~1000 files, and a copy that
    drops mid-transfer leaves silently zero-filled files, which shows up as an
    ImportError from numpy claiming you are "importing numpy from its source
    directory".
Setup.bat fixes the first and detects the second, so `_fatal` points there
rather than trying to tell the two apart from the exception alone.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback


def _fatal(exc: BaseException) -> None:
    """Report a startup failure in plain English, then hold the window open.

    Deliberately uses ONLY the standard library: the whole point is to still
    work when the bundled third-party libraries are the thing that's broken."""
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    print()
    print("=" * 70)
    print("  Trip Review Tool could not start.")
    print("=" * 70)
    print()
    print("  Almost always this is the INSTALL, not the software: either")
    print("  Windows has blocked the files (it does that to anything that came")
    print("  from a shared drive or a zip), or the copy arrived damaged.")
    print("  Both produce errors like the one above, which point at whichever")
    print("  bundled library happened to load first rather than at the cause.")
    print()
    print("  Fix it in this order:")
    print("    1. Double-click Setup.bat in this folder and let it finish.")
    print("       It unblocks the files AND checks every one of them.")
    print("    2. If it says READY, try launching again.")
    print("    3. If it reports damaged files, delete this folder, copy")
    print("       TripReview.zip again from the shared drive, extract it,")
    print("       and run Setup.bat once more.")
    print()
    print("  If Setup.bat says READY and this still happens, send the error")
    print("  text above to Matin: at that point it is not an install problem.")
    print()
    try:
        input("  Press Enter to close this window... ")
    except (EOFError, KeyboardInterrupt, OSError):
        pass  # no console attached; the traceback above still went to stderr
    sys.exit(1)


try:
    import webview

    from app import generate
    from app.server import create_app
except Exception as _exc:  # a damaged copy fails HERE, at import time
    _fatal(_exc)


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

    def save_distributions(self, sid: str) -> dict:
        """Session case, mirrors save_export exactly: generate server-side first
        (the CURRENT live-cleaned state), then hand the file to the user via the
        same native Save As dialog."""
        try:
            from app import distributions
            tmp_dir = tempfile.mkdtemp(prefix="triprev_dist_")
            out = os.path.join(tmp_dir, "distributions.zip")
            distributions.build_distributions_zip_for_session(sid, out)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, save_filename="trip_review_distributions.zip",
            file_types=("Zip files (*.zip)",))
        dest = None
        if result:
            dest = result[0] if isinstance(result, (list, tuple)) else result
            shutil.copyfile(out, dest)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": True, "path": dest} if dest else {"ok": False, "cancelled": True}

    def save_distributions_from_path(self, server_zip_path: str) -> dict:
        """Standalone case (no session), step 2: the page already POSTed the
        loaded file to /api/distributions/from_file/build and built the ZIP
        server-side, since a raw File object can't cross this JS bridge -- this
        call only does the Save As dialog for the path that step handed back.
        The temp-dir prefix check is defense against a stray call, not a real
        trust boundary (this is a local single-user app)."""
        real = os.path.realpath(server_zip_path)
        if (not real.startswith(os.path.realpath(tempfile.gettempdir()))
                or not os.path.isfile(real)):
            return {"ok": False, "error": "invalid or expired path"}

        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, save_filename="trip_review_distributions.zip",
            file_types=("Zip files (*.zip)",))
        dest = None
        if result:
            dest = result[0] if isinstance(result, (list, tuple)) else result
            shutil.copyfile(real, dest)
        shutil.rmtree(os.path.dirname(real), ignore_errors=True)  # cleanup either way
        return {"ok": True, "path": dest} if dest else {"ok": False, "cancelled": True}


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
    try:
        main()
    except Exception as exc:   # same treatment for a failure after import
        _fatal(exc)
