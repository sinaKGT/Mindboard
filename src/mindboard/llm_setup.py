"""Setting up and checking the local model, as the terminal shows it.

One implementation behind both `mindboard ai ...` and the launcher menu, the
same way `diagnostics` serves both `doctor` and the menu.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console
from rich.progress import (
    BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn,
)

from . import llm

DIM = "grey50"
ACCENT = "bright_cyan"


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def describe(console: Console, backend: str = "auto", tier: str = "auto") -> llm.Plan:
    """Say what setting up would download, from where, and why this size -
    before anything is downloaded."""
    p = llm.plan(backend=backend, tier=tier)
    console.print()
    if p.accelerator:
        console.print(f"  Found [bold]{p.accelerator.name}[/bold] "
                      f"[{DIM}]({p.accelerator.memory_gib:.0f} GB of graphics memory)[/{DIM}]")
    else:
        console.print(f"  [{DIM}]No graphics card for the model to use - it will run on the"
                      f" processor.[/{DIM}]")
    picked = "picked for this computer" if tier == "auto" else "as asked"
    console.print(f"  Model: [bold]{p.tier.label}[/bold]  [{DIM}]{p.tier.key}, {picked}[/{DIM}]")
    console.print(f"  [{DIM}]llama.cpp {llm.LLAMA_BUILD} · {p.backend} · into {p.home}[/{DIM}]")
    console.print()
    if not p.todo:
        console.print(f"  [{DIM}]Everything is already here.[/{DIM}]")
        return p
    for pin in p.todo:
        console.print(f"    {_gb(pin.size):>8}  {pin.name}  [{DIM}]{pin.url.split('/')[2]}[/{DIM}]")
    console.print(f"    [bold]{_gb(sum(x.size for x in p.todo)):>8}  in all[/bold]"
                  f"  [{DIM}]downloaded once, each file checked against its SHA-256[/{DIM}]")
    console.print()
    others = [t for t in llm.TIERS if t.key != p.tier.key]
    console.print(f"  [{DIM}]Other sizes: " + " · ".join(
        f"{t.key} {_gb(t.download)} ({t.hint})" for t in others) + f"[/{DIM}]")
    console.print(f"  [{DIM}]Choose one with: mindboard ai install --model <size>[/{DIM}]")
    console.print(f"  [{DIM}]After this, asking a board never uses the internet.[/{DIM}]")
    return p


def setup(console: Console, confirm: Callable[[str], bool], backend: str = "auto",
          tier: str = "auto") -> bool:
    """Describe, ask, download, check. Returns True when Ask your board is ready."""
    try:
        p = describe(console, backend, tier)
    except llm.SetupError as exc:
        console.print(f"\n  [red]{exc}[/red]")
        return False
    if p.todo and not confirm("Download it now?"):
        console.print(f"\n  [{DIM}]Nothing was downloaded.[/{DIM}]")
        return False

    console.print()
    with Progress(TextColumn("    {task.description}"), BarColumn(bar_width=28),
                  DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
                  console=console, transient=False) as bar:
        tasks: dict[str, int] = {}

        def progress(name: str, done: int, total: int) -> None:
            if name not in tasks:
                tasks[name] = bar.add_task(name[:34], total=total)
            bar.update(tasks[name], completed=done)

        try:
            inst = llm.install(backend=backend, tier=tier, progress=progress)
        except llm.SetupError as exc:
            console.print(f"\n  [red]{exc}[/red]")
            return False
    console.print(f"\n  [green]✓[/green] Installed in [{DIM}]{inst.home}[/{DIM}]")
    return run_check(console)


def run_check(console: Console) -> bool:
    inst = llm.find_install()
    if inst is None:
        console.print("\n  Ask your board is not set up yet - run: mindboard ai install")
        return False
    short = llm.memory_shortfall(inst)
    if short:
        console.print(f"\n  [yellow]![/yellow] {short[0].upper()}{short[1:]}")
    console.print(f"\n  Loading {inst.tier.label} ... [{DIM}](a minute or so)[/{DIM}]")
    try:
        r = llm.check(inst)
    except llm.ModelError as exc:
        console.print(f"\n  [red]{exc}[/red]")
        return False
    mem = f"  [{DIM}]{r['gpu_mib'] / 1024:.1f} GB of graphics memory[/{DIM}]" if r["gpu_mib"] else ""
    console.print(f"    [green]✓[/green] loaded in {r['load_seconds']:.0f} s{mem}")
    speed = r["tokens_per_second"]
    console.print(f"    [green]✓[/green] answers" +
                  (f"  [{DIM}]{speed:.0f} tokens a second[/{DIM}]" if speed else ""))
    console.print(f"      [{DIM}]\"{r['answer']}\"[/{DIM}]")
    mark = "[green]✓[/green]" if r["read_the_picture"] else "[red]x[/red]"
    took = r["picture_prompt_seconds"]
    console.print(f"    {mark} reads pictures"
                  + (f"  [{DIM}]{took:.1f} s for the test picture[/{DIM}]" if took else ""))
    console.print(f"      [{DIM}]\"{r['saw']}\"[/{DIM}]")
    ok = r["read_the_picture"] and bool(r["answer"])
    console.print()
    console.print("  Ask your board is ready." if ok
                  else "  [red]The model answered, but not as expected - see above.[/red]")
    return ok


def status(console: Console) -> None:
    inst = llm.find_install()
    home = llm.llm_home()
    console.print()
    if inst is None:
        console.print(f"  Ask your board is not set up. [{DIM}]mindboard ai install[/{DIM}]")
        console.print(f"  [{DIM}]It would live in {home}[/{DIM}]")
        return
    console.print(f"  [bold]Ask your board[/bold]  {inst.tier.label}  [{DIM}]({inst.tier.key})[/{DIM}]")
    console.print(f"    [{DIM}]llama.cpp {llm.LLAMA_BUILD} · {inst.backend} · {home}[/{DIM}]")
    for path in (inst.model, inst.vision):
        console.print(f"    [{DIM}]{_gb(path.stat().st_size):>8}  {path.name}[/{DIM}]")
