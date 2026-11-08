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


def test_diagnostics_cover_the_basics(dd):
    from mindboard.runtime import diagnostics

    seed(dd, "Alpha", 2)
    labels = [c["label"] for c in diagnostics(dd)]
    assert any("python" in l for l in labels)
    assert any("storage writable" in l for l in labels)
    assert any("Alpha" in l for l in labels)
    assert all(set(c) >= {"label", "ok", "detail", "fix"} for c in diagnostics(dd))


def test_menu_backup_folder_is_created(dd):
    from mindboard.runtime import backups_dir

    folder = backups_dir(dd)
    assert folder.is_dir()


# ------------------------------------------------- every file, or said why --

def picture_board(dd, title, data: bytes, card_title: str):
    """A board with one picture card, its file stored on the board."""
    from mindboard.assets import store_bytes

    board = create_board(dd, title, "")
    c = conn_for(dd, board["id"])
    stored = store_bytes(dd, c, board["id"], data, declared_mime="image/png", orig_name="p.png")
    node = {"id": ulid(), "type": "image", "pos": [0, 0], "title": card_title,
            "content": {"sha256": stored["sha256"]}}
    assert not apply_envelopes(c, board["id"], [{"opId": ulid(), "op": {"kind": "node.create", "node": node}}])["rejected"]
    return board, stored["sha256"]


def png_bytes(colour):
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(out, "PNG")
    return out.getvalue()


def test_a_file_lost_from_its_board_is_taken_from_another_boards_copy(dd, tmp_path):
    """DESIGN.md 49: files are named by their hash, so the same file kept by
    another board is the very same bytes."""
    from mindboard.assets import resolve

    same = png_bytes("#123456")
    a, sha = picture_board(dd, "Alpha", same, "Site plan")
    b, sha_b = picture_board(dd, "Beta", same, "Site plan again")
    assert sha == sha_b
    resolve(dd, conn_for(dd, a["id"]), a["id"], sha).unlink()       # lost from Alpha only

    out = create_backup(dd, tmp_path / "b.zip")
    assert out["missing"] == [] and out["recovered"] == 1
    fresh = DataDir(tmp_path / "elsewhere").ensure()
    restore_backup(fresh, out["path"])
    path = resolve(fresh, conn_for(fresh, a["id"]), a["id"], sha)
    assert path is not None and path.read_bytes() == same, "Alpha comes back whole"


def test_a_file_found_nowhere_is_named_never_passed_over(dd, tmp_path):
    from mindboard.assets import resolve
    from mindboard.bundle import check_files

    a, sha = picture_board(dd, "Alpha", png_bytes("#654321"), "Site plan")
    seed(dd, "Beta", 2)
    resolve(dd, conn_for(dd, a["id"]), a["id"], sha).unlink()

    check = check_files(dd)
    assert check["files"] == 1 and check["recovered"] == 0
    assert check["missing"] == [{"sha": sha, "board": a["id"], "board_title": "Alpha", "cards": ["Site plan"]}]

    out = create_backup(dd, tmp_path / "b.zip")
    assert len(out["boards"]) == 2, "the backup is still made - everything else is in it"
    assert out["missing"] == check["missing"]
    with zipfile.ZipFile(out["path"]) as z:
        note = z.read("MISSING-FILES.txt").decode("utf-8")
        import json
        manifest = json.loads(z.read("manifest.json"))
    assert note.startswith("1 file on your boards could not be found on this computer")
    assert 'board "Alpha": Site plan' in note and sha[:12] in note
    assert manifest["missing_files"][0]["cards"] == ["Site plan"]

    fresh = DataDir(tmp_path / "elsewhere").ensure()
    restored = restore_backup(fresh, out["path"])
    assert len(restored["restored"]) == 2 and not restored["failed"], "and it restores"


def test_a_whole_backup_says_nothing_is_missing(dd, tmp_path):
    picture_board(dd, "Alpha", png_bytes("#abcdef"), "Site plan")
    out = create_backup(dd, tmp_path / "b.zip")
    assert out["missing"] == [] and out["recovered"] == 0
    with zipfile.ZipFile(out["path"]) as z:
        assert "MISSING-FILES.txt" not in z.namelist()


def test_the_app_checks_before_backing_up(dd):
    from fastapi.testclient import TestClient

    from mindboard.app import create_app
    from mindboard.assets import resolve

    a, sha = picture_board(dd, "Alpha", png_bytes("#0f0f0f"), "Site plan")
    h = {"X-Mindboard-Token": "t"}
    with TestClient(create_app(dd, "t")) as c:
        assert c.get("/api/backup/check", headers=h).json() == {"files": 1, "missing": [], "recovered": 0}
        resolve(dd, conn_for(dd, a["id"]), a["id"], sha).unlink()
        got = c.get("/api/backup/check", headers=h).json()
        assert got["missing"][0]["cards"] == ["Site plan"]
        res = c.get("/api/backup", headers=h)
        assert res.status_code == 200 and res.headers["X-Mindboard-Missing-Files"] == "1"
