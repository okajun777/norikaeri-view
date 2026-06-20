import json
from datetime import date
from pathlib import Path
from typing import Any

from destination_candidates import preview_destinations
from destination_service import (
    enrich_destinations_from_json,
    parse_and_enrich_destinations_text,
)
from place_lookup import LookupContext, station_coords_from_names
from config import EXPORTS_DIR, IMPORTS_DIR
from db import create_trip, find_trip_for_date, get_trip, log_import, trip_exists_for_date, update_trip_meta
from line_geometry import get_segment_geometry, rebuild_trip_geometries, _geometry_for_transfer
from parser import ParsedSegment, parse_trip_text
from segment_candidates import preview_trip_segments
from station_lookup import lookup_station_pair


def _display_line_name(seg: dict[str, Any]) -> str | None:
    transfer_lines = seg.get("transfer_lines")
    if transfer_lines and len(transfer_lines) >= 2:
        return f"{transfer_lines[0]} → {transfer_lines[1]}"
    line_name = seg.get("line_name")
    if line_name and " → " in line_name:
        return line_name
    return line_name


def _primary_line_name(line_name: str | None) -> str | None:
    if not line_name:
        return None
    if " → " in line_name:
        return line_name.split(" → ", 1)[0].strip()
    return line_name


def enrich_confirmed_segment(seg: dict[str, Any]) -> dict[str, Any]:
    transfer_lines = seg.get("transfer_lines")
    if not transfer_lines and seg.get("line_name") and " → " in seg["line_name"]:
        parts = seg["line_name"].split(" → ", 1)
        transfer_lines = [parts[0].strip(), parts[1].strip()]

    item = {
        "from_station": seg["from_station"],
        "to_station": seg["to_station"],
        "depart_time": seg.get("depart_time"),
        "arrive_time": seg.get("arrive_time"),
        "line_name": _display_line_name(seg),
        "operator": seg.get("operator"),
        "resolved_from": seg.get("resolved_from"),
        "resolved_to": seg.get("resolved_to"),
        "from_lat": seg.get("from_lat"),
        "from_lon": seg.get("from_lon"),
        "to_lat": seg.get("to_lat"),
        "to_lon": seg.get("to_lon"),
        "alighted": seg.get("alighted", True),
    }
    if item["from_lat"] is not None and item["to_lat"] is not None:
        from_name = item["resolved_from"] or item["from_station"]
        to_name = item["resolved_to"] or item["to_station"]
        geometry = None
        if transfer_lines and len(transfer_lines) >= 2:
            geometry = _geometry_for_transfer(
                item["from_station"],
                item["to_station"],
                transfer_lines[0],
                transfer_lines[1],
            )
        if not geometry:
            geometry = get_segment_geometry(
                item["from_lat"],
                item["from_lon"],
                item["to_lat"],
                item["to_lon"],
                from_name=from_name,
                to_name=to_name,
                line_name=_primary_line_name(seg.get("line_name")),
            )
        if not geometry:
            geometry = get_segment_geometry(
                item["from_lat"],
                item["from_lon"],
                item["to_lat"],
                item["to_lon"],
                from_name=from_name,
                to_name=to_name,
                line_name=None,
            )
        item["geometry"] = geometry
    return item


def ensure_trip_geometries(trip: dict[str, Any]) -> dict[str, Any]:
    from db import update_segment_geometries

    segments = trip.get("segments") or []
    if not segments:
        return trip

    needs_rebuild = any(
        not seg.get("geometry") or len(seg.get("geometry") or []) < 2
        for seg in segments
        if seg.get("from_lat") is not None and seg.get("to_lat") is not None
    )
    if not needs_rebuild:
        return trip

    rebuilt = rebuild_trip_geometries(segments)
    update_segment_geometries(trip["id"], rebuilt)
    trip["segments"] = rebuilt
    return trip


def preview_trip(text: str, destinations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "segments": preview_trip_segments(text),
        "destinations": preview_destinations(destinations or []),
    }


def preview_segments(text: str) -> dict[str, Any]:
    return {"segments": preview_trip_segments(text)}


def _trip_lookup_context(trip_id: int | None) -> LookupContext | None:
    if not trip_id:
        return None
    trip = get_trip(trip_id)
    if not trip:
        return None
    coords: list[tuple[float, float]] = []
    stations: list[str] = []
    for seg in trip.get("segments") or []:
        if seg.get("from_station"):
            stations.append(seg["from_station"])
        if seg.get("to_station"):
            stations.append(seg["to_station"])
        if seg.get("from_lat") is not None:
            coords.append((seg["from_lat"], seg["from_lon"]))
        if seg.get("to_lat") is not None:
            coords.append((seg["to_lat"], seg["to_lon"]))
    for dest in trip.get("destinations") or []:
        if dest.get("lat") is not None:
            coords.append((dest["lat"], dest["lon"]))
    unique_stations = list(dict.fromkeys(stations))
    station_coords, prefs = station_coords_from_names(unique_stations)
    coords.extend(station_coords)
    near = None
    if coords:
        near = (
            sum(c[0] for c in coords) / len(coords),
            sum(c[1] for c in coords) / len(coords),
        )
    return LookupContext(
        near=near,
        area_hints=unique_stations,
        station_names=unique_stations,
        station_coords=station_coords,
        region_hints=prefs,
    )


def _trip_center(trip_id: int | None) -> tuple[float, float] | None:
    ctx = _trip_lookup_context(trip_id)
    return ctx.near if ctx else None


def preview_places(
    items: list[dict[str, Any]],
    *,
    trip_id: int | None = None,
) -> dict[str, Any]:
    ctx = _trip_lookup_context(trip_id)
    destinations = preview_destinations(items, context=ctx)
    from place_research import ai_lookup_available
    from place_registry import registry_stats

    return {
        "destinations": destinations,
        "lookup_info": {
            "ai_available": ai_lookup_available(),
            "registry_count": registry_stats()["count"],
            "trip_context": bool(ctx and ctx.station_names),
        },
    }


def save_trip_from_confirmed(
    trip_date: str,
    text: str,
    confirmed_segments: list[dict[str, Any]],
    *,
    title: str | None = None,
    source: str = "text",
    destinations_text: str | None = None,
    confirmed_destinations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not confirmed_segments:
        raise ValueError("区間が選択されていません。")

    segments = [enrich_confirmed_segment(seg) for seg in confirmed_segments]
    if confirmed_destinations is not None:
        destinations = confirmed_destinations
    elif destinations_text:
        destinations = parse_and_enrich_destinations_text(destinations_text)
    else:
        destinations = []
    trip_id = create_trip(
        trip_date,
        segments,
        title=title or f"{trip_date} の旅程",
        source=source,
        raw_text=text,
        destinations=destinations,
    )
    return {
        "trip_id": trip_id,
        "segment_count": len(segments),
        "destination_count": len(destinations),
    }


def save_confirmed_segments_for_trip(
    trip_id: int,
    text: str,
    confirmed_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    from db import replace_segments

    if not confirmed_segments:
        raise ValueError("区間が選択されていません。")
    segments = [enrich_confirmed_segment(seg) for seg in confirmed_segments]
    replace_segments(trip_id, segments, raw_text=text)
    return {"segment_count": len(segments)}


def save_trip_places_only(
    trip_date: str,
    confirmed_destinations: list[dict[str, Any]],
    *,
    title: str | None = None,
    source: str = "text",
) -> dict[str, Any]:
    if not confirmed_destinations:
        raise ValueError("行った場所が選択されていません。")
    trip_id = create_trip(
        trip_date,
        [],
        title=title or f"{trip_date} の記録",
        source=source,
        destinations=confirmed_destinations,
    )
    return {
        "trip_id": trip_id,
        "segment_count": 0,
        "destination_count": len(confirmed_destinations),
    }


def enrich_segments(parsed: list[ParsedSegment]) -> list[dict[str, Any]]:
    enriched = []
    prev_line: str | None = None
    for seg in parsed:
        from_info, to_info, line_name, operator = lookup_station_pair(
            seg.from_station, seg.to_station, prev_line=prev_line
        )
        item: dict[str, Any] = {
            "from_station": seg.from_station,
            "to_station": seg.to_station,
            "depart_time": seg.depart_time,
            "arrive_time": seg.arrive_time,
            "line_name": line_name,
            "operator": operator,
        }
        if from_info and to_info:
            item.update(
                {
                    "from_lat": from_info.lat,
                    "from_lon": from_info.lon,
                    "to_lat": to_info.lat,
                    "to_lon": to_info.lon,
                    "resolved_from": from_info.name,
                    "resolved_to": to_info.name,
                }
            )
            item["geometry"] = get_segment_geometry(
                from_info.lat,
                from_info.lon,
                to_info.lat,
                to_info.lon,
                from_name=seg.from_station,
                to_name=seg.to_station,
                line_name=line_name,
            )
        if line_name:
            prev_line = line_name
        enriched.append(item)
    return enriched


def save_trip_from_text(
    trip_date: str,
    text: str,
    *,
    title: str | None = None,
    source: str = "text",
    destinations_text: str | None = None,
) -> dict[str, Any]:
    parsed = parse_trip_text(text)
    if not parsed:
        raise ValueError("有効な区間が見つかりませんでした。入力形式を確認してください。")

    segments = enrich_segments(parsed)
    destinations = (
        parse_and_enrich_destinations_text(destinations_text)
        if destinations_text
        else []
    )
    trip_id = create_trip(
        trip_date,
        segments,
        title=title or f"{trip_date} の旅程",
        source=source,
        raw_text=text,
        destinations=destinations,
    )
    return {
        "trip_id": trip_id,
        "segment_count": len(segments),
        "destination_count": len(destinations),
    }


def save_trip_from_json(data: dict[str, Any], *, source: str = "json") -> dict[str, Any]:
    trip_date = data.get("date") or data.get("trip_date")
    if not trip_date:
        raise ValueError("date または trip_date が必要です")

    segments_in = data.get("segments", [])
    if not segments_in:
        raise ValueError("segments が空です")

    segments = []
    for seg in segments_in:
        from_name = seg["from"] if "from" in seg else seg["from_station"]
        to_name = seg["to"] if "to" in seg else seg["to_station"]
        from_info, to_info, line_name, operator = lookup_station_pair(from_name, to_name)
        item = {
            "from_station": from_name,
            "to_station": to_name,
            "depart_time": seg.get("depart") or seg.get("depart_time"),
            "arrive_time": seg.get("arrive") or seg.get("arrive_time"),
            "line_name": seg.get("line") or seg.get("line_name") or line_name,
            "operator": seg.get("operator") or operator,
        }
        if from_info and to_info:
            item.update(
                {
                    "from_lat": from_info.lat,
                    "from_lon": from_info.lon,
                    "to_lat": to_info.lat,
                    "to_lon": to_info.lon,
                }
            )
            item["geometry"] = get_segment_geometry(
                from_info.lat,
                from_info.lon,
                to_info.lat,
                to_info.lon,
                from_name=from_name,
                to_name=to_name,
                line_name=item.get("line_name"),
            )
        segments.append(item)

    destinations = []
    if data.get("destinations"):
        destinations = enrich_destinations_from_json(data["destinations"])
    elif data.get("destinations_text"):
        destinations = parse_and_enrich_destinations_text(data["destinations_text"])

    trip_id = create_trip(
        trip_date,
        segments,
        title=data.get("title") or f"{trip_date} の旅程",
        source=source,
        raw_text=json.dumps(data, ensure_ascii=False),
        destinations=destinations,
    )
    return {
        "trip_id": trip_id,
        "segment_count": len(segments),
        "destination_count": len(destinations),
    }


def export_trip_json(trip: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": trip["trip_date"],
        "title": trip.get("title"),
        "source": trip.get("source"),
        "segments": [
            {
                "from": s["from_station"],
                "to": s["to_station"],
                "depart": s.get("depart_time"),
                "arrive": s.get("arrive_time"),
                "line": s.get("line_name"),
                "operator": s.get("operator"),
            }
            for s in trip["segments"]
        ],
        "destinations": [
            {
                "name": d["name"],
                "arrive": d.get("arrive_time"),
                "depart": d.get("depart_time"),
                "memo": d.get("memo"),
                "lat": d.get("lat"),
                "lon": d.get("lon"),
            }
            for d in trip.get("destinations", [])
        ],
    }


def save_destinations_for_trip(trip_id: int, destinations_text: str) -> dict[str, Any]:
    from db import replace_destinations

    destinations = parse_and_enrich_destinations_text(destinations_text)
    replace_destinations(trip_id, destinations)
    return {"destination_count": len(destinations)}


def save_confirmed_destinations_for_trip(
    trip_id: int, confirmed: list[dict[str, Any]]
) -> dict[str, Any]:
    from db import replace_destinations

    replace_destinations(trip_id, confirmed)
    return {"destination_count": len(confirmed)}


def save_places_to_trip(
    trip_date: str,
    confirmed_destinations: list[dict[str, Any]],
    *,
    title: str | None = None,
    trip_id: int | None = None,
) -> dict[str, Any]:
    if not confirmed_destinations:
        raise ValueError("行った場所が選択されていません。")

    target_id = trip_id or find_trip_for_date(trip_date, title)
    if target_id:
        if title:
            update_trip_meta(target_id, title=title)
        save_confirmed_destinations_for_trip(target_id, confirmed_destinations)
        return {
            "trip_id": target_id,
            "segment_count": None,
            "destination_count": len(confirmed_destinations),
            "merged": True,
        }
    return {**save_trip_places_only(trip_date, confirmed_destinations, title=title), "merged": False}


def save_segments_to_trip(
    trip_date: str,
    text: str,
    confirmed_segments: list[dict[str, Any]],
    *,
    title: str | None = None,
    trip_id: int | None = None,
) -> dict[str, Any]:
    if not confirmed_segments:
        raise ValueError("区間が選択されていません。")

    target_id = trip_id or find_trip_for_date(trip_date, title)
    if target_id:
        if title:
            update_trip_meta(target_id, title=title)
        result = save_confirmed_segments_for_trip(target_id, text, confirmed_segments)
        return {
            "trip_id": target_id,
            "segment_count": result["segment_count"],
            "destination_count": None,
            "merged": True,
        }
    return {
        **save_trip_from_confirmed(
            trip_date,
            text,
            confirmed_segments,
            title=title,
            confirmed_destinations=[],
        ),
        "merged": False,
    }


def export_trip_to_file(trip: dict[str, Any]) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORTS_DIR / f"trip_{trip['trip_date']}_{trip['id']}.json"
    out.write_text(
        json.dumps(export_trip_json(trip), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def scan_imports_folder() -> list[dict[str, Any]]:
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(IMPORTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            trip_date = data.get("date") or data.get("trip_date")
            if trip_date and trip_exists_for_date(trip_date, "json", path.name):
                log_import(path.name, "json", "skipped", "既に取り込み済み")
                results.append({"file": path.name, "status": "skipped"})
                continue
            result = save_trip_from_json(data, source=f"import:{path.name}")
            log_import(path.name, "json", "ok", f"trip_id={result['trip_id']}")
            results.append({"file": path.name, "status": "ok", **result})
        except Exception as exc:
            log_import(path.name, "json", "error", str(exc))
            results.append({"file": path.name, "status": "error", "message": str(exc)})
    return results


def default_yesterday() -> str:
    return date.today().isoformat()
