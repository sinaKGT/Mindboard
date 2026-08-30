"""A bundle is a backup, not just a share: import(export(b)) == b."""

import hashlib

import pytest

from mindboard.assets import store_bytes
from mindboard.boards import conn_for
from mindboard.bundle import BundleError, export_board, import_bundle
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def env(op):
    return {"opId": ulid(), "op": op}


def test_round_trip_with_media(dd, board, tmp_path):
    bid = board["id"]
    c = conn_for(dd, bid)
    info = store_bytes(dd, c, bid, PNG, declared_mime="image/png", orig_name="dot.png")

    a = {"id": ulid(), "type": "text", "pos": [10, 20], "title": "Idea",
         "content": {"text": "traffic density"}, "style": {"fontSize": 18}}
    img = {"id": ulid(), "type": "image", "pos": [300, 20], "title": "Site plan",
           "content": {"sha256": info["sha256"], "alt": "plan"}}
    meta = {"id": ulid(), "type": "meta", "pos": [0, 300], "title": "Sensors",
            "content": {"summary": "everything about siting"}}
    child = {"id": ulid(), "type": "text", "parent": meta["id"], "pos": [0, 0],
             "title": "Lidar", "content": {"text": "corner mounting"}}
    apply_envelopes(c, bid, [env({"kind": "node.create", "node": n})
                             for n in (a, img, meta, child)])
    e = {"id": ulid(), "source": a["id"], "target": child["id"],
         "label": "informs", "kind": "supports"}
    apply_envelopes(c, bid, [env({"kind": "edge.create", "edge": e})])

    out = export_board(dd, bid, tmp_path / "b.mindboard")
    assert out.exists() and out.stat().st_size > 0

    before = read_graph(c, bid)
    meta2 = import_bundle(dd, out)
    after = read_graph(conn_for(dd, meta2["id"]), meta2["id"])

    def key(items):
        return sorted(items, key=lambda i: i["id"])

    assert [n["id"] for n in key(after["nodes"])] == [n["id"] for n in key(before["nodes"])]
    for b_node, a_node in zip(key(before["nodes"]), key(after["nodes"])):
        for field in ("type", "parent", "pos", "size", "title", "content", "style"):
            assert a_node[field] == b_node[field], field
        assert a_node["provenance"] == "import"
    assert [e["label"] for e in after["edges"]] == [e["label"] for e in before["edges"]]
    assert after["edges"][0]["kind"] == "supports"

    # the media survived byte-for-byte
    from mindboard.assets import resolve
    p = resolve(dd, conn_for(dd, meta2["id"]), meta2["id"], info["sha256"])
    assert p is not None
    assert hashlib.sha256(p.read_bytes()).hexdigest() == info["sha256"]


def test_rejects_a_dangling_edge(dd, board, tmp_path):
    import json
    import zipfile

    bad = tmp_path / "bad.mindboard"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("graph.json", json.dumps({
            "format": "mindboard/1", "board": {"title": "x"},
            "nodes": [{"id": "A", "type": "text"}],
            "edges": [{"id": "E", "source": "A", "target": "GHOST", "kind": "relates"}],
        }))
    with pytest.raises(BundleError, match="missing from the bundle"):
        import_bundle(dd, bad)


def test_rejects_a_newer_format(dd, tmp_path):
    import json
    import zipfile

    bad = tmp_path / "future.mindboard"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("graph.json", json.dumps({"format": "mindboard/9", "nodes": [], "edges": []}))
    with pytest.raises(BundleError, match="newer"):
        import_bundle(dd, bad)
