"""A backup has to bring a whole library back, and restoring twice must not
double it."""

import zipfile

import pytest

from mindboard.boards import conn_for, create_board, list_boards
from mindboard.bundle import BundleError, create_backup, is_backup, restore_backup
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph
from mindboard.paths import DataDir


def seed(dd, title, n_nodes):
    board = create_board(dd, title, f"{title} description")
    c = conn_for(dd, board["id"])
    nodes = [{"id": ulid(), "type": "text", "pos": [i * 60, i * 20], "title": f"{title} {i}",
              "content": {"text": f"node {i}"}} for i in range(n_nodes)]
    env = [{"opId": ulid(), "op": {"kind": "node.create", "node": n}} for n in nodes]
    env += [{"opId": ulid(), "op": {"kind": "edge.create", "edge": {
        "id": ulid(), "source": nodes[0]["id"], "target": nodes[1]["id"],
        "label": "leads to", "kind": "causes"}}}]
    apply_envelopes(c, board["id"], env)
    return board


def test_backup_holds_every_board(dd, tmp_path):
    seed(dd, "Alpha", 3)
    seed(dd, "Beta", 5)
    out = create_backup(dd, tmp_path / "b.zip")

    assert len(out["boards"]) == 2
    with zipfile.ZipFile(out["path"]) as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert sum(n.startswith("boards/") for n in names) == 2
        # names are readable, not opaque ids alone
        assert any("alpha" in n for n in names)
    assert is_backup(out["path"])


def test_restore_into_an_empty_install(dd, tmp_path):
    a = seed(dd, "Alpha", 3)
    b = seed(dd, "Beta", 5)
    out = create_backup(dd, tmp_path / "b.zip")

    fresh = DataDir(tmp_path / "elsewhere").ensure()
    result = restore_backup(fresh, out["path"])

    assert len(result["restored"]) == 2 and not result["failed"]
    ids = {m["id"] for m in result["restored"]}
    assert ids == {a["id"], b["id"]}, "board identity should survive a backup"
    titles = {m["title"] for m in list_boards(fresh)}
    assert titles == {"Alpha", "Beta"}
    restored_alpha = next(m for m in result["restored"] if m["title"] == "Alpha")
    graph = read_graph(conn_for(fresh, restored_alpha["id"]), restored_alpha["id"])
    assert len(graph["nodes"]) == 3 and len(graph["edges"]) == 1
    assert graph["edges"][0]["label"] == "leads to"


def test_restoring_twice_does_not_double_the_library(dd, tmp_path):
    seed(dd, "Alpha", 3)
    seed(dd, "Beta", 2)
    out = create_backup(dd, tmp_path / "b.zip")

    result = restore_backup(dd, out["path"])  # default: skip what is already here
    assert result["restored"] == []
    assert len(result["skipped"]) == 2
    assert len(list_boards(dd)) == 2


def test_copy_mode_brings_them_in_alongside(dd, tmp_path):
    a = seed(dd, "Alpha", 3)
    out = create_backup(dd, tmp_path / "b.zip")

    result = restore_backup(dd, out["path"], mode="copy")
    assert len(result["restored"]) == 1
    assert result["restored"][0]["id"] != a["id"]
    assert len(list_boards(dd)) == 2


def test_replace_mode_overwrites_in_place(dd, tmp_path):
    a = seed(dd, "Alpha", 3)
    out = create_backup(dd, tmp_path / "b.zip")

    # drift: the live board gains a node the backup does not have
    c = conn_for(dd, a["id"])
    apply_envelopes(c, a["id"], [{"opId": ulid(), "op": {"kind": "node.create", "node": {
        "id": ulid(), "type": "text", "pos": [0, 0], "title": "added later", "content": {}}}}])
    assert len(read_graph(c, a["id"])["nodes"]) == 4

    result = restore_backup(dd, out["path"], mode="replace")
    assert len(result["restored"]) == 1
    assert len(list_boards(dd)) == 1
    back = read_graph(conn_for(dd, a["id"]), a["id"])
    assert len(back["nodes"]) == 3, "replace should restore the backed-up state exactly"


def test_a_single_bundle_is_not_a_backup(dd, tmp_path):
    from mindboard.bundle import export_board

    a = seed(dd, "Alpha", 2)
    single = export_board(dd, a["id"], tmp_path / "one.mindboard")
    assert not is_backup(single)
    with pytest.raises(BundleError, match="not a Mindboard backup"):
        restore_backup(dd, single)


def test_backup_of_an_empty_install(dd, tmp_path):
    out = create_backup(dd, tmp_path / "empty.zip")
    assert out["boards"] == []
    assert is_backup(out["path"])
    fresh = DataDir(tmp_path / "elsewhere").ensure()
    assert restore_backup(fresh, out["path"])["restored"] == []
