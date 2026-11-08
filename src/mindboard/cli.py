"""The whole setup story:

    ./install.ps1        (Windows)  or  ./install.sh
    mindboard run

Everything else - backing up, restoring, checking the install - is a
subcommand here, so the app never needs a settings screen for it.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .paths import DataDir
from .runtime import (
    DEFAULT_PORT, access_token, board_url, diagnostics, open_in_browser, port_free,
    resolve_data_dir, serve, source_root,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Mindboard - map your mind to a board. Everything stays on this machine.",
)

DataDirOpt = typer.Option(None, "--data-dir", help="Where boards are stored.")


def _dd(data_dir: Optional[str]) -> DataDir:
    return resolve_data_dir(data_dir)


def _token(dd: DataDir) -> str:
    return access_token(dd)


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    return port_free(port, host)


def _source_root() -> Optional[Path]:
    return source_root()


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
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p"),
    data_dir: Optional[str] = DataDirOpt,
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
    dev: bool = typer.Option(False, "--dev", help="Reload on code changes."),
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Address to serve on. Try 'localhost' if an embedded video player "
             "refuses to load."),
):
    """Run Mindboard and open it in your browser."""
    from .app import WEB_DIR

    dd = _dd(data_dir)
    token = _token(dd)

    if not (WEB_DIR / "index.html").exists():
        typer.secho("The interface has not been built yet.", fg=typer.colors.RED)
        typer.echo("Run:  mindboard install")
        raise typer.Exit(1)

    if not port_free(port, host):
        typer.secho(f"Port {port} is already in use.", fg=typer.colors.RED)
        typer.echo(f"Either Mindboard is running, or try:  mindboard run --port {port + 1}")
        raise typer.Exit(1)

    url = board_url(port, token, host)
    typer.secho(f"  Mindboard {__version__}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  data   {dd.root}")
    typer.echo(f"  open   {url}")
    typer.secho("  ctrl-c to stop\n", fg=typer.colors.BRIGHT_BLACK)

    if not no_open:
        open_in_browser(url)
    serve(dd, token, port, dev=dev, host=host)


@app.command(hidden=True)
def start(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p"),
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
    if result["missing"]:
        from .bundle import missing_note

        typer.echo()
        typer.secho(missing_note(result["missing"]).rstrip(), fg=typer.colors.YELLOW)
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
def doctor(data_dir: Optional[str] = DataDirOpt, port: int = DEFAULT_PORT):
    """Check that everything Mindboard needs is in order."""
    typer.secho(f"Mindboard {__version__}", bold=True)
    checks = diagnostics(_dd(data_dir), port)
    for c in checks:
        mark = typer.style("ok  ", fg=typer.colors.GREEN) if c["ok"] else typer.style(
            "FAIL", fg=typer.colors.RED)
        note = c["detail"] if c["ok"] else (c["fix"] or c["detail"])
        typer.echo(f"  {mark} {c['label']}{('  - ' + note) if note else ''}")
    raise typer.Exit(0 if all(c["ok"] for c in checks) else 1)


@app.command()
def gc(data_dir: Optional[str] = DataDirOpt, dry_run: bool = typer.Option(False, "--dry-run")):
    """Delete media no node references any more, and copies opened in other apps."""
    from .assets import clear_opened
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
    # working copies handed to a PDF reader are regenerable; any still open stay
    _, copies = clear_opened(dd.opened, dry_run=dry_run)
    freed += copies
    verb = "would free" if dry_run else "freed"
    typer.echo(f"{verb} {freed / 1024 / 1024:.1f} MB")


@app.command()
def where(data_dir: Optional[str] = DataDirOpt):
    """Print the data directory."""
    typer.echo(str(_dd(data_dir).root))


@app.command()
def menu(data_dir: Optional[str] = DataDirOpt):
    """Open the pick-an-option menu (what the desktop launcher runs)."""
    from .menu import run_menu

    run_menu(data_dir)


@app.command()
def version():
    """Print the version."""
    typer.echo(__version__)


# --------------------------------------------------------------------------- #
# ask your board: the local model
# --------------------------------------------------------------------------- #

ai_app = typer.Typer(no_args_is_help=True,
                     help="Ask your board - the local model it runs on this computer.")
app.add_typer(ai_app, name="ai")


def _console():
    from rich.console import Console

    return Console(highlight=False)


@ai_app.command("install")
def ai_install(
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before downloading."),
    model: str = typer.Option("auto", "--model",
                              help="auto (sized for this computer), full, compact or light."),
    backend: str = typer.Option("auto", "--backend",
                                help="auto, cuda, vulkan, metal or cpu."),
):
    """Download llama.cpp and a model sized for this computer, check every file, test it."""
    from .llm_setup import setup

    ok = setup(_console(), lambda q: yes or typer.confirm(f"  {q}", default=False),
               backend, model)
    raise typer.Exit(0 if ok else 1)


@ai_app.command("check")
def ai_check():
    """Load the model, ask it a question and show it a picture."""
    from .llm_setup import run_check

    raise typer.Exit(0 if run_check(_console()) else 1)


@ai_app.command("ask")
def ai_ask(
    question: str = typer.Argument(..., help="What to ask."),
    board: str = typer.Option(..., "--board", "-b", help="Board title (or part of it), or id."),
    card: list[str] = typer.Option([], "--card", "-c",
                                   help="A card in context, by title or id. Repeat for more; "
                                        "a group brings everything inside it."),
    every: bool = typer.Option(False, "--all", help="Every card on the board."),
    think: bool = typer.Option(False, "--think", help="Let the model think first (slower)."),
    data_dir: Optional[str] = DataDirOpt,
):
    """Ask the local model about chosen cards on a board, from the terminal."""
    from .llm_setup import ask_in_terminal

    if not card and not every:
        typer.echo("  Name at least one card with --card, or use --all.")
        raise typer.Exit(2)
    ok = ask_in_terminal(_console(), _dd(data_dir), board, card, question, think, every)
    raise typer.Exit(0 if ok else 1)


@ai_app.command("eval")
def ai_eval(
    board: str = typer.Option(..., "--board", "-b", help="Board title (or part of it), or id."),
    test_set: Optional[str] = typer.Option(None, "--set",
                                           help="Test set file; default: <data>/evals/<board>.json"),
    detail: bool = typer.Option(False, "--detail", help="Show every question."),
    data_dir: Optional[str] = DataDirOpt,
):
    """Measure how well retrieval finds the right cards, against a test set."""
    from pathlib import Path

    from . import evaluate, llm
    from .boards import list_boards
    from .llm_setup import _pick

    dd = _dd(data_dir)
    b = _pick(list_boards(dd), board, "board")
    path = Path(test_set) if test_set else evaluate.default_set_path(dd, b["title"])
    if not path.exists():
        typer.echo(f"  No test set at {path}")
        raise typer.Exit(1)
    data = evaluate.load_set(path)
    inst = llm.find_install()
    if inst is None or inst.embedder is None:
        typer.echo("  The embedding model is not installed - run: mindboard ai install")
        raise typer.Exit(1)
    embedder = llm.EmbedModel(inst)
    reranker = llm.RerankModel(inst) if inst.reranker else None
    try:
        reports = evaluate.run(dd, data, embedder, inst.embedder.name, reranker=reranker)
    finally:
        embedder.stop()
        if reranker is not None:
            reranker.stop()
    kinds = list(dict.fromkeys(q["kind"] for q in data["questions"]))
    typer.echo(f"\n  {b['title']}: {len(data['questions'])} questions\n")
    typer.echo("\n".join("  " + line for line in evaluate.table(reports, kinds).splitlines()))
    if detail:
        for r in reports:
            typer.echo(f"\n  {r.strategy}")
            for s in r.scores:
                typer.echo(f"    rec@10 {s.recall10:.2f}  MRR {s.mrr:.2f}  [{s.kind}] {s.question[:70]}")


@ai_app.command("voice")
def ai_voice():
    """Set up the experts' voice - speaking and listening, on this computer (about 520 MB)."""
    from . import voice

    con = _console()
    if voice.complete():
        con.print("  The voice is set up - experts speak, and listen.")
        return
    con.print("  Setting up the voice - Kokoro (Apache-2.0) to speak, whisper.cpp (MIT) to")
    con.print("  listen, and Smart Turn (BSD-2) to know when you have finished - about 520 MB,")
    con.print("  once. Everything stays on this computer.")
    con.print("  Its pronunciation step uses espeak-ng (GPL-3.0); it runs only on this computer.")

    def show(name, done, total):
        con.print(f"\r  {name}: {100 * done // max(total, 1):3d}%", end="")
    try:
        voice.install(progress=show)
        voice.install_listening(progress=show)
    except voice.VoiceError as exc:
        con.print(f"\n  [red]{exc}[/red]")
        raise typer.Exit(1)
    con.print("\n  Done. Experts now speak their answers - and you can speak to them.")


@ai_app.command("status")
def ai_status():
    """Say whether Ask your board is set up, and where."""
    from .llm_setup import status

    status(_console())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
