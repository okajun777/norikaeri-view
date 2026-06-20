"""閲覧専用の静的サイトを docs/ に出力（GitHub Pages / Firebase 等）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import BASE_DIR, SEGMENT_COLORS
from db import get_trip, init_db, list_trips
from line_colors import JR_LINE_COLORS, LINE_COLORS, PRIVATE_LINE_COLORS
from trip_service import ensure_trip_geometries

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_PATH = BASE_DIR / "templates" / "view_site.html"
OUT_DIR = BASE_DIR / "docs"

JST = timezone(timedelta(hours=9))


def load_home() -> dict | None:
    from app import load_home as _load

    return _load()


def export_view_site(out_dir: Path | None = None) -> Path:
    out = out_dir or OUT_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    init_db()
    trips = []
    for row in list_trips():
        trip = get_trip(row["id"])
        if trip:
            trips.append(ensure_trip_geometries(trip))

    exported_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    view_data = {
        "trips": trips,
        "meta": {
            "segment_colors": SEGMENT_COLORS,
            "line_colors": LINE_COLORS,
            "jr_line_colors": JR_LINE_COLORS,
            "private_line_colors": PRIVATE_LINE_COLORS,
            "home": load_home(),
            "view_only": True,
            "exported_at": exported_at,
        },
    }

    (out / "view-data.js").write_text(
        "window.VIEW_DATA = " + json.dumps(view_data, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    html = TEMPLATE_PATH.read_text(encoding="utf-8").replace("{{ exported_at }}", exported_at)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    for name in ("style.css", "map-ui.js", "trip-map-draw.js", "destinations.js"):
        shutil.copy2(STATIC_DIR / name, out / name)

    return out


def main() -> None:
    out = export_view_site()
    init_db()
    trip_count = len(list_trips())
    dest_count = sum(
        len((get_trip(t["id"]) or {}).get("destinations") or []) for t in list_trips()
    )
    print(f"Exported: {out}")
    print(f"  記録 {trip_count} 件 / 行った場所 {dest_count} か所")
    print()
    print("【おすすめ】GitHub Pages（無料・HTTPS）")
    print("  1. GitHub に private リポジトリを作成")
    print("  2. deploy-github.bat で push")
    print("  3. リポジトリ Settings → Pages → Source: GitHub Actions")
    print()
    print("【Google】Firebase Hosting")
    print("  firebase login && firebase init hosting  → public: docs")
    print("  export-view.bat のあと firebase deploy")
    print()
    print("【その他】Netlify Drop に docs フォルダをドラッグ")
    print("  https://app.netlify.com/drop")


if __name__ == "__main__":
    main()
