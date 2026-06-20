"""ユーザーが確定した場所のレジストリ。検索の最優先ソース。"""

from __future__ import annotations

import re
from typing import Any

from db import get_conn, init_db, now_iso

from place_name import normalize_place_key, parse_place_name

SHOP_SUFFIXES = ("本店", "支店", "直営店", "総本店", "店")


def _row_to_option(row: dict[str, Any]) -> dict[str, Any]:
    resolved = row["resolved_name"] or row["name"]
    address = row.get("address") or ""
    label = f"{resolved}（履歴"
    if address:
        label += f": {address}"
    elif row.get("use_count", 0) > 1:
        label += f": {row['use_count']}回使用"
    label += "）"
    return {
        "label": label,
        "resolved_name": resolved,
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "source": "registry",
        "kind": row.get("kind") or "place",
        "address": address,
    }


def lookup_registry(name: str) -> list[dict[str, Any]]:
    init_db()
    parsed = parse_place_name(name)
    keys: set[str] = set()
    for k in parsed.lookup_keys():
        keys.add(k)
    keys.discard("")
    if not keys:
        return []

    placeholders = ",".join("?" * len(keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM place_registry
            WHERE query_key IN ({placeholders})
            ORDER BY use_count DESC, updated_at DESC
            """,
            tuple(keys),
        ).fetchall()

    seen: set[tuple[float, float]] = set()
    options: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        key = (round(item["lat"], 4), round(item["lon"], 4))
        if key in seen:
            continue
        seen.add(key)
        options.append(_row_to_option(item))
    return options


def register_place(
    name: str,
    *,
    resolved_name: str,
    lat: float,
    lon: float,
    address: str | None = None,
    geo_source: str | None = None,
    kind: str | None = None,
) -> None:
    name = (name or "").strip()
    if not name or lat is None or lon is None:
        return

    init_db()
    parsed = parse_place_name(name)
    keys = parsed.lookup_keys()
    if not keys:
        keys = [normalize_place_key(name)]
    ts = now_iso()

    with get_conn() as conn:
        for key in keys:
            existing = conn.execute(
                "SELECT id FROM place_registry WHERE query_key = ?",
                (key,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE place_registry SET
                        name = ?,
                        resolved_name = ?,
                        lat = ?, lon = ?,
                        address = COALESCE(?, address),
                        geo_source = COALESCE(?, geo_source),
                        kind = COALESCE(?, kind),
                        use_count = use_count + 1,
                        updated_at = ?
                    WHERE query_key = ?
                    """,
                    (
                        parsed.original or name,
                        resolved_name or name,
                        lat,
                        lon,
                        address,
                        geo_source,
                        kind,
                        ts,
                        key,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO place_registry (
                        query_key, name, resolved_name, lat, lon,
                        address, geo_source, kind, use_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        key,
                        parsed.original or name,
                        resolved_name or name,
                        lat,
                        lon,
                        address,
                        geo_source,
                        kind,
                        ts,
                        ts,
                    ),
                )


def register_places(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item.get("lat") is None or item.get("lon") is None:
            continue
        register_place(
            item.get("name") or item.get("resolved_name") or "",
            resolved_name=item.get("resolved_name") or item.get("name") or "",
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            address=item.get("address"),
            geo_source=item.get("geo_source"),
            kind=item.get("kind"),
        )


def bootstrap_from_destinations() -> int:
    """既存の destinations からレジストリを初期構築。"""
    init_db()
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM place_registry").fetchone()[0]
        if count > 0:
            return 0
        rows = conn.execute(
            """
            SELECT name, resolved_name, lat, lon, geo_source
            FROM destinations
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            """
        ).fetchall()
    register_places([dict(r) for r in rows])
    return len(rows)


def registry_stats() -> dict[str, int]:
    init_db()
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM place_registry").fetchone()[0]
    return {"count": count}
