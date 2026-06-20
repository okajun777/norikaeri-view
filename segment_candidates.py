from typing import Any

from station_lookup import (
    _station_index_on_line,
    build_line_cache,
    collect_station_records,
    find_all_direct_lines,
    find_all_transfer_paths,
    get_line_station_names,
    lookup_station_pair,
)
from parser import parse_trip_text


def get_station_records(name: str) -> list[dict]:
    return collect_station_records(name)


def _record_for_line(
    records: list[dict],
    line_name: str,
    input_name: str,
) -> dict | None:
    matched = [r for r in records if r.get("line_name") == line_name]
    if matched:
        return matched[0]

    lines = build_line_cache()
    line = lines.get(line_name)
    if not line:
        return None
    stations = line.get("stations", [])
    idx = _station_index_on_line(stations, input_name)
    if idx is None:
        return None
    st = stations[idx]
    return {
        "name": st["name"],
        "lat": st["lat"],
        "lon": st["lon"],
        "line_name": line_name,
        "operator": line.get("operator") or "",
    }


def _make_option(
    from_rec: dict,
    to_rec: dict,
    *,
    line_name: str | None,
    operator: str | None,
    label_suffix: str = "",
    is_transfer: bool = False,
    from_input: str | None = None,
    to_input: str | None = None,
) -> dict[str, Any]:
    suffix = f" {label_suffix}" if label_suffix else ""
    line_label = line_name or "路線未特定"
    via_names = (
        get_line_station_names(
            from_input or from_rec["name"],
            to_input or to_rec["name"],
            line_name,
        )
        if line_name and not is_transfer
        else None
    )
    if via_names:
        route_label = " → ".join(via_names)
    else:
        route_label = f"{from_rec['name']} → {to_rec['name']}"
    label = f"{line_label}（{route_label}）{suffix}".strip()
    return {
        "line_name": line_name,
        "operator": operator,
        "resolved_from": from_rec["name"],
        "resolved_to": to_rec["name"],
        "from_lat": from_rec["lat"],
        "from_lon": from_rec["lon"],
        "to_lat": to_rec["lat"],
        "to_lon": to_rec["lon"],
        "label": label,
        "is_transfer": is_transfer,
        "via_stations": via_names,
    }


def _option_key(opt: dict) -> tuple:
    if opt.get("is_transfer"):
        lines = tuple(opt.get("transfer_lines") or ())
        return ("transfer", lines, opt.get("transfer_station"))
    return ("direct", opt.get("line_name"))


def get_segment_options(
    from_name: str,
    to_name: str,
    prev_line: str | None = None,
) -> list[dict[str, Any]]:
    from_records = get_station_records(from_name)
    to_records = get_station_records(to_name)
    options: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def add_option(opt: dict[str, Any]) -> None:
        key = _option_key(opt)
        if key in seen:
            return
        seen.add(key)
        options.append(opt)

    for line_name in find_all_direct_lines(from_name, to_name):
        f_rec = _record_for_line(from_records, line_name, from_name)
        t_rec = _record_for_line(to_records, line_name, to_name)
        if not f_rec or not t_rec:
            continue
        add_option(
            _make_option(
                f_rec,
                t_rec,
                line_name=line_name,
                operator=f_rec.get("operator"),
                from_input=from_name,
                to_input=to_name,
            )
        )

    for transfer in find_all_transfer_paths(from_name, to_name):
        line_a = transfer[0][2]
        line_b = transfer[1][2]
        transfer_name = transfer[0][1]
        f_rec = _record_for_line(from_records, line_a, from_name)
        t_rec = _record_for_line(to_records, line_b, to_name)
        if not f_rec or not t_rec:
            continue
        opt = _make_option(
            f_rec,
            t_rec,
            line_name=line_a,
            operator=f_rec.get("operator"),
            label_suffix=f"→ {line_b}（{transfer_name}乗換）",
            is_transfer=True,
            from_input=from_name,
            to_input=to_name,
        )
        opt["transfer_lines"] = [line_a, line_b]
        opt["transfer_station"] = transfer_name
        add_option(opt)

    if not options and from_records and to_records:
        f_rec, t_rec, line, op = lookup_station_pair(from_name, to_name, prev_line=prev_line)
        if f_rec and t_rec:
            add_option(
                _make_option(
                    {
                        "name": f_rec.name,
                        "lat": f_rec.lat,
                        "lon": f_rec.lon,
                        "line_name": f_rec.line_name,
                        "operator": f_rec.operator,
                    },
                    {
                        "name": t_rec.name,
                        "lat": t_rec.lat,
                        "lon": t_rec.lon,
                        "line_name": t_rec.line_name,
                        "operator": t_rec.operator,
                    },
                    line_name=line,
                    operator=op,
                    from_input=from_name,
                    to_input=to_name,
                )
            )

    recommended = lookup_station_pair(from_name, to_name, prev_line=prev_line)
    rec_key = None
    if recommended[0] and recommended[1]:
        rec_key = (
            recommended[0].name,
            recommended[1].name,
            recommended[2],
        )

    def sort_key(opt: dict) -> tuple[int, int, str]:
        key = (opt["resolved_from"], opt["resolved_to"], opt.get("line_name"))
        is_rec = key == rec_key
        is_transfer = opt.get("is_transfer", False)
        return (0 if is_rec else 1, 1 if is_transfer else 0, opt["label"])

    options.sort(key=sort_key)

    for i, opt in enumerate(options):
        opt["id"] = f"opt_{i}"

    return options


def preview_trip_segments(text: str) -> list[dict[str, Any]]:
    parsed = parse_trip_text(text)
    if not parsed:
        raise ValueError("有効な区間が見つかりませんでした。入力形式を確認してください。")

    preview = []
    prev_line: str | None = None
    for seq, seg in enumerate(parsed, start=1):
        options = get_segment_options(seg.from_station, seg.to_station, prev_line=prev_line)
        recommended_id = options[0]["id"] if options else None
        if options and options[0].get("line_name"):
            prev_line = options[0]["line_name"]
        preview.append(
            {
                "seq": seq,
                "from_station": seg.from_station,
                "to_station": seg.to_station,
                "depart_time": seg.depart_time,
                "arrive_time": seg.arrive_time,
                "recommended_option_id": recommended_id,
                "options": options,
            }
        )
    return preview
