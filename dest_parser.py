import re
from dataclasses import dataclass


TIME = r"(\d{1,2}:\d{2})"


@dataclass
class ParsedDestination:
    name: str
    arrive_time: str | None = None
    depart_time: str | None = None
    memo: str | None = None


def _normalize_name(name: str) -> str:
    return name.strip()


def _parse_line(line: str) -> ParsedDestination | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    m = re.match(rf"^{TIME}-{TIME}\s+(.+)$", line)
    if m:
        return ParsedDestination(
            name=_normalize_name(m.group(3)),
            arrive_time=m.group(1),
            depart_time=m.group(2),
        )

    m = re.match(rf"^(.+?)\s+{TIME}-{TIME}(?:\s+(.+))?$", line)
    if m:
        return ParsedDestination(
            name=_normalize_name(m.group(1)),
            arrive_time=m.group(2),
            depart_time=m.group(3),
            memo=m.group(4).strip() if m.group(4) else None,
        )

    m = re.match(rf"^(.+?)\s+{TIME}(?:\s+(.+))?$", line)
    if m:
        return ParsedDestination(
            name=_normalize_name(m.group(1)),
            arrive_time=m.group(2),
            memo=m.group(3).strip() if m.group(3) else None,
        )

    return ParsedDestination(name=_normalize_name(line))


def parse_destinations_text(text: str) -> list[ParsedDestination]:
    results: list[ParsedDestination] = []
    for line in text.splitlines():
        dest = _parse_line(line)
        if dest and dest.name:
            results.append(dest)
    return results
