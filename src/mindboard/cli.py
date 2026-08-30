"""The whole setup story:

    ./install.ps1        (Windows)  or  ./install.sh
    mindboard run

Everything else - backing up, restoring, checking the install - is a
subcommand here, so the app never needs a settings screen for it.
"""

import os
import secrets
import shutil
import socket
import subprocess
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
    no_args_is_help=True,
    help="Mindboard - map your mind to a board. Everything stays on this machine.",
)

DataDirOpt = typer.Option(None, "--data-dir", help="Where boards are stored.")


def _dd(data_dir: Optional[str]) -> DataDir:
    return DataDir(Path(data_dir) if data_dir else None).ensure()


def _token(dd: DataDir) -> str:
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


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _source_root() -> Optional[Path]:
    """The repository, when running from a checkout rather than a wheel."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "web" / "package.json").exists() else None


def _ok(label: str, detail: str = "") -> None:
    typer.echo(f"  {typer.style('ok', fg=typer.colors.GREEN)}   {label}"
               f"{('  ' + typer.style(detail, fg=typer.colors.BRIGHT_BLACK)) if detail else ''}")


def _warn(label: str, detail: str = "") -> None:
    typer.echo(f"  {typer.style('!', fg=typer.colors.YELLOW)}    {label}"
               f"{('  ' + typer.style(detail, fg=typer.colors.BRIGHT_BLACK)) if detail else ''}")


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #

@app.command()
def run(
    port: int = typer.Option(7788, "--port", "-p"),
    data_dir: Optional[str] = DataDirOpt,
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
    dev: bool = typer.Option(False, "--dev", help="Reload on code changes."),
):
    """Run Mindboard and open it in your browser."""
    import uvicorn

    from .app import WEB_DIR, create_app

    dd = _dd(data_dir)
    token = _token(dd)

    if not (WEB_DIR / "index.html").exists():
        typer.secho("The interface has not been built yet.", fg=typer.colors.RED)
        typer.echo("Run:  mindboard install")
        raise typer.Exit(1)

    if not _port_free(port):
        typer.secho(f"Port {port} is already in use.", fg=typer.colors.RED)
        typer.echo(f"Either Mindboard is running, or try:  mindboard run --port {port + 1}")
        raise typer.Exit(1)

    url = f"http://127.0.0.1:{port}/?t={token}"
    typer.secho(f"  Mindboard {__version__}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  data   {dd.root}")
    typer.echo(f"  open   {url}")
    typer.secho("  ctrl-c to stop\n", fg=typer.colors.BRIGHT_BLACK)

    if not no_open:
        threading.Thread(
            target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True
        ).start()

    uvicorn.run(create_app(dd, token, dev=dev), host="127.0.0.1", port=port, log_level="warning")


@app.command(hidden=True)
def start(
    port: int = typer.Option(7788, "--port", "-p"),
    data_dir: Optional[str] = DataDirOpt,
    no_open: bool = typer.Option(False, "--no-open"),
    dev: bool = typer.Option(False, "--dev"),
):
    """Deprecated name for `run`."""
    run(port=port, data_dir=data_dir, no_open=no_open, dev=dev)


# --------------------------------------------------------------------------- #
# installing
# --------------------------------------------------------------------------- #

@app.command()
def install(
    data_dir: Optional[str] = DataDirOpt,
    skip_build: bool = typer.Option(False, "--skip-build", help="Do not build the interface."),
    with_demo: bool = typer.Option(False, "--with-demo", help="Create a sample board."),
):
    """Set Mindboard up on this machine: storage, keys, and the interface."""
    from .app import WEB_DIR

    typer.secho(f"Installing Mindboard {__version__}", bold=True)

    dd = _dd(data_dir)
    _ok("storage ready", str(dd.root))
    _token(dd)
    _ok("access key generated", "kept in the data directory, never leaves this machine")

    built = (WEB_DIR / "index.html").exists()
    root = _source_root()
    if not built and root and not skip_build:
        npm = shutil.which("npm")
        if npm is None:
            _warn("interface not built", "Node is not installed - install Node, then rerun")
        else:
            typer.echo("  ...  building the interface (one time, needs Node)")
            try:
                subprocess.run([npm, "install", "--no-audit", "--no-fund"],
                               cwd=root / "web", check=True, capture_output=True)
                subprocess.run([npm, "run", "build"], cwd=root / "web",
                               check=True, capture_output=True)
                built = (WEB_DIR / "index.html").exists()
            except subprocess.CalledProcessError as exc:
                tail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
                _warn("interface build failed", " / ".join(tail) or "see npm output")
    if built:
        _ok("interface built")
    elif skip_build:
        _warn("interface not built", "you passed --skip-build")
    elif not root:
        _warn("interface missing from this install", "reinstall the package")

    from .boards import list_boards

    boards = list_boards(dd)
    if with_demo and not boards and root:
        demo = root / "scripts" / "demo_board.py"
        if demo.exists():
            subprocess.run([sys.executable, str(demo), "--data-dir", str(dd.root)], check=False)
            boards = list_boards(dd)
    _ok(f"{len(boards)} board{'' if len(boards) == 1 else 's'} in storage")

    typer.echo()
    typer.secho("  Done. Start it with:  ", nl=False)
    typer.secho("mindboard run", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"  Back everything up any time with:  mindboard backup",
                fg=typer.colors.BRIGHT_BLACK)
    if not built:
        raise typer.Exit(1)


# --------------------------------------------------------------------------- #
# backup and restore
# --------------------------------------------------------------------------- #

@app.command()
def backup(
    out: Optional[Path] = typer.Option(None, "--out", "-o",
                                       help="Where to write the zip. Defaults to this folder."),
    data_dir: Optional[str] = DataDirOpt,
):
    """Back up every board - text, images and video - into a single zip."""
    from .bundle import backup_filename, create_backup

    dd = _dd(data_dir)
    target = Path(out) if out else Path.cwd() / backup_filename()
    if target.is_dir():
        target = target / backup_filename()

    result = create_backup(dd, target)
    if not result["boards"]:
        _warn("nothing to back up", "there are no boards yet")
        raise typer.Exit(0)

    typer.secho(f"Backed up {len(result['boards'])} board"
                f"{'' if len(result['boards']) == 1 else 's'}", bold=True)
    for b in result["boards"]:
        typer.echo(f"  {b['title'][:38]:<38}  {b['nodes']:>4} nodes")
    size = result["bytes"] / 1024 / 1024
    typer.echo()
    typer.secho(f"  {result['path']}", fg=typer.colors.CYAN)
    typer.secho(f"  {size:.1f} MB - keep it anywhere; restore with: mindboard restore <file>",
                fg=typer.colors.BRIGHT_BLACK)


@app.command()
def restore(
    archive: Path = typer.Argument(..., exists=True, help="A backup zip, or a single .mindboard."),
    data_dir: Optional[str] = DataDirOpt,
    replace: bool = typer.Option(False, "--replace",
                                 help="Overwrite boards that are already here."),
    copy: bool = typer.Option(False, "--copy",
                              help="Bring everything in as new boards, keeping what you have."),
):
    """Put the boards from a backup back into the app."""
    from .bundle import BundleError, import_bundle, is_backup, restore_backup

    dd = _dd(data_dir)
    if replace and copy:
        typer.secho("Choose either --replace or --copy, not both.", fg=typer.colors.RED)
        raise typer.Exit(1)
    mode = "replace" if replace else "copy" if copy else "skip"

    try:
        if not is_backup(archive):
            meta = import_bundle(dd, archive)
            typer.secho(f"Restored 1 board", bold=True)
            typer.echo(f"  {meta['title']}  ({meta['node_count']} nodes)")
            return
        result = restore_backup(dd, archive, mode=mode)
    except BundleError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"Restored {len(result['restored'])} board"
                f"{'' if len(result['restored']) == 1 else 's'}", bold=True)
    for b in result["restored"]:
        typer.echo(f"  {typer.style('+', fg=typer.colors.GREEN)} {b['title'][:38]:<38}"
                   f"  {b['node_count']:>4} nodes")
    for b in result["skipped"]:
        typer.echo(f"  {typer.style('=', fg=typer.colors.BRIGHT_BLACK)} {b['title'][:38]:<38}"
                   f"  already here")
    for f in result["failed"]:
        typer.echo(f"  {typer.style('x', fg=typer.colors.RED)} {f['title'][:38]:<38}  {f['error']}")
    if result["skipped"] and mode == "skip":
        typer.secho("\n  Boards already here were left alone. Use --replace to overwrite them,"
                    "\n  or --copy to bring them in alongside.", fg=typer.colors.BRIGHT_BLACK)


# --------------------------------------------------------------------------- #
# boards
# --------------------------------------------------------------------------- #

@app.command()
def boards(data_dir: Optional[str] = DataDirOpt):
    """List your boards."""
    from .boards import list_boards

    rows = list_boards(_dd(data_dir))
    if not rows:
        typer.echo('No boards yet. Try: mindboard new "My first board"')
        return
    for b in rows:
        typer.echo(
            f"{b['id']}  {b['title'][:32]:<32}  {b['node_count']:>4} nodes  {b['created_at'][:10]}"
        )


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


@app.command("export")
def export_cmd(
    board_id: str = typer.Argument(...),
    out: Path = typer.Option(..., "--out", "-o"),
    data_dir: Optional[str] = DataDirOpt,
):
    """Export one board to a .mindboard bundle."""
    from .bundle import export_board

    p = export_board(_dd(data_dir), board_id, out)
    typer.echo(f"wrote {p}  ({p.stat().st_size / 1024:.0f} KB)")


@app.command("import")
def import_cmd(
    bundle: Path = typer.Argument(..., exists=True),
    data_dir: Optional[str] = DataDirOpt,
):
    """Import one .mindboard bundle (for a whole backup, use `restore`)."""
    from .bundle import import_bundle, is_backup

    dd = _dd(data_dir)
    if is_backup(bundle):
        typer.secho("That is a full backup - use:  mindboard restore <file>", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    meta = import_bundle(dd, bundle)
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


# --------------------------------------------------------------------------- #
# maintenance
# --------------------------------------------------------------------------- #

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
    check("interface built", (WEB_DIR / "index.html").exists(), fix="run: mindboard install")
    check(f"port {port} free", _port_free(port), fix="Mindboard may already be running")
    for b in list_boards(dd):
        check(f"board {b['title'][:24]!r}", integrity_ok(conn_for(dd, b["id"])),
              f"{b['node_count']} nodes")
    raise typer.Exit(0 if ok else 1)


@app.command()
def gc(data_dir: Optional[str] = DataDirOpt, dry_run: bool = typer.Option(False, "--dry-run")):
    """Delete media no node references any more."""
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
def where(data_dir: Optional[str] = DataDirOpt):
    """Print the data directory."""
    typer.echo(str(_dd(data_dir).root))


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
