import json
import math
import re
import time
from typing import Any

import requests

from config import DATA_DIR, KNOWN_PLACES_PATH, OVERPASS_URL
from geo_regions import coords_plausible, in_japan, region_search_suffixes, sort_by_proximity
from segment_candidates import get_station_records
from station_lookup import collect_station_records, lookup_station

GEOCODE_CACHE_PATH = DATA_DIR / "geocode_cache.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_request = 0.0

POI_KEYWORDS = (
    "公園", "パーク", "美術館", "博物館", "神社", "寺", "ランド", "動物",
    "ショッピング", "モール", "餃子", "レストラン", "カフェ", "展望", "県庁",
    "水族", "温泉", "城", "タワー", "ホール", "スタジアム", "シーキャンドル", "キャンドル",
)
# ひらがな・カタカナ表記 → 検索用の漢字地名
HIRAGANA_PLACE_ALIASES: dict[str, list[str]] = {
    "えのしま": ["江の島", "江ノ島"],
    "えのじま": ["江の島", "江ノ島"],
    "かまくら": ["鎌倉"],
    "ふじさわ": ["藤沢"],
    "はまおう": ["浜名湖"],
}
KATAKANA_PLACE_ALIASES: dict[str, list[str]] = {
    "エノシマ": ["江の島", "江ノ島"],
}
# 目的地検索用（駅名ではなく場所・施設名の別名）
EXTRA_SEARCH_ALIASES: dict[str, list[str]] = {
    "宇都宮": ["宇都宮市"],
}
KNOWN_PLACE_SEARCH_ALIASES: dict[str, list[str]] = {
    "栃木県庁展望室": ["栃木県庁 宇都宮", "栃木県庁"],
    "宇都宮餃子（香蘭）": ["宇都宮 香蘭", "餃子 香蘭 宇都宮"],
    "宇都宮餃子(香蘭)": ["宇都宮 香蘭", "餃子 香蘭 宇都宮"],
    "江の島": ["江ノ島 神奈川", "片瀬江ノ島", "湘南江の島"],
    "江ノ島": ["江の島 神奈川", "片瀬江ノ島"],
    "湘南": ["湘南 神奈川", "藤沢 神奈川"],
    "鎌倉": ["鎌倉市 神奈川"],
    "えのしまシーキャンドル": [
        "江の島シーキャンドル", "江ノ島 シーキャンドル", "Enoshima Sea Candle",
    ],
    "江の島シーキャンドル": ["江ノ島 シーキャンドル", "Enoshima Sea Candle"],
    "湘南のシーキャンドル": ["江の島シーキャンドル", "江ノ島 シーキャンドル"],
}

PREFERRED_PLACE_KINDS = {
    "townhall", "restaurant", "garden", "viewpoint", "museum",
    "attraction", "cafe", "fast_food", "theme_park", "tower", "lighthouse",
}

KIND_LABELS_JA = {
    "garden": "庭園",
    "townhall": "庁舎",
    "restaurant": "レストラン",
    "viewpoint": "展望",
    "museum": "博物館",
    "attraction": "観光",
    "cafe": "カフェ",
    "fast_food": "ファストフード",
    "theme_park": "テーマパーク",
    "tower": "タワー",
    "lighthouse": "灯台",
    "station": "駅",
    "stop": "停留所",
    "saved": "保存済み",
    "known": "登録済み",
    "registry": "履歴",
    "manual": "手動指定",
    "overpass": "地図データ",
    "ai": "AI調査",
    "web": "Web調査",
}


SHOP_SUFFIXES = ("本店", "支店", "直営店", "総本店")

_known_places_index: dict[str, dict] | None = None


def _kind_label(kind: str) -> str:
    return KIND_LABELS_JA.get(kind, kind)


def _load_cache() -> dict[str, dict]:
    if GEOCODE_CACHE_PATH.exists():
        return json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    GEOCODE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOCODE_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _nominatim_search(
    name: str,
    limit: int = 5,
    *,
    viewbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    headers = {"User-Agent": "norikaeri-kiroku/1.0 (local trip recorder)"}
    params: dict[str, Any] = {
        "q": name,
        "format": "json",
        "limit": limit,
        "countrycodes": "jp",
        "accept-language": "ja",
    }
    if viewbox:
        west, south, east, north = viewbox
        params["viewbox"] = f"{west},{north},{east},{south}"
        # viewbox は優先度ヒントのみ（bounded=1 だと全国検索で候補が消える）
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
        _last_request = time.time()
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _normalize_place_key(name: str) -> str:
    key = re.sub(r"\s", "", name.strip())
    for suffix in SHOP_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            key = key[: -len(suffix)]
    return key


def _load_known_places_index() -> dict[str, dict]:
    global _known_places_index
    if _known_places_index is not None:
        return _known_places_index
    from place_name import normalize_place_key as pn_normalize

    index: dict[str, dict] = {}
    if KNOWN_PLACES_PATH.exists():
        for item in json.loads(KNOWN_PLACES_PATH.read_text(encoding="utf-8")):
            for key in item.get("keys", []):
                index[_normalize_place_key(key)] = item
                index[pn_normalize(key)] = item
                index[re.sub(r"[\s　]+", "", key.strip())] = item
    _known_places_index = index
    return index


def _known_place_matches(name: str) -> list[dict]:
    from place_name import parse_place_name

    index = _load_known_places_index()
    parsed = parse_place_name(name)
    keys: set[str] = set(parsed.lookup_keys())
    seen: set[str] = set()
    matches: list[dict] = []
    for key in keys:
        item = index.get(key)
        if not item:
            continue
        item_id = item.get("resolved_name", key)
        if item_id in seen:
            continue
        seen.add(item_id)
        matches.append(item)
    return matches


def _shop_search_variants(name: str, region_hints: list[str] | None = None) -> list[str]:
    base = name.strip()
    core = base
    for suffix in SHOP_SUFFIXES:
        if core.endswith(suffix) and len(core) > len(suffix) + 1:
            core = core[: -len(suffix)].strip()
    variants: list[str] = []
    if core and core != base:
        variants.append(core)
    search_core = core or base
    for region in region_search_suffixes(region_hints):
        variants.append(f"{search_core} {region}")
    return variants


def _looks_like_shop(name: str) -> bool:
    from place_name import looks_like_shop_name

    if _is_poi_like(name):
        return True
    return looks_like_shop_name(name)


def _viewbox_around(lat: float, lon: float, delta: float = 0.35) -> tuple[float, float, float, float]:
    return (lon - delta, lat - delta, lon + delta, lat + delta)


def _viewbox_for_context(
    near: tuple[float, float] | None,
    anchor_coords: list[tuple[float, float]] | None,
) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    if near:
        points.append(near)
    if anchor_coords:
        points.extend(anchor_coords)
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)
    delta = max(0.35, lat_span * 0.6 + 0.2, lon_span * 0.6 + 0.2)
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    return _viewbox_around(center_lat, center_lon, delta=min(delta, 3.0))


def _overpass_search_places(name: str, *, bbox: tuple[float, float, float, float]) -> list[dict]:
    core = _normalize_place_key(name)
    if len(core) < 2:
        return []
    south, west, north, east = bbox
    pattern = re.escape(core)
    query = f"""
    [out:json][timeout:15];
    (
      node["name"~"{pattern}",i]({south},{west},{north},{east});
      way["name"~"{pattern}",i]({south},{west},{north},{east});
      node["name:ja"~"{pattern}",i]({south},{west},{north},{east});
      way["name:ja"~"{pattern}",i]({south},{west},{north},{east});
    );
    out center 5;
    """
    headers = {"User-Agent": "norikaeri-kiroku/1.0 (local trip recorder)"}
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=headers,
            timeout=18,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    results: list[dict] = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        label_name = tags.get("name:ja") or tags.get("name") or name
        kind = tags.get("amenity") or tags.get("tourism") or tags.get("shop") or "place"
        results.append(
            {
                "label": f"{label_name}（{_kind_label(kind)}）",
                "resolved_name": label_name,
                "lat": float(lat),
                "lon": float(lon),
                "source": "overpass",
                "kind": kind,
            }
        )
    return results


def _coord_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def _is_poi_like(name: str) -> bool:
    return any(k in name for k in POI_KEYWORDS)


def _is_station_query(name: str) -> bool:
    n = name.strip()
    return n.endswith("駅") or n.endswith("駅)")


def _script_place_variants(name: str) -> list[str]:
    variants: list[str] = []
    for hira, kanji_list in HIRAGANA_PLACE_ALIASES.items():
        if hira not in name:
            continue
        for kanji in kanji_list:
            variants.append(name.replace(hira, kanji, 1))
    upper = name.upper()
    for kata, kanji_list in KATAKANA_PLACE_ALIASES.items():
        idx = upper.find(kata)
        if idx < 0:
            continue
        original = name[idx : idx + len(kata)]
        for kanji in kanji_list:
            variants.append(name.replace(original, kanji, 1))
    return variants


def _area_station_queries(name: str) -> list[str]:
    queries: list[str] = []
    for hira, kanji_list in HIRAGANA_PLACE_ALIASES.items():
        if hira in name:
            queries.extend(kanji_list)
    upper = name.upper()
    for kata, kanji_list in KATAKANA_PLACE_ALIASES.items():
        if kata in upper:
            queries.extend(kanji_list)
    seen: set[str] = set()
    ordered: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            ordered.append(q)
    return ordered


def _search_variants(name: str, region_hints: list[str] | None = None) -> list[str]:
    variants: list[str] = [name.strip()]
    variants.extend(EXTRA_SEARCH_ALIASES.get(name, []))
    variants.extend(KNOWN_PLACE_SEARCH_ALIASES.get(name, []))
    variants.extend(_script_place_variants(name))
    variants.extend(_shop_search_variants(name, region_hints))
    for match in re.findall(r"[（(]([^）)]+)[）)]", name):
        part = match.strip()
        if part:
            variants.append(part)
            base = re.split(r"[（(]", name, maxsplit=1)[0].strip()
            if base:
                variants.append(f"{part} {base}")
                variants.append(f"{base} {part}")
    seen: set[str] = set()
    ordered: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered


def _add_known_place_options(name: str, add_option) -> None:
    for item in _known_place_matches(name):
        label_name = item["resolved_name"]
        address = item.get("address")
        label = f"{label_name}（登録済み）"
        if address:
            label = f"{label_name}（{address}）"
        add_option(
            label=label,
            resolved_name=label_name,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            source="known",
            kind=item.get("kind") or "place",
        )
        for station_name in item.get("near_stations") or []:
            for rec in get_station_records(station_name):
                line = rec.get("line_name") or ""
                station_label = (
                    f"最寄り駅: {rec['name']}（{line}）" if line else f"最寄り駅: {rec['name']}"
                )
                add_option(
                    label=station_label,
                    resolved_name=rec["name"],
                    lat=rec["lat"],
                    lon=rec["lon"],
                    source="station",
                    kind="station",
                )
                break


def _add_nominatim_options(
    name: str,
    add_option,
    *,
    limit: int = 5,
    viewbox: tuple[float, float, float, float] | None = None,
) -> None:
    for item in _nominatim_search(name, limit=limit, viewbox=viewbox):
        display = item.get("display_name", name)
        short = display.split(",")[0]
        kind = item.get("type") or item.get("class") or "place"
        add_option(
            label=f"{short}（{_kind_label(kind)}）",
            resolved_name=short,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            source="nominatim",
            kind=kind,
        )


def _add_station_options(name: str, add_option, *, reference_only: bool) -> None:
    prefix = "最寄り駅" if reference_only else "駅"
    search_names = [name] + EXTRA_SEARCH_ALIASES.get(name, [])
    for q in search_names:
        for rec in get_station_records(q):
            line = rec.get("line_name") or ""
            label = f"{prefix}: {rec['name']}（{line}）" if line else f"{prefix}: {rec['name']}"
            add_option(
                label=label,
                resolved_name=rec["name"],
                lat=rec["lat"],
                lon=rec["lon"],
                source="station",
                kind="station",
            )


def _in_japan(lat: float, lon: float) -> bool:
    return in_japan(lat, lon)


def _valid_coords(
    lat: float,
    lon: float,
    *,
    near: tuple[float, float] | None = None,
    anchor_coords: list[tuple[float, float]] | None = None,
) -> bool:
    return coords_plausible(lat, lon, near=near, anchor_coords=anchor_coords)


def assign_option_ids(options: list[dict[str, Any]]) -> None:
    for i, opt in enumerate(options):
        opt["id"] = f"dopt_{i}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1, p2 = lat1 * math.pi / 180, lat2 * math.pi / 180
    dp = (lat2 - lat1) * math.pi / 180
    dl = (lon2 - lon1) * math.pi / 180
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def pick_recommended_place_option(
    options: list[dict[str, Any]],
    *,
    query_name: str = "",
    near: tuple[float, float] | None = None,
    anchor_coords: list[tuple[float, float]] | None = None,
) -> str | None:
    if not options:
        return None

    def valid(o: dict) -> bool:
        return _valid_coords(o["lat"], o["lon"], near=near, anchor_coords=anchor_coords)

    def by_source(sources: tuple[str, ...]) -> list[dict]:
        items = [o for o in options if o.get("source") in sources and valid(o)]
        return sort_by_proximity(items, near) if near else items

    for sources in (("registry",), ("known",), ("saved",)):
        ranked = by_source(sources)
        if ranked:
            return ranked[0]["id"]

    researched = by_source(("ai", "web"))
    if researched:
        if query_name and _looks_like_shop(query_name):
            return researched[0]["id"]
        if not collect_station_records(query_name):
            return researched[0]["id"]

    if query_name and _looks_like_shop(query_name):
        shops = [
            o for o in options
            if o.get("source") != "station"
            and o.get("kind") in PREFERRED_PLACE_KINDS
            and valid(o)
        ]
        if shops:
            best = sort_by_proximity(shops, near)[0] if near else shops[0]
            return best["id"]

    if query_name and collect_station_records(query_name):
        stations = by_source(("station",))
        if stations:
            return stations[0]["id"]

    places = [o for o in options if o.get("source") != "station" and valid(o)]
    stations = [o for o in options if o.get("source") == "station" and valid(o)]
    pool = places or stations or [o for o in options if valid(o)] or options

    if "餃子" in query_name or "香蘭" in query_name:
        restaurants = [o for o in pool if o.get("kind") == "restaurant"]
        if len(restaurants) > 1 and near:
            return sort_by_proximity(restaurants, near)[0]["id"]

    for opt in pool:
        if opt.get("kind") in PREFERRED_PLACE_KINDS:
            return opt["id"]
    if pool:
        return (sort_by_proximity(pool, near)[0] if near else pool[0])["id"]
    return options[0]["id"]


def search_place_candidates(
    name: str,
    *,
    near: tuple[float, float] | None = None,
    context: Any | None = None,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen_coords: set[tuple[float, float]] = set()

    from place_lookup import LookupContext, build_region_hints, needs_research, sort_options
    from place_name import parse_place_name
    from place_registry import lookup_registry

    parsed = parse_place_name(name)
    search_name = parsed.core or name

    if context is None and near is not None:
        context = LookupContext(near=near)
    elif context is not None and not isinstance(context, LookupContext):
        context = LookupContext(
            near=getattr(context, "near", None) or near,
            area_hints=list(getattr(context, "area_hints", []) or []),
            station_names=list(getattr(context, "station_names", []) or []),
            station_coords=list(getattr(context, "station_coords", []) or []),
            region_hints=list(getattr(context, "region_hints", []) or []),
        )

    if parsed.area_hint and context:
        if parsed.area_hint not in context.region_hints:
            context.region_hints.insert(0, parsed.area_hint)
        if parsed.area_hint not in context.station_names:
            context.station_names.insert(0, parsed.area_hint)

    ctx_near = context.near if context else near
    anchor_coords = list(context.station_coords) if context and context.station_coords else None
    region_hints = build_region_hints(context)
    if parsed.area_hint and parsed.area_hint not in region_hints:
        region_hints.insert(0, parsed.area_hint)

    def add_option(
        *,
        label: str,
        resolved_name: str,
        lat: float,
        lon: float,
        source: str,
        kind: str,
    ) -> None:
        if source not in ("registry", "known", "saved", "manual"):
            if not in_japan(lat, lon):
                return
            if ctx_near or anchor_coords:
                if not _valid_coords(lat, lon, near=ctx_near, anchor_coords=anchor_coords):
                    return
        key = _coord_key(lat, lon)
        if key in seen_coords:
            return
        seen_coords.add(key)
        options.append(
            {
                "label": label,
                "resolved_name": resolved_name,
                "lat": lat,
                "lon": lon,
                "source": source,
                "kind": kind,
            }
        )

    for lookup_name in parsed.lookup_names():
        for item in lookup_registry(lookup_name):
            add_option(
                label=item["label"],
                resolved_name=item["resolved_name"],
                lat=item["lat"],
                lon=item["lon"],
                source=item["source"],
                kind=item["kind"],
            )
            break
        if any(o.get("source") == "registry" for o in options):
            break

    _add_known_place_options(search_name, add_option)
    if search_name != name:
        _add_known_place_options(name, add_option)
    has_known = any(o.get("source") in ("known", "registry") for o in options)

    search_names = _search_variants(search_name, region_hints)
    priority_queries = list(KNOWN_PLACE_SEARCH_ALIASES.get(search_name, []))
    priority_queries.extend(KNOWN_PLACE_SEARCH_ALIASES.get(name, []))
    other_queries = [q for q in search_names if q not in priority_queries]
    viewbox = _viewbox_for_context(ctx_near, anchor_coords)

    if not has_known:
        for q in priority_queries:
            _add_nominatim_options(q, add_option, limit=5, viewbox=viewbox)
        for q in other_queries[:8]:
            _add_nominatim_options(
                q,
                add_option,
                limit=5 if q == name.strip() else 3,
                viewbox=viewbox,
            )

    if ctx_near and _looks_like_shop(name):
        south, west, north, east = (
            ctx_near[0] - 0.15,
            ctx_near[1] - 0.15,
            ctx_near[0] + 0.15,
            ctx_near[1] + 0.15,
        )
        for item in _overpass_search_places(name, bbox=(south, west, north, east)):
            add_option(**item)

    station_records = collect_station_records(name)
    station_like = _is_station_query(name)
    if station_like or not options:
        _add_station_options(name, add_option, reference_only=bool(options))
    elif _is_poi_like(name) or _looks_like_shop(name) or station_records or has_known:
        _add_station_options(name, add_option, reference_only=True)
        for area in _area_station_queries(name):
            _add_station_options(area, add_option, reference_only=True)

    if not options:
        station = lookup_station(name)
        if station:
            add_option(
                label=f"駅: {station.name}",
                resolved_name=station.name,
                lat=station.lat,
                lon=station.lon,
                source="station",
                kind="station",
            )

    if needs_research(name, options):
        from place_research import research_place_candidates

        area_hint = region_hints[0] if region_hints else None
        for item in research_place_candidates(
            search_name,
            near=ctx_near,
            area_hint=area_hint or parsed.area_hint,
            station_names=(context.station_names if context else []),
            anchor_coords=anchor_coords,
            region_hints=region_hints,
        ):
            add_option(**item)

    return sort_options(options, ctx_near)


def geocode_place(name: str, *, force: bool = False) -> dict | None:
    key = name.strip()
    cache = _load_cache()
    if not force and key in cache:
        cached = cache[key]
        if cached.get("source") != "station":
            return cached

    options = search_place_candidates(name)
    if not options:
        return cache.get(key)

    assign_option_ids(options)
    best_id = pick_recommended_place_option(options, query_name=key)
    best = next(o for o in options if o["id"] == best_id)
    result = {
        "name": best["resolved_name"],
        "lat": best["lat"],
        "lon": best["lon"],
        "source": best["source"],
    }
    cache[key] = result
    _save_cache(cache)
    return result
