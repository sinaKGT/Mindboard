"""SQLite access. One database per board, WAL mode, forward-only migrations."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Forward-only. executescript() implicitly commits, so the transaction has
    to live inside the script text rather than around the call."""
    conn.execute("CREATE TABLE IF NOT EXISTS _migration (name TEXT PRIMARY KEY, applied_at TEXT)")
    done = {r["name"] for r in conn.execute("SELECT name FROM _migration")}
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in done:
            continue
        name = sql_file.name.replace("'", "''")
        script = (
            "BEGIN;\n"
            + sql_file.read_text(encoding="utf-8")
            + f"\nINSERT INTO _migration (name, applied_at) VALUES ('{name}', '{now()}');\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def integrity_ok(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
