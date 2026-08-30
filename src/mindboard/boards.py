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
