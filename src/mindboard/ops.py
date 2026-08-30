"""The single mutation path.

Every change to a board - a drag, a keystroke, a paste, an import, and in v2 an
AI suggestion - arrives here as an op. Nothing else writes to node/edge tables.
That is what makes undo, persistence and (later) AI editing one mechanism
instead of three.
"""

import json
import sqlite3
from typing import Any

from .db import now
from .models import EDGE_KINDS, NODE_TYPES, EdgeDTO, NodeDTO

OP_KINDS = (
    "node.create", "node.update", "node.move", "node.delete",
    "edge.create", "edge.update", "edge.delete",
    "meta.wrap", "meta.unwrap",
    "board.update",
)


class OpError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #

def serialize_node(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "parent": r["parent_id"],
        "type": r["type"],
        "pos": [r["x"], r["y"]],
        "size": [r["w"], r["h"]],
        "z": r["z"],
        "title": r["title"],
        "content": json.loads(r["content_json"]),
        "style": json.loads(r["style_json"]),
        "collapsed": bool(r["collapsed"]),
        "provenance": r["provenance"],
    }


def serialize_edge(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "source": r["source_id"],
        "target": r["target_id"],
        "sourceHandle": r["source_handle"],
        "targetHandle": r["target_handle"],
        "label": r["label"],
        "kind": r["kind"],
        "directed": bool(r["directed"]),
        "style": json.loads(r["style_json"]),
        "provenance": r["provenance"],
    }


def read_graph(conn: sqlite3.Connection, board_id: str) -> dict[str, Any]:
    nodes = [serialize_node(r) for r in conn.execute(
        "SELECT * FROM node WHERE board_id=? ORDER BY z, created_at", (board_id,))]
    edges = [serialize_edge(r) for r in conn.execute(
        "SELECT * FROM edge WHERE board_id=? ORDER BY created_at", (board_id,))]
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# full-text index
# --------------------------------------------------------------------------- #

def _searchable_body(node: dict[str, Any]) -> str:
    c = node.get("content") or {}
    if node.get("type") == "text":
        return str(c.get("text", ""))
    if node.get("type") == "link":
        return " ".join(str(c.get(k, "")) for k in ("url", "excerpt"))
    if node.get("type") == "meta":
        return str(c.get("summary", ""))
    return str(c.get("alt", "") or c.get("filename", ""))


def _fts_set(conn: sqlite3.Connection, node: dict[str, Any]) -> None:
    conn.execute("DELETE FROM node_fts WHERE node_id=?", (node["id"],))
    conn.execute(
        "INSERT INTO node_fts (node_id, title, body) VALUES (?,?,?)",
        (node["id"], node.get("title", ""), _searchable_body(node)),
    )


def _fts_del(conn: sqlite3.Connection, node_ids: list[str]) -> None:
    for nid in node_ids:
        conn.execute("DELETE FROM node_fts WHERE node_id=?", (nid,))


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #

def insert_node(conn: sqlite3.Connection, board_id: str, dto: dict[str, Any]) -> None:
    n = NodeDTO(**dto)
    if n.type not in NODE_TYPES:
        raise OpError(f"unknown node type {n.type!r}")
    ts = now()
    conn.execute(
        """INSERT INTO node (id, board_id, parent_id, type, x, y, w, h, z, title,
                             content_json, style_json, collapsed, provenance,
                             created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (n.id, board_id, n.parent, n.type, n.pos[0], n.pos[1],
         n.size[0] if n.size else None, n.size[1] if len(n.size) > 1 else None,
         n.z, n.title, json.dumps(n.content), json.dumps(n.style),
         int(n.collapsed), n.provenance, ts, ts),
    )
    _fts_set(conn, n.model_dump())


_NODE_COLS = {
    "parent": "parent_id", "type": "type", "z": "z", "title": "title",
    "collapsed": "collapsed", "provenance": "provenance",
}


def update_node(conn: sqlite3.Connection, board_id: str, node_id: str,
                patch: dict[str, Any]) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    for key, col in _NODE_COLS.items():
        if key in patch:
            v = patch[key]
            sets.append(f"{col}=?")
            vals.append(int(v) if key == "collapsed" else v)
    if "pos" in patch:
        sets += ["x=?", "y=?"]
        vals += [patch["pos"][0], patch["pos"][1]]
    if "size" in patch:
        sets += ["w=?", "h=?"]
        vals += [patch["size"][0], patch["size"][1]]
    if "content" in patch:
        sets.append("content_json=?")
        vals.append(json.dumps(patch["content"]))
    if "style" in patch:
        sets.append("style_json=?")
        vals.append(json.dumps(patch["style"]))
    if not sets:
        return
    sets.append("updated_at=?")
    vals += [now(), node_id, board_id]
    conn.execute(f"UPDATE node SET {', '.join(sets)} WHERE id=? AND board_id=?", vals)
    row = conn.execute("SELECT * FROM node WHERE id=?", (node_id,)).fetchone()
    if row is not None:
        _fts_set(conn, serialize_node(row))


def insert_edge(conn: sqlite3.Connection, board_id: str, dto: dict[str, Any]) -> None:
    e = EdgeDTO(**dto)
    if e.kind not in EDGE_KINDS:
        raise OpError(f"unknown edge kind {e.kind!r}")
    if e.source == e.target:
        raise OpError("an edge cannot join a node to itself")
    ts = now()
    conn.execute(
        """INSERT INTO edge (id, board_id, source_id, target_id, source_handle,
                             target_handle, label, kind, directed, style_json,
                             provenance, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (e.id, board_id, e.source, e.target, e.sourceHandle, e.targetHandle,
         e.label, e.kind, int(e.directed), json.dumps(e.style), e.provenance, ts, ts),
    )


_EDGE_COLS = {"label": "label", "kind": "kind", "directed": "directed",
              "sourceHandle": "source_handle", "targetHandle": "target_handle"}


def update_edge(conn: sqlite3.Connection, board_id: str, edge_id: str,
                patch: dict[str, Any]) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    for key, col in _EDGE_COLS.items():
        if key in patch:
            v = patch[key]
            if key == "kind" and v not in EDGE_KINDS:
                raise OpError(f"unknown edge kind {v!r}")
            sets.append(f"{col}=?")
            vals.append(int(v) if key == "directed" else v)
    if "style" in patch:
        sets.append("style_json=?")
        vals.append(json.dumps(patch["style"]))
    if not sets:
        return
    sets.append("updated_at=?")
    vals += [now(), edge_id, board_id]
    conn.execute(f"UPDATE edge SET {', '.join(sets)} WHERE id=? AND board_id=?", vals)


# --------------------------------------------------------------------------- #
# op application
# --------------------------------------------------------------------------- #

def _apply_one(conn: sqlite3.Connection, board_id: str, op: dict[str, Any]) -> None:
    kind = op.get("kind")
    if kind not in OP_KINDS:
        raise OpError(f"unknown op {kind!r}")

    if kind == "node.create":
        insert_node(conn, board_id, op["node"])

    elif kind == "node.update":
        update_node(conn, board_id, op["id"], op["patch"])

    elif kind == "node.move":
        ts = now()
        for m in op["moves"]:
            conn.execute(
                "UPDATE node SET x=?, y=?, updated_at=? WHERE id=? AND board_id=?",
                (m["x"], m["y"], ts, m["id"], board_id),
            )

    elif kind == "node.delete":
        ids = op["ids"]
        _fts_del(conn, ids)
        conn.executemany(
            "DELETE FROM node WHERE id=? AND board_id=?", [(i, board_id) for i in ids]
        )

    elif kind == "edge.create":
        insert_edge(conn, board_id, op["edge"])

    elif kind == "edge.update":
        update_edge(conn, board_id, op["id"], op["patch"])

    elif kind == "edge.delete":
        conn.executemany(
            "DELETE FROM edge WHERE id=? AND board_id=?", [(i, board_id) for i in op["ids"]]
        )

    elif kind == "meta.wrap":
        insert_node(conn, board_id, op["meta"])
        meta_id = op["meta"]["id"]
        ts = now()
        for c in op["children"]:
            conn.execute(
                "UPDATE node SET parent_id=?, x=?, y=?, updated_at=? WHERE id=? AND board_id=?",
                (meta_id, c["x"], c["y"], ts, c["id"], board_id),
            )

    elif kind == "meta.unwrap":
        ts = now()
        for c in op["children"]:
            conn.execute(
                "UPDATE node SET parent_id=?, x=?, y=?, updated_at=? WHERE id=? AND board_id=?",
                (c.get("parent"), c["x"], c["y"], ts, c["id"], board_id),
            )
        _fts_del(conn, [op["metaId"]])
        conn.execute("DELETE FROM node WHERE id=? AND board_id=?", (op["metaId"], board_id))

    elif kind == "board.update":
        patch = op["patch"]
        sets, vals = [], []
        if "title" in patch:
            sets.append("title=?"); vals.append(patch["title"])
        if "description" in patch:
            sets.append("description=?"); vals.append(patch["description"])
        if "settings" in patch:
            sets.append("settings_json=?"); vals.append(json.dumps(patch["settings"]))
        if sets:
            sets.append("updated_at=?")
            vals += [now(), board_id]
            conn.execute(f"UPDATE board SET {', '.join(sets)} WHERE id=?", vals)


def apply_envelopes(conn: sqlite3.Connection, board_id: str,
                    envelopes: list[Any]) -> dict[str, Any]:
    """Apply a batch atomically. Returns the new revision and per-op results."""
    applied: list[str] = []
    rejected: list[dict[str, str]] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for env in envelopes:
            op = env.op if hasattr(env, "op") else env["op"]
            op_id = env.opId if hasattr(env, "opId") else env["opId"]
            try:
                _apply_one(conn, board_id, op)
                applied.append(op_id)
            except Exception as exc:  # one bad op must not lose the batch
                rejected.append({"opId": op_id, "error": str(exc)})
        row = conn.execute("SELECT revision FROM board WHERE id=?", (board_id,)).fetchone()
        revision = (row["revision"] if row else 0) + 1
        conn.execute(
            "UPDATE board SET revision=?, updated_at=? WHERE id=?", (revision, now(), board_id)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"revision": revision, "applied": applied, "rejected": rejected}
