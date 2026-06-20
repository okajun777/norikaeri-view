from typing import Any

from dest_parser import ParsedDestination, parse_destinations_text
from geocode import geocode_place


def enrich_destinations(parsed: list[ParsedDestination]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for dest in parsed:
        item: dict[str, Any] = {
            "name": dest.name,
            "arrive_time": dest.arrive_time,
            "depart_time": dest.depart_time,
            "memo": dest.memo,
        }
        geo = geocode_place(dest.name)
        if geo:
            item.update(
                {
                    "resolved_name": geo["name"],
                    "lat": geo["lat"],
                    "lon": geo["lon"],
                    "geo_source": geo["source"],
                }
            )
        enriched.append(item)
    return enriched


def parse_and_enrich_destinations_text(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    return enrich_destinations(parse_destinations_text(text))


def enrich_destinations_from_json(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = []
    for item in items:
        name = item.get("name") or item.get("place")
        if not name:
            continue
        parsed.append(
            ParsedDestination(
                name=name,
                arrive_time=item.get("arrive") or item.get("arrive_time"),
                depart_time=item.get("depart") or item.get("depart_time"),
                memo=item.get("memo"),
            )
        )
    return enrich_destinations(parsed)
