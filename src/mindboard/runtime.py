"""Pieces the CLI and the double-click menu both need, so there is one
implementation of "where does the data live" and "how do we serve"."""

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from .paths import DataDir

DEFAULT_PORT = 7788


def resolve_data_dir(data_dir: Optional[str] = None) -> DataDir:
    return DataDir(Path(data_dir) if data_dir else None).ensure()


def access_token(dd: DataDir) -> str:
    """A per-install key. Localhost is not a trust boundary - any process on the
    machine can reach 127.0.0.1 - so the API asks for this."""
    f = dd.token_file
    if f.exists():
        existing = f.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(token, encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return token


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def next_free_port(start: int = DEFAULT_PORT, tries: int = 20) -> Optional[int]:
    for port in range(start, start + tries):
        if port_free(port):
            return port
    return None


def source_root() -> Optional[Path]:
    """The repository, when running from a checkout rather than a wheel."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "web" / "package.json").exists() else None


def ui_built() -> bool:
    from .app import WEB_DIR

    return (WEB_DIR / "index.html").exists()


def board_url(port: int, token: str, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}/?t={token}"


def open_in_browser(url: str, delay: float = 1.0) -> None:
    threading.Thread(target=lambda: (time.sleep(delay), webbrowser.open(url)),
                     daemon=True).start()


def open_folder(path: Path) -> bool:
    """Show a folder in Explorer / Finder / the desktop file manager."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except Exception:
        return False


def quiet_disconnects() -> None:
    """Stop Windows shouting every time a browser walks away mid-request.

    A page that navigates, a video that seeks, a tab that closes - each ends a
    request the server was still writing to, and Windows reports that as
    WinError 10054. asyncio's proactor transport raises it from inside
    `_call_connection_lost`, where nobody can catch it, so it lands in the
    console as an unhandled-callback traceback. Nothing is wrong and nothing is
    lost: the socket was already closing. This swallows exactly that, on
    Windows, and leaves every other error alone.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
    except Exception:
        return
    original = _ProactorBasePipeTransport._call_connection_lost
    if getattr(original, "_mindboard_quiet", False):
        return

    def quiet(self, exc):
        try:
            original(self, exc)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    quiet._mindboard_quiet = True  # type: ignore[attr-defined]
    _ProactorBasePipeTransport._call_connection_lost = quiet  # type: ignore[method-assign]


def serve(dd: DataDir, token: str, port: int, dev: bool = False,
          host: str = "127.0.0.1") -> None:
    """Blocks until the server stops. Ctrl+C returns cleanly."""
    import uvicorn

    from .app import create_app

    quiet_disconnects()
    uvicorn.run(create_app(dd, token, dev=dev), host=host, port=port, log_level="warning")


def diagnostics(dd: DataDir, port: int = DEFAULT_PORT) -> list[dict]:
    """One list of checks, rendered by both `mindboard doctor` and the menu."""
    from .boards import conn_for, list_boards
    from .db import integrity_ok

    checks: list[dict] = [
        {"label": "python 3.10 or newer", "ok": sys.version_info >= (3, 10),
         "detail": sys.version.split()[0],
         "fix": "install a newer Python and run the installer again"},
        {"label": "storage writable", "ok": os.access(dd.root, os.W_OK),
         "detail": str(dd.root), "fix": "check folder permissions"},
        {"label": "interface built", "ok": ui_built(), "detail": "",
         "fix": "run: mindboard install"},
        {"label": f"port {port} free", "ok": port_free(port), "detail": "",
         "fix": "Mindboard may already be running"},
    ]
    for b in list_boards(dd):
        checks.append({
            "label": f"board {b['title'][:26]!r}",
            "ok": integrity_ok(conn_for(dd, b["id"])),
            "detail": f"{b['node_count']} nodes",
            "fix": "database reports corruption - restore this board from a backup",
        })
    return checks


def running_instance(port: int = DEFAULT_PORT) -> Optional[dict]:
    """Is a Mindboard already answering on this port?"""
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
        data = r.json()
        return data if r.status_code == 200 and data.get("ok") else None
    except Exception:
        return None


def backups_dir(dd: DataDir) -> Path:
    root = source_root()
    base = (root / "backups") if root else (dd.root / "backups")
    base.mkdir(parents=True, exist_ok=True)
    return base


def serve_background(dd: DataDir, token: str, port: int, dev: bool = False):
    """Run the server on a thread so the caller can stop it on a keypress.

    The menu uses this rather than blocking: pressing Ctrl+C inside a .bat file
    on Windows makes cmd ask "Terminate batch job (Y/N)?", which is a confusing
    way to close an app.
    """
    import uvicorn

    from .app import create_app

    quiet_disconnects()
    config = uvicorn.Config(create_app(dd, token, dev=dev), host="127.0.0.1",
                            port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    return server, thread


def stop_server(server, thread, timeout: float = 10.0) -> None:
    server.should_exit = True
    thread.join(timeout=timeout)
