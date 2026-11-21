"""Your own themes (DESIGN.md 52): a name, the built-in theme it starts from,
and the colours you chose - saved once, offered on every board.

They belong to the library, not to a board, like the experts: kept in
`<data>/themes.json` and carried in backups. A board that uses one keeps its
own copy of the colours too (its settings), so it looks the same in an
exported bundle, or after the theme is deleted.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .ids import ulid
from .paths import DataDir

# the built-in themes a custom one can start from (web/src/theme.ts THEMES)
BASES = ("midnight", "slate", "ink", "forest", "ember", "ocean", "dusk", "daylight", "paper", "mist")
# the colours a theme may set (web/src/theme.ts CUSTOMISABLE) - nothing else
KEYS = ("--mb-bg", "--mb-grid", "--mb-surface", "--mb-text", "--mb-accent",
        "--mb-type-text", "--mb-type-image", "--mb-type-video", "--mb-type-meta")
HEX = re.compile(r"\A#[0-9a-fA-F]{6}\Z")
MOST = 50
NAME_CHARS = 40

_lock = threading.Lock()


class ThemeError(ValueError):
    """Said to the person as it is."""


def _path(dd: DataDir) -> Path:
    return dd.root / "themes.json"


def load(dd: DataDir) -> list[dict]:
    try:
        got = json.loads(_path(dd).read_text(encoding="utf-8"))
        return [t for t in got if isinstance(t, dict) and t.get("id")] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(dd: DataDir, themes: list[dict]) -> None:
    path = _path(dd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(themes, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def clean(body: dict) -> dict:
    """A theme as it may be kept: a name, a known base, known colours as #rrggbb."""
    name = " ".join(str(body.get("name") or "").split())[:NAME_CHARS]
    if not name:
        raise ThemeError("give the theme a name")
    base = str(body.get("base") or "midnight")
    if base not in BASES:
        base = "midnight"
    palette = body.get("palette") if isinstance(body.get("palette"), dict) else {}
    palette = {k: str(v).lower() for k, v in palette.items() if k in KEYS and HEX.match(str(v))}
    return {"name": name, "base": base, "palette": palette}


def save(dd: DataDir, body: dict) -> dict:
    """Save a theme. One with the same name (case aside) is updated in place,
    so saving again after changing a colour keeps one theme, not two."""
    t = clean(body)
    with _lock:
        themes = load(dd)
        same = next((x for x in themes if x["name"].casefold() == t["name"].casefold()), None)
        if same is not None:
            same.update(t)
            _save(dd, themes)
            return same
        if len(themes) >= MOST:
            raise ThemeError(f"you have {MOST} themes already - delete one first")
        t = {"id": ulid(), **t}
        themes.append(t)
        _save(dd, themes)
        return t


def delete(dd: DataDir, theme_id: str) -> bool:
    with _lock:
        themes = load(dd)
        kept = [t for t in themes if t["id"] != theme_id]
        if len(kept) == len(themes):
            return False
        _save(dd, kept)
        return True


def restore(dd: DataDir, incoming: list, replace: bool = False) -> int:
    """Themes from a backup: new ones added; ones already here kept, or
    replaced with `replace`. Anything that is not a theme is skipped."""
    with _lock:
        themes = {t["id"]: t for t in load(dd)}
        added = 0
        for t in incoming if isinstance(incoming, list) else []:
            if not isinstance(t, dict) or not t.get("id") or (t["id"] in themes and not replace):
                continue
            try:
                themes[t["id"]] = {"id": str(t["id"]), **clean(t)}
                added += 1
            except ThemeError:
                continue
        _save(dd, list(themes.values())[:MOST])
        return added
