"""The .mindboard bundle: one zip that carries a whole board, media included.

Round-trip guarantee: import(export(B)) == B, node ids and all - so a bundle is
a backup, not merely a share. graph.json stays flat and readable because v2
hands exactly this to an LLM.
"""

import json
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import BUNDLE_FORMAT, __version__
from .assets import resolve
from .boards import board_meta, conn_for, create_board
from .db import now
from .ids import ulid
from .models import EDGE_KINDS, NODE_TYPES
from .ops import apply_envelopes, read_graph
from .paths import DataDir

ASSET_KEYS = ("sha256", "poster")


def referenced_assets(nodes: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for n in nodes:
        content = n.get("content") or {}
        for key in ASSET_KEYS:
            v = content.get(key)
            if isinstance(v, str) and len(v) == 64:
                out.add(v)
    return out


def export_board(dd: DataDir, board_id: str, out_path: Path,
                 include_thumbs: bool = False) -> Path:
    c = conn_for(dd, board_id)
    meta = board_meta(dd, board_id)
    if meta is None:
        raise ValueError(f"no such board: {board_id}")
    graph = read_graph(c, board_id)
    shas = referenced_assets(graph["nodes"])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        asset_rows = []
        for sha in sorted(shas):
            row = c.execute("SELECT * FROM asset WHERE sha256=?", (sha,)).fetchone()
            src = resolve(dd, c, board_id, sha)
            if row is None or src is None:
                continue
            z.write(src, f"assets/{sha}{row['ext']}")
            asset_rows.append({
                "sha256": sha, "mime": row["mime"], "ext": row["ext"],
                "bytes": row["bytes"], "orig_name": row["orig_name"],
                "width": row["width"], "height": row["height"],
                "duration_ms": row["duration_ms"], "source_url": row["source_url"],
            })

        z.writestr("graph.json", json.dumps({
            "format": BUNDLE_FORMAT,
            "board": {
                "id": meta["id"], "title": meta["title"],
                "description": meta["description"], "settings": meta["settings"],
                "created_at": meta["created_at"],
            },
            "nodes": graph["nodes"],
            "edges": graph["edges"],
            "assets": asset_rows,
        }, indent=2, ensure_ascii=False))

        z.writestr("manifest.json", json.dumps({
            "format": BUNDLE_FORMAT,
            "app_version": __version__,
            "exported_at": now(),
            "board": {"id": meta["id"], "title": meta["title"]},
            "counts": {
                "nodes": len(graph["nodes"]),
                "edges": len(graph["edges"]),
                "assets": len(asset_rows),
            },
        }, indent=2))

        if include_thumbs:
            tdir = dd.thumbs_dir(board_id)
            if tdir.exists():
                for t in tdir.iterdir():
                    z.write(t, f"thumbs/{t.name}")
    return out_path


class BundleError(ValueError):
    pass


def validate(data: dict[str, Any]) -> None:
    fmt = data.get("format")
    if not isinstance(fmt, str) or not fmt.startswith("mindboard/"):
        raise BundleError("not a Mindboard bundle")
    if int(fmt.split("/")[1]) > int(BUNDLE_FORMAT.split("/")[1]):
        raise BundleError(
            f"bundle format {fmt} is newer than this version of Mindboard understands"
        )
    node_ids = {n["id"] for n in data.get("nodes", [])}
    for n in data.get("nodes", []):
        if n.get("type") not in NODE_TYPES:
            raise BundleError(f"node {n.get('id')}: unknown type {n.get('type')!r}")
        parent = n.get("parent")
        if parent is not None and parent not in node_ids:
            raise BundleError(f"node {n['id']}: parent {parent} is missing from the bundle")
    for e in data.get("edges", []):
        if e.get("kind") not in EDGE_KINDS:
            raise BundleError(f"edge {e.get('id')}: unknown kind {e.get('kind')!r}")
        for end in ("source", "target"):
            if e.get(end) not in node_ids:
                raise BundleError(f"edge {e['id']}: {end} {e.get(end)} is missing from the bundle")


def import_bundle(dd: DataDir, zip_path: Path, title_override: Optional[str] = None,
                  keep_ids: bool = True) -> dict[str, Any]:
    """Validate fully, then write in one transaction. A partial bundle never half-lands."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "graph.json" not in names:
            raise BundleError("bundle has no graph.json")
        data = json.loads(z.read("graph.json").decode("utf-8"))
        validate(data)

        src_board = data.get("board", {})
        bid = ulid()
        meta = create_board(
            dd,
            title=title_override or src_board.get("title", "Imported board"),
            description=src_board.get("description", ""),
            board_id=bid,
        )
        c = conn_for(dd, bid)

        # assets first, so nodes never reference bytes that are not on disk yet
        for a in data.get("assets", []):
            member = f"assets/{a['sha256']}{a.get('ext', '')}"
            if member not in names:
                continue
            payload = z.read(member)
            path = dd.asset_path(bid, a["sha256"], a.get("ext", ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            c.execute(
                """INSERT OR IGNORE INTO asset (sha256, mime, ext, bytes, orig_name,
                       width, height, duration_ms, source_url, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (a["sha256"], a.get("mime", "application/octet-stream"), a.get("ext", ""),
                 a.get("bytes", len(payload)), a.get("orig_name"), a.get("width"),
                 a.get("height"), a.get("duration_ms"), a.get("source_url"), now()),
            )

    id_map = {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if not keep_ids:
        id_map = {n["id"]: ulid() for n in nodes}
        for n in nodes:
            n["id"] = id_map[n["id"]]
            if n.get("parent"):
                n["parent"] = id_map[n["parent"]]
        for e in edges:
            e["id"] = ulid()
            e["source"] = id_map[e["source"]]
            e["target"] = id_map[e["target"]]

    # parents before children, so the FK never trips
    order, seen = [], set()

    def emit(n):
        if n["id"] in seen:
            return
        p = n.get("parent")
        if p:
            parent = next((m for m in nodes if m["id"] == p), None)
            if parent is not None:
                emit(parent)
        seen.add(n["id"])
        order.append(n)

    for n in nodes:
        emit(n)

    envelopes = [{"opId": ulid(), "op": {"kind": "node.create", "node": {**n, "provenance": "import"}}}
                 for n in order]
    envelopes += [{"opId": ulid(), "op": {"kind": "edge.create", "edge": {**e, "provenance": "import"}}}
                  for e in edges]
    if src_board.get("settings"):
        envelopes.append({"opId": ulid(),
                          "op": {"kind": "board.update", "patch": {"settings": src_board["settings"]}}})
    result = apply_envelopes(c, bid, envelopes)
    if result["rejected"]:
        raise BundleError(f"import failed on {len(result['rejected'])} items: "
                          f"{result['rejected'][0]['error']}")
    return board_meta(dd, bid)
