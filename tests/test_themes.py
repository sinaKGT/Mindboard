"""Your own themes (DESIGN.md 52): named, saved once, offered on every board,
kept to known colours, and carried in backups."""

import pytest
from fastapi.testclient import TestClient

from mindboard import themes
from mindboard.app import create_app
from mindboard.boards import create_board
from mindboard.bundle import create_backup, restore_backup
from mindboard.paths import DataDir

GOLD = {"--mb-bg": "#120E0A", "--mb-accent": "#e8b864"}


def test_a_theme_is_a_name_a_base_and_its_colours(dd):
    t = themes.save(dd, {"name": "  Desert   night ", "base": "ink", "palette": {
        **GOLD, "--mb-text": "not a colour", "--evil": "#ffffff", "--mb-grid": "#123"}})
    assert t["name"] == "Desert night" and t["base"] == "ink"
    assert t["palette"] == {"--mb-bg": "#120e0a", "--mb-accent": "#e8b864"}, "only known keys, as #rrggbb"
    assert themes.load(dd) == [t]
    assert themes.save(dd, {"name": "x", "base": "nowhere"})["base"] == "midnight"


def test_saving_under_the_same_name_updates_it(dd):
    a = themes.save(dd, {"name": "Desert night", "base": "ink", "palette": GOLD})
    b = themes.save(dd, {"name": "desert NIGHT", "base": "ember", "palette": {"--mb-accent": "#ff0000"}})
    assert b["id"] == a["id"] and len(themes.load(dd)) == 1, "one theme, not two"
    assert themes.load(dd)[0]["base"] == "ember" and themes.load(dd)[0]["palette"] == {"--mb-accent": "#ff0000"}


def test_a_theme_needs_a_name_and_there_is_a_limit(dd, monkeypatch):
    with pytest.raises(themes.ThemeError, match="name"):
        themes.save(dd, {"name": "   "})
    monkeypatch.setattr(themes, "MOST", 2)
    themes.save(dd, {"name": "one"}); themes.save(dd, {"name": "two"})
    with pytest.raises(themes.ThemeError, match="delete one first"):
        themes.save(dd, {"name": "three"})
    assert themes.save(dd, {"name": "TWO"})["name"] == "TWO", "updating one is still allowed"


def test_the_themes_api(dd):
    h = {"X-Mindboard-Token": "t"}
    with TestClient(create_app(dd, "t")) as c:
        assert c.get("/api/themes", headers=h).json() == {"themes": []}
        saved = c.post("/api/themes", headers=h, json={"name": "Desert night", "base": "ink", "palette": GOLD}).json()
        assert c.get("/api/themes", headers=h).json()["themes"][0]["id"] == saved["id"]
        assert c.post("/api/themes", headers=h, json={"name": ""}).status_code == 400
        assert c.post("/api/themes", json={"name": "no token"}).status_code == 401
        assert c.delete(f"/api/themes/{saved['id']}", headers=h).json() == {"deleted": True}
        assert c.delete(f"/api/themes/{saved['id']}", headers=h).json() == {"deleted": False}


def test_your_themes_travel_in_a_backup(dd, tmp_path):
    create_board(dd, "A board", "")
    t = themes.save(dd, {"name": "Desert night", "base": "ink", "palette": GOLD})
    out = create_backup(dd, tmp_path / "b.zip")
    fresh = DataDir(tmp_path / "elsewhere").ensure()
    restore_backup(fresh, out["path"])
    assert themes.load(fresh) == [t]
    restore_backup(fresh, out["path"])
    assert len(themes.load(fresh)) == 1, "restoring twice does not double them"
