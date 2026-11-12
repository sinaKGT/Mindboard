"""Getting the latest version (DESIGN.md 58): git pull --ff-only from GitHub,
stopped by changes of your own, then the installer."""

import shutil
import subprocess

import pytest

from mindboard import updater

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repos(tmp_path):
    """A 'GitHub' repository and a copy cloned from it, as a person has."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "v2")
    git(origin, "config", "user.email", "t@t"); git(origin, "config", "user.name", "t")
    (origin / "app.py").write_text("v1\n")
    git(origin, "add", "."); git(origin, "commit", "-qm", "first")
    git(tmp_path, "clone", "-q", str(origin), "copy")
    copy = tmp_path / "copy"
    git(copy, "config", "user.email", "t@t"); git(copy, "config", "user.name", "t")
    return origin, copy


def test_it_says_when_already_up_to_date(repos):
    _, copy = repos
    got = updater.pull(copy)
    assert got["ok"] and got["new"] == 0 and got["said"] == "already up to date"


def test_it_gets_new_changes_and_names_them(repos):
    origin, copy = repos
    (origin / "app.py").write_text("v2\n")
    git(origin, "commit", "-qam", "Answers show their pictures")
    got = updater.pull(copy)
    assert got["ok"] and got["new"] == 1 and got["titles"] == ["Answers show their pictures"]
    assert (copy / "app.py").read_text() == "v2\n"


def test_changes_of_your_own_stop_it_and_are_kept(repos):
    origin, copy = repos
    (origin / "app.py").write_text("v2\n")
    git(origin, "commit", "-qam", "upstream")
    (copy / "app.py").write_text("my edit\n")
    (copy / "notes.txt").write_text("untracked files do not count\n")
    got = updater.pull(copy)
    assert not got["ok"] and "app.py" in got["said"] and "git stash" in got["said"]
    assert (copy / "app.py").read_text() == "my edit\n", "nothing of yours is overwritten"


def test_a_copy_without_git_says_how_to_get_one(tmp_path):
    got = updater.pull(tmp_path)
    assert not got["ok"] and "git clone" in got["said"]
    assert not updater.pull(None)["ok"]
