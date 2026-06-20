from flask import Flask, jsonify, redirect, render_template, request
from dotenv import load_dotenv

load_dotenv()

from config import APP_HOST, APP_PORT, HOME_PATH, SEGMENT_COLORS, VIEW_ONLY
from line_colors import JR_LINE_COLORS, LINE_COLORS, PRIVATE_LINE_COLORS
from db import delete_trip, get_trip, init_db, list_trips, update_segment_geometries, update_trip_meta
from line_geometry import rebuild_trip_geometries
from station_lookup import build_station_cache, get_line_station_names
from trip_service import (
    ensure_trip_geometries,
    export_trip_json,
    export_trip_to_file,
    preview_places,
    preview_segments,
    preview_trip,
    save_confirmed_destinations_for_trip,
    save_confirmed_segments_for_trip,
    save_destinations_for_trip,
    save_places_to_trip,
    save_segments_to_trip,
    save_trip_from_confirmed,
    save_trip_from_json,
    save_trip_from_text,
    save_trip_places_only,
    scan_imports_folder,
)
app = Flask(__name__)


def load_home() -> dict | None:
    if not HOME_PATH.exists():
        return None
    import json

    data = json.loads(HOME_PATH.read_text(encoding="utf-8"))
    if data.get("lat") is None or data.get("lon") is None:
        return None
    return {
        "label": data.get("label") or "自宅",
        "address": data.get("address") or "",
        "lat": float(data["lat"]),
        "lon": float(data["lon"]),
    }


@app.before_request
def _ensure_db():
    init_db()
    if not getattr(app, "_registry_bootstrapped", False):
        from place_registry import bootstrap_from_destinations

        bootstrap_from_destinations()
        app._registry_bootstrapped = True


@app.before_request
def _view_only_guard():
    if not VIEW_ONLY:
        return
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return jsonify({"error": "閲覧専用モードです。変更はできません。"}), 403


@app.context_processor
def _inject_view_only():
    return {"view_only": VIEW_ONLY}


@app.route("/api/places/config")
def api_places_config():
    from place_registry import registry_stats
    from place_research import ai_lookup_available

    return jsonify(
        {
            "ai_available": ai_lookup_available(),
            "registry_count": registry_stats()["count"],
        }
    )


@app.route("/")
def index():
    if VIEW_ONLY:
        return redirect("/destinations")
    return render_template("index.html")


@app.route("/destinations")
def destinations_page():
    return render_template("destinations.html")


@app.route("/api/trips", methods=["GET"])
def api_list_trips():
    return jsonify(list_trips())


def _enrich_trip_segments(trip: dict) -> dict:
    trip = ensure_trip_geometries(trip)
    for seg in trip.get("segments") or []:
        names = get_line_station_names(
            seg.get("from_station", ""),
            seg.get("to_station", ""),
            seg.get("line_name"),
        )
        if names:
            seg["via_stations"] = names
    return trip


@app.route("/api/trips/<int:trip_id>", methods=["GET"])
def api_get_trip(trip_id: int):
    trip = get_trip(trip_id)
    if not trip:
        return jsonify({"error": "not found"}), 404
    return jsonify(_enrich_trip_segments(trip))


@app.route("/api/trips/preview", methods=["POST"])
def api_preview_trip():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text が必要です"}), 400
    try:
        destinations = data.get("destinations") or []
        result = preview_trip(text, destinations)
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/segments/preview", methods=["POST"])
def api_preview_segments():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text が必要です"}), 400
    try:
        return jsonify({"ok": True, **preview_segments(text)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/places/preview", methods=["POST"])
def api_preview_places():
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("destinations") or []
    if not items:
        return jsonify({"error": "destinations が必要です"}), 400
    trip_id = data.get("trip_id")
    return jsonify({"ok": True, **preview_places(items, trip_id=trip_id)})


@app.route("/api/destinations/preview", methods=["POST"])
def api_preview_destinations():
    from destination_candidates import preview_destinations

    data = request.get_json(force=True, silent=True) or {}
    items = data.get("destinations") or []
    if not items:
        return jsonify({"error": "destinations が必要です"}), 400
    return jsonify({"ok": True, "destinations": preview_destinations(items)})


@app.route("/api/trips", methods=["POST"])
def api_create_trip():
    data = request.get_json(force=True, silent=True) or {}
    try:
        if data.get("confirmed_segments"):
            text = data.get("text", "").strip()
            trip_date = data.get("date", "").strip()
            if not trip_date:
                return jsonify({"error": "date が必要です"}), 400
            trip_id = data.get("trip_id")
            if trip_id:
                if data.get("title"):
                    update_trip_meta(trip_id, title=data["title"])
                result = save_confirmed_segments_for_trip(trip_id, text, data["confirmed_segments"])
                result["trip_id"] = trip_id
            else:
                result = save_segments_to_trip(
                    trip_date,
                    text,
                    data["confirmed_segments"],
                    title=data.get("title"),
                )
        elif data.get("confirmed_destinations"):
            trip_date = data.get("date", "").strip()
            if not trip_date:
                return jsonify({"error": "date が必要です"}), 400
            trip_id = data.get("trip_id")
            if trip_id:
                if data.get("title"):
                    update_trip_meta(trip_id, title=data["title"])
                result = save_confirmed_destinations_for_trip(
                    trip_id, data["confirmed_destinations"]
                )
                result["trip_id"] = trip_id
            else:
                result = save_places_to_trip(
                    trip_date,
                    data["confirmed_destinations"],
                    title=data.get("title"),
                )
        elif data.get("format") == "json" or data.get("segments"):
            result = save_trip_from_json(data)
        else:
            text = data.get("text", "").strip()
            trip_date = data.get("date", "").strip()
            if not text or not trip_date:
                return jsonify({"error": "date と text が必要です"}), 400
            result = save_trip_from_text(
                trip_date,
                text,
                title=data.get("title"),
                destinations_text=data.get("destinations_text"),
            )
        trip = get_trip(result["trip_id"])
        return jsonify({"ok": True, "trip": trip, **result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"保存に失敗しました: {exc}"}), 500


@app.route("/api/trips/<int:trip_id>", methods=["DELETE"])
def api_delete_trip(trip_id: int):
    if delete_trip(trip_id):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


@app.route("/api/trips/<int:trip_id>/export", methods=["POST"])
def api_export_trip(trip_id: int):
    trip = get_trip(trip_id)
    if not trip:
        return jsonify({"error": "not found"}), 404
    path = export_trip_to_file(trip)
    return jsonify({"ok": True, "path": str(path), "data": export_trip_json(trip)})


@app.route("/api/import/scan", methods=["POST"])
def api_scan_imports():
    results = scan_imports_folder()
    return jsonify({"ok": True, "results": results, "trips": list_trips()})


@app.route("/api/stations/refresh", methods=["POST"])
def api_refresh_stations():
    from config import STATION_CACHE_PATH

    if STATION_CACHE_PATH.exists():
        STATION_CACHE_PATH.unlink()
    cache = build_station_cache()
    return jsonify({"ok": True, "station_count": len(cache)})


@app.route("/api/trips/<int:trip_id>/rebuild-geometry", methods=["POST"])
def api_rebuild_geometry(trip_id: int):
    trip = get_trip(trip_id)
    if not trip:
        return jsonify({"error": "not found"}), 404
    try:
        updated = rebuild_trip_geometries(trip["segments"])
        update_segment_geometries(trip_id, updated)
        trip = _enrich_trip_segments(get_trip(trip_id))
        return jsonify({"ok": True, "trip": trip})
    except Exception as exc:
        return jsonify({"error": f"再生成に失敗しました: {exc}"}), 500


@app.route("/api/trips/<int:trip_id>/segments", methods=["PUT"])
def api_update_segments(trip_id: int):
    trip = get_trip(trip_id)
    if not trip:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip()
    confirmed = data.get("confirmed_segments") or []
    if not text or not confirmed:
        return jsonify({"error": "text と confirmed_segments が必要です"}), 400
    try:
        result = save_confirmed_segments_for_trip(trip_id, text, confirmed)
        trip = get_trip(trip_id)
        return jsonify({"ok": True, "trip": trip, **result})
    except Exception as exc:
        return jsonify({"error": f"保存に失敗しました: {exc}"}), 500


@app.route("/api/trips/<int:trip_id>/destinations", methods=["PUT"])
def api_update_destinations(trip_id: int):
    trip = get_trip(trip_id)
    if not trip:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("destinations_text", "").strip()
    if "destinations" not in data and not text:
        return jsonify({"error": "destinations または destinations_text が必要です"}), 400
    try:
        if "confirmed_destinations" in data:
            result = save_confirmed_destinations_for_trip(
                trip_id, data["confirmed_destinations"]
            )
        elif "destinations" in data:
            from destination_service import enrich_destinations_from_json
            from db import replace_destinations

            destinations = enrich_destinations_from_json(data["destinations"])
            replace_destinations(trip_id, destinations)
            result = {"destination_count": len(destinations)}
        else:
            result = save_destinations_for_trip(trip_id, text)
        trip = get_trip(trip_id)
        return jsonify({"ok": True, "trip": trip, **result})
    except Exception as exc:
        return jsonify({"error": f"保存に失敗しました: {exc}"}), 500


@app.route("/api/meta")
def api_meta():
    return jsonify({
        "segment_colors": SEGMENT_COLORS,
        "line_colors": LINE_COLORS,
        "jr_line_colors": JR_LINE_COLORS,
        "private_line_colors": PRIVATE_LINE_COLORS,
        "destination_color": "#7b2cbf",
        "home": load_home(),
        "view_only": VIEW_ONLY,
    })


if __name__ == "__main__":
    init_db()
    if VIEW_ONLY:
        print(f"閲覧専用モード: http://{APP_HOST}:{APP_PORT}/destinations")
    app.run(debug=not VIEW_ONLY, host=APP_HOST, port=APP_PORT)
