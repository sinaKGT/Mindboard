"""Getting the latest version (DESIGN.md 58): `mindboard update`, and menu
option 9.

A copy cloned with git is brought up to date from GitHub - `git pull
--ff-only` on the branch it follows, so nothing is merged or overwritten - and
then reinstalled, which installs new packages and rebuilds the interface.
Changes of your own to Mindboard's files stop it, and it says which: they are
yours to keep (git stash) before updating. Your boards are never touched -
they live in the data folder, not in the code.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

GIT_TIMEOUT = 180


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT)


def pull(root: Optional[Path]) -> dict:
    """Bring the code up to date. {"ok", "said", "new": changes pulled,
    "titles": what they were, newest first}."""
    if root is None or not (root / ".git").exists():
        return {"ok": False, "new": 0, "titles": [],
                "said": "this copy was not downloaded with git, so it cannot update itself - "
                        "download it again with: git clone -b v2 https://github.com/sinaKGT/Mindboard.git"}
    if shutil.which("git") is None:
        return {"ok": False, "new": 0, "titles": [],
                "said": "git is not installed - on a Mac: brew install git; on Windows: winget install Git.Git"}
    try:
        fetched = _git(root, "fetch", "--quiet")
        if fetched.returncode != 0:
            return {"ok": False, "new": 0, "titles": [],
                    "said": "could not reach GitHub: " + (fetched.stderr.strip().splitlines() or ["no answer"])[-1]}
        mine = [ln[3:] for ln in _git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()]
        if mine:
            shown = ", ".join(mine[:5]) + (f" and {len(mine) - 5} more" if len(mine) > 5 else "")
            return {"ok": False, "new": 0, "titles": [],
                    "said": f"you have changed Mindboard's own files ({shown}) - keep them safe with "
                            "'git stash', then update again"}
        behind = _git(root, "rev-list", "--count", "HEAD..@{u}")
        if behind.returncode != 0:
            return {"ok": False, "new": 0, "titles": [],
                    "said": "this copy does not follow a branch on GitHub - run: git branch -u origin/v2"}
        new = int(behind.stdout.strip() or 0)
        if new == 0:
            return {"ok": True, "new": 0, "titles": [], "said": "already up to date"}
        titles = _git(root, "log", "--format=%s", "-n", "8", "HEAD..@{u}").stdout.splitlines()
        pulled = _git(root, "pull", "--ff-only", "--quiet")
        if pulled.returncode != 0:
            return {"ok": False, "new": 0, "titles": [],
                    "said": "could not update: " + (pulled.stderr.strip().splitlines() or ["git said no more"])[-1]}
        return {"ok": True, "new": new, "titles": titles,
                "said": f"got {new} new change{'' if new == 1 else 's'}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "new": 0, "titles": [], "said": "GitHub did not answer in time - try again"}
