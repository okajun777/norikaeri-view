import json
import re
from dataclasses import dataclass

import requests

from config import DATA_DIR, EKIDATA_BASE, PREFECTURE_CODES, STATION_CACHE_PATH
from geo_regions import PREFECTURE_CODE_NAMES

LINE_CACHE_PATH = DATA_DIR / "lines.json"


ALIASES = {
    "押上": [
        "押上〈スカイツリー前〉",
        "押上（スカイツリー前）",
        "とうきょうスカイツリー",
        "スカイツリー",
    ],
    "江の島": ["江ノ島", "湘南江の島"],
    "東武動物公園": ["東武動物公園駅"],
    "宇都宮": ["宇都宮駅", "JR宇都宮"],
    "東武宇都宮": ["東武宇都宮駅"],
    "あしかがフラワーパーク": ["足利フラワーパーク", "アシカガフラワーパーク"],
    "佐野": ["佐野駅"],
    "小山": ["小山駅"],
    "館林": ["館林駅"],
    "南栗橋": ["南栗橋駅"],
}


@dataclass
class StationInfo:
    name: str
    lat: float
    lon: float
    line_name: str | None = None
    operator: str | None = None


def _operator_from_line(line_name: str) -> str:
    if line_name.startswith("JR") or "JR" in line_name:
        return "JR東日本"
    if line_name.startswith("東武"):
        return "東武鉄道"
    if line_name.startswith("西武"):
        return "西武鉄道"
    if line_name.startswith("京成"):
        return "京成電鉄"
    if line_name.startswith("東京メトロ") or line_name.startswith("都営"):
        return "東京都交通局"
    return ""


def _normalize_key(name: str) -> str:
    name = re.sub(r"[〈〉《》（）()\s　]", "", name)
    name = name.replace("駅", "")
    # ヶ / ケ / が などの表記ゆれ（例: 茅ヶ崎 / 茅ケ崎）
    name = name.replace("ヶ", "ケ").replace("が", "ケ")
    # 駅データ.jp と入力テキストの表記ゆれ（例: 橋 U+6A58 / U+6A4B）
    name = name.replace("\u6a58", "\u6a4b")
    name = name.replace("スカイツリー前", "")
    return name


def _dedupe_station_records(records: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for rec in records:
        key = (rec["name"], rec.get("line_name") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique


def collect_station_records(name: str) -> list[dict]:
    cache = build_station_cache()
    key = _normalize_key(name)
    candidates: list[dict] = []
    seen_keys: set[str] = set()

    def add_from_cache(cache_key: str) -> None:
        if cache_key in seen_keys:
            return
        seen_keys.add(cache_key)
        candidates.extend(cache.get(cache_key, []))

    add_from_cache(key)
    for alias in ALIASES.get(name, []):
        add_from_cache(_normalize_key(alias))
        add_from_cache(alias)
    for cache_key in cache:
        if _normalize_key(cache_key) == key:
            add_from_cache(cache_key)
    if not candidates:
        for cache_key, vals in cache.items():
            if key in cache_key or cache_key in key:
                candidates.extend(vals)
    return _dedupe_station_records(candidates)


def _load_cache() -> dict[str, list[dict]]:
    if STATION_CACHE_PATH.exists():
        return json.loads(STATION_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(data: dict[str, list[dict]]) -> None:
    STATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATION_CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_station_cache() -> dict[str, list[dict]]:
    cache = _load_cache()
    loaded_prefs: set[int] = set()
    for records in cache.values():
        for rec in records:
            if rec.get("pref_cd") is not None:
                loaded_prefs.add(int(rec["pref_cd"]))

    missing = [p for p in PREFECTURE_CODES if p not in loaded_prefs]
    if not missing and cache:
        return cache

    entries: dict[str, list[dict]] = dict(cache) if cache else {}
    prefs_to_fetch = missing if cache else list(PREFECTURE_CODES)

    for pref_cd in prefs_to_fetch:
        try:
            pref = requests.get(f"{EKIDATA_BASE}/p/{pref_cd}.json", timeout=30).json()
        except Exception:
            continue
        pref_name = PREFECTURE_CODE_NAMES.get(pref_cd, "")
        for line_info in pref.get("line", []):
            line_cd = line_info["line_cd"]
            line_name = line_info["line_name"]
            operator = _operator_from_line(line_name)
            try:
                line_data = requests.get(f"{EKIDATA_BASE}/l/{line_cd}.json", timeout=30).json()
            except Exception:
                continue
            for station in line_data.get("station_l", []):
                record = {
                    "name": station["station_name"],
                    "lat": station["lat"],
                    "lon": station["lon"],
                    "line_name": line_name,
                    "operator": operator,
                    "pref_cd": pref_cd,
                    "prefecture": pref_name,
                }
                key = _normalize_key(station["station_name"])
                entries.setdefault(key, []).append(record)

    for alias, names in ALIASES.items():
        key = _normalize_key(alias)
        merged = list(entries.get(key, []))
        for name in names:
            merged.extend(entries.get(_normalize_key(name), []))
        if merged:
            entries[key] = _dedupe_station_records(merged)

    _save_cache(entries)
    return entries


def lookup_station(name: str) -> StationInfo | None:
    candidates = collect_station_records(name)
    if not candidates:
        return None

    # 同名駅は最初の候補（後で路線推定で絞る）
    best = candidates[0]
    return StationInfo(
        name=best["name"],
        lat=best["lat"],
        lon=best["lon"],
        line_name=best.get("line_name"),
        operator=best.get("operator"),
    )


def lookup_station_pair(
    from_name: str, to_name: str, prev_line: str | None = None
) -> tuple[StationInfo | None, StationInfo | None, str | None, str | None]:
    from_candidates = collect_station_records(from_name)
    to_candidates = collect_station_records(to_name)

    if not from_candidates:
        from_candidates = [_record_from_info(lookup_station(from_name))]
    if not to_candidates:
        to_candidates = [_record_from_info(lookup_station(to_name))]

    from_candidates = [c for c in from_candidates if c]
    to_candidates = [c for c in to_candidates if c]

    if not from_candidates or not to_candidates:
        return (
            lookup_station(from_name),
            lookup_station(to_name),
            None,
            None,
        )

    best_line = None
    best_operator = None
    best_from = from_candidates[0]
    best_to = to_candidates[0]
    best_dist = float("inf")

    for f in from_candidates:
        for t in to_candidates:
            if f["line_name"] == t["line_name"]:
                dist = abs(f["lat"] - t["lat"]) + abs(f["lon"] - t["lon"])
                if dist < best_dist:
                    best_dist = dist
                    best_from = f
                    best_to = t
                    best_line = f["line_name"]
                    best_operator = f["operator"]

    if not best_line:
        if prev_line:
            for f in from_candidates:
                if f["line_name"] == prev_line:
                    best_from = f
                    best_line = prev_line
                    best_operator = f["operator"]
                    break
        if not best_line and prev_line and "東武" in prev_line:
            for f in from_candidates:
                if f.get("operator") == "東武鉄道":
                    best_from = f
                    best_line = f["line_name"]
                    best_operator = f["operator"]
                    break
        if not best_line:
            best_from = from_candidates[0]
            best_line = best_from["line_name"]
            best_operator = best_from["operator"]
        for t in to_candidates:
            if t["line_name"] == best_line:
                best_to = t
                break

    return (
        StationInfo(
            best_from["name"], best_from["lat"], best_from["lon"],
            best_from.get("line_name"), best_from.get("operator"),
        ),
        StationInfo(
            best_to["name"], best_to["lat"], best_to["lon"],
            best_to.get("line_name"), best_to.get("operator"),
        ),
        best_line,
        best_operator,
    )


def _record_from_info(info: StationInfo | None) -> dict | None:
    if not info:
        return None
    return {
        "name": info.name,
        "lat": info.lat,
        "lon": info.lon,
        "line_name": info.line_name,
        "operator": info.operator,
    }


def get_lines_for_station(name: str) -> set[str]:
    lines = build_line_cache()
    result: set[str] = set()
    for line_name, data in lines.items():
        if _station_index_on_line(data.get("stations", []), name) is not None:
            result.add(line_name)
    return result


def find_all_direct_lines(from_name: str, to_name: str) -> list[str]:
    direct: list[str] = []
    for line_name in get_lines_for_station(from_name):
        if _on_same_line(line_name, from_name, to_name):
            direct.append(line_name)
    return sorted(direct)


def _on_same_line(line_name: str, from_name: str, to_name: str) -> bool:
    lines = build_line_cache()
    stations = lines.get(line_name, {}).get("stations", [])
    i_from = _station_index_on_line(stations, from_name)
    i_to = _station_index_on_line(stations, to_name)
    return i_from is not None and i_to is not None and i_from != i_to


def find_all_transfer_paths(
    from_name: str, to_name: str
) -> list[list[tuple[str, str, str | None]]]:
    """2路線に分割できる全区間候補を返す"""
    from_lines = get_lines_for_station(from_name)
    to_lines = get_lines_for_station(to_name)
    if not from_lines or not to_lines:
        return []

    lines = build_line_cache()
    results: list[list[tuple[str, str, str | None]]] = []
    seen: set[tuple[str, str, str]] = set()

    for line_a in sorted(from_lines):
        stations_a = lines.get(line_a, {}).get("stations", [])
        if _station_index_on_line(stations_a, from_name) is None:
            continue
        for line_b in sorted(to_lines):
            if line_a == line_b:
                continue
            stations_b = lines.get(line_b, {}).get("stations", [])
            if _station_index_on_line(stations_b, to_name) is None:
                continue
            for st in stations_b:
                if _station_index_on_line(stations_a, st["name"]) is None:
                    continue
                sig = (line_a, _normalize_key(st["name"]), line_b)
                if sig in seen:
                    continue
                seen.add(sig)
                results.append(
                    [
                        (from_name, st["name"], line_a),
                        (st["name"], to_name, line_b),
                    ]
                )
    return results


def find_transfer_path(
    from_name: str, to_name: str
) -> list[tuple[str, str, str | None]] | None:
    """同一区間を2路線に分割できる場合、[(from, transfer, line), (transfer, to, line)]"""
    paths = find_all_transfer_paths(from_name, to_name)
    return paths[0] if paths else None


def _load_line_cache() -> dict[str, dict]:
    if LINE_CACHE_PATH.exists():
        return json.loads(LINE_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_line_cache(data: dict[str, dict]) -> None:
    LINE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINE_CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_line_cache() -> dict[str, dict]:
    cache = _load_line_cache()
    if cache:
        return cache

    lines: dict[str, dict] = {}
    for pref_cd in PREFECTURE_CODES:
        pref = requests.get(f"{EKIDATA_BASE}/p/{pref_cd}.json", timeout=30).json()
        for line_info in pref.get("line", []):
            line_cd = line_info["line_cd"]
            line_name = line_info["line_name"]
            if line_name in lines:
                continue
            line_data = requests.get(f"{EKIDATA_BASE}/l/{line_cd}.json", timeout=30).json()
            stations = []
            for station in line_data.get("station_l", []):
                stations.append(
                    {
                        "name": station["station_name"],
                        "lat": station["lat"],
                        "lon": station["lon"],
                    }
                )
            lines[line_name] = {
                "line_cd": line_cd,
                "operator": _operator_from_line(line_name),
                "stations": stations,
            }

    _save_line_cache(lines)
    return lines


def _station_index_on_line(stations: list[dict], name: str) -> int | None:
    key = _normalize_key(name)
    aliases = {_normalize_key(a) for a in ALIASES.get(name, [])}

    exact: list[int] = []
    partial: list[int] = []
    for i, st in enumerate(stations):
        st_key = _normalize_key(st["name"])
        if st_key == key or st_key in aliases:
            exact.append(i)
            continue
        if key in st_key or st_key in key:
            partial.append(i)

    if exact:
        return exact[0]
    if len(partial) == 1:
        return partial[0]
    return None


def get_line_station_path(
    from_name: str, to_name: str, line_name: str | None
) -> list[tuple[float, float]] | None:
    if not line_name:
        return None

    lines = build_line_cache()
    line = lines.get(line_name)
    if not line:
        return None

    stations = line["stations"]
    i_from = _station_index_on_line(stations, from_name)
    i_to = _station_index_on_line(stations, to_name)
    if i_from is None or i_to is None or i_from == i_to:
        return None

    if i_from < i_to:
        path = stations[i_from : i_to + 1]
    else:
        path = list(reversed(stations[i_to : i_from + 1]))

    return [(st["lat"], st["lon"]) for st in path]


def get_line_station_names(
    from_name: str, to_name: str, line_name: str | None
) -> list[str] | None:
    if not line_name:
        return None

    lines = build_line_cache()
    line = lines.get(line_name)
    if not line:
        return None

    stations = line["stations"]
    i_from = _station_index_on_line(stations, from_name)
    i_to = _station_index_on_line(stations, to_name)
    if i_from is None or i_to is None or i_from == i_to:
        return None

    if i_from < i_to:
        path = stations[i_from : i_to + 1]
    else:
        path = list(reversed(stations[i_to : i_from + 1]))

    return [st["name"] for st in path]
