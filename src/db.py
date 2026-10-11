"""SQLite storage, WAL mode, one connection per call."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    username      TEXT    NOT NULL,
    timezone      TEXT,
    cost_type_id  INTEGER,
    member_id     INTEGER,
    created_at    TEXT    NOT NULL,
    verified_at   TEXT,
    horizon_min   TEXT,
    horizon_max   TEXT,
    horizon_at    TEXT
);

CREATE TABLE IF NOT EXISTS schedulers (
    id            INTEGER PRIMARY KEY,
    facility_id   INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    scheduler_id  INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    cost_type_id  INTEGER,
    timezone      TEXT,
    min_interval  INTEGER NOT NULL DEFAULT 60,
    court_count   INTEGER NOT NULL DEFAULT 0,
    position      INTEGER NOT NULL DEFAULT 0,
    open_time     TEXT,
    close_time    TEXT,
    UNIQUE (facility_id, scheduler_id)
);

CREATE TABLE IF NOT EXISTS log (
    id           INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,
    facility_id  INTEGER,
    level        TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    message      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS log_facility_idx ON log(facility_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    config.ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


MIGRATIONS = [
    ("facilities", "horizon_min", "ALTER TABLE facilities ADD COLUMN horizon_min TEXT"),
    ("facilities", "horizon_max", "ALTER TABLE facilities ADD COLUMN horizon_max TEXT"),
    ("facilities", "horizon_at", "ALTER TABLE facilities ADD COLUMN horizon_at TEXT"),
    ("schedulers", "open_time", "ALTER TABLE schedulers ADD COLUMN open_time TEXT"),
    ("schedulers", "close_time", "ALTER TABLE schedulers ADD COLUMN close_time TEXT"),
]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
            if column not in cols:
                conn.execute(ddl)


def list_facilities() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM facilities ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_facility(facility_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM facilities WHERE id = ?", (facility_id,)).fetchone()
    return dict(row) if row else None


def get_facility_by_org(org_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM facilities WHERE org_id = ?", (org_id,)).fetchone()
    return dict(row) if row else None


def insert_facility(
    *,
    org_id: int,
    name: str,
    username: str,
    timezone: str | None,
    cost_type_id: int | None,
    member_id: int | None,
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO facilities
               (org_id, name, username, timezone, cost_type_id, member_id, created_at, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_id, name, username, timezone, cost_type_id, member_id, ts, ts),
        )
        row = conn.execute("SELECT * FROM facilities WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def mark_verified(facility_id: int, **fields: Any) -> None:
    allowed = {"name", "timezone", "cost_type_id", "member_id"}
    sets = ["verified_at = ?"]
    vals: list[Any] = [now_iso()]
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    vals.append(facility_id)
    with connect() as conn:
        conn.execute(f"UPDATE facilities SET {', '.join(sets)} WHERE id = ?", vals)


def update_facility(facility_id: int, **fields: Any) -> None:
    allowed = {"horizon_min", "horizon_max", "horizon_at"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(facility_id)
    with connect() as conn:
        conn.execute(f"UPDATE facilities SET {', '.join(sets)} WHERE id = ?", vals)


def delete_facility(facility_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))


def list_schedulers(facility_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedulers WHERE facility_id = ? ORDER BY position, id", (facility_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def replace_schedulers(facility_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.execute("BEGIN")
        keep = [int(i["scheduler_id"]) for i in items]
        if keep:
            conn.execute(
                f"DELETE FROM schedulers WHERE facility_id = ? AND scheduler_id NOT IN ({','.join('?' * len(keep))})",
                [facility_id, *keep],
            )
        else:
            conn.execute("DELETE FROM schedulers WHERE facility_id = ?", (facility_id,))
        for pos, it in enumerate(items):
            conn.execute(
                """INSERT INTO schedulers (facility_id, scheduler_id, name, cost_type_id, timezone, min_interval, position, open_time, close_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(facility_id, scheduler_id) DO UPDATE SET
                     name = excluded.name, cost_type_id = excluded.cost_type_id,
                     timezone = excluded.timezone, min_interval = excluded.min_interval,
                     position = excluded.position, open_time = excluded.open_time, close_time = excluded.close_time""",
                (facility_id, it["scheduler_id"], it["name"], it.get("cost_type_id"),
                 it.get("timezone"), it.get("min_interval", 60), pos, it.get("open_time"), it.get("close_time")),
            )
        conn.execute("COMMIT")
    return list_schedulers(facility_id)


def bump_court_count(scheduler_row_id: int, count: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE schedulers SET court_count = MAX(court_count, ?) WHERE id = ?", (count, scheduler_row_id)
        )


def add_log(level: str, source: str, message: str, facility_id: int | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO log (ts, facility_id, level, source, message) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="milliseconds"), facility_id, level, source, message),
        )
        return int(cur.lastrowid)


def read_log(after_id: int = 0, limit: int = 200, facility_id: int | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM log WHERE id > ?"
    args: list[Any] = [after_id]
    if facility_id is not None:
        sql += " AND (facility_id = ? OR facility_id IS NULL)"
        args.append(facility_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in reversed(rows)]
