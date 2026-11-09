"""The pick-an-option screen behind the desktop launcher.

Double-clicking Mindboard.bat (or Mindboard.command) lands here, so running the
app, backing it up and putting it back never requires remembering a command.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console

from . import __version__
from .runtime import (
    DEFAULT_PORT, access_token, backups_dir, board_url, diagnostics, next_free_port,
    open_folder, open_in_browser, port_free, resolve_data_dir, running_instance,
    serve_background, source_root, stop_server,
)

console = Console(highlight=False)

DIM = "grey50"
ACCENT = "bright_cyan"


def _rule() -> None:
    console.print()


def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = console.input(f"  [bold]{prompt}[/bold] ").strip()
    except (EOFError, KeyboardInterrupt):
        return "q"
    return answer or default


def _yes(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} [{DIM}]({hint})[/{DIM}]", "").lower()
    if not answer:
        return default
    return answer.startswith("y")


def _pause() -> None:
    try:
        console.input(f"\n  [{DIM}]press Enter to go back[/{DIM}] ")
    except (EOFError, KeyboardInterrupt):
        pass


def _size(path: Path) -> str:
    try:
        mb = path.stat().st_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 0.1 else f"{path.stat().st_size / 1024:.0f} KB"
    except OSError:
        return "?"


def _when(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %Y %H:%M")
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# screens
# --------------------------------------------------------------------------- #

def _header(dd) -> None:
    from .boards import list_boards

    console.clear()
    try:
        boards = list_boards(dd)
    except Exception:
        boards = []
    console.print()
    console.print(f"  [{ACCENT}]●[/{ACCENT}] [bold]Mindboard[/bold] "
                  f"[{DIM}]{__version__}[/{DIM}]")
    count = f"{len(boards)} board{'' if len(boards) == 1 else 's'}"
    console.print(f"    [{DIM}]{count} · {dd.root}[/{DIM}]")
    console.print()


OPTIONS = [
    ("1", "Open Mindboard", "the first page: your boards or your experts"),
    ("2", "Talk to my experts", "run Mindboard and open the Experts"),
    ("3", "Back up all boards", "everything, media included, into one zip"),
    ("4", "Restore from a backup", "put your boards back"),
    ("5", "My boards", "list them here"),
    ("6", "Set up the AI", "the local model and the experts' voice"),
    ("7", "Check the install", ""),
    ("8", "Open the data folder", ""),
    ("9", "Reinstall / update", "get the latest version"),
    ("q", "Quit", ""),
]


def _menu(dd) -> str:
    _header(dd)
    for key, label, note in OPTIONS:
        tail = f"[{DIM}]{note}[/{DIM}]" if note else ""
        console.print(f"    [bold {ACCENT}]{key}[/bold {ACCENT}]  {label:<23}{tail}")
    console.print()
    return _ask(f"Choose [{DIM}](1)[/{DIM}]:", "1").lower()


def _open_app(dd, page: str = "") -> None:
    """Run Mindboard and open the browser on a page: the boards, or with
    page="#/experts" the Experts."""
    token = access_token(dd)
    already = running_instance(DEFAULT_PORT)
    if already:
        console.print(f"\n  Mindboard is already running on port {DEFAULT_PORT}.")
        if _yes("Open it in your browser?", True):
            open_in_browser(board_url(DEFAULT_PORT, token) + page, delay=0.1)
        _pause()
        return

    port = DEFAULT_PORT if port_free(DEFAULT_PORT) else (next_free_port(DEFAULT_PORT + 1) or 0)
    if not port:
        console.print(f"\n  [red]Could not find a free port near {DEFAULT_PORT}.[/red]")
        _pause()
        return

    from .runtime import ui_built

    if not ui_built():
        console.print("\n  [red]The interface has not been built yet.[/red]")
        console.print(f"  [{DIM}]Choose 9 (Reinstall / update) to finish setting up.[/{DIM}]")
        _pause()
        return

    url = board_url(port, token) + page
    console.clear()
    console.print()
    console.print(f"  [{ACCENT}]●[/{ACCENT}] [bold]Starting Mindboard...[/bold]")

    server, thread = serve_background(dd, token, port)
    if not getattr(server, "started", False):
        console.print("\n  [red]The server did not start. Choose 7 to check the"
                      " install.[/red]")
        stop_server(server, thread, timeout=2)
        _pause()
        return

    open_in_browser(url, delay=0.1)
    console.clear()
    console.print()
    console.print(f"  [{ACCENT}]●[/{ACCENT}] [bold]Mindboard is running[/bold]")
    console.print(f"    [{DIM}]{url}[/{DIM}]")
    console.print()
    console.print(f"  [{DIM}]Your browser should open by itself. If it does not, paste"
                  f" that address into it.[/{DIM}]")
    console.print(f"  [{DIM}]Your work saves as you go - nothing to save by hand.[/{DIM}]")
    console.print()
    try:
        console.input("  [bold]Press Enter to stop Mindboard[/bold] ")
    except (EOFError, KeyboardInterrupt):
        pass
    console.print(f"\n  [{DIM}]Stopping...[/{DIM}]")
    stop_server(server, thread)
    console.print(f"  [{DIM}]Stopped.[/{DIM}]")
    _pause()


def _backup(dd) -> None:
    from .bundle import backup_filename, create_backup

    target = backups_dir(dd) / backup_filename()
    console.print(f"\n  Backing up to [{DIM}]{target.parent}[/{DIM}] ...")
    try:
        result = create_backup(dd, target)
    except Exception as exc:
        console.print(f"\n  [red]Backup failed: {exc}[/red]")
        _pause()
        return

    if not result["boards"]:
        console.print("\n  There are no boards to back up yet.")
        _pause()
        return

    console.print()
    for b in result["boards"]:
        console.print(f"    [green]✓[/green] {b['title'][:40]:<40} "
                      f"[{DIM}]{b['nodes']} nodes[/{DIM}]")
    if result["missing"]:
        from rich.markup import escape

        from .bundle import missing_note

        console.print()
        for line in missing_note(result["missing"]).rstrip().splitlines():
            # card titles are the person's words: never read as markup
            console.print(f"  [yellow]{escape(line)}[/yellow]", highlight=False)
    console.print()
    console.print(f"  [bold]{target.name}[/bold]  [{DIM}]{_size(target)}[/{DIM}]")
    console.print(f"  [{DIM}]{target.parent}[/{DIM}]")
    console.print()
    if _yes("Open that folder?", False):
        open_folder(target.parent)
    _pause()


def _candidates(dd) -> list[Path]:
    """Backups worth offering, newest first, wherever people actually keep them."""
    seen: dict[Path, None] = {}
    places = [backups_dir(dd), Path.cwd(), Path.home() / "Downloads", Path.home() / "Desktop"]
    root = source_root()
    if root:
        places.append(root)
    for place in places:
        try:
            if not place.is_dir():
                continue
            for pattern in ("mindboard-backup-*.zip", "*.mindboard"):
                for f in place.glob(pattern):
                    if f.is_file():
                        seen.setdefault(f.resolve(), None)
        except OSError:
            continue
    return sorted(seen, key=lambda p: p.stat().st_mtime, reverse=True)[:8]


def _browse() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Choose a Mindboard backup",
            filetypes=[("Mindboard backup or board", "*.zip *.mindboard"),
                       ("All files", "*.*")],
        )
        root.destroy()
        return Path(chosen) if chosen else None
    except Exception:
        return None


def _restore(dd) -> None:
    from .bundle import BundleError, import_bundle, is_backup, restore_backup

    found = _candidates(dd)
    console.print()
    if found:
        console.print("  [bold]Backups I can see[/bold]")
        console.print()
        for i, f in enumerate(found, 1):
            console.print(f"    [bold {ACCENT}]{i}[/bold {ACCENT}]  {f.name}")
            console.print(f"       [{DIM}]{_when(f)} · {_size(f)} · {f.parent}[/{DIM}]")
    else:
        console.print(f"  [{DIM}]No backups found in the usual places.[/{DIM}]")
    console.print()
    console.print(f"    [bold {ACCENT}]b[/bold {ACCENT}]  Browse for a file...")
    console.print(f"    [bold {ACCENT}]Enter[/bold {ACCENT}]  go back")
    console.print()

    answer = _ask("Which one?", "")
    if not answer or answer == "q":
        return
    if answer == "b":
        chosen = _browse()
        if chosen is None:
            console.print(f"\n  [{DIM}]No file chosen. You can also paste a full path"
                          f" at the prompt.[/{DIM}]")
            _pause()
            return
    elif answer.isdigit() and 1 <= int(answer) <= len(found):
        chosen = found[int(answer) - 1]
    else:
        chosen = Path(answer.strip('"').strip("'")).expanduser()

    if not chosen.is_file():
        console.print(f"\n  [red]Cannot find {chosen}[/red]")
        _pause()
        return

    mode = "skip"
    if is_backup(chosen):
        overlap = _overlap(dd, chosen)
        if overlap:
            console.print(f"\n  {overlap} of those boards"
                          f" {'is' if overlap == 1 else 'are'} already here.")
            console.print()
            console.print(f"    [bold {ACCENT}]1[/bold {ACCENT}]  Leave mine alone,"
                          f" only add what is missing   [{DIM}](safe)[/{DIM}]")
            console.print(f"    [bold {ACCENT}]2[/bold {ACCENT}]  Replace mine with the"
                          f" backup's version")
            console.print(f"    [bold {ACCENT}]3[/bold {ACCENT}]  Add the backup's copies"
                          f" alongside mine")
            console.print()
            mode = {"2": "replace", "3": "copy"}.get(_ask(f"Choose [{DIM}](1)[/{DIM}]:", "1"),
                                                     "skip")

    console.print(f"\n  Restoring from [bold]{chosen.name}[/bold] ...")
    try:
        if not is_backup(chosen):
            meta = import_bundle(dd, chosen)
            console.print(f"\n    [green]+[/green] {meta['title']}  "
                          f"[{DIM}]{meta['node_count']} nodes[/{DIM}]")
            _pause()
            return
        result = restore_backup(dd, chosen, mode=mode)
    except BundleError as exc:
        console.print(f"\n  [red]{exc}[/red]")
        _pause()
        return
    except Exception as exc:
        console.print(f"\n  [red]Could not read that file: {exc}[/red]")
        _pause()
        return

    console.print()
    for b in result["restored"]:
        console.print(f"    [green]+[/green] {b['title'][:40]:<40} "
                      f"[{DIM}]{b['node_count']} nodes[/{DIM}]")
    for b in result["skipped"]:
        console.print(f"    [{DIM}]= {b['title'][:40]:<40} already here[/{DIM}]")
    for f in result["failed"]:
        console.print(f"    [red]x[/red] {f['title'][:40]:<40} [red]{f['error']}[/red]")
    if not result["restored"] and not result["skipped"] and not result["failed"]:
        console.print(f"    [{DIM}]that backup is empty[/{DIM}]")
    _pause()


def _overlap(dd, archive: Path) -> int:
    import json
    import zipfile

    try:
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        present = set(dd.list_board_ids())
        return sum(1 for b in manifest.get("boards", []) if b.get("id") in present)
    except Exception:
        return 0


def _boards(dd) -> None:
    from .boards import list_boards

    rows = list_boards(dd)
    console.print()
    if not rows:
        console.print(f"  [{DIM}]No boards yet. Choose 1 to open Mindboard and make"
                      f" your first one.[/{DIM}]")
        _pause()
        return
    for b in rows:
        console.print(f"    [bold]{b['title'][:44]}[/bold]")
        console.print(f"      [{DIM}]{b['node_count']} nodes · {b['edge_count']} links ·"
                      f" created {b['created_at'][:10]}[/{DIM}]")
        if b["description"]:
            console.print(f"      [{DIM}]{b['description'][:70]}[/{DIM}]")
    _pause()


def _check(dd) -> None:
    console.print()
    checks = diagnostics(dd, DEFAULT_PORT)
    for c in checks:
        mark = "[green]ok  [/green]" if c["ok"] else "[red]FAIL[/red]"
        note = c["detail"] if c["ok"] else (c["fix"] or c["detail"])
        console.print(f"    {mark} {c['label']}"
                      f"{f'  [{DIM}]{note}[/{DIM}]' if note else ''}")
    if all(c["ok"] for c in checks):
        console.print(f"\n  [{DIM}]Everything looks right.[/{DIM}]")
    _pause()


def _reinstall(dd) -> None:
    root = source_root()
    console.print()
    if root is None:
        console.print("  This copy was installed as a package. Update it with:")
        console.print(f"    [{ACCENT}]uv tool install --force mindboard[/{ACCENT}]")
        _pause()
        return
    script = root / ("install.ps1" if sys.platform.startswith("win") else "install.sh")
    if not script.exists():
        console.print(f"  [red]Cannot find {script.name}[/red]")
        _pause()
        return
    if running_instance(DEFAULT_PORT):
        console.print(f"  Mindboard is still running on port {DEFAULT_PORT} - in another window.")
        console.print(f"  [{DIM}]Stop it there first (press Enter in that window), then choose 9 again.[/{DIM}]")
        _pause()
        return
    if sys.platform.startswith("win"):
        # This menu runs from the environment the installer updates, and Windows
        # will not replace a program that is running. Run inside it, the
        # installer half-deleted .venv and stopped. So hand over: the installer
        # gets its own window and waits for this one to close.
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit",
                 "-File", str(script)],
                cwd=root, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as exc:
            console.print(f"  [red]Could not start the installer: {exc}[/red]")
            _pause()
            return
        console.print("  The installer has opened in its own window.")
        console.print(f"  [{DIM}]This window closes now so it can update Mindboard. If anything"
                      f" else from Mindboard is still open, the installer says what and offers"
                      f" to stop it. Start Mindboard again when it says it is done.[/{DIM}]\n")
        raise SystemExit(0)
    console.print(f"  Running [bold]{script.name}[/bold] ...\n")
    try:
        subprocess.run(["bash", str(script)], cwd=root, check=False)
    except Exception as exc:
        console.print(f"\n  [red]Could not run the installer: {exc}[/red]")
    _pause()


# --------------------------------------------------------------------------- #

def run_menu(data_dir: Optional[str] = None) -> None:
    dd = resolve_data_dir(data_dir)
    actions = {"1": _open_app, "2": lambda d: _open_app(d, "#/experts"),
               "3": _backup, "4": _restore, "5": _boards, "6": _setup_ai,
               "7": _check, "8": lambda d: _open_data(d), "9": _reinstall}
    while True:
        choice = _menu(dd)
        if choice in ("q", "quit", "exit"):
            console.print(f"\n  [{DIM}]Bye.[/{DIM}]\n")
            return
        action = actions.get(choice)
        if action is None:
            continue
        try:
            action(dd)
        except KeyboardInterrupt:
            continue


def _setup_ai(dd) -> None:
    """The local model, set up the first time and checked after; then the
    experts' voice - to speak and to listen - if it is not set up yet."""
    from . import voice
    from .llm import find_install
    from .llm_setup import run_check, setup

    if find_install() is None:
        setup(console, lambda q: _yes(q, False))
    else:
        run_check(console)
    if find_install() is not None and not voice.complete():
        console.print()
        console.print("  [bold]The experts' voice[/bold] - they speak their answers, and you can")
        console.print(f"  speak to them. About 520 MB, once; everything stays on this computer.")
        console.print(f"  [{DIM}]Its pronunciation step uses espeak-ng (GPL-3.0).[/{DIM}]")
        if voice.setup_note():
            console.print(f"  [{DIM}]{voice.setup_note()}[/{DIM}]")
        if _yes("Set up the voice now?", True):
            def show(name, done, total):
                console.print(f"\r  {name}: {100 * done // max(total, 1):3d}%", end="")
            try:
                voice.install(progress=show)
                voice.install_listening(progress=show)
                console.print("\n  The voice is set up.")
            except voice.VoiceError as exc:
                console.print(f"\n  [red]{exc}[/red]")
    elif voice.installed():
        console.print(f"\n  [{DIM}]The experts' voice is set up - speaking and listening.[/{DIM}]")
    _pause()


def _open_data(dd) -> None:
    console.print(f"\n  [{DIM}]{dd.root}[/{DIM}]")
    if not open_folder(dd.root):
        console.print(f"  [{DIM}]Could not open a file manager - the path is above.[/{DIM}]")
    _pause()
