"""店名・施設名の入力を正規化（「あさまる本店　茅ヶ崎」→ 店名 + エリア）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from geo_regions import PREFECTURE_CENTERS

SHOP_SUFFIXES = ("本店", "支店", "直営店", "総本店")

# 末尾に付く地名・駅名として扱う語（都道府県＋主要都市）
AREA_TOKENS: set[str] = set(PREFECTURE_CENTERS.keys())
AREA_TOKENS.update(
    {
        "茅ケ崎",
        "茅ヶ崎",
        "湘南",
        "江の島",
        "江ノ島",
        "片瀬",
        "鎌倉",
        "藤沢",
        "平塚",
        "大磯",
        "箱根",
        "横浜",
        "川崎",
        "宇都宮",
        "日光",
        "栃木",
        "関東",
        "関西",
        "大阪",
        "京都",
        "神戸",
        "名古屋",
        "福岡",
        "札幌",
        "仙台",
        "広島",
        "那覇",
    }
)


def _compact(s: str) -> str:
    return re.sub(r"[\s　]+", "", s.strip())


def normalize_place_key(name: str) -> str:
    key = _compact(name)
    for suffix in SHOP_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            key = key[: -len(suffix)]
    return key


def _is_area_token(token: str) -> bool:
    t = token.strip().rstrip("駅")
    if t in AREA_TOKENS:
        return True
    if t.endswith(("市", "区", "町", "村")) and len(t) >= 2:
        return True
    if t.endswith("県") or t in ("東京", "大阪", "京都", "北海道"):
        return True
    return False


@dataclass
class ParsedPlaceName:
    original: str
    core: str
    area_hint: str | None

    def lookup_names(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(n: str) -> None:
            n = n.strip()
            if n and n not in seen:
                seen.add(n)
                names.append(n)

        add(self.original)
        add(self.core)
        add(_compact(self.original))
        add(_compact(self.core))
        if self.area_hint and self.core:
            add(f"{self.core} {self.area_hint}")
            add(f"{self.core}　{self.area_hint}")
        return names

    def lookup_keys(self) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for n in self.lookup_names():
            for k in (normalize_place_key(n), _compact(n)):
                if k and k not in seen:
                    seen.add(k)
                    keys.append(k)
        return keys


def parse_place_name(name: str) -> ParsedPlaceName:
    original = (name or "").strip()
    if not original:
        return ParsedPlaceName(original="", core="", area_hint=None)

    parts = [p for p in re.split(r"[\s　]+", original) if p.strip()]
    if len(parts) >= 2:
        last = parts[-1].strip().rstrip("駅")
        if _is_area_token(last):
            core_parts = parts[:-1]
            core = "".join(core_parts) if len(core_parts) == 1 else " ".join(core_parts)
            if len(core_parts) > 1 and all(len(p) > 1 for p in core_parts):
                core = "".join(core_parts)
            return ParsedPlaceName(original=original, core=core.strip(), area_hint=last)

    # 「店名茅ヶ崎」のようにスペースなしで地名が末尾につく場合
    compact = _compact(original)
    for token in sorted(AREA_TOKENS, key=len, reverse=True):
        if compact.endswith(token) and len(compact) > len(token) + 2:
            core = compact[: -len(token)]
            return ParsedPlaceName(original=original, core=core, area_hint=token)

    return ParsedPlaceName(original=original, core=original, area_hint=None)


def looks_like_shop_name(name: str) -> bool:
    parsed = parse_place_name(name)
    core = parsed.core
    if any(s in core for s in SHOP_SUFFIXES):
        return True
    if any(k in core for k in ("料理", "食堂", "カフェ", "レストラン", "ラーメン", "餃子")):
        return True
    return False
