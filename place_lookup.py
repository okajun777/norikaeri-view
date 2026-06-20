"""目的地検索の統合パイプライン。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from geo_regions import area_hint_from_coords, nearest_prefecture


@dataclass
class LookupContext:
    near: tuple[float, float] | None = None
    area_hints: list[str] = field(default_factory=list)
    station_names: list[str] = field(default_factory=list)
    station_coords: list[tuple[float, float]] = field(default_factory=list)
    region_hints: list[str] = field(default_factory=list)


def build_region_hints(ctx: LookupContext | None) -> list[str]:
    if not ctx:
        return []
    hints: list[str] = []
    seen: set[str] = set()

    def add(h: str | None) -> None:
        if h and h not in seen:
            seen.add(h)
            hints.append(h)

    for pref in ctx.region_hints:
        add(pref)
    for name in ctx.station_names:
        add(name)
    for lat, lon in ctx.station_coords:
        add(area_hint_from_coords(lat, lon))
    if ctx.near:
        add(area_hint_from_coords(ctx.near[0], ctx.near[1]))
    return hints


def area_hint_from_context(ctx: LookupContext | None) -> str | None:
    hints = build_region_hints(ctx)
    return hints[0] if hints else None


def has_place_match(options: list[dict[str, Any]]) -> bool:
    return any(
        o.get("source") in ("registry", "known", "ai", "web", "nominatim", "overpass", "manual")
        for o in options
    )


def needs_research(name: str, options: list[dict[str, Any]]) -> bool:
    if has_place_match(options):
        return False
    if not options:
        return True
    from geocode import _is_poi_like, _is_station_query
    from place_name import looks_like_shop_name

    if _is_station_query(name):
        return False
    if looks_like_shop_name(name) or _is_poi_like(name):
        return not any(o.get("source") not in ("station",) for o in options)
    # 店名入力なのに駅しか候補がない
    if any(o.get("source") == "station" for o in options):
        return True
    return False


def sort_options(options: list[dict[str, Any]], near: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    from geo_regions import sort_by_proximity

    source_rank = {
        "registry": 0,
        "known": 1,
        "saved": 2,
        "ai": 3,
        "web": 4,
        "nominatim": 5,
        "overpass": 6,
        "manual": 7,
        "station": 9,
    }
    ranked = sorted(
        options,
        key=lambda o: (
            source_rank.get(o.get("source"), 8),
            o.get("kind") == "station",
        ),
    )
    if near:
        # 同一ソース帯内では旅程に近い候補を優先
        return sort_by_proximity(ranked, near)
    return ranked


def station_coords_from_names(names: list[str]) -> tuple[list[tuple[float, float]], list[str]]:
    from segment_candidates import get_station_records

    coords: list[tuple[float, float]] = []
    prefs: list[str] = []
    seen: set[tuple[float, float]] = set()
    for name in names:
        for rec in get_station_records(name)[:2]:
            lat, lon = rec.get("lat"), rec.get("lon")
            if lat is None or lon is None:
                continue
            key = (round(float(lat), 4), round(float(lon), 4))
            if key in seen:
                continue
            seen.add(key)
            coords.append((float(lat), float(lon)))
            if rec.get("prefecture"):
                prefs.append(rec["prefecture"])
            else:
                pref = nearest_prefecture(float(lat), float(lon))
                if pref:
                    prefs.append(pref)
    return coords, prefs
