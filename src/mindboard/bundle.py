"""The .mindboard bundle: one zip that carries a whole board, media included.

Round-trip guarantee: import(export(B)) == B, node ids and all - so a bundle is
a backup, not merely a share. graph.json stays flat and readable because v2
hands exactly this to an LLM.
"""

import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import BACKUP_FORMAT, BUNDLE_FORMAT, __version__
from .assets import resolve
from .boards import board_meta, conn_for, create_board, delete_board, list_boards
from .db import now
from .ids import ulid
from .models import EDGE_KINDS, NODE_TYPES
from .ops import apply_envelopes, read_graph
from .paths import DataDir

SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def referenced_assets(nodes: list[dict[str, Any]]) -> set[str]:
    """Every hash a node points at, whatever key it hides under.

    Matching on the shape of a hash rather than on a list of key names is
    deliberate. Media lives in content.sha256, a video's poster frame in
    content.poster, a link's cover picture in content.thumb - and the next kind
    of card will invent its own key. A key missed here is a file silently left
    out of every export and backup, and then deleted by `gc`: the two ways this
    app could lose something the user put on a board.
    """
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            if SHA_RE.match(v):
                out.add(v)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    for n in nodes:
        walk(n.get("content") or {})
    return out


# Every file a card points to goes into the bundle (DESIGN.md 49). A file is
# named by its hash, so when one is missing from its own board the same file
# kept by another board is the very same bytes, and is taken from there. A file
# found nowhere cannot be included - that is said, by board and card, never
# passed over in silence: the backup still holds the cards, and the person
# knows what it lacks.

def find_file(dd: DataDir, board_id: str, sha: str, conn=None):
    """A file a card points to: (path, its asset row, taken from another
    board?) - its own board's copy first, then the same file kept by another
    board. (None, None, False) when it is on no board at all."""
    conn = conn or conn_for(dd, board_id)
    row = conn.execute("SELECT * FROM asset WHERE sha256=?", (sha,)).fetchone()
    if row is not None:
        path = resolve(dd, conn, board_id, sha)
        if path is not None:
            return path, row, False
    for b in list_boards(dd):
        if b["id"] == board_id:
            continue
        other = conn_for(dd, b["id"])
        orow = other.execute("SELECT * FROM asset WHERE sha256=?", (sha,)).fetchone()
        path = resolve(dd, other, b["id"], sha) if orow is not None else None
        if path is not None:
            return path, orow, True
    return None, None, False


def board_files(dd: DataDir, board_id: str, conn=None, nodes: Optional[list[dict]] = None) -> dict[str, Any]:
    """Every file a board's cards point to, found: {"found": [(sha, path,
    row)], "recovered": [sha] taken from another board, "missing": [{"sha",
    "board", "board_title", "cards"}] found nowhere}."""
    conn = conn or conn_for(dd, board_id)
    nodes = nodes if nodes is not None else read_graph(conn, board_id)["nodes"]
    meta = board_meta(dd, board_id) or {}
    out: dict[str, Any] = {"found": [], "recovered": [], "missing": []}
    for sha in sorted(referenced_assets(nodes)):
        path, row, elsewhere = find_file(dd, board_id, sha, conn)
        if path is None:
            cards = [n.get("title") or "Untitled" for n in nodes if sha in referenced_assets([n])]
            out["missing"].append({"sha": sha, "board": board_id, "board_title": meta.get("title", ""),
                                   "cards": cards})
            continue
        out["found"].append((sha, path, row))
        if elsewhere:
            out["recovered"].append(sha)
    return out


def check_files(dd: DataDir) -> dict[str, Any]:
    """What a backup made now would hold and lack - quick, nothing written: the
    files found nowhere, and how many were taken from another board."""
    missing, recovered, files = [], 0, 0
    for b in list_boards(dd):
        got = board_files(dd, b["id"])
        missing += got["missing"]
        recovered += len(got["recovered"])
        files += len(got["found"]) + len(got["missing"])
    return {"files": files, "missing": missing, "recovered": recovered}


def missing_note(missing: list[dict]) -> str:
    """The files a backup could not include, said plainly - the backup's
    MISSING-FILES.txt, and the terminal's warning."""
    lines = [f"{len(missing)} file{'' if len(missing) == 1 else 's'} on your boards could not be found on "
             "this computer, so this backup does not include "
             f"{'it' if len(missing) == 1 else 'them'}. The cards themselves are backed up.", ""]
    for m in missing:
        lines.append(f"- board \"{m['board_title']}\": {', '.join(m['cards'][:3]) or 'a card'}"
                     f"{' and others' if len(m['cards']) > 3 else ''}  (file {m['sha'][:12]})")
    return "\n".join(lines) + "\n"


def export_board(dd: DataDir, board_id: str, out_path: Path,
                 include_thumbs: bool = False, report: Optional[dict] = None) -> Path:
    """One board, every file its cards point to included. `report`, if given,
    is filled with the files taken from another board and those found nowhere."""
    c = conn_for(dd, board_id)
    meta = board_meta(dd, board_id)
    if meta is None:
        raise ValueError(f"no such board: {board_id}")
    graph = read_graph(c, board_id)
    files = board_files(dd, board_id, c, graph["nodes"])
    if report is not None:
        report.update(recovered=files["recovered"], missing=files["missing"])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        asset_rows = []
        for sha, src, row in files["found"]:
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
                "missing": len(files["missing"]),
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
                  keep_ids: bool = True, board_id: Optional[str] = None) -> dict[str, Any]:
    """Validate fully, then write in one transaction. A partial bundle never half-lands."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "graph.json" not in names:
            raise BundleError("bundle has no graph.json")
        data = json.loads(z.read("graph.json").decode("utf-8"))
        validate(data)

        src_board = data.get("board", {})
        bid = board_id or ulid()
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


# --------------------------------------------------------------------------- #
# whole-library backup
# --------------------------------------------------------------------------- #

def _slug(text: str, limit: int = 40) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in text.strip().lower()]
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:limit] or "board"


def backup_filename(when: Optional[str] = None) -> str:
    stamp = (when or now())[:10]
    return f"mindboard-backup-{stamp}.zip"


def create_backup(dd: DataDir, out_path: Path) -> dict[str, Any]:
    """Every board, media included, in one ordinary zip.

    Each board is written as its own `.mindboard` bundle inside, so a backup is
    inspectable with any zip tool and restores through the same path a single
    board does - one format to trust rather than two.
    """
    boards = list_boards(dd)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    recovered = 0

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for b in boards:
                member = f"boards/{_slug(b['title'])}-{b['id']}.mindboard"
                staged = tmpdir / f"{b['id']}.mindboard"
                report: dict[str, Any] = {}
                export_board(dd, b["id"], staged, report=report)
                missing += report["missing"]
                recovered += len(report["recovered"])
                z.write(staged, member)
                staged.unlink(missing_ok=True)
                entries.append({
                    "id": b["id"], "title": b["title"], "description": b["description"],
                    "nodes": b["node_count"], "edges": b["edge_count"],
                    "created_at": b["created_at"], "updated_at": b["updated_at"],
                    "file": member,
                })
            # experts span boards, so they travel with the library (DESIGN.md 40)
            from .experts import load as load_experts
            if load_experts(dd):
                z.writestr("experts.json", json.dumps(load_experts(dd), indent=2, ensure_ascii=False))
            if missing:
                z.writestr("MISSING-FILES.txt", missing_note(missing))
            z.writestr("manifest.json", json.dumps({
                "format": BACKUP_FORMAT,
                "app_version": __version__,
                "created_at": now(),
                "board_count": len(entries),
                "boards": entries,
                "missing_files": missing,
            }, indent=2, ensure_ascii=False))
    return {"path": out_path, "boards": entries, "missing": missing, "recovered": recovered,
            "bytes": out_path.stat().st_size if out_path.exists() else 0}


def is_backup(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as z:
            if "manifest.json" not in z.namelist():
                return False
            fmt = json.loads(z.read("manifest.json").decode("utf-8")).get("format", "")
            return isinstance(fmt, str) and fmt.startswith("mindboard-backup/")
    except Exception:
        return False


def restore_backup(dd: DataDir, zip_path: Path, mode: str = "skip") -> dict[str, Any]:
    """Put every board in a backup back into the app.

    mode: 'skip' leaves boards that are already here alone (the safe default -
    restoring twice must not double your library), 'replace' overwrites them,
    'copy' brings everything in as new boards alongside what you have.
    """
    if mode not in ("skip", "replace", "copy"):
        raise BundleError(f"unknown restore mode {mode!r}")

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise BundleError("that zip is not a Mindboard backup")
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        fmt = manifest.get("format", "")
        if not str(fmt).startswith("mindboard-backup/"):
            raise BundleError("that zip is not a Mindboard backup")
        if int(str(fmt).split("/")[1]) > int(BACKUP_FORMAT.split("/")[1]):
            raise BundleError(f"backup format {fmt} is newer than this version understands")

        present = set(dd.list_board_ids())
        restored: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            for entry in manifest.get("boards", []):
                member = entry.get("file")
                if member not in names:
                    failed.append({"title": entry.get("title", "?"), "error": "missing from the zip"})
                    continue
                staged = Path(tmp) / "board.mindboard"
                staged.write_bytes(z.read(member))
                exists = entry.get("id") in present
                try:
                    if exists and mode == "skip":
                        skipped.append(entry)
                        continue
                    if exists and mode == "replace":
                        delete_board(dd, entry["id"])
                        present.discard(entry["id"])
                    target_id = None if (exists and mode == "copy") else entry.get("id")
                    meta = import_bundle(dd, staged, board_id=target_id)
                    present.add(meta["id"])
                    restored.append(meta)
                except Exception as exc:
                    failed.append({"title": entry.get("title", "?"), "error": str(exc)})
                finally:
                    staged.unlink(missing_ok=True)

        # experts come back too - not in "copy" mode, where every board gets a
        # new id and their links would point nowhere
        if "experts.json" in names and mode != "copy":
            from .experts import restore as restore_experts
            restore_experts(dd, json.loads(z.read("experts.json").decode("utf-8")), replace=mode == "replace")

    return {"restored": restored, "skipped": skipped, "failed": failed,
            "backup_created_at": manifest.get("created_at")}
