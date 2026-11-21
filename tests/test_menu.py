"""The menu the launcher opens: Mindboard's first page - where you choose your
boards or your experts - or straight to the experts; and the AI setup named
for what it does."""

from mindboard import menu


def test_the_menu_opens_the_first_page_or_the_experts():
    labels = {key: label for key, label, _ in menu.OPTIONS}
    assert labels["1"] == "Open Mindboard" and labels["2"] == "Talk to my experts"
    assert labels["6"] == "Set up the AI", "no 'Ask your board' beside the way in"
    assert labels["9"] == "Update"
    assert len(labels) == len(menu.OPTIONS), "every key once"


def test_each_way_in_opens_its_own_page(dd, monkeypatch):
    opened = []
    monkeypatch.setattr(menu, "running_instance", lambda port: True)
    monkeypatch.setattr(menu, "open_in_browser", lambda url, delay=0: opened.append(url))
    monkeypatch.setattr(menu, "_yes", lambda q, default=False: True)
    monkeypatch.setattr(menu, "_pause", lambda: None)
    menu._open_app(dd)
    menu._open_app(dd, "#/experts")
    assert "#/" not in opened[0], "option 1 opens the first page, not a list"
    assert opened[1].endswith("#/experts")


def test_reinstall_says_when_mindboard_is_still_running(dd, monkeypatch, capsys):
    said = []
    monkeypatch.setattr(menu, "running_instance", lambda port: True)
    monkeypatch.setattr(menu, "_pause", lambda: None)
    monkeypatch.setattr(menu.console, "print", lambda *a, **k: said.append(" ".join(map(str, a))))
    menu._reinstall(dd)
    text = " ".join(said)
    assert "still running" in text and "press Enter in that window" in text
