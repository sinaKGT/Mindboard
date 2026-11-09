"""Listening on a Mac (DESIGN.md 50): whisper.cpp publishes no macOS program,
so a Mac listens through Homebrew's whisper-cpp - installed when the voice is
set up, found where Homebrew puts it. Tested here as a Mac would see it: the
platform says darwin, and Homebrew is a stand-in."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from mindboard import voice


@pytest.fixture()
def mac(monkeypatch, tmp_path):
    """A Mac with nothing set up yet: no whisper anywhere, the models' folder
    empty. Downloads are recorded, not made."""
    monkeypatch.setattr(sys, "platform", "darwin")
    found: dict[str, object] = {}
    monkeypatch.setattr(voice, "_on_path", lambda name: found.get(name))
    got = []
    monkeypatch.setattr(voice, "download", lambda pin, folder, progress=None: got.append(pin.name))
    return SimpleNamespace(found=found, downloads=got, home=tmp_path)


def test_a_mac_listens_through_homebrews_whisper(mac):
    assert voice.whisper_cli(mac.home) is None
    mac.found["whisper-cli"] = "/opt/homebrew/bin/whisper-cli"
    assert voice.whisper_cli(mac.home) == "/opt/homebrew/bin/whisper-cli"
    del mac.found["whisper-cli"]
    mac.found["whisper-cpp"] = "/usr/local/bin/whisper-cpp"
    assert voice.whisper_cli(mac.home) == "/usr/local/bin/whisper-cpp", "Homebrew's older name too"


def test_setting_up_installs_it_with_homebrew_then_the_pinned_models(mac, monkeypatch):
    mac.found["brew"] = "/opt/homebrew/bin/brew"
    ran = []

    def fake_run(cmd, **kw):
        ran.append((cmd, kw.get("env", {}).get("HOMEBREW_NO_AUTO_UPDATE")))
        mac.found["whisper-cli"] = "/opt/homebrew/bin/whisper-cli"     # what brew leaves behind
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    steps = []
    voice.install_listening(mac.home, progress=lambda name, done, total: steps.append((name, done)))
    assert ran == [(["/opt/homebrew/bin/brew", "install", "whisper-cpp"], "1")], "no slow brew update first"
    assert steps[0] == ("whisper-cpp (Homebrew)", 0) and steps[-1] == ("whisper-cpp (Homebrew)", 1)
    from mindboard import listen, turn
    assert mac.downloads == [voice.WHISPER_MODEL.name, turn.MODEL.name, listen.VAD.name], \
        "the same pinned models as on Windows and Linux - and no Windows program"

    ran.clear()
    voice.install_listening(mac.home)
    assert ran == [], "already there: Homebrew is not asked again"


def test_without_homebrew_it_says_what_to_do(mac):
    assert "needs Homebrew" in voice.setup_note()
    with pytest.raises(voice.VoiceError, match=r"brew\.sh.*brew install whisper-cpp"):
        voice.install_listening(mac.home)
    assert mac.downloads == [], "nothing downloaded for a voice that cannot listen"


def test_a_failed_homebrew_install_says_why(mac, monkeypatch):
    mac.found["brew"] = "/opt/homebrew/bin/brew"
    assert "installed with Homebrew" in voice.setup_note()
    monkeypatch.setattr(voice.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "==> Downloading\nError: No space left on device"))
    with pytest.raises(voice.VoiceError, match="No space left on device.*brew install whisper-cpp"):
        voice.install_listening(mac.home)


def test_homebrews_folders_are_searched_when_path_lacks_them(monkeypatch, tmp_path):
    """A double-clicked launcher may not have /opt/homebrew/bin on its PATH."""
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    (tmp_path / "whisper-cli").write_text("")
    monkeypatch.setattr(voice, "BREW_PREFIXES", ("/nowhere", str(tmp_path)))
    assert voice._on_path("whisper-cli") == tmp_path / "whisper-cli"
    assert voice._on_path("brew") is None


def test_other_computers_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    assert voice.setup_note() == ""
    monkeypatch.setattr(voice, "_on_path", lambda name: "/opt/homebrew/bin/whisper-cli")
    assert voice.whisper_cli(tmp_path) is None, "Homebrew is only looked for on a Mac"
