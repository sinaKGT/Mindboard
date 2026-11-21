"""The first page (DESIGN.md 57): groups of boards and the page's look, kept by
Mindboard and carried in backups."""

from fastapi.testclient import TestClient

from mindboard import home
from mindboard.app import create_app
from mindboard.boards import create_board
from mindboard.bundle import create_backup, restore_backup
from mindboard.paths import DataDir


def test_a_board_is_in_one_group_or_none(dd):
    a, b = create_board(dd, "RAG", ""), create_board(dd, "Agents", "")
    ai = home.add_group(dd, {"name": "  AI   work ", "colour": "#E8B864"})
    teach = home.add_group(dd, {"name": "Teaching", "colour": "not a colour"})
    assert ai["name"] == "AI work" and ai["colour"] == "#e8b864" and teach["colour"] == ""
    home.place(dd, a["id"], ai["id"]); home.place(dd, b["id"], ai["id"])
    home.place(dd, a["id"], teach["id"])
    groups = {g["name"]: g["boards"] for g in home.load(dd)["groups"]}
    assert groups == {"AI work": [b["id"]], "Teaching": [a["id"]]}, "moved, not copied"
    home.place(dd, a["id"], None)
    assert all(a["id"] not in g["boards"] for g in home.load(dd)["groups"])
    assert home.delete_group(dd, ai["id"]) and len(home.load(dd)["groups"]) == 1


def test_the_look_keeps_only_known_themes_and_patterns(dd):
    assert home.load(dd)["look"] == {"theme": "midnight", "pattern": "dots"}
    assert home.set_look(dd, {"theme": "ocean", "pattern": "dunes"}) == {"theme": "ocean", "pattern": "dunes"}
    assert home.set_look(dd, {"theme": "neon", "pattern": "<script>"}) == {"theme": "ocean", "pattern": "dunes"}


def test_the_home_api(dd):
    b = create_board(dd, "RAG", "")
    h = {"X-Mindboard-Token": "t"}
    with TestClient(create_app(dd, "t")) as c:
        g = c.post("/api/home/groups", headers=h, json={"name": "AI work"}).json()
        assert c.post("/api/home/groups", headers=h, json={"name": " "}).status_code == 400
        got = c.post("/api/home/place", headers=h, json={"board": b["id"], "group": g["id"]}).json()
        assert got["groups"][0]["boards"] == [b["id"]]
        assert c.post("/api/home/place", headers=h, json={"board": b["id"], "group": "nope"}).status_code == 400
        assert c.patch(f"/api/home/groups/{g['id']}", headers=h, json={"name": "AI"}).json()["name"] == "AI"
        assert c.patch("/api/home/look", headers=h, json={"pattern": "grid"}).json()["pattern"] == "grid"
        c.delete(f"/api/boards/{b['id']}", headers=h)
        assert c.get("/api/home", headers=h).json()["groups"][0]["boards"] == [], "a deleted board is not counted"


def test_groups_and_look_travel_in_a_backup(dd, tmp_path):
    b = create_board(dd, "RAG", "")
    g = home.add_group(dd, {"name": "AI work"})
    home.place(dd, b["id"], g["id"])
    home.set_look(dd, {"theme": "paper"})
    out = create_backup(dd, tmp_path / "b.zip")
    fresh = DataDir(tmp_path / "elsewhere").ensure()
    restore_backup(fresh, out["path"])
    assert home.load(fresh)["groups"][0]["boards"] == [b["id"]]
    restore_backup(fresh, out["path"])
    assert len(home.load(fresh)["groups"]) == 1, "restoring twice does not double them"
