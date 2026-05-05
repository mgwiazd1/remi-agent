"""Auto-composition kill switch. Checked before every auto-trigger compose call."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "remi_intelligence.db"


def is_auto_enabled() -> bool:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM bogwizard_state WHERE key='auto_compose_enabled'"
        ).fetchone()
        return bool(row and row[0] == "true")
    finally:
        conn.close()


def set_auto_enabled(enabled: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO bogwizard_state (key, value, updated_at)
               VALUES ('auto_compose_enabled', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            ("true" if enabled else "false",),
        )
        conn.commit()
    finally:
        conn.close()
