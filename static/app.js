let map;
let layerGroup;
let colors = [];
let lineColorMap = {};
let jrLineColorMap = {};
let privateLineColorMap = {};
let destinationColor = "#7b2cbf";
let selectedTripId = null;
let creatingNewTrip = false;
let pendingSegmentPreview = null;
let pendingPlacePreview = null;
let editingPlaces = [];
let placePinTargetSeq = null;
let placePinMarker = null;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "API error");
  return data;
}

const MAP_WHEEL_ZOOM_STEP = 1 / 18;
const MAP_WHEEL_PX_PER_ZOOM = 180;

function initMap() {
  map = L.map("map", {
    zoomSnap: MAP_WHEEL_ZOOM_STEP,
    wheelPxPerZoomLevel: MAP_WHEEL_PX_PER_ZOOM,
  }).setView([36.2, 139.7], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    maxZoom: 18,
  }).addTo(map);
  layerGroup = L.layerGroup().addTo(map);

  window.addEventListener("resize", () => {
    if (map) map.invalidateSize();
  });
  requestAnimationFrame(() => {
    if (map) map.invalidateSize();
  });

  map.on("click", (e) => {
    if (!placePinTargetSeq || !pendingPlacePreview) return;
    const dest = pendingPlacePreview.destinations.find((d) => d.seq === placePinTargetSeq);
    if (!dest) return;
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;
    const manual = {
      id: "dopt_manual",
      label: `地図で指定（${lat.toFixed(5)}, ${lon.toFixed(5)}）`,
      resolved_name: dest.name,
      lat,
      lon,
      source: "manual",
      kind: "manual",
    };
    dest.options = [manual];
    dest.recommended_option_id = manual.id;
    disablePlacePinMode();
    if (placePinMarker) {
      layerGroup.removeLayer(placePinMarker);
    }
    placePinMarker = L.marker([lat, lon]).addTo(layerGroup);
    renderPlaceConfirm(pendingPlacePreview);
    map.setView([lat, lon], 15);
  });
}

function setDefaultDate() {
  const input = document.getElementById("trip-date");
  const d = new Date();
  d.setDate(d.getDate() - 1);
  input.value = d.toISOString().slice(0, 10);
}

function formatDestinationLine(dest) {
  let line = dest.name;
  if (dest.arrive_time && dest.depart_time) {
    line += ` ${dest.arrive_time}-${dest.depart_time}`;
  } else if (dest.arrive_time) {
    line += ` ${dest.arrive_time}`;
  }
  if (dest.memo) line += ` (${dest.memo})`;
  return line;
}

function destinationsToText(destinations) {
  return (destinations || []).map(formatDestinationLine).join("\n");
}

function haversineM(lat1, lon1, lat2, lon2) {
  const r = 6371000;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dp / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

function geometryLengthM(geometry) {
  if (!geometry || geometry.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < geometry.length; i++) {
    const [lon1, lat1] = geometry[i - 1];
    const [lon2, lat2] = geometry[i];
    total += haversineM(lat1, lon1, lat2, lon2);
  }
  return total;
}

function segmentDistanceM(seg) {
  if (seg.geometry && seg.geometry.length >= 2) {
    return { meters: geometryLengthM(seg.geometry), mode: "route" };
  }
  if (seg.from_lat && seg.from_lon && seg.to_lat && seg.to_lon) {
    return {
      meters: haversineM(seg.from_lat, seg.from_lon, seg.to_lat, seg.to_lon),
      mode: "straight",
    };
  }
  return null;
}

function formatDistance(meters) {
  if (meters == null || !Number.isFinite(meters)) return "-";
  if (meters >= 1000) return `約 ${(meters / 1000).toFixed(1)} km`;
  return `約 ${Math.round(meters)} m`;
}

function tripTotalDistanceM(segments) {
  let total = 0;
  let hasAny = false;
  for (const seg of segments || []) {
    const dist = segmentDistanceM(seg);
    if (dist) {
      total += dist.meters;
      hasAny = true;
    }
  }
  return hasAny ? total : null;
}

function namesSimilar(a, b) {
  const na = (a || "").replace(/\s/g, "");
  const nb = (b || "").replace(/\s/g, "");
  if (!na || !nb) return false;
  return na.includes(nb) || nb.includes(na);
}

function isDuplicateDestAndStation(dest, stationName, lat, lon) {
  if (!dest.lat || !dest.lon || !lat || !lon) return false;
  return haversineM(dest.lat, dest.lon, lat, lon) < 120 && namesSimilar(dest.name, stationName);
}

function readPlaceForm() {
  const name = document.getElementById("place-name").value.trim();
  const arrive = document.getElementById("place-arrive").value.trim();
  const depart = document.getElementById("place-depart").value.trim();
  const memo = document.getElementById("place-memo").value.trim();
  return { name, arrive_time: arrive || null, depart_time: depart || null, memo: memo || null };
}

function clearPlaceForm() {
  document.getElementById("place-name").value = "";
  document.getElementById("place-arrive").value = "";
  document.getElementById("place-depart").value = "";
  document.getElementById("place-memo").value = "";
}

function renderEditableDestinationList(listId, items, { onRemove, onClick }) {
  const list = document.getElementById(listId);
  list.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "dest-empty";
    li.textContent = "行った場所なし";
    list.appendChild(li);
    return;
  }
  items.forEach((dest, i) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "dest-label";
    const hasGeo = dest.lat && dest.lon;
    if (!hasGeo && dest._geoPending === false) {
      label.classList.add("no-geo");
    }
    label.textContent = `${i + 1}. ${formatDestinationLine(dest)}${hasGeo || dest._geoPending !== false ? "" : " (位置不明)"}`;
    if (onClick && hasGeo) {
      label.addEventListener("click", () => onClick(dest, i));
    }
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-dest-btn";
    removeBtn.textContent = "削除";
    removeBtn.addEventListener("click", () => onRemove(i));
    li.appendChild(label);
    li.appendChild(removeBtn);
    list.appendChild(li);
  });
}

function renderPlaceList() {
  renderEditableDestinationList("place-list", editingPlaces, {
    onRemove: (index) => {
      editingPlaces.splice(index, 1);
      renderPlaceList();
    },
    onClick: (dest, i) => {
      if (dest.lat && dest.lon) {
        map.setView([dest.lat, dest.lon], 14);
        showDestinationDetail(dest, i);
      }
    },
  });
  updatePlaceSaveState();
}

function canSavePlacesDirectly() {
  return (
    editingPlaces.length > 0 &&
    editingPlaces.every((p) => p.name && p.lat != null && p.lon != null)
  );
}

function placesNeedGeocode() {
  return editingPlaces.some((p) => p.name && (p.lat == null || p.lon == null));
}

function updatePlaceSaveState() {
  const directBtn = document.getElementById("save-place-direct-btn");
  const previewBtn = document.getElementById("preview-place-btn");
  if (directBtn) {
    directBtn.disabled = !canSavePlacesDirectly();
  }
  if (previewBtn) {
    previewBtn.disabled = !editingPlaces.length;
  }
}

function confirmedPlacesFromEditing() {
  return editingPlaces.map((p) => ({
    name: p.name,
    arrive_time: p.arrive_time,
    depart_time: p.depart_time,
    memo: p.memo,
    resolved_name: p.resolved_name || p.name,
    lat: p.lat,
    lon: p.lon,
    geo_source: p.geo_source || "saved",
  }));
}

async function savePlaces(confirmed) {
  const date = document.getElementById("trip-date").value;
  const title = document.getElementById("trip-title").value.trim();
  if (!date) {
    alert("日付を入力してください");
    return false;
  }
  const payload = { date, title, confirmed_destinations: confirmed };
  const tripId = await resolveTripIdForSave(date, title);
  if (tripId) payload.trip_id = tripId;
  const result = await api("/api/trips", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  selectedTripId = result.trip_id;
  creatingNewTrip = false;
  clearPlaceConfirm();
  await refreshTrips();
  if (result.trip) {
    drawTrip(result.trip);
    updateSelectedTripHint(result.trip);
  }
  return true;
}

function addPlaceToList() {
  const item = readPlaceForm();
  if (!item.name) {
    alert("行った場所の名前を入力してください");
    return false;
  }
  editingPlaces.push({ ...item, _geoPending: true });
  clearPlaceForm();
  renderPlaceList();
  return true;
}

function renderTripList(trips) {
  const list = document.getElementById("trip-list");
  list.innerHTML = "";
  if (!trips.length) {
    const li = document.createElement("li");
    li.className = "trip-empty";
    li.textContent = "記録なし";
    list.appendChild(li);
    return;
  }
  trips.forEach((trip) => {
    const li = document.createElement("li");
    li.dataset.id = trip.id;
    if (trip.id === selectedTripId) li.classList.add("active");
    const destCount = trip.destination_count || 0;

    const body = document.createElement("div");
    body.className = "trip-item-body";
    body.innerHTML = `
      <div><strong>${trip.title || trip.trip_date}</strong></div>
      <div class="trip-meta">${trip.trip_date} / ${trip.segment_count}区間 / 行った場所${destCount}件</div>
    `;
    body.addEventListener("click", () => selectTrip(trip.id));

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-trip-btn";
    removeBtn.textContent = "削除";
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteTrip(trip.id, trip.title || trip.trip_date);
    });

    li.appendChild(body);
    li.appendChild(removeBtn);
    list.appendChild(li);
  });
}

function clearTripView() {
  layerGroup.clearLayers();
  document.getElementById("segment-detail").classList.add("hidden");
  document.getElementById("export-btn").disabled = true;
  document.getElementById("rebuild-btn").disabled = true;
  editingPlaces = [];
  renderPlaceList();
  renderSegmentList([]);
  document.getElementById("selected-trip-hint").textContent =
    "区間と行った場所は別々に入力し、同じ日付の記録にまとめて保存されます";
}

function startNewTrip() {
  selectedTripId = null;
  creatingNewTrip = true;
  pendingSegmentPreview = null;
  pendingPlacePreview = null;
  clearSegmentConfirm();
  clearPlaceConfirm();
  setDefaultDate();
  document.getElementById("trip-title").value = "";
  document.getElementById("trip-text").value = "";
  clearPlaceForm();
  clearTripView();
  document.getElementById("selected-trip-hint").textContent =
    "新規記録 — 入力後に保存すると新しい記録として登録されます";
  refreshTrips({ autoSelect: false });
}

function getSelectedOptionForSegment(seg) {
  if (!seg.options?.length) return null;
  const select = document.querySelector(`select[data-kind="segment"][data-seq="${seg.seq}"]`);
  const optionId = select ? select.value : seg.recommended_option_id;
  return seg.options.find((o) => o.id === optionId) || seg.options[0];
}

function optionLineLabel(option) {
  if (!option) return "（路線未特定）";
  if (option.is_transfer && option.transfer_lines?.length >= 2) {
    return `${option.transfer_lines[0]} → ${option.transfer_lines[1]}`;
  }
  return option.line_name || "（路線未特定）";
}

function skippedPreviewSegments(preview) {
  return (preview?.segments || []).filter((s) => !s.options?.length);
}

function renderSegmentListFromPreview(preview) {
  if (!preview?.segments?.length) return;
  const segments = preview.segments
    .map((seg) => {
      const option = getSelectedOptionForSegment(seg);
      if (!option) return null;
      return {
        from_station: seg.from_station,
        to_station: seg.to_station,
        depart_time: seg.depart_time,
        arrive_time: seg.arrive_time,
        line_name: optionLineLabel(option),
        operator: option.operator,
      };
    })
    .filter(Boolean);
  renderSegmentList(segments);
}

function renderSegmentList(segments) {
  const list = document.getElementById("segment-list");
  if (!list) return;
  list.innerHTML = "";
  if (!segments?.length) return;

  const lineColors = assignLineColors(segments, colors);
  segments.forEach((seg, i) => {
    const li = document.createElement("li");
    li.className = "segment-list-item";
    const line = seg.line_name || "（路線未特定）";
    const time = `${seg.depart_time || "-"} → ${seg.arrive_time || "-"}`;
    const lineColor = lineColors[i];

    const swatch = document.createElement("span");
    swatch.className = "seg-line-swatch";
    swatch.style.background = lineColor;

    const body = document.createElement("div");
    body.className = "seg-list-body";

    const route = document.createElement("div");
    route.className = "seg-list-route";
    route.innerHTML = `<span class="seg-list-num">区間 ${i + 1}</span> ${seg.from_station} → ${seg.to_station}`;

    const lineEl = document.createElement("div");
    lineEl.className = "seg-list-line";
    lineEl.style.color = lineColor;
    lineEl.textContent = line;

    const timeEl = document.createElement("div");
    timeEl.className = "seg-list-time";
    timeEl.textContent = time;

    body.appendChild(route);
    body.appendChild(lineEl);
    body.appendChild(timeEl);

    li.appendChild(swatch);
    li.appendChild(body);
    li.addEventListener("click", () => showSegmentDetail(seg, i, segments));
    list.appendChild(li);
  });
}

async function resolveTripIdForSave(date, title) {
  if (creatingNewTrip) return null;
  if (selectedTripId) {
    const current = await api(`/api/trips/${selectedTripId}`);
    if (current.trip_date === date) return selectedTripId;
  }
  const trips = await api("/api/trips");
  const sameDate = trips.filter((t) => t.trip_date === date);
  if (title) {
    const matched = sameDate.find((t) => (t.title || "") === title);
    if (matched) return matched.id;
  }
  if (sameDate.length === 1) return sameDate[0].id;
  return null;
}

function updateSelectedTripHint(trip) {
  const el = document.getElementById("selected-trip-hint");
  if (!trip) {
    el.textContent =
      "区間と行った場所は別々に入力し、同じ日付の記録にまとめて保存されます";
    return;
  }
  const segCount = trip.segments ? trip.segments.length : trip.segment_count || 0;
  const placeCount = trip.destinations ? trip.destinations.length : trip.destination_count || 0;
  el.textContent = `記録: ${trip.title || trip.trip_date}（区間${segCount} / 行った場所${placeCount}）`;
}

async function deleteTrip(tripId, label) {
  if (!confirm(`「${label}」を削除しますか？\nこの操作は取り消せません。`)) {
    return;
  }
  try {
    await api(`/api/trips/${tripId}`, { method: "DELETE" });
    if (selectedTripId === tripId) {
      selectedTripId = null;
      clearTripView();
    }
    await refreshTrips();
  } catch (err) {
    alert(err.message);
  }
}

function loadPlacesFromTrip(destinations) {
  editingPlaces = (destinations || []).map((d) => ({
    name: d.name,
    arrive_time: d.arrive_time,
    depart_time: d.depart_time,
    memo: d.memo,
    lat: d.lat,
    lon: d.lon,
    resolved_name: d.resolved_name,
    geo_source: d.geo_source,
  }));
  renderPlaceList();
}

function showSegmentDetail(seg, index, allSegments) {
  const panel = document.getElementById("segment-detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.textContent = `区間 ${index + 1}: ${seg.from_station} → ${seg.to_station}${
    seg.line_name ? `（${seg.line_name}）` : ""
  }`;
  const dist = segmentDistanceM(seg);
  const distLabel = dist
    ? `${formatDistance(dist.meters)}（${dist.mode === "route" ? "線路沿い" : "直線"}）`
    : "-";
  const totalM = tripTotalDistanceM(allSegments);
  const totalHtml =
    totalM != null
      ? `<dt>全区間合計</dt><dd>${formatDistance(totalM)}</dd>`
      : "";
  const viaHtml =
    seg.via_stations && seg.via_stations.length > 1
      ? `<dt>経由駅</dt><dd>${seg.via_stations.join(" → ")}</dd>`
      : "";
  body.innerHTML = `
    <dt>出発</dt><dd>${seg.depart_time || "-"}</dd>
    <dt>到着</dt><dd>${seg.arrive_time || "-"}</dd>
    <dt>路線</dt><dd>${seg.line_name || "（未特定）"}</dd>
    ${viaHtml}
    <dt>事業者</dt><dd>${seg.operator || "-"}</dd>
    <dt>距離</dt><dd>${distLabel}</dd>
    ${totalHtml}
  `;
  openMapDetailPanel(panel);
}

function showDestinationDetail(dest, index) {
  const panel = document.getElementById("segment-detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.textContent = `行った場所 ${index + 1}: ${dest.name}`;
  const time =
    dest.arrive_time && dest.depart_time
      ? `${dest.arrive_time} - ${dest.depart_time}`
      : dest.arrive_time || dest.depart_time || "-";
  const placeLabel =
    dest.geo_source === "station"
      ? `${dest.resolved_name || dest.name}（最寄り駅）`
      : dest.resolved_name || (dest.lat ? "地図上の📍" : "未取得");
  body.innerHTML = `
    <dt>滞在時間</dt><dd>${time}</dd>
    <dt>メモ</dt><dd>${dest.memo || "-"}</dd>
    <dt>場所</dt><dd>${placeLabel}</dd>
  `;
  openMapDetailPanel(panel);
}

const LINE_WEIGHT = 6;
const LINE_OVERLAP_BOTTOM_FACTOR = 2.25;
const LINE_SAME_LINE_TOP_COLOR = "#ffffff";
const LINE_OFFSET_SEP_M = 80;
const LINE_OVERLAP_THRESHOLD_M = 90;
const LINE_OVERLAP_RATIO = 0.2;
const LINE_JUNCTION_MAX_WINDOW = 20;
const LINE_JUNCTION_MIN_RATIO = 0.15;
const MARKER_OVERLAP_THRESHOLD_M = 50;
const MARKER_SEPARATION_M = 100;

function pointNearGeometry(lon, lat, geom, thresholdM) {
  for (const [olon, olat] of geom) {
    if (haversineM(lat, lon, olat, olon) < thresholdM) return true;
  }
  return false;
}

function shouldCollectOverlap(segments, i, j) {
  const gi = segments[i].geometry;
  const gj = segments[j].geometry;
  if (!gi || !gj) return false;

  const trackRatio = segmentOverlapRatio(gi, gj);
  if (sameLineName(segments[i], segments[j])) {
    // 同一路線の分割区間（4→5 など）で線路が重なる場合のみ
    return trackRatio >= LINE_OVERLAP_RATIO;
  }
  if (trackRatio >= LINE_OVERLAP_RATIO) return true;
  if (Math.abs(j - i) === 1 && junctionOverlapRatio(gi, gj) >= LINE_JUNCTION_MIN_RATIO) {
    return true;
  }
  return false;
}

/** 重なりの下線は区間番号が小さい方（先に通った方）だけ描く */
function collectUnderlayOverlapGeometries(segments, index) {
  const result = [];
  for (let j = index + 1; j < segments.length; j++) {
    if (!shouldCollectOverlap(segments, index, j)) continue;
    const geom = segments[j].geometry;
    if (geom && geom.length >= 2) result.push(geom);
  }
  return result;
}

function splitGeometryRuns(geom, flags) {
  const runs = [];
  if (geom.length < 2) return runs;
  let runStart = 0;
  let runOverlap = !!flags[0];
  for (let i = 1; i < geom.length; i++) {
    const overlap = !!flags[i];
    if (overlap !== runOverlap) {
      if (i - runStart >= 1) {
        runs.push({ overlap: runOverlap, geom: geom.slice(runStart, i + 1) });
      }
      runStart = i;
      runOverlap = overlap;
    }
  }
  if (geom.length - runStart >= 2) {
    runs.push({ overlap: runOverlap, geom: geom.slice(runStart) });
  }
  return runs;
}

function segmentOverlapFlags(segGeom, otherGeoms) {
  if (!otherGeoms.length) return segGeom.map(() => false);
  return segGeom.map(([lon, lat]) =>
    otherGeoms.some((geom) => pointNearGeometry(lon, lat, geom, LINE_OVERLAP_THRESHOLD_M))
  );
}

function segmentOverlapRatio(geomA, geomB) {
  if (!geomA || !geomB || geomA.length < 2 || geomB.length < 2) return 0;
  const samples = geomA.length <= geomB.length ? geomA : geomB;
  const other = geomA.length <= geomB.length ? geomB : geomA;
  let close = 0;
  const step = Math.max(1, Math.floor(samples.length / 24));
  const total = Math.ceil(samples.length / step) || 1;
  for (let i = 0; i < samples.length; i += step) {
    const [lon, lat] = samples[i];
    let minD = Infinity;
    for (const [olon, olat] of other) {
      minD = Math.min(minD, haversineM(lat, lon, olat, olon));
    }
    if (minD < LINE_OVERLAP_THRESHOLD_M) close++;
  }
  return close / total;
}

function junctionOverlapRatio(geomA, geomB) {
  if (!geomA || !geomB || geomA.length < 2 || geomB.length < 2) return 0;
  const maxN = Math.min(
    LINE_JUNCTION_MAX_WINDOW,
    geomA.length - 1,
    geomB.length - 1
  );
  let best = 0;
  for (let n = 3; n <= maxN; n += 1) {
    best = Math.max(best, segmentOverlapRatio(geomA.slice(-n), geomB.slice(0, n)));
  }
  return best;
}

function segmentsOverlap(segments, i, j) {
  const gi = segments[i].geometry;
  const gj = segments[j].geometry;
  if (!gi || !gj) return false;
  if (segmentOverlapRatio(gi, gj) >= LINE_OVERLAP_RATIO) return true;
  if (j === i + 1 && junctionOverlapRatio(gi, gj) >= LINE_JUNCTION_MIN_RATIO) {
    return true;
  }
  return false;
}

function lookupLineColor(name, colorMap) {
  if (colorMap[name]) return colorMap[name];
  let best = null;
  let bestLen = 0;
  for (const [key, color] of Object.entries(colorMap)) {
    if (name.includes(key) || key.includes(name)) {
      if (key.length > bestLen) {
        bestLen = key.length;
        best = color;
      }
    }
  }
  return best;
}

function isJrLine(lineName, operator) {
  const name = (lineName || "").trim();
  const op = (operator || "").trim();
  return name.startsWith("JR") || name.includes("JR") || op.includes("JR");
}

function isPrivateLine(lineName, operator) {
  const name = (lineName || "").trim();
  const op = (operator || "").trim();
  if (name.startsWith("東武")) return true;
  return /東武|西武|京王|小田急|東急|京成|京急|新京成|北総|芝山|相模|相鉄|東葉|つくば/.test(op);
}

function resolveOfficialLineColor(lineName, operator) {
  const name = (lineName || "").trim();
  if (!name) return null;

  const maps = isJrLine(name, operator)
    ? [jrLineColorMap, privateLineColorMap, lineColorMap]
    : isPrivateLine(name, operator)
      ? [privateLineColorMap, jrLineColorMap, lineColorMap]
      : [jrLineColorMap, privateLineColorMap, lineColorMap];

  for (const map of maps) {
    const color = lookupLineColor(name, map);
    if (color) return color;
  }
  return null;
}

function assignLineColors(segments, palette) {
  const lineColors = new Map();
  const result = [];
  let nextIdx = 0;

  for (const seg of segments) {
    const line = (seg.line_name || "").trim() || `_unknown_${result.length}`;
    const cacheKey = `${line}\0${seg.operator || ""}`;
    if (!lineColors.has(cacheKey)) {
      const official = resolveOfficialLineColor(line, seg.operator);
      lineColors.set(
        cacheKey,
        official || palette[nextIdx % palette.length] || "#e63946"
      );
      if (!official) nextIdx += 1;
    }
    result.push(lineColors.get(cacheKey));
  }
  return result;
}

function sameLineName(segA, segB) {
  const lineA = (segA.line_name || "").trim();
  const lineB = (segB.line_name || "").trim();
  return Boolean(lineA && lineB && lineA === lineB);
}

function getSegmentUnderlayRuns(drawGeom, segIndex, segments) {
  if (!drawGeom || drawGeom.length < 2) return [];
  const others = collectUnderlayOverlapGeometries(segments, segIndex);
  if (!others.length) return [];
  const flags = segmentOverlapFlags(segments[segIndex].geometry, others);
  const runs = splitGeometryRuns(drawGeom, flags);
  return runs.filter((run) => run.overlap);
}

function collectSameLineTopOverlapGeometries(segments, index) {
  const result = [];
  for (let j = 0; j < index; j++) {
    if (!sameLineName(segments[index], segments[j])) continue;
    if (!shouldCollectOverlap(segments, index, j)) continue;
    const geom = segments[j].geometry;
    if (geom && geom.length >= 2) result.push(geom);
  }
  return result;
}

function assignOverlapLineOffsets(segments) {
  const offsets = new Array(segments.length).fill(0);
  const refIndex = new Array(segments.length).fill(null);
  const half = LINE_OFFSET_SEP_M / 2;

  for (let i = 0; i < segments.length; i++) {
    for (let j = i + 1; j < segments.length; j++) {
      if (!shouldCollectOverlap(segments, i, j)) continue;
      offsets[i] = -half;
      offsets[j] = half;
      refIndex[i] = i;
      refIndex[j] = i;
    }
  }
  return { offsets, refIndex };
}

function offsetPointPerpendicular(lon, lat, lon1, lat1, lon2, lat2, offsetM) {
  const mPerDegLat = 110540;
  const mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180);
  const dx = (lon2 - lon1) * mPerDegLon;
  const dy = (lat2 - lat1) * mPerDegLat;
  const len = Math.hypot(dx, dy) || 1;
  const ox = (-dy / len) * offsetM;
  const oy = (dx / len) * offsetM;
  return [lon + ox / mPerDegLon, lat + oy / mPerDegLat];
}

function offsetGeometryPerpendicular(geometry, offsetM, refGeometry) {
  if (!offsetM || !geometry?.length) return geometry;
  const ref = refGeometry?.length >= 2 ? refGeometry : geometry;
  return geometry.map(([lon, lat], i) => {
    const refIdx = Math.min(
      Math.max(
        0,
        Math.round((i / Math.max(1, geometry.length - 1)) * (ref.length - 2))
      ),
      ref.length - 2
    );
    const [r0lon, r0lat] = ref[refIdx];
    const [r1lon, r1lat] = ref[refIdx + 1];
    return offsetPointPerpendicular(lon, lat, r0lon, r0lat, r1lon, r1lat, offsetM);
  });
}

function buildDrawGeometries(segments, lineOffsets) {
  return segments.map((seg, index) => {
    const geom = seg.geometry;
    if (!geom?.length) return geom;
    const off = lineOffsets.offsets[index];
    if (!off) return geom;
    const refIdx = lineOffsets.refIndex[index] ?? index;
    const refGeom = segments[refIdx]?.geometry;
    return offsetGeometryPerpendicular(geom, off, refGeom);
  });
}

function drawSegmentPolylines(
  seg,
  segIndex,
  segments,
  drawGeom,
  color,
  distText,
  layerGroup
) {
  if (!drawGeom || drawGeom.length < 2) return [];
  const boundsPoints = [];
  const popupHtml =
    `<b>区間 ${segIndex + 1}</b><br>${seg.from_station} → ${seg.to_station}` +
    (seg.line_name ? `<br>${seg.line_name}` : "") +
    (distText ? `<br>${distText}` : "");

  const sameLineTop = collectSameLineTopOverlapGeometries(segments, segIndex);
  const runs =
    sameLineTop.length > 0
      ? splitGeometryRuns(
          drawGeom,
          segmentOverlapFlags(seg.geometry, sameLineTop)
        )
      : [{ overlap: false, geom: drawGeom }];

  for (const run of runs) {
    const latlngs = geometryToLatLngs(run.geom);
    if (latlngs.length < 2) continue;
    const line = L.polyline(latlngs, {
      color: run.overlap ? LINE_SAME_LINE_TOP_COLOR : color,
      weight: LINE_WEIGHT,
      opacity: 0.95,
      lineCap: "round",
      lineJoin: "round",
    });
    line.on("click", () => showSegmentDetail(seg, segIndex, segments));
    line.bindPopup(popupHtml);
    line.addTo(layerGroup);
    latlngs.forEach((p) => boundsPoints.push(p));
  }

  return boundsPoints;
}

function parseTime(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function shiftLonMeters(lat, lon, meters) {
  const dLon =
    (meters / (6371000 * Math.cos((lat * Math.PI) / 180))) * (180 / Math.PI);
  return [lat, lon + dLon];
}

function clusterMarkers(markers, thresholdM) {
  const parent = markers.map((_, i) => i);
  const find = (i) => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  const union = (a, b) => {
    parent[find(a)] = find(b);
  };

  for (let i = 0; i < markers.length; i++) {
    for (let j = i + 1; j < markers.length; j++) {
      if (
        haversineM(markers[i].lat, markers[i].lon, markers[j].lat, markers[j].lon) <
        thresholdM
      ) {
        union(i, j);
      }
    }
  }

  const groups = new Map();
  markers.forEach((marker, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(marker);
  });
  return [...groups.values()];
}

function markerSpreadOrder(marker) {
  if (marker.kind === "transfer") return marker.transfer.seq;
  if (marker.kind === "station") {
    return (marker.segIndex + 1) * 2 + (marker.endpoint === "from" ? 0 : 1);
  }
  if (marker.kind === "dest") return 10000 + marker.index;
  return 0;
}

function applyMarkerSpreadToGroup(markers) {
  for (const cluster of clusterMarkers(markers, MARKER_OVERLAP_THRESHOLD_M)) {
    if (cluster.length <= 1) continue;
    cluster.sort(
      (a, b) =>
        markerSpreadOrder(a) - markerSpreadOrder(b) ||
        String(a.id).localeCompare(String(b.id))
    );

    const n = cluster.length;
    for (let i = 0; i < n; i++) {
      const slot = i - (n - 1) / 2;
      const offsetM = slot * MARKER_SEPARATION_M;
      const target = cluster[i];
      [target.displayLat, target.displayLon] = shiftLonMeters(
        target.lat,
        target.lon,
        offsetM
      );
    }
  }
}

function applyTransferSpread(markers) {
  const groups = new Map();
  for (const marker of markers) {
    if (marker.kind !== "transfer") continue;
    const key =
      marker.transfer.stationKey ||
      `${marker.lat.toFixed(5)},${marker.lon.toFixed(5)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(marker);
  }

  for (const group of groups.values()) {
    if (group.length <= 1) continue;
    group.sort((a, b) => a.transfer.seq - b.transfer.seq);
    const n = group.length;
    for (let i = 0; i < n; i++) {
      const slot = i - (n - 1) / 2;
      const offsetM = slot * MARKER_SEPARATION_M;
      [group[i].displayLat, group[i].displayLon] = shiftLonMeters(
        group[i].lat,
        group[i].lon,
        offsetM
      );
    }
  }
}

function applyMarkerSpread(markers) {
  const spread = markers.map((marker) => ({
    ...marker,
    displayLat: marker.lat,
    displayLon: marker.lon,
  }));
  applyTransferSpread(spread);
  applyMarkerSpreadToGroup(spread.filter((m) => m.kind === "station"));
  applyMarkerSpreadToGroup(spread.filter((m) => m.kind === "dest"));
  return spread;
}

function geometryToLatLngs(geometry) {
  return geometry.map(([lon, lat]) => [lat, lon]);
}

function isSightseeingStop(segment, destinations) {
  if (!destinations?.length) return false;
  return destinations.some(
    (dest) =>
      namesSimilar(dest.name, segment.to_station) ||
      namesSimilar(dest.resolved_name || "", segment.to_station)
  );
}

function segmentAlighted(seg) {
  return seg.alighted !== false && seg.alighted !== 0;
}

function stationKey(name) {
  return (name || "").replace(/\s/g, "").trim();
}

function collectTransferPoints(segments, destinations = []) {
  const canonical = new Map();
  const transfers = [];

  function coordsForJunction(prev, next, samePlace) {
    if (samePlace) {
      const key = stationKey(prev.to_station);
      if (!canonical.has(key)) {
        canonical.set(key, {
          lat: prev.to_lat ?? next.from_lat,
          lon: prev.to_lon ?? next.from_lon,
        });
      }
      return { ...canonical.get(key), stationKey: key };
    }
    return {
      lat: next.from_lat ?? prev.to_lat,
      lon: next.from_lon ?? prev.to_lon,
      stationKey: `link-${prev.to_station}-${next.from_station}`,
    };
  }

  for (let i = 0; i < segments.length - 1; i++) {
    const prev = segments[i];
    const next = segments[i + 1];

    if (!segmentAlighted(prev)) continue;
    if (isSightseeingStop(prev, destinations)) continue;

    const samePlace = namesSimilar(prev.to_station, next.from_station);
    const { lat, lon, stationKey: key } = coordsForJunction(prev, next, samePlace);
    if (!lat || !lon) continue;

    transfers.push({
      seq: i + 1,
      stationKey: key,
      name: samePlace ? prev.to_station : `${prev.to_station} → ${next.from_station}`,
      fromSegment: i + 1,
      toSegment: i + 2,
      lat,
      lon,
      prev,
      next,
    });
  }
  return transfers;
}

function createTransferIcon(seq) {
  return L.divIcon({
    className: "",
    html: `<div class="transfer-marker"><span>${seq}</span></div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -13],
  });
}

function showTransferDetail(transfer) {
  const panel = document.getElementById("segment-detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.textContent = `乗換 ${transfer.seq}: ${transfer.name}`;
  body.innerHTML = `
    <dt>区間</dt><dd>${transfer.fromSegment} → ${transfer.toSegment}</dd>
    <dt>降車</dt><dd>${transfer.prev.to_station} ${transfer.prev.arrive_time || ""}</dd>
    <dt>乗車</dt><dd>${transfer.next.from_station} ${transfer.next.depart_time || ""}</dd>
    <dt>路線</dt><dd>${transfer.prev.line_name || "-"} → ${transfer.next.line_name || "-"}</dd>
  `;
  openMapDetailPanel(panel);
}

function createDestinationIcon(seq) {
  return L.divIcon({
    className: "",
    html: `<div class="destination-marker"><span>${seq}</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 28],
    popupAnchor: [0, -28],
  });
}

function drawTrip(trip) {
  layerGroup.clearLayers();
  const bounds = [];
  const destinations = trip.destinations || [];
  const segments = trip.segments || [];
  const lineColors = assignLineColors(segments, colors);
  const lineOffsets = assignOverlapLineOffsets(segments);
  const drawGeometries = buildDrawGeometries(segments, lineOffsets);
  const markerPoints = [];
  const transfers = collectTransferPoints(segments, destinations);

  segments.forEach((seg, i) => {
    const color = lineColors[i];
    const drawGeom = drawGeometries[i];
    for (const run of getSegmentUnderlayRuns(drawGeom, i, segments)) {
      const latlngs = geometryToLatLngs(run.geom);
      if (latlngs.length < 2) continue;
      L.polyline(latlngs, {
        color,
        weight: LINE_WEIGHT * LINE_OVERLAP_BOTTOM_FACTOR,
        opacity: 1,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(layerGroup);
    }
  });

  segments.forEach((seg, i) => {
    const color = lineColors[i];
    const dist = segmentDistanceM(seg);
    const distText = dist ? formatDistance(dist.meters) : "";
    drawSegmentPolylines(
      seg,
      i,
      segments,
      drawGeometries[i],
      color,
      distText,
      layerGroup
    ).forEach((p) => bounds.push(p));
    if (seg.from_lat && seg.from_lon) {
      markerPoints.push({
        id: `seg-${i}-from`,
        kind: "station",
        lat: seg.from_lat,
        lon: seg.from_lon,
        sortKey: parseTime(seg.depart_time) ?? i * 1000,
        seg,
        endpoint: "from",
        color,
        segIndex: i,
      });
    }
    if (seg.to_lat && seg.to_lon) {
      markerPoints.push({
        id: `seg-${i}-to`,
        kind: "station",
        lat: seg.to_lat,
        lon: seg.to_lon,
        sortKey: parseTime(seg.arrive_time) ?? i * 1000 + 1,
        seg,
        endpoint: "to",
        color,
        segIndex: i,
      });
    }
  });

  transfers.forEach((transfer) => {
    markerPoints.push({
      id: `transfer-${transfer.seq}`,
      kind: "transfer",
      lat: transfer.lat,
      lon: transfer.lon,
      sortKey: parseTime(transfer.prev.arrive_time) ?? transfer.seq * 1000,
      transfer,
    });
  });

  destinations.forEach((dest, i) => {
    if (!dest.lat || !dest.lon) return;
    markerPoints.push({
      id: `dest-${i}`,
      kind: "dest",
      lat: dest.lat,
      lon: dest.lon,
      sortKey: parseTime(dest.arrive_time) ?? 10000 + i,
      dest,
      index: i,
    });
  });

  applyMarkerSpread(markerPoints).forEach((marker) => {
    const pos = [marker.displayLat, marker.displayLon];
    bounds.push(pos);

    if (marker.kind === "station") {
      const { seg, endpoint, color } = marker;
      const label = endpoint === "from" ? seg.from_station : seg.to_station;
      const time =
        endpoint === "from" ? seg.depart_time || "" : seg.arrive_time || "";
      const timeLabel = endpoint === "from" ? "発" : "着";
      L.circleMarker(pos, {
        radius: 6,
        color: "#fff",
        weight: 2,
        fillColor: color,
        fillOpacity: 1,
      })
        .bindPopup(`<b>${label}</b><br>${timeLabel} ${time}`)
        .addTo(layerGroup);
      return;
    }

    if (marker.kind === "transfer") {
      const transfer = marker.transfer;
      L.marker(pos, {
        icon: createTransferIcon(transfer.seq),
        zIndexOffset: 900,
      })
        .bindPopup(
          `<b>乗換 ${transfer.seq}: ${transfer.name}</b><br>` +
            `区間 ${transfer.fromSegment}→${transfer.toSegment}<br>` +
            `${transfer.prev.arrive_time || "-"} → ${transfer.next.depart_time || "-"}<br>` +
            `${transfer.prev.line_name || "-"} → ${transfer.next.line_name || "-"}`
        )
        .on("click", () => showTransferDetail(transfer))
        .addTo(layerGroup);
      return;
    }

    if (marker.kind === "dest") {
      const dest = marker.dest;
      const i = marker.index;
      const time =
        dest.arrive_time && dest.depart_time
          ? `${dest.arrive_time} - ${dest.depart_time}`
          : dest.arrive_time || dest.depart_time || "";
      const place = dest.resolved_name || dest.name;
      const placeNote = dest.geo_source === "station" ? "（最寄り駅）" : "";
      L.marker(pos, {
        icon: createDestinationIcon(i + 1),
        zIndexOffset: 1000,
      })
        .bindPopup(`<b>📍 ${dest.name}</b><br>${place}${placeNote}<br>${time}`)
        .on("click", () => showDestinationDetail(dest, i))
        .addTo(layerGroup);
    }
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
  if (trip.destinations?.length) {
    showDestinationDetail(trip.destinations[0], 0);
  } else if (trip.segments.length) {
    showSegmentDetail(trip.segments[0], 0, trip.segments);
  }

  loadPlacesFromTrip(trip.destinations);
  renderSegmentList(trip.segments || []);
}

async function selectTrip(id) {
  selectedTripId = id;
  creatingNewTrip = false;
  pendingSegmentPreview = null;
  pendingPlacePreview = null;
  clearSegmentConfirm();
  clearPlaceConfirm();
  document.getElementById("export-btn").disabled = false;
  document.getElementById("rebuild-btn").disabled = false;
  const trips = await api("/api/trips");
  renderTripList(trips);
  const trip = await api(`/api/trips/${id}`);
  document.getElementById("trip-date").value = trip.trip_date;
  document.getElementById("trip-title").value = trip.title || "";
  document.getElementById("trip-text").value = trip.raw_text || "";
  loadPlacesFromTrip(trip.destinations);
  drawTrip(trip);
  updateSelectedTripHint(trip);
  if (!trip.segments?.length && (trip.raw_text || "").trim()) {
    document.getElementById("selected-trip-hint").textContent +=
      " — 区間テキストは残っています。「区間を確認」から再解析できます";
  }
}

async function refreshTrips({ autoSelect = true } = {}) {
  const trips = await api("/api/trips");
  renderTripList(trips);
  if (selectedTripId) {
    try {
      const trip = await api(`/api/trips/${selectedTripId}`);
      drawTrip(trip);
    } catch {
      selectedTripId = null;
      clearTripView();
    }
  } else if (autoSelect && trips.length) {
    await selectTrip(trips[0].id);
  } else {
    clearTripView();
  }
}

function enablePlacePinMode(seq) {
  placePinTargetSeq = seq;
  if (map) map.getContainer().style.cursor = "crosshair";
  const hint = document.getElementById("place-save-hint");
  if (hint) {
    hint.textContent = "地図をクリックして位置を指定してください（右の地図）";
  }
}

function disablePlacePinMode() {
  placePinTargetSeq = null;
  if (map) map.getContainer().style.cursor = "";
  const hint = document.getElementById("place-save-hint");
  if (hint) hint.textContent = "";
}

function renderOptionCards(containerId, items, type) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  const segmentCount = type === "segment" ? items.length : 0;
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "segment-option-card";
    const title = document.createElement("h4");
    let lineLabel = null;
    if (type === "segment") {
      title.textContent = `区間 ${item.seq}: ${item.from_station} → ${item.to_station}`;
      const times = document.createElement("div");
      times.className = "times";
      times.textContent = `${item.depart_time || "-"} → ${item.arrive_time || "-"}`;
      lineLabel = document.createElement("div");
      lineLabel.className = "segment-line-label";
      card.appendChild(title);
      card.appendChild(times);
      card.appendChild(lineLabel);
    } else {
      title.textContent = `行った場所 ${item.seq}: ${item.name}`;
      const times = document.createElement("div");
      times.className = "times";
      const t =
        item.arrive_time && item.depart_time
          ? `${item.arrive_time} - ${item.depart_time}`
          : item.arrive_time || item.depart_time || "-";
      times.textContent = `滞在: ${t}`;
      card.appendChild(title);
      card.appendChild(times);
      if (item.memo) {
        const memo = document.createElement("div");
        memo.className = "times place-memo";
        memo.textContent = item.memo;
        card.appendChild(memo);
      }
    }
    const select = document.createElement("select");
    select.dataset.kind = type;
    select.dataset.seq = item.seq;
    if (!item.options || !item.options.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "候補が見つかりません";
      select.appendChild(opt);
      select.disabled = true;
      if (type === "destination") {
        const hint = document.createElement("p");
        hint.className = "place-pin-hint";
        hint.textContent =
          "地図データに未登録の可能性があります。下のボタンで地図をクリックして指定できます。";
        card.appendChild(hint);
        const pinBtn = document.createElement("button");
        pinBtn.type = "button";
        pinBtn.className = "place-pin-btn";
        pinBtn.textContent = "地図で位置を指定";
        pinBtn.addEventListener("click", () => enablePlacePinMode(item.seq));
        card.appendChild(pinBtn);
      }
    } else {
      item.options.forEach((option) => {
        const opt = document.createElement("option");
        opt.value = option.id;
        opt.textContent = option.label;
        if (option.id === item.recommended_option_id) opt.selected = true;
        select.appendChild(opt);
      });
    }
    card.appendChild(select);
    if (type === "segment" && lineLabel) {
      const updateLineLabel = () => {
        const optionId = select.value || item.recommended_option_id;
        const option = item.options.find((o) => o.id === optionId) || item.options[0];
        lineLabel.textContent = option
          ? `路線: ${optionLineLabel(option)}`
          : "路線: （未特定）";
      };
      select.addEventListener("change", () => {
        updateLineLabel();
        if (pendingSegmentPreview) renderSegmentListFromPreview(pendingSegmentPreview);
      });
      updateLineLabel();
    }
    if (type === "segment" && item.seq < segmentCount) {
      const alightLabel = document.createElement("label");
      alightLabel.className = "alight-check";
      const alightInput = document.createElement("input");
      alightInput.type = "checkbox";
      alightInput.dataset.kind = "segment-alight";
      alightInput.dataset.seq = item.seq;
      alightInput.checked = item.alighted !== false;
      alightLabel.appendChild(alightInput);
      alightLabel.appendChild(
        document.createTextNode(" 到着駅で降車（オフ＝そのまま乗車・乗換に数えない）")
      );
      card.appendChild(alightLabel);
    }
    if (type === "destination") {
      select.addEventListener("change", () => {
        const option = item.options.find((o) => o.id === select.value);
        if (option?.lat != null && option?.lon != null) {
          map.setView([option.lat, option.lon], 15);
        }
      });
      const initial = item.options.find((o) => o.id === select.value);
      if (initial?.lat != null && initial?.lon != null) {
        map.setView([initial.lat, initial.lon], 14);
      }
    }
    container.appendChild(card);
  });
}

function renderSegmentConfirm(preview) {
  renderOptionCards("segment-options", preview.segments || [], "segment");
  document.getElementById("segment-confirm").classList.remove("hidden");
  const skipped = skippedPreviewSegments(preview);
  const saveable = (preview.segments || []).filter((s) => s.options?.length).length;
  document.getElementById("save-segment-btn").disabled = saveable === 0;
  const hintEl = document.getElementById("segment-save-hint");
  if (skipped.length) {
    hintEl.textContent =
      `${skipped.length} 区間は路線候補がないため保存されません: ` +
      skipped.map((s) => `${s.from_station}→${s.to_station}`).join("、");
  } else {
    hintEl.textContent = "路線を選んで「区間を保存」を押すと、一覧と地図に反映されます";
  }
  renderSegmentListFromPreview(preview);
}

function renderPlaceConfirm(preview) {
  renderOptionCards("place-options", preview.destinations || [], "destination");
  document.getElementById("place-confirm").classList.remove("hidden");
  const destBad = (preview.destinations || []).some((d) => !d.options || !d.options.length);
  document.getElementById("save-place-btn").disabled = destBad;
  document.getElementById("save-place-direct-btn").disabled = true;
  const hint = document.getElementById("place-save-hint");
  if (!hint) return;
  const info = preview.lookup_info || {};
  const parts = [];
  if (info.registry_count > 0) parts.push(`履歴 ${info.registry_count} 件`);
  if (info.ai_available) parts.push("AI調査 ON");
  else parts.push("Web調査");
  if (info.trip_context) parts.push("旅程エリア参照");
  const suffix = parts.length ? `（${parts.join(" / ")}）` : "";
  if (!destBad) {
    hint.textContent =
      `候補を選んで保存してください。次回同じ名前は自動で見つかります ${suffix}`;
  } else {
    hint.textContent =
      `見つからない場所は「地図で位置を指定」${suffix}`;
  }
}

function clearSegmentConfirm() {
  pendingSegmentPreview = null;
  document.getElementById("segment-confirm").classList.add("hidden");
  document.getElementById("segment-options").innerHTML = "";
  document.getElementById("segment-save-hint").textContent = "";
  document.getElementById("save-segment-btn").disabled = true;
}

function clearPlaceConfirm() {
  pendingPlacePreview = null;
  disablePlacePinMode();
  if (placePinMarker && layerGroup) {
    layerGroup.removeLayer(placePinMarker);
    placePinMarker = null;
  }
  document.getElementById("place-confirm").classList.add("hidden");
  document.getElementById("place-options").innerHTML = "";
  document.getElementById("save-place-btn").disabled = true;
  const hint = document.getElementById("place-save-hint");
  if (hint) hint.textContent = "";
  updatePlaceSaveState();
}

function buildConfirmedSegments() {
  if (!pendingSegmentPreview) return [];
  const total = pendingSegmentPreview.segments.length;
  return pendingSegmentPreview.segments
    .filter((seg) => seg.options?.length)
    .map((seg) => {
      const option = getSelectedOptionForSegment(seg);
      if (!option) return null;
      const alightInput =
        seg.seq < total
          ? document.querySelector(`input[data-kind="segment-alight"][data-seq="${seg.seq}"]`)
          : null;
      return {
        from_station: seg.from_station,
        to_station: seg.to_station,
        depart_time: seg.depart_time,
        arrive_time: seg.arrive_time,
        line_name: optionLineLabel(option),
        operator: option.operator,
        resolved_from: option.resolved_from,
        resolved_to: option.resolved_to,
        from_lat: option.from_lat,
        from_lon: option.from_lon,
        to_lat: option.to_lat,
        to_lon: option.to_lon,
        transfer_lines: option.transfer_lines,
        is_transfer: option.is_transfer,
        alighted: alightInput ? alightInput.checked : true,
      };
    })
    .filter(Boolean);
}

function buildConfirmedPlaces(preview, containerId = "place-options") {
  if (!preview || !preview.destinations) return [];
  const root = document.getElementById(containerId);
  return preview.destinations.map((dest) => {
    const select = root
      ? root.querySelector(`select[data-kind="destination"][data-seq="${dest.seq}"]`)
      : document.querySelector(`select[data-kind="destination"][data-seq="${dest.seq}"]`);
    const optionId = select ? select.value : dest.recommended_option_id;
    const option = dest.options.find((o) => o.id === optionId) || dest.options[0];
    return {
      name: dest.name,
      arrive_time: dest.arrive_time,
      depart_time: dest.depart_time,
      memo: dest.memo,
      resolved_name: option.resolved_name,
      lat: option.lat,
      lon: option.lon,
      geo_source: option.source,
      address: option.address,
      kind: option.kind,
    };
  });
}

document.getElementById("new-trip-btn").addEventListener("click", startNewTrip);

document.getElementById("preview-segment-btn").addEventListener("click", async () => {
  const text = document.getElementById("trip-text").value.trim();
  if (!text) {
    alert("区間テキストを入力してください");
    return;
  }
  const btn = document.getElementById("preview-segment-btn");
  btn.disabled = true;
  btn.textContent = "解析中...";
  try {
    const result = await api("/api/segments/preview", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    pendingSegmentPreview = result;
    renderSegmentConfirm(result);
    document.getElementById("segment-confirm").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert(err.message);
    clearSegmentConfirm();
  } finally {
    btn.disabled = false;
    btn.textContent = "区間を確認";
  }
});

document.getElementById("save-segment-btn").addEventListener("click", async () => {
  const date = document.getElementById("trip-date").value;
  const text = document.getElementById("trip-text").value.trim();
  const title = document.getElementById("trip-title").value.trim();
  if (!date || !text || !pendingSegmentPreview) {
    alert("日付と区間の確認が必要です");
    return;
  }
  const btn = document.getElementById("save-segment-btn");
  btn.disabled = true;
  btn.textContent = "保存中（線路データ取得）...";
  try {
    const skipped = skippedPreviewSegments(pendingSegmentPreview);
    const confirmed = buildConfirmedSegments();
    if (!confirmed.length) {
      alert("保存できる区間がありません");
      return;
    }
    const payload = {
      date,
      title,
      text,
      confirmed_segments: confirmed,
    };
    const tripId = await resolveTripIdForSave(date, title);
    if (tripId) payload.trip_id = tripId;
    const result = await api("/api/trips", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    selectedTripId = result.trip_id;
    creatingNewTrip = false;
    clearSegmentConfirm();
    await refreshTrips();
    if (result.trip) {
      drawTrip(result.trip);
      updateSelectedTripHint(result.trip);
      document.getElementById("trip-text").value = result.trip.raw_text || text;
      if (skipped.length) {
        document.getElementById("selected-trip-hint").textContent +=
          `（${skipped.length} 区間は路線候補なしで未保存）`;
      }
    }
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "区間を保存";
  }
});

document.getElementById("add-place-btn").addEventListener("click", () => {
  addPlaceToList();
});

document.getElementById("preview-place-btn").addEventListener("click", async () => {
  if (!editingPlaces.length) {
    alert("行った場所を1件以上追加してください");
    return;
  }
  const btn = document.getElementById("preview-place-btn");
  btn.disabled = true;
  btn.textContent = "調査中...";
  try {
    const result = await api("/api/places/preview", {
      method: "POST",
      body: JSON.stringify({
        destinations: editingPlaces,
        trip_id: selectedTripId || undefined,
      }),
    });
    pendingPlacePreview = result;
    renderPlaceConfirm(result);
    document.getElementById("place-confirm").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert(err.message);
    clearPlaceConfirm();
  } finally {
    btn.disabled = false;
    btn.textContent = "📍の位置を確認";
  }
});

document.getElementById("save-place-direct-btn").addEventListener("click", async () => {
  if (!canSavePlacesDirectly()) {
    alert("📍の位置が未確定の場所があります。「📍の位置を確認」から選んでください");
    return;
  }
  const btn = document.getElementById("save-place-direct-btn");
  btn.disabled = true;
  btn.textContent = "保存中...";
  try {
    await savePlaces(confirmedPlacesFromEditing());
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "行った場所を保存";
    updatePlaceSaveState();
  }
});

document.getElementById("save-place-btn").addEventListener("click", async () => {
  const date = document.getElementById("trip-date").value;
  const title = document.getElementById("trip-title").value.trim();
  if (!date || !pendingPlacePreview) {
    alert("日付と行った場所の確認が必要です");
    return;
  }
  const btn = document.getElementById("save-place-btn");
  btn.disabled = true;
  btn.textContent = "保存中...";
  try {
    const confirmed = buildConfirmedPlaces(pendingPlacePreview);
    await savePlaces(confirmed);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "行った場所を保存";
    updatePlaceSaveState();
  }
});

document.getElementById("import-btn").addEventListener("click", async () => {
  const btn = document.getElementById("import-btn");
  btn.disabled = true;
  btn.textContent = "読み込み中...";
  try {
    const result = await api("/api/import/scan", { method: "POST" });
    const ok = result.results.filter((r) => r.status === "ok");
    alert(ok.length ? `${ok.length} 件を取り込みました` : "新しいファイルはありませんでした");
    renderTripList(result.trips);
    if (ok.length) await selectTrip(ok[ok.length - 1].trip_id);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "インポートフォルダを読み込み";
  }
});

document.getElementById("export-btn").addEventListener("click", async () => {
  if (!selectedTripId) return;
  try {
    const result = await api(`/api/trips/${selectedTripId}/export`, { method: "POST" });
    alert(`JSONを出力しました:\n${result.path}`);
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("rebuild-btn").addEventListener("click", async () => {
  if (!selectedTripId) return;
  const btn = document.getElementById("rebuild-btn");
  btn.disabled = true;
  btn.textContent = "線路を再取得中...";
  try {
    const result = await api(`/api/trips/${selectedTripId}/rebuild-geometry`, { method: "POST" });
    drawTrip(result.trip);
    alert("線路形状を更新しました");
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "線路形状を再取得";
  }
});

async function boot() {
  initMap();
  initMapToolbar("segments");
  setDefaultDate();
  const meta = await api("/api/meta");
  colors = meta.segment_colors || [];
  lineColorMap = meta.line_colors || {};
  jrLineColorMap = meta.jr_line_colors || {};
  privateLineColorMap = meta.private_line_colors || {};
  destinationColor = meta.destination_color || "#7b2cbf";
  drawHomeMarker(map, meta.home);
  document.getElementById("trip-text").value = [
    "押上6:34→東武動物公園7:23",
    "東武動物公園7:34→館林8:11",
    "館林8:16→8:33佐野",
    "佐野8:37→8:43あしかがフラワーパーク",
    "あしかがフラワーパーク11:50→12:23小山",
    "小山12:41→13:10宇都宮",
    "東武宇都宮17:26→南栗橋18:45",
    "南栗橋18:53→押上19:48",
  ].join("\n");
  editingPlaces = [];
  renderPlaceList();
  updatePlaceSaveState();
  await refreshTrips();
}

boot();
