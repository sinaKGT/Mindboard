"""The first page (DESIGN.md 57): groups of boards, and the page's own look.

Groups gather boards - "AI work", "Teaching" - and show how many each holds;
a board is in at most one group, or in none. The look is the page's theme
and its background pattern. Both belong to the library, not to a board, like
the experts and your themes: kept in `<data>/home.json` and carried in
backups.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .ids import ulid
from .paths import DataDir

THEMES = ("midnight", "slate", "ink", "forest", "ember", "ocean", "dusk", "daylight", "paper", "mist")
PATTERNS = ("dots", "grid", "lines", "dunes", "stars", "plain")
HEX = re.compile(r"\A#[0-9a-fA-F]{6}\Z")
NAME_CHARS = 40
MOST = 60

_lock = threading.Lock()


class HomeError(ValueError):
    """Said to the person as it is."""


def _path(dd: DataDir) -> Path:
    return dd.root / "home.json"


def load(dd: DataDir) -> dict:
    try:
        got = json.loads(_path(dd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    look = got.get("look") if isinstance(got.get("look"), dict) else {}
    groups = [g for g in got.get("groups", []) if isinstance(g, dict) and g.get("id")] \
        if isinstance(got.get("groups"), list) else []
    return {"look": {"theme": look.get("theme") if look.get("theme") in THEMES else "midnight",
                     "pattern": look.get("pattern") if look.get("pattern") in PATTERNS else "dots"},
            "groups": groups}


def _save(dd: DataDir, home: dict) -> None:
    path = _path(dd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(home, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _name(v) -> str:
    name = " ".join(str(v or "").split())[:NAME_CHARS]
    if not name:
        raise HomeError("give the group a name")
    return name


def _colour(v) -> str:
    return str(v).lower() if v and HEX.match(str(v)) else ""


def set_look(dd: DataDir, patch: dict) -> dict:
    with _lock:
        home = load(dd)
        if patch.get("theme") in THEMES:
            home["look"]["theme"] = patch["theme"]
        if patch.get("pattern") in PATTERNS:
            home["look"]["pattern"] = patch["pattern"]
        _save(dd, home)
        return home["look"]


def add_group(dd: DataDir, body: dict) -> dict:
    with _lock:
        home = load(dd)
        if len(home["groups"]) >= MOST:
            raise HomeError(f"you have {MOST} groups already")
        g = {"id": ulid(), "name": _name(body.get("name")), "colour": _colour(body.get("colour")), "boards": []}
        home["groups"].append(g)
        _save(dd, home)
        return g


def update_group(dd: DataDir, gid: str, body: dict) -> dict:
    with _lock:
        home = load(dd)
        g = next((g for g in home["groups"] if g["id"] == gid), None)
        if g is None:
            raise HomeError("no such group")
        if "name" in body:
            g["name"] = _name(body["name"])
        if "colour" in body:
            g["colour"] = _colour(body["colour"])
        _save(dd, home)
        return g


def delete_group(dd: DataDir, gid: str) -> bool:
    """The group goes; its boards stay, in no group."""
    with _lock:
        home = load(dd)
        kept = [g for g in home["groups"] if g["id"] != gid]
        if len(kept) == len(home["groups"]):
            return False
        home["groups"] = kept
        _save(dd, home)
        return True


def place(dd: DataDir, board: str, gid: str | None) -> dict:
    """Put a board in a group - taking it out of any other - or in none."""
    with _lock:
        home = load(dd)
        if gid and not any(g["id"] == gid for g in home["groups"]):
            raise HomeError("no such group")
        for g in home["groups"]:
            g["boards"] = [b for b in g.get("boards", []) if b != board]
            if g["id"] == gid:
                g["boards"].append(board)
        _save(dd, home)
        return home


def restore(dd: DataDir, incoming: dict, replace: bool = False) -> None:
    """Groups and look from a backup: groups not here are added (boards that
    are not here dropped from them), and the look only with `replace`."""
    if not isinstance(incoming, dict):
        return
    from .boards import list_boards

    present = {b["id"] for b in list_boards(dd)}
    with _lock:
        home = load(dd)
        mine = {g["id"]: g for g in home["groups"]}
        placed = {b for g in home["groups"] for b in g.get("boards", [])}
        for g in incoming.get("groups", []) if isinstance(incoming.get("groups"), list) else []:
            if not isinstance(g, dict) or not g.get("id") or (g["id"] in mine and not replace):
                continue
            try:
                boards = [b for b in g.get("boards", []) if b in present and (replace or b not in placed)]
                mine[g["id"]] = {"id": str(g["id"]), "name": _name(g.get("name")),
                                 "colour": _colour(g.get("colour")), "boards": boards}
            except HomeError:
                continue
        home["groups"] = list(mine.values())[:MOST]
        if replace and isinstance(incoming.get("look"), dict):
            look = incoming["look"]
            if look.get("theme") in THEMES:
                home["look"]["theme"] = look["theme"]
            if look.get("pattern") in PATTERNS:
                home["look"]["pattern"] = look["pattern"]
        _save(dd, home)
