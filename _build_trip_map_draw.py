from pathlib import Path

app_lines = Path("static/app.js").read_text(encoding="utf-8").splitlines()

dist_start = next(i for i, l in enumerate(app_lines) if l.startswith("function haversineM"))
dist_end = next(i for i, l in enumerate(app_lines) if l.startswith("function namesSimilar"))
ns_start = dist_end
ns_end = next(i for i, l in enumerate(app_lines) if l.startswith("function isDuplicateDestAndStation"))
draw_start = next(i for i, l in enumerate(app_lines) if l.startswith("function pointNearGeometry"))
draw_end = next(i for i, l in enumerate(app_lines) if l.startswith("function showTransferDetail"))
const_start = next(i for i, l in enumerate(app_lines) if l.startswith("const LINE_WEIGHT"))
const_end = draw_start

block = "\n".join(
    app_lines[dist_start:dist_end]
    + app_lines[ns_start:ns_end]
    + app_lines[const_start:const_end]
    + app_lines[draw_start:draw_end]
)
block = block.replace(
    "function drawSegmentPolylines(\n  seg,\n  segIndex,\n  segments,\n  drawGeom,\n  color,\n  distText,\n  layerGroup\n)",
    "function drawSegmentPolylines(\n  seg,\n  segIndex,\n  segments,\n  drawGeom,\n  color,\n  distText,\n  layerGroup,\n  onSegmentDetail\n)",
)
block = block.replace(
    'line.on("click", () => showSegmentDetail(seg, segIndex, segments));',
    'line.on("click", () => onSegmentDetail && onSegmentDetail(seg, segIndex, segments));',
)

footer = Path("_trip_map_draw_footer.js").read_text(encoding="utf-8")
header = "/* Shared trip map drawing */\nwindow.TripMapDraw = (function () {\n"
out = header + block + footer
Path("static/trip-map-draw.js").write_text(out, encoding="utf-8")
print("ok", len(out))
