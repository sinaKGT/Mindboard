"""Every op, and the inverse the client would use for undo."""

from mindboard.boards import board_meta, conn_for
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph


def env(op):
    return {"opId": ulid(), "op": op}


def node(nid=None, **kw):
    base = {"id": nid or ulid(), "type": "text", "pos": [0, 0], "title": "n",
            "content": {"text": "hello"}}
    base.update(kw)
    return base


def test_create_update_move_delete(dd, board):
    bid = board["id"]
    c = conn_for(dd, bid)
    a, b = node(title="A"), node(title="B")

    r = apply_envelopes(c, bid, [env({"kind": "node.create", "node": a}),
                                 env({"kind": "node.create", "node": b})])
    assert r["rejected"] == [] and r["revision"] == 1

    e = {"id": ulid(), "source": a["id"], "target": b["id"],
         "label": "constrains", "kind": "causes"}
    apply_envelopes(c, bid, [env({"kind": "edge.create", "edge": e})])

    apply_envelopes(c, bid, [
        env({"kind": "node.update", "id": a["id"], "patch": {"title": "A prime",
                                                            "content": {"text": "changed"}}}),
        env({"kind": "node.move", "moves": [{"id": b["id"], "x": 120, "y": -40}]}),
        env({"kind": "edge.update", "id": e["id"], "patch": {"label": "blocks",
                                                             "kind": "contradicts"}}),
    ])

    g = read_graph(c, bid)
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id[a["id"]]["title"] == "A prime"
    assert by_id[a["id"]]["content"]["text"] == "changed"
    assert by_id[b["id"]]["pos"] == [120, -40]
    assert g["edges"][0]["label"] == "blocks" and g["edges"][0]["kind"] == "contradicts"

    # deleting a node takes its edges with it (FK cascade)
    apply_envelopes(c, bid, [env({"kind": "node.delete", "ids": [a["id"]]})])
    g = read_graph(c, bid)
    assert len(g["nodes"]) == 1 and g["edges"] == []


def test_bad_op_does_not_lose_the_batch(dd, board):
    bid = board["id"]
    c = conn_for(dd, bid)
    good = node(title="survivor")
    r = apply_envelopes(c, bid, [
        env({"kind": "node.create", "node": good}),
        env({"kind": "edge.create", "edge": {"id": ulid(), "source": "x", "target": "x"}}),
    ])
    assert len(r["applied"]) == 1 and len(r["rejected"]) == 1
    assert len(read_graph(c, bid)["nodes"]) == 1


def test_meta_wrap_preserves_every_edge(dd, board):
    """The thing meta-nodes must never do is silently drop a relationship."""
    bid = board["id"]
    c = conn_for(dd, bid)
    inside_a, inside_b, outside = node(title="ia"), node(title="ib"), node(title="out")
    apply_envelopes(c, bid, [env({"kind": "node.create", "node": n})
                             for n in (inside_a, inside_b, outside)])
    edges = [
        {"id": ulid(), "source": inside_a["id"], "target": inside_b["id"], "label": "internal"},
        {"id": ulid(), "source": inside_a["id"], "target": outside["id"], "label": "crosses out"},
        {"id": ulid(), "source": outside["id"], "target": inside_b["id"], "label": "crosses in"},
    ]
    apply_envelopes(c, bid, [env({"kind": "edge.create", "edge": e}) for e in edges])

    meta = node(type="meta", title="Group", content={"summary": ""})
    apply_envelopes(c, bid, [env({"kind": "meta.wrap", "meta": meta, "children": [
        {"id": inside_a["id"], "x": 0, "y": 0},
        {"id": inside_b["id"], "x": 200, "y": 0},
    ]})])

    g = read_graph(c, bid)
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id[inside_a["id"]]["parent"] == meta["id"]
    assert by_id[outside["id"]]["parent"] is None
    assert len(g["edges"]) == 3, "wrapping must not drop cross-boundary edges"

    apply_envelopes(c, bid, [env({"kind": "meta.unwrap", "metaId": meta["id"], "children": [
        {"id": inside_a["id"], "parent": None, "x": 10, "y": 10},
        {"id": inside_b["id"], "parent": None, "x": 210, "y": 10},
    ]})])
    g = read_graph(c, bid)
    assert len(g["nodes"]) == 3 and len(g["edges"]) == 3
    assert all(n["parent"] is None for n in g["nodes"])


def test_revision_advances(dd, board):
    bid = board["id"]
    c = conn_for(dd, bid)
    for i in range(3):
        apply_envelopes(c, bid, [env({"kind": "node.create", "node": node(title=str(i))})])
    assert board_meta(dd, bid)["revision"] == 3
