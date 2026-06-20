import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DB_PATH, DATA_DIR


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_date TEXT NOT NULL,
                title TEXT,
                source TEXT NOT NULL DEFAULT 'text',
                raw_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                from_station TEXT NOT NULL,
                to_station TEXT NOT NULL,
                depart_time TEXT,
                arrive_time TEXT,
                line_name TEXT,
                operator TEXT,
                from_lat REAL,
                from_lon REAL,
                to_lat REAL,
                to_lon REAL,
                geometry_json TEXT,
                alighted INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                name TEXT NOT NULL,
                resolved_name TEXT,
                arrive_time TEXT,
                depart_time TEXT,
                memo TEXT,
                lat REAL,
                lon REAL,
                geo_source TEXT,
                FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_trips_date ON trips(trip_date);
            CREATE INDEX IF NOT EXISTS idx_segments_trip ON segments(trip_id);
            CREATE INDEX IF NOT EXISTS idx_destinations_trip ON destinations(trip_id);

            CREATE TABLE IF NOT EXISTS place_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                resolved_name TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                address TEXT,
                geo_source TEXT,
                kind TEXT,
                use_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_place_registry_key ON place_registry(query_key);
            """
        )
        _ensure_column(conn, "segments", "alighted", "INTEGER NOT NULL DEFAULT 1")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_trip(
    trip_date: str,
    segments: list[dict[str, Any]],
    *,
    title: str | None = None,
    source: str = "text",
    raw_text: str | None = None,
    destinations: list[dict[str, Any]] | None = None,
) -> int:
    init_db()
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trips (trip_date, title, source, raw_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (trip_date, title, source, raw_text, ts, ts),
        )
        trip_id = cur.lastrowid
        for seq, seg in enumerate(segments, start=1):
            conn.execute(
                """
                INSERT INTO segments (
                    trip_id, seq, from_station, to_station,
                    depart_time, arrive_time, line_name, operator,
                    from_lat, from_lon, to_lat, to_lon, geometry_json, alighted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trip_id,
                    seq,
                    seg["from_station"],
                    seg["to_station"],
                    seg.get("depart_time"),
                    seg.get("arrive_time"),
                    seg.get("line_name"),
                    seg.get("operator"),
                    seg.get("from_lat"),
                    seg.get("from_lon"),
                    seg.get("to_lat"),
                    seg.get("to_lon"),
                    json.dumps(seg.get("geometry"), ensure_ascii=False)
                    if seg.get("geometry")
                    else None,
                    1 if seg.get("alighted", True) else 0,
                ),
            )
        _insert_destinations(conn, trip_id, destinations or [])
    if destinations:
        from place_registry import register_places

        register_places(destinations)
    return trip_id


def update_trip_meta(
    trip_id: int,
    *,
    title: str | None = None,
    trip_date: str | None = None,
) -> None:
    init_db()
    fields: list[str] = ["updated_at = ?"]
    params: list[Any] = [now_iso()]
    if title is not None:
        fields.append("title = ?")
        params.append(title or None)
    if trip_date is not None:
        fields.append("trip_date = ?")
        params.append(trip_date)
    params.append(trip_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE trips SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def find_trip_for_date(trip_date: str, title: str | None = None) -> int | None:
    with get_conn() as conn:
        if title:
            row = conn.execute(
                """
                SELECT id FROM trips
                WHERE trip_date = ? AND COALESCE(title, '') = ?
                ORDER BY id DESC LIMIT 1
                """,
                (trip_date, title),
            ).fetchone()
            if row:
                return int(row["id"])
        rows = conn.execute(
            "SELECT id FROM trips WHERE trip_date = ? ORDER BY id DESC",
            (trip_date,),
        ).fetchall()
        if len(rows) == 1:
            return int(rows[0]["id"])
        return None


def replace_segments(
    trip_id: int,
    segments: list[dict[str, Any]],
    *,
    raw_text: str | None = None,
) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM segments WHERE trip_id = ?", (trip_id,))
        for seq, seg in enumerate(segments, start=1):
            conn.execute(
                """
                INSERT INTO segments (
                    trip_id, seq, from_station, to_station,
                    depart_time, arrive_time, line_name, operator,
                    from_lat, from_lon, to_lat, to_lon, geometry_json, alighted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trip_id,
                    seq,
                    seg["from_station"],
                    seg["to_station"],
                    seg.get("depart_time"),
                    seg.get("arrive_time"),
                    seg.get("line_name"),
                    seg.get("operator"),
                    seg.get("from_lat"),
                    seg.get("from_lon"),
                    seg.get("to_lat"),
                    seg.get("to_lon"),
                    json.dumps(seg.get("geometry"), ensure_ascii=False)
                    if seg.get("geometry")
                    else None,
                    1 if seg.get("alighted", True) else 0,
                ),
            )
        updates = ["updated_at = ?"]
        params: list[Any] = [now_iso()]
        if raw_text is not None:
            updates.append("raw_text = ?")
            params.append(raw_text)
        params.append(trip_id)
        conn.execute(
            f"UPDATE trips SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def _insert_destinations(conn, trip_id: int, destinations: list[dict[str, Any]]) -> None:
    for seq, dest in enumerate(destinations, start=1):
        conn.execute(
            """
            INSERT INTO destinations (
                trip_id, seq, name, resolved_name,
                arrive_time, depart_time, memo,
                lat, lon, geo_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trip_id,
                seq,
                dest["name"],
                dest.get("resolved_name"),
                dest.get("arrive_time"),
                dest.get("depart_time"),
                dest.get("memo"),
                dest.get("lat"),
                dest.get("lon"),
                dest.get("geo_source"),
            ),
        )


def list_trips() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.*,
                   COUNT(DISTINCT s.id) AS segment_count,
                   COUNT(DISTINCT d.id) AS destination_count
            FROM trips t
            LEFT JOIN segments s ON s.trip_id = t.id
            LEFT JOIN destinations d ON d.trip_id = t.id
            GROUP BY t.id
            ORDER BY t.trip_date DESC, t.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_trip(trip_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if not trip:
            return None
        segments = conn.execute(
            "SELECT * FROM segments WHERE trip_id = ? ORDER BY seq",
            (trip_id,),
        ).fetchall()
        destinations = conn.execute(
            "SELECT * FROM destinations WHERE trip_id = ? ORDER BY seq",
            (trip_id,),
        ).fetchall()
        result = dict(trip)
        result["segments"] = []
        for seg in segments:
            item = dict(seg)
            if item.get("geometry_json"):
                item["geometry"] = json.loads(item["geometry_json"])
            else:
                item["geometry"] = None
            del item["geometry_json"]
            item["alighted"] = bool(item.get("alighted", 1))
            result["segments"].append(item)
        result["destinations"] = [dict(d) for d in destinations]
        return result


def delete_trip(trip_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        return cur.rowcount > 0


def log_import(filename: str, source_type: str, status: str, message: str = "") -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO import_log (filename, source_type, status, message, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (filename, source_type, status, message, now_iso()),
        )


def trip_exists_for_date(trip_date: str, source: str, raw_text: str | None = None) -> bool:
    with get_conn() as conn:
        if raw_text:
            row = conn.execute(
                "SELECT 1 FROM trips WHERE trip_date = ? AND source = ? AND raw_text = ?",
                (trip_date, source, raw_text),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM trips WHERE trip_date = ? AND source = ?",
                (trip_date, source),
            ).fetchone()
        return row is not None


def update_segment_geometries(trip_id: int, segments: list[dict[str, Any]]) -> None:
    init_db()
    with get_conn() as conn:
        for seg in segments:
            conn.execute(
                """
                UPDATE segments
                SET geometry_json = ?
                WHERE trip_id = ? AND seq = ?
                """,
                (
                    json.dumps(seg.get("geometry"), ensure_ascii=False)
                    if seg.get("geometry")
                    else None,
                    trip_id,
                    seg["seq"],
                ),
            )
        conn.execute(
            "UPDATE trips SET updated_at = ? WHERE id = ?",
            (now_iso(), trip_id),
        )


def replace_destinations(trip_id: int, destinations: list[dict[str, Any]]) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM destinations WHERE trip_id = ?", (trip_id,))
        _insert_destinations(conn, trip_id, destinations)
        conn.execute(
            "UPDATE trips SET updated_at = ? WHERE id = ?",
            (now_iso(), trip_id),
        )
    from place_registry import register_places

    register_places(destinations)
