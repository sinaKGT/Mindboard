"""Media belongs to the board.

Everything put on a canvas is copied into the app's own folder, and every file a
node points at travels with it - into an export, into a backup, and never into
the bin while a node still shows it. These are the tests that keep that true.
"""

import hashlib
import zipfile

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mindboard.app import create_app
from mindboard.assets import store_bytes
from mindboard.boards import conn_for, create_board
from mindboard.bundle import create_backup, export_board, import_bundle, referenced_assets
from mindboard.cli import app as cli_app
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph
from mindboard.paths import DataDir

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
TOKEN = "test-token"


def env(op):
    return {"opId": ulid(), "op": op}


def auth():
    return {"X-Mindboard-Token": TOKEN}


def _three_kinds(dd, bid):
    """A board holding media, a video poster frame and a link cover."""
    c = conn_for(dd, bid)
    media = store_bytes(dd, c, bid, PNG, declared_mime="image/png", orig_name="shot.png")
    poster = store_bytes(dd, c, bid, PNG + b"poster", orig_name="poster.webp",
                         declared_mime="image/webp")
    cover = store_bytes(dd, c, bid, PNG + b"cover", orig_name="preview",
                        declared_mime="image/png")
    nodes = [
        {"id": ulid(), "type": "image", "pos": [0, 0], "title": "Shot",
         "content": {"sha256": media["sha256"], "alt": "shot.png"}},
        {"id": ulid(), "type": "video", "pos": [400, 0], "title": "Clip",
         "content": {"sha256": media["sha256"], "poster": poster["sha256"]}},
        {"id": ulid(), "type": "link", "pos": [800, 0], "title": "A page",
         "content": {"url": "https://example.com/a", "host": "example.com",
                     "thumb": cover["sha256"]}},
    ]
    apply_envelopes(c, bid, [env({"kind": "node.create", "node": n}) for n in nodes])
    return {"media": media["sha256"], "poster": poster["sha256"], "cover": cover["sha256"]}


def test_every_hash_a_node_holds_counts_as_referenced(dd, board):
    shas = _three_kinds(dd, board["id"])
    graph = read_graph(conn_for(dd, board["id"]), board["id"])
    found = referenced_assets(graph["nodes"])
    # a link cover is as much a file the board owns as the picture on an image node
    assert found == set(shas.values())


def test_export_carries_the_link_cover(dd, board, tmp_path):
    shas = _three_kinds(dd, board["id"])
    out = export_board(dd, board["id"], tmp_path / "b.mindboard")
    with zipfile.ZipFile(out) as z:
        stored = {n.split("/")[-1].split(".")[0] for n in z.namelist() if n.startswith("assets/")}
    assert stored == set(shas.values())

    fresh = DataDir(tmp_path / "other").ensure()
    meta = import_bundle(fresh, out)
    conn = conn_for(fresh, meta["id"])
    graph = read_graph(conn, meta["id"])
    for sha in referenced_assets(graph["nodes"]):
        row = conn.execute("SELECT ext FROM asset WHERE sha256=?", (sha,)).fetchone()
        assert row is not None
        assert fresh.asset_path(meta["id"], sha, row["ext"]).exists()


def test_gc_keeps_media_a_node_still_shows(dd, board):
    shas = _three_kinds(dd, board["id"])
    loose = store_bytes(dd, conn_for(dd, board["id"]), board["id"], b"nothing points here",
                        orig_name="orphan.bin")
    res = CliRunner().invoke(cli_app, ["gc", "--data-dir", str(dd.root)])
    assert res.exit_code == 0, res.output

    conn = conn_for(dd, board["id"])
    for sha in shas.values():
        row = conn.execute("SELECT ext FROM asset WHERE sha256=?", (sha,)).fetchone()
        assert row is not None, "gc deleted a file a node is still showing"
        assert dd.asset_path(board["id"], sha, row["ext"]).exists()
    assert not dd.asset_path(board["id"], loose["sha256"], loose["ext"]).exists()


def test_backup_holds_the_bytes_of_every_file(dd, board, tmp_path):
    shas = _three_kinds(dd, board["id"])
    out = create_backup(dd, tmp_path / "all.zip")["path"]
    with zipfile.ZipFile(out) as z:
        inner = [n for n in z.namelist() if n.endswith(".mindboard")]
        assert len(inner) == 1
        z.extract(inner[0], tmp_path / "x")
    with zipfile.ZipFile(tmp_path / "x" / inner[0]) as z:
        names = {n.split("/")[-1].split(".")[0]: n for n in z.namelist() if n.startswith("assets/")}
        assert set(names) == set(shas.values())
        for sha, name in names.items():
            assert hashlib.sha256(z.read(name)).hexdigest() == sha


def test_upload_takes_a_copy_and_forgets_where_it_came_from(dd, tmp_path):
    c = TestClient(create_app(dd, TOKEN))
    bid = c.post("/api/boards", json={"title": "Media"}, headers=auth()).json()["id"]

    # something big enough to prove it is not held whole in memory on the way in
    blob = (b"\x1a\x45\xdf\xa3" + b"mindboard video bytes " * 200_000)[:4_000_000]
    src = tmp_path / "deep" / "folders" / "Screen recording.webm"
    src.parent.mkdir(parents=True)
    src.write_bytes(blob)

    with src.open("rb") as fh:
        r = c.post(f"/api/boards/{bid}/assets", headers=auth(),
                   files={"file": ("Screen recording.webm", fh, "video/webm")})
    assert r.status_code == 200
    info = r.json()
    assert info["sha256"] == hashlib.sha256(blob).hexdigest()
    assert info["bytes"] == len(blob)

    kept = dd.asset_path(bid, info["sha256"], info["ext"])
    assert kept.exists() and kept.read_bytes() == blob

    # the original can go: the board is not holding a path into the user's disk
    src.unlink()
    row = conn_for(dd, bid).execute(
        "SELECT orig_name, source_url FROM asset WHERE sha256=?", (info["sha256"],)).fetchone()
    assert row["orig_name"] == "Screen recording.webm"
    assert row["source_url"] is None
    assert c.get(f"/api/boards/{bid}/assets/{info['sha256']}", headers=auth()).status_code == 200

    # and no half-written scraps left behind
    assert not list(dd.assets_dir(bid).glob("*.part"))


def test_a_node_pasted_into_another_board_brings_its_file(dd):
    c = TestClient(create_app(dd, TOKEN))
    src = create_board(dd, "From")
    dst = create_board(dd, "To")
    info = store_bytes(dd, conn_for(dd, src["id"]), src["id"], PNG,
                       declared_mime="image/png", orig_name="dot.png")

    before = c.get(f"/api/boards/{dst['id']}/assets/{info['sha256']}", headers=auth())
    assert before.status_code == 404

    r = c.post(f"/api/boards/{dst['id']}/assets/copy", headers=auth(),
               json={"from_board": src["id"], "sha256": info["sha256"]})
    assert r.status_code == 200
    assert dd.asset_path(dst["id"], info["sha256"], info["ext"]).exists()
    assert c.get(f"/api/boards/{dst['id']}/assets/{info['sha256']}",
                 headers=auth()).content == PNG
