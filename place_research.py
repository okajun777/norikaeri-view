"""店舗・施設名の AI / Web 調査 → 座標候補を返す。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import requests

from config import DATA_DIR
from geo_regions import coords_plausible, in_japan, region_search_suffixes

RESEARCH_CACHE_PATH = DATA_DIR / "research_cache.json"
CACHE_TTL_SEC = 30 * 24 * 3600

PREFECTURE_ADDR_RE = re.compile(
    r"((?:北海道|"
    r"(?:青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|埼玉|千葉|東京|神奈川|"
    r"新潟|富山|石川|福井|山梨|長野|岐阜|静岡|愛知|三重|滋賀|京都|大阪|兵庫|"
    r"奈良|和歌山|鳥取|島根|岡山|広島|山口|徳島|香川|愛媛|高知|福岡|佐賀|"
    r"長崎|熊本|大分|宮崎|鹿児島|沖縄)県|"
    r"東京都|京都府|大阪府|北海道)"
    r"[^<\s、,。]{2,45}?"
    r"(?:\d+[-－−‐]\d+|\d+番\d+(?:-\d+)?|\d+))"
)

SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>([^<]+)')
TITLE_RE = re.compile(r'class="result__a"[^>]*>([^<]+)')

_last_web_request = 0.0


def _load_cache() -> dict[str, dict]:
    if RESEARCH_CACHE_PATH.exists():
        return json.loads(RESEARCH_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    RESEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESEARCH_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cache_key(name: str, near: tuple[float, float] | None, region_hints: list[str]) -> str:
    region = region_hints[0] if region_hints else ""
    if near:
        return f"{name.strip()}@{near[0]:.2f},{near[1]:.2f}@{region}"
    return f"{name.strip()}@{region}"


def _area_hint_from_near(near: tuple[float, float] | None) -> str | None:
    if not near:
        return None
    from geo_regions import area_hint_from_coords

    return area_hint_from_coords(near[0], near[1])


def _duckduckgo_html(query: str) -> str:
    global _last_web_request
    elapsed = time.time() - _last_web_request
    if elapsed < 0.8:
        time.sleep(0.8 - elapsed)
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "b": "", "kl": "jp-jp"},
        headers={"User-Agent": "norikaeri-kiroku/1.0 (local trip recorder)"},
        timeout=18,
    )
    _last_web_request = time.time()
    resp.raise_for_status()
    return resp.text


def _extract_addresses(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in PREFECTURE_ADDR_RE.finditer(text):
        addr = re.sub(r"\s+", "", m.group(1))
        if addr not in seen and len(addr) >= 8:
            seen.add(addr)
            found.append(addr)
    return found


def _web_search_queries(
    name: str,
    area_hint: str | None,
    station_names: list[str],
    region_hints: list[str],
) -> list[str]:
    areas = list(region_search_suffixes(region_hints))
    if area_hint and area_hint not in areas:
        areas.insert(0, area_hint)
    queries = [f"{name} 住所", f"{name} 地図"]
    for area in areas[:4]:
        queries.append(f"{name} {area} 住所")
    for station in station_names[:3]:
        queries.append(f"{name} {station} 住所")
    if any(s in name for s in ("本店", "支店", "店")):
        core = re.sub(r"(本店|支店|直営店|総本店)$", "", name.strip())
        if core and core != name:
            for area in areas[:2]:
                queries.append(f"{core} {area} 住所")
    seen: set[str] = set()
    ordered: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            ordered.append(q)
    return ordered[:8]


def _web_research_leads(
    name: str,
    area_hint: str | None,
    station_names: list[str],
    region_hints: list[str],
) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    seen_addr: set[str] = set()

    for query in _web_search_queries(name, area_hint, station_names, region_hints):
        try:
            html = _duckduckgo_html(query)
        except Exception:
            continue
        text = " ".join(SNIPPET_RE.findall(html) + TITLE_RE.findall(html))
        for addr in _extract_addresses(text):
            if addr in seen_addr:
                continue
            seen_addr.add(addr)
            leads.append(
                {
                    "resolved_name": name,
                    "address": addr,
                    "kind": "place",
                    "search_queries": [addr, f"{name} {addr}"],
                    "confidence": "medium",
                    "source_detail": "web",
                }
            )
        if leads:
            break
    return leads[:5]


def _ai_research_leads(
    name: str,
    *,
    near: tuple[float, float] | None,
    area_hint: str | None,
    station_names: list[str],
    region_hints: list[str],
) -> list[dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []

    context_parts = []
    if near:
        from geo_regions import area_hint_from_coords

        pref = area_hint_from_coords(near[0], near[1])
        context_parts.append(
            f"旅程の中心付近: 緯度{near[0]:.3f}, 経度{near[1]:.3f}"
            + (f"（{pref}付近）" if pref else "")
        )
    if region_hints:
        context_parts.append(f"想定エリア: {', '.join(region_hints[:6])}")
    elif area_hint:
        context_parts.append(f"想定エリア: {area_hint}")
    if station_names:
        context_parts.append(f"関連駅・区間: {', '.join(station_names[:10])}")
    context = "\n".join(context_parts)

    prompt = f"""日本の店舗・観光地・施設の位置を調べてください。

入力名: {name}
{context}

ルール:
- 実在が確かな施設のみ（不明なら candidates は空配列）
- 正式名称と都道府県から始まる完全な住所
- 緯度経度は出力しない
- 関連駅・エリアがあればその近くを優先

JSONのみ:
{{"candidates":[{{"resolved_name":"正式名称","address":"都道府県…","kind":"restaurant|attraction|cafe|museum|shop|other","search_queries":["検索語"],"confidence":"high|medium|low"}}]}}"""

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
    except Exception:
        return []

    leads: list[dict[str, Any]] = []
    for item in payload.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        resolved = (item.get("resolved_name") or name).strip()
        address = (item.get("address") or "").strip()
        queries = [q.strip() for q in (item.get("search_queries") or []) if q and q.strip()]
        if address and address not in queries:
            queries.insert(0, address)
        if not queries:
            continue
        leads.append(
            {
                "resolved_name": resolved,
                "address": address,
                "kind": item.get("kind") or "place",
                "search_queries": queries[:4],
                "confidence": item.get("confidence") or "medium",
                "source_detail": "ai",
            }
        )
    return leads


def _address_geocode_variants(address: str) -> list[str]:
    addr = re.sub(r"\s+", "", address.strip())
    if not addr:
        return []
    variants = [addr]
    variants.append(re.sub(r"\d+[-－−‐]\d+$", "", addr))
    variants.append(re.sub(r"\d+$", "", addr))
    city = re.match(r"((?:北海道|(?:..)+県|東京都|京都府|大阪府).+?(?:市|区|町|村))", addr)
    if city:
        variants.append(city.group(1))
    seen: set[str] = set()
    ordered: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered


def _geocode_queries(
    queries: list[str],
    geocode_fn: Callable[[str, int], list[dict]],
) -> tuple[float, float] | None:
    tried: set[str] = set()
    for q in queries:
        for variant in _address_geocode_variants(q) if ("県" in q or "都" in q or "府" in q) else [q]:
            if not variant or variant in tried:
                continue
            tried.add(variant)
            for item in geocode_fn(variant, 1):
                try:
                    lat, lon = float(item["lat"]), float(item["lon"])
                    if in_japan(lat, lon):
                        return lat, lon
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _lead_to_option(
    lead: dict[str, Any],
    coords: tuple[float, float],
    *,
    near: tuple[float, float] | None,
    anchor_coords: list[tuple[float, float]] | None,
) -> dict[str, Any] | None:
    lat, lon = coords
    if not coords_plausible(lat, lon, near=near, anchor_coords=anchor_coords):
        return None
    source = lead.get("source_detail") or "web"
    source_label = "AI調査" if source == "ai" else "Web調査"
    address = lead.get("address") or ""
    resolved = lead.get("resolved_name") or ""
    label = f"{resolved}（{source_label}"
    if address:
        label += f": {address}"
    label += "）"
    return {
        "label": label,
        "resolved_name": resolved,
        "lat": lat,
        "lon": lon,
        "source": source,
        "kind": lead.get("kind") or "place",
        "address": address,
    }


def research_place_candidates(
    name: str,
    *,
    near: tuple[float, float] | None = None,
    area_hint: str | None = None,
    station_names: list[str] | None = None,
    anchor_coords: list[tuple[float, float]] | None = None,
    region_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    from geocode import _nominatim_search

    stations = station_names or []
    regions = list(region_hints or [])
    if area_hint and area_hint not in regions:
        regions.insert(0, area_hint)

    key = _cache_key(name, near, regions)
    cache = _load_cache()
    cached = cache.get(key)
    if cached and time.time() - cached.get("ts", 0) < CACHE_TTL_SEC:
        return list(cached.get("options") or [])

    hint = area_hint or _area_hint_from_near(near)
    leads: list[dict[str, Any]] = []
    leads.extend(
        _ai_research_leads(
            name,
            near=near,
            area_hint=hint,
            station_names=stations,
            region_hints=regions,
        )
    )
    if not leads:
        leads.extend(_web_research_leads(name, hint, stations, regions))

    options: list[dict[str, Any]] = []
    seen_coords: set[tuple[float, float]] = set()

    for lead in leads:
        queries = list(dict.fromkeys(lead.get("search_queries") or []))
        if lead.get("address"):
            queries = _address_geocode_variants(lead["address"]) + queries
        coords = _geocode_queries(queries, _nominatim_search)
        if not coords:
            continue
        key_coord = (round(coords[0], 4), round(coords[1], 4))
        if key_coord in seen_coords:
            continue
        seen_coords.add(key_coord)
        opt = _lead_to_option(lead, coords, near=near, anchor_coords=anchor_coords)
        if opt:
            options.append(opt)

    if options:
        cache[key] = {"ts": time.time(), "options": options}
        _save_cache(cache)
    return options


def ai_lookup_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())
