let map;
let layerGroup;
let checkedTripIds = new Set();
let tripDetailsCache = new Map();
let tripColors = [
  "#e63946", "#457b9d", "#2a9d8f", "#e9c46a",
  "#f4a261", "#264653", "#8338ec", "#fb5607",
];
let lineColorMap = {};
let jrLineColorMap = {};
let privateLineColorMap = {};
const tripColorById = new Map();
const SHOW_ROUTES_KEY = "viewShowRoutes";
const SHOW_DESTINATIONS_KEY = "viewShowDestinations";
let showRoutesOnMap = localStorage.getItem(SHOW_ROUTES_KEY) !== "false";
let showDestinationsOnMap = localStorage.getItem(SHOW_DESTINATIONS_KEY) !== "false";

async function api(path, options = {}) {
  if (window.VIEW_DATA) {
    return staticApi(path, options);
  }
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "API error");
  return data;
}

function staticApi(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET") {
    throw new Error("閲覧専用サイトでは変更できません");
  }
  const data = window.VIEW_DATA;
  if (path === "/api/trips") {
    return data.trips.map((t) => ({
      id: t.id,
      trip_date: t.trip_date,
      title: t.title,
      segment_count: (t.segments || []).length,
      destination_count: (t.destinations || []).length,
    }));
  }
  const tripMatch = path.match(/^\/api\/trips\/(\d+)$/);
  if (tripMatch) {
    const trip = data.trips.find((t) => t.id === Number(tripMatch[1]));
    if (!trip) throw new Error("not found");
    return trip;
  }
  if (path === "/api/meta") {
    return data.meta;
  }
  throw new Error("API error");
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
  setTimeout(() => {
    if (map) map.invalidateSize();
  }, 300);
  loadHomeMarker(map);
}

function assignTripColors(trips) {
  tripColorById.clear();
  const sorted = [...trips].sort((a, b) =>
    String(a.trip_date).localeCompare(String(b.trip_date)) || a.id - b.id
  );
  sorted.forEach((trip, i) => {
    tripColorById.set(trip.id, tripColors[i % tripColors.length]);
  });
}

function tripColor(tripId) {
  return tripColorById.get(tripId) || tripColors[0];
}

function createDestinationIcon(seq, color) {
  const bg = color || "#7b2cbf";
  return L.divIcon({
    className: "",
    html: `<div class="destination-marker" style="background:${bg}"><span>${seq}</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 28],
    popupAnchor: [0, -28],
  });
}

function formatDestinationTime(dest) {
  if (dest.arrive_time && dest.depart_time) {
    return `${dest.arrive_time} - ${dest.depart_time}`;
  }
  return dest.arrive_time || dest.depart_time || "";
}

function showDestinationDetail(entry) {
  const { dest, index, tripLabel } = entry;
  const panel = document.getElementById("destination-detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  const tripNote = checkedTripIds.size > 1 ? ` · ${tripLabel}` : "";
  title.textContent = `📍 ${index + 1}: ${dest.name}${tripNote}`;
  const time = formatDestinationTime(dest) || "-";
  const placeLabel =
    dest.geo_source === "station"
      ? `${dest.resolved_name || dest.name}（最寄り駅）`
      : dest.resolved_name || (dest.lat ? "地図上の📍" : "未取得");
  body.innerHTML = `
    <dt>記録</dt><dd>${tripLabel}</dd>
    <dt>滞在時間</dt><dd>${time}</dd>
    <dt>メモ</dt><dd>${dest.memo || "-"}</dd>
    <dt>場所</dt><dd>${placeLabel}</dd>
  `;
  openMapDetailPanel(panel);
}

function showSegmentDetail(seg, index, allSegments) {
  const panel = document.getElementById("destination-detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.textContent = `区間 ${index + 1}: ${seg.from_station} → ${seg.to_station}${
    seg.line_name ? `（${seg.line_name}）` : ""
  }`;
  body.innerHTML = `
    <dt>出発</dt><dd>${seg.depart_time || "-"}</dd>
    <dt>到着</dt><dd>${seg.arrive_time || "-"}</dd>
    <dt>路線</dt><dd>${seg.line_name || "（未特定）"}</dd>
    <dt>事業者</dt><dd>${seg.operator || "-"}</dd>
  `;
  openMapDetailPanel(panel);
}

function showTransferDetail(transfer) {
  const panel = document.getElementById("destination-detail");
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

function renderDestinationList(entries) {
  const list = document.getElementById("destination-list");
  const hint = document.getElementById("destination-hint");
  list.innerHTML = "";

  if (!entries.length) {
    if (checkedTripIds.size) {
      hint.textContent = "チェックした記録に行った場所がありません";
    } else {
      hint.textContent = "記録を選び、路線・📍 の表示を切り替えてください";
    }
    return;
  }

  const tripCount = new Set(entries.map((e) => e.tripId)).size;
  hint.textContent =
    tripCount > 1
      ? `${tripCount} 件の記録 · ${entries.length} か所を表示中`
      : `${entries.length} か所`;

  let lastTripId = null;
  entries.forEach((entry) => {
    const { dest, index, tripId, tripLabel, tripDate } = entry;

    if (tripCount > 1 && tripId !== lastTripId) {
      lastTripId = tripId;
      const header = document.createElement("li");
      header.className = "dest-list-trip-header";
      header.style.borderLeftColor = entry.tripColor;
      header.innerHTML =
        `<span class="trip-color-dot" style="background:${entry.tripColor}"></span>` +
        `${tripDate}${tripLabel !== tripDate ? ` · ${tripLabel}` : ""}`;
      list.appendChild(header);
    }

    const li = document.createElement("li");
    if (tripCount > 1) {
      li.style.borderLeftColor = entry.tripColor;
      li.classList.add("dest-list-colored");
    }
    const time = formatDestinationTime(dest);
    const geoNote =
      dest.lat != null && dest.lon != null
        ? dest.resolved_name && dest.resolved_name !== dest.name
          ? ` · ${dest.resolved_name}`
          : ""
        : " · 位置未取得";

    li.innerHTML =
      `<strong>📍 ${index + 1}</strong> ${dest.name}` +
      (time
        ? `<div class="dest-list-meta">${time}${geoNote}</div>`
        : `<div class="dest-list-meta">${geoNote.trim()}</div>`) +
      (dest.memo ? `<div class="dest-list-memo">${dest.memo}</div>` : "");

    if (dest.lat != null && dest.lon != null) {
      li.addEventListener("click", () => {
        map.setView([dest.lat, dest.lon], 14);
        showDestinationDetail(entry);
      });
    } else {
      li.classList.add("no-geo");
    }

    list.appendChild(li);
  });
}

function buildTripDrawEntries(trips) {
  return [...trips]
    .filter((t) => checkedTripIds.has(t.id))
    .sort((a, b) => String(a.trip_date).localeCompare(String(b.trip_date)))
    .map((trip) => ({
      trip,
      tripLabel: trip.title || trip.trip_date,
      tripColor: tripColor(trip.id),
    }));
}

function drawCheckedMap(trips) {
  const listEntries = buildDestinationEntries(trips);
  renderDestinationList(listEntries);

  const pointCount = TripMapDraw.drawTrips({
    map,
    layerGroup,
    tripEntries: buildTripDrawEntries(trips),
    palette: tripColors,
    lineColorMap,
    jrLineColorMap,
    privateLineColorMap,
    createDestinationIcon,
    onSegmentDetail: showSegmentDetail,
    onDestinationDetail: showDestinationDetail,
    onTransferDetail: showTransferDetail,
    showRoutes: showRoutesOnMap,
    showDestinations: showDestinationsOnMap,
  });

  if (!pointCount) {
    document.getElementById("destination-detail").classList.add("hidden");
    return;
  }
  const first = listEntries.find((e) => e.dest.lat != null && e.dest.lon != null);
  if (first) {
    showDestinationDetail(first);
  }
}

async function fetchTripDetails(tripId) {
  if (tripDetailsCache.has(tripId)) {
    return tripDetailsCache.get(tripId);
  }
  const trip = await api(`/api/trips/${tripId}`);
  tripDetailsCache.set(tripId, trip);
  return trip;
}

function buildDestinationEntries(trips) {
  const entries = [];
  const sortedTrips = [...trips].sort((a, b) =>
    String(a.trip_date).localeCompare(String(b.trip_date))
  );
  for (const trip of sortedTrips) {
    if (!checkedTripIds.has(trip.id)) continue;
    const tripLabel = trip.title || trip.trip_date;
    (trip.destinations || []).forEach((dest, i) => {
      entries.push({
        dest,
        index: i,
        tripId: trip.id,
        tripLabel,
        tripDate: trip.trip_date,
        tripColor: tripColor(trip.id),
      });
    });
  }
  return entries;
}

async function redrawCheckedTrips() {
  if (!checkedTripIds.size) {
    layerGroup.clearLayers();
    renderDestinationList([]);
    document.getElementById("destination-detail").classList.add("hidden");
    document.getElementById("destination-hint").textContent =
      "記録を選び、路線・📍 の表示を切り替えてください";
    updateTripListHighlight();
    return;
  }

  const trips = [];
  for (const tripId of checkedTripIds) {
    trips.push(await fetchTripDetails(tripId));
  }
  drawCheckedMap(trips);
  updateTripListHighlight();
}

function updateTripListHighlight() {
  document.querySelectorAll("#trip-list li[data-trip-id]").forEach((li) => {
    const id = Number(li.dataset.tripId);
    li.classList.toggle("active", checkedTripIds.has(id));
  });
}

function setTripChecked(tripId, checked) {
  if (checked) {
    checkedTripIds.add(tripId);
  } else {
    checkedTripIds.delete(tripId);
    tripDetailsCache.delete(tripId);
  }
}

function clearView() {
  checkedTripIds.clear();
  tripDetailsCache.clear();
  layerGroup.clearLayers();
  renderDestinationList([]);
  document.getElementById("destination-detail").classList.add("hidden");
  document.getElementById("destination-hint").textContent =
    "記録にチェックを入れると 📍 と路線が地図に表示されます";
}

async function refreshTrips() {
  const trips = await api("/api/trips");
  const list = document.getElementById("trip-list");
  list.innerHTML = "";

  if (!trips.length) {
    list.innerHTML = "<li class='trip-empty'>記録がありません</li>";
    clearView();
    return;
  }

  const knownIds = new Set(trips.map((t) => t.id));
  for (const id of [...checkedTripIds]) {
    if (!knownIds.has(id)) checkedTripIds.delete(id);
  }
  assignTripColors(trips);

  trips.forEach((trip) => {
    const li = document.createElement("li");
    li.dataset.tripId = trip.id;
    if (checkedTripIds.has(trip.id)) li.classList.add("active");

    const label = document.createElement("label");
    label.className = "trip-check-label";

    const colorDot = document.createElement("span");
    colorDot.className = "trip-color-dot";
    colorDot.style.background = tripColor(trip.id);
    colorDot.title = "記録の色";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "trip-map-check";
    checkbox.checked = checkedTripIds.has(trip.id);
    checkbox.title = "地図に表示";
    checkbox.addEventListener("change", async (e) => {
      setTripChecked(trip.id, e.target.checked);
      await redrawCheckedTrips();
    });

    const body = document.createElement("div");
    body.className = "trip-item-body";
    const segCount = trip.segment_count || 0;
    const destCount = trip.destination_count || 0;
    body.innerHTML =
      `<strong>${trip.title || trip.trip_date}</strong>` +
      `<div class="trip-meta">${trip.trip_date} · ${segCount}区間 · 📍 ${destCount} か所</div>`;

    label.appendChild(colorDot);
    label.appendChild(checkbox);
    label.appendChild(body);
    li.appendChild(label);
    list.appendChild(li);
  });

  if (!checkedTripIds.size) {
    const firstWithPlaces = trips.find((t) => (t.destination_count || 0) > 0);
    const initial = firstWithPlaces || trips[0];
    setTripChecked(initial.id, true);
  }

  await redrawCheckedTrips();
}

function updateLayerToggleButtons() {
  const routesCheck = document.getElementById("show-routes-check");
  const destCheck = document.getElementById("show-destinations-check");
  if (routesCheck) routesCheck.checked = showRoutesOnMap;
  if (destCheck) destCheck.checked = showDestinationsOnMap;
}

function initLayerToggles() {
  const routesCheck = document.getElementById("show-routes-check");
  const destCheck = document.getElementById("show-destinations-check");
  updateLayerToggleButtons();

  routesCheck?.addEventListener("change", async (e) => {
    showRoutesOnMap = e.target.checked;
    localStorage.setItem(SHOW_ROUTES_KEY, showRoutesOnMap ? "true" : "false");
    await redrawCheckedTrips();
  });
  destCheck?.addEventListener("change", async (e) => {
    showDestinationsOnMap = e.target.checked;
    localStorage.setItem(SHOW_DESTINATIONS_KEY, showDestinationsOnMap ? "true" : "false");
    await redrawCheckedTrips();
  });
}

async function init() {
  initMap();
  initMapToolbar("destinations");
  initLayerToggles();
  try {
    const meta = window.VIEW_DATA?.meta ?? (await fetch("/api/meta").then((res) => res.json()));
    if (meta.segment_colors?.length) {
      tripColors = meta.segment_colors;
    }
    lineColorMap = meta.line_colors || {};
    jrLineColorMap = meta.jr_line_colors || {};
    privateLineColorMap = meta.private_line_colors || {};
    await refreshTrips();
  } catch (err) {
    alert(err.message);
  }
}

init();
