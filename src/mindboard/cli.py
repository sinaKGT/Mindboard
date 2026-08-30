"""The whole setup story: `uv tool install mindboard` then `mindboard start`."""

import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .paths import DataDir

app = typer.Typer(
    add_completion=False,
    help="Mindboard - map your mind to a board. Everything stays on this machine.",
)


def _dd(data_dir: Optional[str]) -> DataDir:
    return DataDir(Path(data_dir) if data_dir else None).ensure()


def _token(dd: DataDir) -> str:
    f = dd.token_file
    if f.exists():
        t = f.read_text(encoding="utf-8").strip()
        if t:
            return t
    t = secrets.token_urlsafe(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(t, encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return t


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


DataDirOpt = typer.Option(None, "--data-dir", help="Where boards are stored.")


@app.command()
def start(
    port: int = typer.Option(7788, "--port", "-p"),
    data_dir: Optional[str] = DataDirOpt,
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
    dev: bool = typer.Option(False, "--dev", help="Reload on code changes."),
):
    """Run Mindboard and open it in your browser."""
    import uvicorn

    from .app import create_app

    dd = _dd(data_dir)
    token = _token(dd)
    if not _port_free(port):
        typer.secho(
            f"Port {port} is already in use. Try: mindboard start --port {port + 1}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    url = f"http://127.0.0.1:{port}/?t={token}"
    typer.secho(f"  Mindboard {__version__}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  data   {dd.root}")
    typer.echo(f"  open   {url}\n")

    if not no_open:
        threading.Thread(
            target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True
        ).start()

    uvicorn.run(create_app(dd, token, dev=dev), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def new(
    title: str = typer.Argument("Untitled board"),
    description: str = typer.Option("", "--description", "-d"),
    data_dir: Optional[str] = DataDirOpt,
):
    """Create a board."""
    from .boards import create_board

    meta = create_board(_dd(data_dir), title, description)
    typer.echo(f"{meta['id']}  {meta['title']}")


@app.command()
def boards(data_dir: Optional[str] = DataDirOpt):
    """List boards."""
    from .boards import list_boards

    rows = list_boards(_dd(data_dir))
    if not rows:
        typer.echo("No boards yet. Try: mindboard new \"My first board\"")
        return
    for b in rows:
        typer.echo(
            f"{b['id']}  {b['title'][:32]:<32}  {b['node_count']:>4} nodes  {b['created_at'][:10]}"
        )


@app.command("export")
def export_cmd(
    board_id: str = typer.Argument(...),
    out: Path = typer.Option(..., "--out", "-o"),
    data_dir: Optional[str] = DataDirOpt,
):
    """Export a board to a .mindboard bundle."""
    from .bundle import export_board

    p = export_board(_dd(data_dir), board_id, out)
    typer.echo(f"wrote {p}  ({p.stat().st_size / 1024:.0f} KB)")


@app.command("import")
def import_cmd(
    bundle: Path = typer.Argument(..., exists=True),
    data_dir: Optional[str] = DataDirOpt,
):
    """Import a .mindboard bundle as a new board."""
    from .bundle import import_bundle

    meta = import_bundle(_dd(data_dir), bundle)
    typer.echo(f"imported {meta['title']} -> {meta['id']}  ({meta['node_count']} nodes)")


@app.command()
def delete(board_id: str, data_dir: Optional[str] = DataDirOpt,
           yes: bool = typer.Option(False, "--yes", "-y")):
    """Delete a board and everything in it."""
    from .boards import board_meta, delete_board

    dd = _dd(data_dir)
    meta = board_meta(dd, board_id)
    if meta is None:
        typer.secho("no such board", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Delete '{meta['title']}' and its {meta['node_count']} nodes?", abort=True)
    delete_board(dd, board_id)
    typer.echo("deleted")


@app.command()
def where(data_dir: Optional[str] = DataDirOpt):
    """Print the data directory."""
    typer.echo(str(_dd(data_dir).root))


@app.command()
def gc(data_dir: Optional[str] = DataDirOpt, dry_run: bool = typer.Option(False, "--dry-run")):
    """Delete assets no node references any more."""
    from .boards import conn_for, list_boards
    from .bundle import referenced_assets
    from .ops import read_graph

    dd = _dd(data_dir)
    freed = 0
    for b in list_boards(dd):
        c = conn_for(dd, b["id"])
        keep = referenced_assets(read_graph(c, b["id"])["nodes"])
        for row in c.execute("SELECT sha256, ext, bytes FROM asset").fetchall():
            if row["sha256"] in keep:
                continue
            p = dd.asset_path(b["id"], row["sha256"], row["ext"])
            freed += row["bytes"]
            if not dry_run:
                p.unlink(missing_ok=True)
                c.execute("DELETE FROM asset WHERE sha256=?", (row["sha256"],))
    verb = "would free" if dry_run else "freed"
    typer.echo(f"{verb} {freed / 1024 / 1024:.1f} MB")


@app.command()
def doctor(data_dir: Optional[str] = DataDirOpt, port: int = 7788):
    """Check that everything Mindboard needs is in order."""
    from .app import WEB_DIR
    from .boards import conn_for, list_boards
    from .db import integrity_ok

    ok = True

    def check(label: str, good: bool, detail: str = "", fix: str = ""):
        """`detail` is context either way; `fix` only shows when something failed."""
        nonlocal ok
        ok = ok and good
        mark = typer.style("ok  ", fg=typer.colors.GREEN) if good else typer.style(
            "FAIL", fg=typer.colors.RED)
        note = detail if good else (fix or detail)
        typer.echo(f"  {mark} {label}{('  - ' + note) if note else ''}")

    typer.secho(f"Mindboard {__version__}", bold=True)
    check("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])
    dd = _dd(data_dir)
    check("data directory writable", os.access(dd.root, os.W_OK), str(dd.root))
    check("interface built", (WEB_DIR / "index.html").exists(),
          fix="run: cd web && npm install && npm run build")
    check(f"port {port} free", _port_free(port))
    for b in list_boards(dd):
        check(f"board {b['title'][:24]!r}", integrity_ok(conn_for(dd, b["id"])),
              f"{b['node_count']} nodes")
    raise typer.Exit(0 if ok else 1)


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
