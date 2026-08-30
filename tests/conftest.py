import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mindboard.boards import close_all, create_board  # noqa: E402
from mindboard.paths import DataDir  # noqa: E402


@pytest.fixture()
def dd(tmp_path):
    d = DataDir(tmp_path / "data").ensure()
    yield d
    close_all()


@pytest.fixture()
def board(dd):
    return create_board(dd, "Test board", "a board for tests")
