import hashlib
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

import requests

from config import GEOMETRY_CACHE_DIR, OVERPASS_URL
from station_lookup import (
    find_all_transfer_paths,
    find_transfer_path,
    get_line_station_path,
    lookup_station,
)

CACHE_VERSION = "v7"
LONG_SEGMENT_M = 18000
MAX_PATH_RATIO = 2.2


def _cache_key(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> str:
    raw = f"{CACHE_VERSION}:{from_lat:.5f},{from_lon:.5f},{to_lat:.5f},{to_lon:.5f}"
    return hashlib.md5(raw.encode()).hexdigest()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bbox(
    lat1: float, lon1: float, lat2: float, to_lon: float, pad: float | None = None
) -> tuple[float, float, float, float]:
    if pad is None:
        dist = _haversine(lat1, lon1, lat2, to_lon)
        if dist > 50000:
            pad = 0.02
        elif dist > 15000:
            pad = 0.012
        elif dist > 3000:
            pad = 0.010
        else:
            pad = 0.015
    south = min(lat1, lat2) - pad
    north = max(lat1, lat2) + pad
    west = min(lon1, to_lon) - pad
    east = max(lon1, to_lon) + pad
    return south, west, north, east


def _point_segment_distance_m(
    lat: float, lon: float,
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111320
    dy = (lat2 - lat1) * 110540
    px = (lon - lon1) * math.cos(math.radians((lat1 + lat) / 2)) * 111320
    py = (lat - lat1) * 110540
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * dx + py * dy) / seg_len2))
    proj_x = t * dx
    proj_y = t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _filter_ways_in_corridor(
    ways: list[dict],
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
    width_m: float = 1800,
) -> list[dict]:
    filtered = []
    for way in ways:
        for lon, lat in way["coords"]:
            if _point_segment_distance_m(lat, lon, from_lat, from_lon, to_lat, to_lon) <= width_m:
                filtered.append(way)
                break
    return filtered


def _fetch_rail_ways(south: float, west: float, north: float, east: float) -> list[dict]:
    query = f"""
    [out:json][timeout:40];
    way["railway"~"^(rail|light_rail|subway|tram|narrow_gauge)$"]
        ({south},{west},{north},{east});
    out geom;
    """
    headers = {
        "User-Agent": "norikaeri-kiroku/1.0 (local trip recorder)",
        "Accept": "application/json",
    }
    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers=headers,
        timeout=60,
    )
    if resp.status_code == 406:
        resp = requests.get(
            OVERPASS_URL,
            params={"data": query},
            headers=headers,
            timeout=60,
        )
    resp.raise_for_status()
    ways = []
    for el in resp.json().get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
        if len(coords) >= 2:
            ways.append({"id": el["id"], "coords": coords})
    return ways


class _RailGraph:
    MERGE_M = 15.0
    CELL = 0.00022

    def __init__(self) -> None:
        self.nodes: list[tuple[float, float]] = []
        self.adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self._cells: dict[tuple[int, int], list[int]] = defaultdict(list)

    def _cell_key(self, lon: float, lat: float) -> tuple[int, int]:
        return (int(lon / self.CELL), int(lat / self.CELL))

    def _find_nearby_node(self, lon: float, lat: float) -> int | None:
        cx, cy = self._cell_key(lon, lat)
        best_id = None
        best_d = self.MERGE_M
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid in self._cells.get((cx + dx, cy + dy), []):
                    nlon, nlat = self.nodes[nid]
                    d = _haversine(lat, lon, nlat, nlon)
                    if d < best_d:
                        best_d = d
                        best_id = nid
        return best_id

    def add_node(self, lon: float, lat: float) -> int:
        existing = self._find_nearby_node(lon, lat)
        if existing is not None:
            return existing
        nid = len(self.nodes)
        self.nodes.append((lon, lat))
        self._cells[self._cell_key(lon, lat)].append(nid)
        return nid

    def add_way(self, coords: list[list[float]]) -> None:
        prev: int | None = None
        for lon, lat in coords:
            nid = self.add_node(lon, lat)
            if prev is not None and prev != nid:
                nlon, nlat = self.nodes[prev]
                dist = _haversine(nlat, nlon, lat, lon)
                if dist > 0.5:
                    self.adj[prev].append((nid, dist))
                    self.adj[nid].append((prev, dist))
            prev = nid

    def nearest_nodes(self, lon: float, lat: float, max_dist: float) -> list[tuple[int, float]]:
        cx, cy = self._cell_key(lon, lat)
        radius = max(1, int(math.ceil(max_dist / (self.CELL * 111000))))
        found: list[tuple[int, float]] = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for nid in self._cells.get((cx + dx, cy + dy), []):
                    nlon, nlat = self.nodes[nid]
                    d = _haversine(lat, lon, nlat, nlon)
                    if d <= max_dist:
                        found.append((nid, d))
        found.sort(key=lambda x: x[1])
        return found[:6]

    def shortest_path(self, start_id: int, end_id: int) -> list[int] | None:
        if start_id == end_id:
            return [start_id]
        dist: dict[int, float] = {start_id: 0.0}
        prev: dict[int, int] = {}
        heap: list[tuple[float, int]] = [(0.0, start_id)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            if u == end_id:
                break
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        if end_id not in dist:
            return None
        path = [end_id]
        while path[-1] != start_id:
            path.append(prev[path[-1]])
        path.reverse()
        return path


def _pathfind_on_network(
    ways: list[dict],
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
) -> list[list[float]] | None:
    ways = _filter_ways_in_corridor(ways, from_lat, from_lon, to_lat, to_lon)
    if not ways:
        return None

    graph = _RailGraph()
    for way in ways:
        graph.add_way(way["coords"])
    if not graph.nodes:
        return None

    direct = _haversine(from_lat, from_lon, to_lat, to_lon)
    if direct < 2500:
        snap = min(900.0, max(450.0, direct * 0.35))
    else:
        snap = min(1500.0, max(350.0, direct * 0.06))
    start_candidates = graph.nearest_nodes(from_lon, from_lat, snap)
    end_candidates = graph.nearest_nodes(to_lon, to_lat, snap)
    if not start_candidates or not end_candidates:
        return None

    best_coords: list[list[float]] | None = None
    best_score = float("inf")

    for start_id, start_d in start_candidates[:4]:
        for end_id, end_d in end_candidates[:4]:
            node_path = graph.shortest_path(start_id, end_id)
            if not node_path or len(node_path) < 2:
                continue
            coords = [[graph.nodes[n][0], graph.nodes[n][1]] for n in node_path]
            rail_len = sum(
                _haversine(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0])
                for i in range(len(coords) - 1)
            )
            if rail_len > direct * 2.5:
                continue
            if rail_len < direct * 0.85:
                continue
            # 直線に近すぎる短絡より、線路に沿ったやや長い経路を優先
            score = start_d + end_d + max(0.0, direct * 1.01 - rail_len) * 2.0
            if len(coords) >= 4:
                score -= min(120.0, (rail_len - direct) * 0.04)
            if score < best_score:
                best_score = score
                best_coords = coords

    return _dedupe_coords(best_coords) if best_coords else None


def _dedupe_coords(coords: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for lon, lat in coords:
        if out and abs(out[-1][0] - lon) < 1e-7 and abs(out[-1][1] - lat) < 1e-7:
            continue
        out.append([lon, lat])
    return out


def _concat_paths(parts: list[list[list[float]]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for part in parts:
        for pt in part:
            if merged and merged[-1][0] == pt[0] and merged[-1][1] == pt[1]:
                continue
            merged.append(pt)
    return merged


def _rail_pathfind(
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
) -> list[list[float]] | None:
    direct = _haversine(from_lat, from_lon, to_lat, to_lon)
    if direct < 3000:
        pads: list[float | None] = [None, 0.015, 0.022, 0.035]
    elif direct < 8000:
        pads = [None, 0.015, 0.025]
    else:
        pads = [None, 0.02]

    for pad in pads:
        try:
            south, west, north, east = _bbox(from_lat, from_lon, to_lat, to_lon, pad=pad)
            ways = _fetch_rail_ways(south, west, north, east)
            path = _pathfind_on_network(ways, from_lat, from_lon, to_lat, to_lon)
            if not path or len(path) < 3:
                continue
            length = _path_length_m(path)
            if length < direct * 0.85 or length > direct * 2.5:
                continue
            return path
        except Exception:
            continue
    return None


def _is_good_path(
    path: list[list[float]], from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> bool:
    if len(path) < 4:
        return False
    direct = _haversine(from_lat, from_lon, to_lat, to_lon)
    length = sum(
        _haversine(path[i][1], path[i][0], path[i + 1][1], path[i + 1][0])
        for i in range(len(path) - 1)
    )
    return length <= direct * 2.2


def _hop_geometry(
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
    *, use_cache: bool = True,
) -> list[list[float]]:
    GEOMETRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = GEOMETRY_CACHE_DIR / f"{_cache_key(from_lat, from_lon, to_lat, to_lon)}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    path = _rail_pathfind(from_lat, from_lon, to_lat, to_lon)
    if not path:
        path = [[from_lon, from_lat], [to_lon, to_lat]]

    cache_file.write_text(json.dumps(path, ensure_ascii=False), encoding="utf-8")
    return path


def _geometry_via_stations(
    from_name: str, to_name: str, line_name: str,
) -> list[list[float]] | None:
    station_path = get_line_station_path(from_name, to_name, line_name)
    if not station_path or len(station_path) < 2:
        return None
    hops = []
    for i in range(len(station_path) - 1):
        lat1, lon1 = station_path[i]
        lat2, lon2 = station_path[i + 1]
        hops.append(_hop_geometry(lat1, lon1, lat2, lon2))
    return _concat_paths(hops)


def _geometry_for_transfer(
    from_name: str,
    to_name: str,
    line_a: str,
    line_b: str,
) -> list[list[float]] | None:
    for path in find_all_transfer_paths(from_name, to_name):
        if path[0][2] != line_a or path[1][2] != line_b:
            continue
        parts: list[list[list[float]]] = []
        for leg_from, leg_to, leg_line in path:
            if not leg_line:
                continue
            via = _geometry_via_stations(leg_from, leg_to, leg_line)
            if via:
                parts.append(via)
                continue
            from_st = lookup_station(leg_from)
            to_st = lookup_station(leg_to)
            if from_st and to_st:
                leg_path = _rail_pathfind(from_st.lat, from_st.lon, to_st.lat, to_st.lon)
                if leg_path:
                    parts.append(leg_path)
        merged = _concat_paths(parts)
        if merged:
            return merged
    return _build_via_transfer(from_name, to_name)


def _build_via_transfer(from_name: str, to_name: str) -> list[list[float]] | None:
    legs = find_transfer_path(from_name, to_name)
    if not legs:
        return None
    parts = []
    for leg_from, leg_to, leg_line in legs:
        via = _geometry_via_stations(leg_from, leg_to, leg_line)
        if via:
            parts.append(via)
            continue
        from_st = lookup_station(leg_from)
        to_st = lookup_station(leg_to)
        if from_st and to_st:
            leg_path = _rail_pathfind(from_st.lat, from_st.lon, to_st.lat, to_st.lon)
            if leg_path:
                parts.append(leg_path)
    return _concat_paths(parts) if parts else None


def _ensure_geometry_endpoints(
    path: list[list[float]],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    *,
    max_gap_m: float = 150,
) -> list[list[float]]:
    if len(path) < 2:
        return path

    out = [list(pt) for pt in path]

    if _haversine(from_lat, from_lon, out[0][1], out[0][0]) > max_gap_m:
        lead = _rail_pathfind(from_lat, from_lon, out[0][1], out[0][0])
        if lead and len(lead) >= 2:
            out = _concat_paths([lead, out[1:]])
        else:
            out = [[from_lon, from_lat]] + out

    if _haversine(to_lat, to_lon, out[-1][1], out[-1][0]) > max_gap_m:
        tail = _rail_pathfind(out[-1][1], out[-1][0], to_lat, to_lon)
        if tail and len(tail) >= 2:
            out = _concat_paths([out, tail[1:]])
        else:
            out = out + [[to_lon, to_lat]]

    if _haversine(from_lat, from_lon, out[0][1], out[0][0]) > 30:
        out[0] = [from_lon, from_lat]
    if _haversine(to_lat, to_lon, out[-1][1], out[-1][0]) > 30:
        out[-1] = [to_lon, to_lat]

    return _dedupe_coords(out)


def _path_length_m(path: list[list[float]]) -> float:
    if len(path) < 2:
        return 0.0
    return sum(
        _haversine(path[i][1], path[i][0], path[i + 1][1], path[i + 1][0])
        for i in range(len(path) - 1)
    )


def _pick_best_path(
    candidates: list[tuple[str, list[list[float]]]],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    *,
    line_name: str | None = None,
) -> list[list[float]] | None:
    direct = _haversine(from_lat, from_lon, to_lat, to_lon)
    if not candidates:
        return None

    order = ("stations", "direct", "transfer", "hop")
    if line_name:
        priority = {name: idx for idx, name in enumerate(order)}
    else:
        priority = {"direct": 0, "stations": 1, "transfer": 2, "hop": 3}

    scored: list[tuple[float, int, float, list[list[float]]]] = []
    for kind, path in candidates:
        if len(path) < 2:
            continue
        length = _path_length_m(path)
        if length > direct * MAX_PATH_RATIO:
            continue
        ratio_gap = abs(length - direct) / max(direct, 1.0)
        scored.append((priority.get(kind, 9), ratio_gap, length, path))

    if not scored:
        return None

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return scored[0][3]


def get_segment_geometry(
    from_lat: float, from_lon: float,
    to_lat: float, to_lon: float,
    *,
    from_name: str | None = None,
    to_name: str | None = None,
    line_name: str | None = None,
) -> list[list[float]]:
    route_key = (
        f"{CACHE_VERSION}|{from_name}|{to_name}|{line_name}|"
        f"{from_lat:.5f},{from_lon:.5f},{to_lat:.5f},{to_lon:.5f}"
    )
    route_cache = GEOMETRY_CACHE_DIR / f"route_{hashlib.md5(route_key.encode()).hexdigest()}.json"
    if route_cache.exists():
        return json.loads(route_cache.read_text(encoding="utf-8"))

    # 区間確認で選んだ路線がある場合は、その路線の駅順だけを使う
    if line_name and from_name and to_name:
        via_stations = _geometry_via_stations(from_name, to_name, line_name)
        if via_stations and len(via_stations) >= 2:
            path = _ensure_geometry_endpoints(
                via_stations, from_lat, from_lon, to_lat, to_lon
            )
            GEOMETRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            route_cache.write_text(json.dumps(path, ensure_ascii=False), encoding="utf-8")
            return path

    direct_dist = _haversine(from_lat, from_lon, to_lat, to_lon)
    candidates: list[tuple[str, list[list[float]]]] = []

    if from_name and to_name:
        via_transfer = _build_via_transfer(from_name, to_name)
        if via_transfer:
            candidates.append(("transfer", via_transfer))

    if direct_dist <= LONG_SEGMENT_M:
        direct = _rail_pathfind(from_lat, from_lon, to_lat, to_lon)
        if direct and _is_good_path(direct, from_lat, from_lon, to_lat, to_lon):
            candidates.append(("direct", direct))

    candidates.append(("hop", _hop_geometry(from_lat, from_lon, to_lat, to_lon)))

    path = _pick_best_path(
        candidates,
        from_lat,
        from_lon,
        to_lat,
        to_lon,
        line_name=None,
    )
    if not path:
        path = [[from_lon, from_lat], [to_lon, to_lat]]
    else:
        path = _ensure_geometry_endpoints(path, from_lat, from_lon, to_lat, to_lon)

    GEOMETRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    route_cache.write_text(json.dumps(path, ensure_ascii=False), encoding="utf-8")
    return path


def rebuild_trip_geometries(segments: list[dict]) -> list[dict]:
    updated: list[dict] = []
    for seg in segments:
        if not seg.get("from_lat") or not seg.get("to_lat"):
            updated.append(seg)
            continue

        seg = dict(seg)
        from_lat = seg["from_lat"]
        from_lon = seg["from_lon"]

        if updated:
            prev = updated[-1]
            prev_geom = prev.get("geometry") or []
            same_transfer = (
                (prev.get("to_station") or "").strip()
                == (seg.get("from_station") or "").strip()
            )
            if same_transfer and len(prev_geom) >= 2:
                from_lon, from_lat = prev_geom[-1]

        seg["geometry"] = _segment_geometry_for_saved(seg, from_lat, from_lon)
        updated.append(seg)
    return updated


def _segment_geometry_for_saved(
    seg: dict,
    from_lat: float,
    from_lon: float,
) -> list[list[float]] | None:
    from_name = seg.get("from_station")
    to_name = seg.get("to_station")
    line_name = seg.get("line_name")
    transfer_lines = seg.get("transfer_lines")
    if not transfer_lines and line_name and " → " in line_name:
        parts = line_name.split(" → ", 1)
        transfer_lines = [parts[0].strip(), parts[1].strip()]
        line_name = parts[0].strip()

    if transfer_lines and len(transfer_lines) >= 2:
        geom = _geometry_for_transfer(
            from_name, to_name, transfer_lines[0], transfer_lines[1]
        )
        if geom:
            return geom

    primary_line = line_name
    if primary_line and " → " in primary_line:
        primary_line = primary_line.split(" → ", 1)[0].strip()

    return get_segment_geometry(
        from_lat,
        from_lon,
        seg["to_lat"],
        seg["to_lon"],
        from_name=seg.get("resolved_from") or from_name,
        to_name=seg.get("resolved_to") or to_name,
        line_name=primary_line,
    )
