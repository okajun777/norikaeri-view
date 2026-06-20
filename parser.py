import re
from dataclasses import dataclass


TIME = r"(\d{1,2}:\d{2})"
STATION = r"(.+?)"
# 時刻なし区間: 押上ー新橋 / 新橋－押上 / 押上→新橋 など
STATION_SEP = r"[ー\-－—→~〜]+"


@dataclass
class ParsedSegment:
    from_station: str
    to_station: str
    depart_time: str | None
    arrive_time: str | None


def _normalize_station(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[〈〉《》]", "", name)
    name = name.replace("スカイツリー前", "").strip()
    name = name.replace("ヶ", "ケ").replace("が", "ケ")
    return name


def _parse_line(line: str, prev_to: str | None) -> ParsedSegment | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    patterns = [
        # 押上6:34→東武動物公園7:23
        re.compile(rf"^{STATION}{TIME}→{STATION}{TIME}$"),
        # 館林8:16→8:33佐野
        re.compile(rf"^{STATION}{TIME}→{TIME}{STATION}$"),
        # 小山12:41→13:10宇都宮
        re.compile(rf"^{STATION}{TIME}→{TIME}{STATION}$"),
    ]

    m = patterns[0].match(line)
    if m:
        return ParsedSegment(
            from_station=_normalize_station(m.group(1)),
            to_station=_normalize_station(m.group(3)),
            depart_time=m.group(2),
            arrive_time=m.group(4),
        )

    m = patterns[1].match(line)
    if m:
        from_station = _normalize_station(m.group(1)) or prev_to
        to_station = _normalize_station(m.group(4))
        if not from_station:
            return None
        return ParsedSegment(
            from_station=from_station,
            to_station=to_station,
            depart_time=m.group(2),
            arrive_time=m.group(3),
        )

    # フォールバック: 前の到着駅を出発に使う
    m = re.match(rf"^{TIME}→{TIME}{STATION}$", line)
    if m and prev_to:
        return ParsedSegment(
            from_station=prev_to,
            to_station=_normalize_station(m.group(3)),
            depart_time=m.group(1),
            arrive_time=m.group(2),
        )

    # 時刻なし: 押上ー新橋 / 新橋－押上
    m = re.match(rf"^{STATION}{STATION_SEP}{STATION}$", line)
    if m:
        from_station = _normalize_station(m.group(1))
        to_station = _normalize_station(m.group(2))
        if from_station and to_station:
            return ParsedSegment(
                from_station=from_station,
                to_station=to_station,
                depart_time=None,
                arrive_time=None,
            )

    return None


def parse_trip_text(text: str) -> list[ParsedSegment]:
    segments: list[ParsedSegment] = []
    prev_to: str | None = None
    for raw_line in text.splitlines():
        seg = _parse_line(raw_line, prev_to)
        if seg:
            segments.append(seg)
            prev_to = seg.to_station
    return segments
