"""Where Mindboard keeps its data. Overridable everywhere via --data-dir."""

import os
import sys
from pathlib import Path

APP_NAME = "Mindboard"


def default_data_dir() -> Path:
    env = os.environ.get("MINDBOARD_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "mindboard"


class DataDir:
    """Resolves every path inside a data directory."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else default_data_dir()

    def ensure(self) -> "DataDir":
        self.boards.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def boards(self) -> Path:
        return self.root / "boards"

    @property
    def token_file(self) -> Path:
        return self.root / "token"

    def board_dir(self, board_id: str) -> Path:
        return self.boards / board_id

    def db_path(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "board.db"

    def assets_dir(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "assets"

    def thumbs_dir(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "thumbs"

    @property
    def opened(self) -> Path:
        """Copies of board files handed to another app to open - a PDF to your
        PDF reader. Outside the board folders on purpose: a reader holding one
        open must not stop a board from being deleted. Regenerable; `gc` clears it."""
        return self.root / "opened"

    def opened_dir(self, board_id: str) -> Path:
        return self.opened / board_id

    def asset_path(self, board_id: str, sha256: str, ext: str = "") -> Path:
        # sharded so a board with thousands of images stays browsable
        return self.assets_dir(board_id) / sha256[:2] / f"{sha256}{ext}"

    def list_board_ids(self) -> list[str]:
        if not self.boards.exists():
            return []
        return sorted(
            p.name for p in self.boards.iterdir() if p.is_dir() and (p / "board.db").exists()
        )
