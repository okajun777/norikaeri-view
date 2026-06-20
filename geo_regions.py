"""日本全国の地理範囲・地域推定（目的地検索・候補選別用）。"""

from __future__ import annotations

import math

# 日本本土＋沖縄・離島をおおまかに包含
JAPAN_LAT_MIN, JAPAN_LAT_MAX = 24.0, 46.2
JAPAN_LON_MIN, JAPAN_LON_MAX = 122.0, 154.0

# 都道府県代表点（検索ヒント・近傍判定用）
PREFECTURE_CENTERS: dict[str, tuple[float, float]] = {
    "北海道": (43.06, 141.35),
    "青森": (40.82, 140.74),
    "岩手": (39.70, 141.15),
    "宮城": (38.27, 140.87),
    "秋田": (39.72, 140.10),
    "山形": (38.24, 140.33),
    "福島": (37.75, 140.47),
    "茨城": (36.34, 140.45),
    "栃木": (36.57, 139.88),
    "群馬": (36.39, 139.06),
    "埼玉": (35.86, 139.65),
    "千葉": (35.60, 140.12),
    "東京": (35.69, 139.69),
    "神奈川": (35.45, 139.64),
    "新潟": (37.90, 139.02),
    "富山": (36.70, 137.21),
    "石川": (36.59, 136.63),
    "福井": (36.07, 136.22),
    "山梨": (35.66, 138.57),
    "長野": (36.65, 138.18),
    "岐阜": (35.39, 136.72),
    "静岡": (34.98, 138.38),
    "愛知": (35.18, 136.91),
    "三重": (34.73, 136.51),
    "滋賀": (35.00, 135.87),
    "京都": (35.02, 135.76),
    "大阪": (34.69, 135.52),
    "兵庫": (34.69, 135.18),
    "奈良": (34.69, 135.83),
    "和歌山": (34.23, 135.17),
    "鳥取": (35.50, 134.24),
    "島根": (35.47, 133.05),
    "岡山": (34.66, 133.93),
    "広島": (34.40, 132.46),
    "山口": (34.19, 131.47),
    "徳島": (34.07, 134.56),
    "香川": (34.34, 134.04),
    "愛媛": (33.84, 132.77),
    "高知": (33.56, 133.53),
    "福岡": (33.59, 130.40),
    "佐賀": (33.25, 130.30),
    "長崎": (32.75, 129.88),
    "熊本": (32.79, 130.74),
    "大分": (33.24, 131.61),
    "宮崎": (31.91, 131.42),
    "鹿児島": (31.56, 130.56),
    "沖縄": (26.21, 127.68),
}

PREFECTURE_CODE_NAMES: dict[int, str] = {
    1: "北海道", 2: "青森", 3: "岩手", 4: "宮城", 5: "秋田", 6: "山形", 7: "福島",
    8: "茨城", 9: "栃木", 10: "群馬", 11: "埼玉", 12: "千葉", 13: "東京", 14: "神奈川",
    15: "新潟", 16: "富山", 17: "石川", 18: "福井", 19: "山梨", 20: "長野", 21: "岐阜",
    22: "静岡", 23: "愛知", 24: "三重", 25: "滋賀", 26: "京都", 27: "大阪", 28: "兵庫",
    29: "奈良", 30: "和歌山", 31: "鳥取", 32: "島根", 33: "岡山", 34: "広島", 35: "山口",
    36: "徳島", 37: "香川", 38: "愛媛", 39: "高知", 40: "福岡", 41: "佐賀", 42: "長崎",
    43: "熊本", 44: "大分", 45: "宮崎", 46: "鹿児島", 47: "沖縄",
}

DEFAULT_REGION_SEARCH_SUFFIXES = ("日本",)


def in_japan(lat: float, lon: float) -> bool:
    return JAPAN_LAT_MIN <= lat <= JAPAN_LAT_MAX and JAPAN_LON_MIN <= lon <= JAPAN_LON_MAX


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1, p2 = lat1 * math.pi / 180, lat2 * math.pi / 180
    dp = (lat2 - lat1) * math.pi / 180
    dl = (lon2 - lon1) * math.pi / 180
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_prefecture(lat: float, lon: float) -> str | None:
    if not in_japan(lat, lon):
        return None
    best_name: str | None = None
    best_dist = float("inf")
    for name, (plat, plon) in PREFECTURE_CENTERS.items():
        d = haversine_m(lat, lon, plat, plon)
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name


def area_hint_from_coords(lat: float, lon: float) -> str | None:
    return nearest_prefecture(lat, lon)


def region_search_suffixes(region_hints: list[str] | None = None) -> tuple[str, ...]:
    if not region_hints:
        return DEFAULT_REGION_SEARCH_SUFFIXES
    seen: set[str] = set()
    ordered: list[str] = []
    for hint in region_hints:
        h = hint.strip()
        if h and h not in seen:
            seen.add(h)
            ordered.append(h)
    for fallback in DEFAULT_REGION_SEARCH_SUFFIXES:
        if fallback not in seen:
            ordered.append(fallback)
    return tuple(ordered[:6])


def coords_plausible(
    lat: float,
    lon: float,
    *,
    near: tuple[float, float] | None = None,
    anchor_coords: list[tuple[float, float]] | None = None,
    max_from_near_m: float = 650_000,
    max_from_anchor_m: float = 120_000,
) -> bool:
    if not in_japan(lat, lon):
        return False
    if anchor_coords:
        for alat, alon in anchor_coords:
            if haversine_m(lat, lon, alat, alon) <= max_from_anchor_m:
                return True
    if near:
        return haversine_m(lat, lon, near[0], near[1]) <= max_from_near_m
    return True


def sort_by_proximity(
    options: list[dict],
    near: tuple[float, float] | None,
) -> list[dict]:
    if not near:
        return options
    return sorted(
        options,
        key=lambda o: haversine_m(o["lat"], o["lon"], near[0], near[1]),
    )
