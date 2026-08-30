"""Packaging guards.

Both of these exist because of one real bug: a `force-include` mapping
`src/mindboard/web` into the wheel made *editable* installs copy that single
folder into site-packages and write no .pth at all. `import mindboard` then
found an empty namespace package and every submodule vanished:

    ModuleNotFoundError: No module named 'mindboard.cli'
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:  # tomllib is 3.11+; we support 3.10
    import tomllib
except ModuleNotFoundError:
    tomllib = None

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_ships_the_built_interface(tmp_path):
    """The interface is gitignored, so it only reaches the wheel via
    `artifacts`. Without it the wheel installs but has no UI to serve."""
    pytest.importorskip("hatchling")
    if not (ROOT / "src" / "mindboard" / "web" / "index.html").exists():
        pytest.skip("interface not built in this checkout")

    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel",
                    "-d", str(tmp_path)], cwd=ROOT, check=True, capture_output=True)
    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "no wheel was produced"
    names = zipfile.ZipFile(wheels[0]).namelist()

    for required in ("mindboard/__init__.py", "mindboard/cli.py", "mindboard/menu.py",
                     "mindboard/runtime.py", "mindboard/web/index.html"):
        assert required in names, f"{required} is missing from the wheel"
    assert any(n.startswith("mindboard/web/assets/index-") for n in names), \
        "the built js/css bundles are missing from the wheel"
    assert any(n.endswith(".sql") for n in names), "migrations are missing from the wheel"
    shutil.rmtree(ROOT / "dist", ignore_errors=True)


def test_no_force_include_on_the_wheel_target():
    """force-include remaps paths, which breaks editable installs. Use
    `artifacts` to pull in gitignored files instead."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    reason = (
        "force-include on the wheel target makes editable installs copy that path "
        "into site-packages and skip the .pth, hiding every submodule. "
        "Use artifacts = [...] instead."
    )
    if tomllib is None:
        # comments in pyproject explain the trap by name; only the settings count
        settings = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#"))
        assert "force-include" not in settings, reason
        assert "artifacts" in settings, "the built interface must be listed in artifacts"
        return
    wheel = tomllib.loads(text)["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "force-include" not in wheel, reason
    assert any("web" in a for a in wheel.get("artifacts", [])), \
        "the built interface must be listed in artifacts or it will not ship"
