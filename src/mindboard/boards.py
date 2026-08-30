"""Board lifecycle. One folder, one SQLite file, one asset tree per board -
so a board backs up, exports and deletes on its own."""

import json
import shutil
import sqlite3
from typing import Any, Optional

from . import SCHEMA_VERSION
from .db import connect, now
from .ids import ulid
from .models import DEFAULT_SETTINGS
from .paths import DataDir

_conns: dict[str, sqlite3.Connection] = {}


def conn_for(dd: DataDir, board_id: str) -> sqlite3.Connection:
    key = f"{dd.root}::{board_id}"
    c = _conns.get(key)
    if c is None:
        c = connect(dd.db_path(board_id))
        _conns[key] = c
    return c


def close_all() -> None:
    for c in _conns.values():
        try:
            c.close()
        except Exception:
            pass
    _conns.clear()


def create_board(dd: DataDir, title: str = "Untitled board", description: str = "",
                 board_id: Optional[str] = None) -> dict[str, Any]:
    dd.ensure()
    bid = board_id or ulid()
    dd.board_dir(bid).mkdir(parents=True, exist_ok=True)
    c = conn_for(dd, bid)
    ts = now()
    c.execute(
        """INSERT INTO board (id, title, description, schema_version, revision,
                              settings_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (bid, title, description, SCHEMA_VERSION, 0, json.dumps(DEFAULT_SETTINGS), ts, ts),
    )
    return board_meta(dd, bid)


MAX_PREVIEW_NODES = 60
MAX_PREVIEW_EDGES = 90


def board_preview(c: sqlite3.Connection, board_id: str) -> dict[str, Any]:
    """A miniature of the board's top level, normalised to the unit square, so a
    launcher card can draw the shape of the actual graph rather than a stock
    placeholder. Nodes inside meta-nodes resolve to the meta-node that holds
    them, which is exactly what you see when you open the board."""
    rows = c.execute(
        "SELECT id, type, x, y, style_json FROM node "
        "WHERE board_id=? AND parent_id IS NULL ORDER BY created_at LIMIT ?",
        (board_id, MAX_PREVIEW_NODES),
    ).fetchall()
    if not rows:
        return {"nodes": [], "edges": []}

    index = {r["id"]: i for i, r in enumerate(rows)}
    parents = {
        r["id"]: r["parent_id"]
        for r in c.execute("SELECT id, parent_id FROM node WHERE board_id=?", (board_id,))
    }

    def root_of(node_id: str) -> Optional[str]:
        seen = set()
        cur = node_id
        while cur is not None and cur not in seen:
            seen.add(cur)
            parent = parents.get(cur)
            if parent is None:
                return cur
            cur = parent
        return None

    xs = [r["x"] for r in rows]
    ys = [r["y"] for r in rows]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    nodes = []
    for r in rows:
        try:
            colour = json.loads(r["style_json"]).get("color")
        except Exception:
            colour = None
        nodes.append({
            "x": round((r["x"] - min(xs)) / span_x, 4),
            "y": round((r["y"] - min(ys)) / span_y, 4),
            "t": r["type"],
            **({"c": colour} if colour else {}),
        })

    edges: list[list[int]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for e in c.execute("SELECT source_id, target_id FROM edge WHERE board_id=?", (board_id,)):
        a, b = root_of(e["source_id"]), root_of(e["target_id"])
        if a is None or b is None or a == b:
            continue
        if a not in index or b not in index:
            continue
        pair = (index[a], index[b])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edges.append([pair[0], pair[1]])
        if len(edges) >= MAX_PREVIEW_EDGES:
            break
    return {"nodes": nodes, "edges": edges}


def board_meta(dd: DataDir, board_id: str) -> Optional[dict[str, Any]]:
    if not dd.db_path(board_id).exists():
        return None
    c = conn_for(dd, board_id)
    row = c.execute("SELECT * FROM board WHERE id=?", (board_id,)).fetchone()
    if row is None:
        return None
    counts = c.execute(
        "SELECT (SELECT COUNT(*) FROM node WHERE board_id=?) AS n,"
        " (SELECT COUNT(*) FROM edge WHERE board_id=?) AS e", (board_id, board_id)
    ).fetchone()
    by_type = {
        r["type"]: r["n"]
        for r in c.execute(
            "SELECT type, COUNT(*) AS n FROM node WHERE board_id=? GROUP BY type", (board_id,))
    }
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "revision": row["revision"],
        "settings": json.loads(row["settings_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "node_count": counts["n"],
        "edge_count": counts["e"],
        "counts_by_type": by_type,
        "preview": board_preview(c, board_id),
    }


def list_boards(dd: DataDir) -> list[dict[str, Any]]:
    out = []
    for bid in dd.list_board_ids():
        try:
            meta = board_meta(dd, bid)
            if meta:
                out.append(meta)
        except Exception:
            continue  # a corrupt board must not hide the healthy ones
    out.sort(key=lambda b: b["updated_at"], reverse=True)
    return out


def delete_board(dd: DataDir, board_id: str) -> bool:
    key = f"{dd.root}::{board_id}"
    c = _conns.pop(key, None)
    if c is not None:
        c.close()
    d = dd.board_dir(board_id)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False
