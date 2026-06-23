"""Entrypoint: start the local Flask server on a free port and open the browser.

Binds 127.0.0.1 only (data stays on the reviewer's machine) and picks a free
port so multiple copies don't collide. This is what the PyInstaller exe will run.
"""

from __future__ import annotations

import socket
import threading
import webbrowser

from app.server import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    app = create_app()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Trip Review Tool running -> {url}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
