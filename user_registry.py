from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def init_users_db(db_path: Path) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def record_user(db_path: Path, user, *, increment_requests: bool = False) -> None:
    if user is None:
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    increment = 1 if increment_requests else 0

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, last_name,
                first_seen, last_seen, request_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen = excluded.last_seen,
                request_count = bot_users.request_count + ?
            """,
            (
                int(user.id),
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                now,
                now,
                increment,
                increment,
            ),
        )


def users_summary(db_path: Path, *, limit: int = 100) -> tuple[int, int, list[dict]]:
    with _connect(db_path) as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS users, COALESCE(SUM(request_count), 0) AS requests
            FROM bot_users
            """
        ).fetchone()

        rows = connection.execute(
            """
            SELECT
                user_id, username, first_name, last_name,
                first_seen, last_seen, request_count
            FROM bot_users
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

    return (
        int(totals["users"]),
        int(totals["requests"]),
        [dict(row) for row in rows],
    )
