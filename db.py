import json
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

DB_PATH = Path(__file__).parent / "travel_calendar.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'Untitled Trip',
            itinerary_json TEXT,
            status TEXT NOT NULL DEFAULT 'planning',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (trip_id) REFERENCES trips(id)
        );

        CREATE INDEX IF NOT EXISTS idx_trips_chat ON trips(chat_id);
        CREATE INDEX IF NOT EXISTS idx_conv_trip ON conversations(trip_id);
    """)
    conn.commit()
    conn.close()


def create_trip(trip_id: str, chat_id: str, title: str = "Untitled Trip") -> dict:
    conn = get_conn()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO trips (id, chat_id, title, status, created_at, updated_at) VALUES (?, ?, ?, 'planning', ?, ?)",
        (trip_id, chat_id, title, now, now),
    )
    conn.commit()
    trip = dict(conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone())
    conn.close()
    return trip


def get_trip(trip_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_trips_for_chat(chat_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trips WHERE chat_id = ? ORDER BY updated_at DESC", (str(chat_id),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_trip(chat_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM trips WHERE chat_id = ? AND status = 'planning' ORDER BY updated_at DESC LIMIT 1",
        (str(chat_id),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_trip(trip_id: str, **kwargs):
    conn = get_conn()
    now = datetime.now(UTC).isoformat()
    sets = ["updated_at = ?"]
    vals = [now]
    for k, v in kwargs.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(trip_id)
    conn.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_trip(trip_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM conversations WHERE trip_id = ?", (trip_id,))
    conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    conn.commit()
    conn.close()


def add_message(trip_id: str, role: str, content: str):
    conn = get_conn()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO conversations (trip_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (trip_id, role, content, now),
    )
    conn.commit()
    conn.close()


def get_conversation(trip_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE trip_id = ? ORDER BY id ASC",
        (trip_id,),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]
