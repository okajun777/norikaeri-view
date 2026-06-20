from typing import Any

from geocode import _haversine_m
from place_lookup import LookupContext

def _find_existing_option(options: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any] | None:
    lat, lon = item.get("lat"), item.get("lon")
    if lat is None or lon is None:
        return None
    for opt in options:
        if _haversine_m(lat, lon, opt["lat"], opt["lon"]) < 120:
            return opt
    return None


def _prepend_saved_option(options: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    label_name = item.get("resolved_name") or item.get("name", "")
    saved = {
        "label": f"{label_name}（保存済み）",
        "resolved_name": label_name,
        "lat": item["lat"],
        "lon": item["lon"],
        "source": item.get("geo_source") or "saved",
        "kind": "saved",
    }
    return [saved, *options]


def preview_destinations(
    items: list[dict[str, Any]],
    *,
    near: tuple[float, float] | None = None,
    context: LookupContext | None = None,
) -> list[dict[str, Any]]:
    from geocode import assign_option_ids, pick_recommended_place_option, search_place_candidates

    if context is None and near is not None:
        context = LookupContext(near=near)
    elif context is not None and near is not None and context.near is None:
        context.near = near

    preview: list[dict[str, Any]] = []
    for seq, item in enumerate(items, start=1):
        name = item.get("name", "").strip()
        if not name:
            continue
        options = search_place_candidates(name, near=context.near if context else near, context=context)
        existing = _find_existing_option(options, item)
        if item.get("lat") is not None and item.get("lon") is not None and not existing:
            options = _prepend_saved_option(options, item)

        assign_option_ids(options)

        if existing:
            for opt in options:
                if _haversine_m(item["lat"], item["lon"], opt["lat"], opt["lon"]) < 120:
                    recommended = opt["id"]
                    break
            else:
                recommended = pick_recommended_place_option(
                    options,
                    query_name=name,
                    near=context.near if context else None,
                    anchor_coords=(context.station_coords if context else None) or None,
                )
        else:
            recommended = pick_recommended_place_option(
                options,
                query_name=name,
                near=context.near if context else None,
                anchor_coords=(context.station_coords if context else None) or None,
            )

        preview.append(
            {
                "seq": seq,
                "name": name,
                "arrive_time": item.get("arrive_time"),
                "depart_time": item.get("depart_time"),
                "memo": item.get("memo"),
                "recommended_option_id": recommended,
                "options": options,
            }
        )
    return preview


def confirmed_from_preview(preview_item: dict, option: dict) -> dict[str, Any]:
    return {
        "name": preview_item["name"],
        "arrive_time": preview_item.get("arrive_time"),
        "depart_time": preview_item.get("depart_time"),
        "memo": preview_item.get("memo"),
        "resolved_name": option["resolved_name"],
        "lat": option["lat"],
        "lon": option["lon"],
        "geo_source": option["source"],
        "address": option.get("address"),
        "kind": option.get("kind"),
    }
