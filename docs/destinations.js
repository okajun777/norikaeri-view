let map;
let layerGroup;
let checkedTripIds = new Set();
let tripDetailsCache = new Map();
let tripColors = [
  "#e63946", "#457b9d", "#2a9d8f", "#e9c46a",
  "#f4a261", "#264653", "#8338ec", "#fb5607",
];
const tripColorById = new Map();

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
const MARKER_OVERLAP_THRESHOLD_M = 50;
const MARKER_SEPARATION_M = 80;

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
  loadHomeMarker(map);
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

function applyMarkerSpread(markers) {
  const spread = markers.map((marker) => ({
    ...marker,
    displayLat: marker.lat,
    displayLon: marker.lon,
  }));

  for (const cluster of clusterMarkers(spread, MARKER_OVERLAP_THRESHOLD_M)) {
    if (cluster.length <= 1) continue;
    cluster.sort((a, b) => {
      const ta = a.sortKey ?? 0;
      const tb = b.sortKey ?? 0;
      return ta - tb || String(a.id).localeCompare(String(b.id));
    });

    const n = cluster.length;
    for (let i = 0; i < n; i++) {
      const slot = i - (n - 1) / 2;
      const offsetM = slot * MARKER_SEPARATION_M;
      const target = spread.find((m) => m.id === cluster[i].id);
      [target.displayLat, target.displayLon] = shiftLonMeters(
        target.lat,
        target.lon,
        offsetM
      );
    }
  }

  return spread;
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

function renderDestinationList(entries) {
  const list = document.getElementById("destination-list");
  const hint = document.getElementById("destination-hint");
  list.innerHTML = "";

  if (!entries.length) {
    if (checkedTripIds.size) {
      hint.textContent = "チェックした記録に行った場所がありません";
    } else {
      hint.textContent = "記録にチェックを入れると 📍 が地図に表示されます";
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

function drawDestinationMarkers(entries) {
  layerGroup.clearLayers();
  const bounds = [];
  const markers = [];

  entries.forEach((entry) => {
    const { dest, index, tripId } = entry;
    if (dest.lat == null || dest.lon == null) return;
    markers.push({
      id: `dest-${tripId}-${index}`,
      lat: dest.lat,
      lon: dest.lon,
      sortKey: parseTime(dest.arrive_time) ?? index,
      entry,
    });
  });

  applyMarkerSpread(markers).forEach((marker) => {
    const pos = [marker.displayLat, marker.displayLon];
    bounds.push(pos);
    const { dest, index, tripLabel, tripId } = marker.entry;
    const time = formatDestinationTime(dest);
    const place = dest.resolved_name || dest.name;
    const placeNote = dest.geo_source === "station" ? "（最寄り駅）" : "";
    const tripNote = checkedTripIds.size > 1 ? `<br><small>${tripLabel}</small>` : "";
    const color = tripColor(tripId);

    L.marker(pos, {
      icon: createDestinationIcon(index + 1, color),
      zIndexOffset: 1000,
    })
      .bindPopup(
        `<b>📍 ${dest.name}</b><br>${place}${placeNote}` +
          (time ? `<br>${time}` : "") +
          tripNote
      )
      .on("click", () => showDestinationDetail(marker.entry))
      .addTo(layerGroup);
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [48, 48], maxZoom: 14 });
  } else {
    document.getElementById("destination-detail").classList.add("hidden");
  }

  renderDestinationList(entries);

  const firstWithGeo = entries.find((e) => e.dest.lat != null && e.dest.lon != null);
  if (firstWithGeo) {
    showDestinationDetail(firstWithGeo);
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
      "記録にチェックを入れると 📍 が地図に表示されます";
    updateTripListHighlight();
    return;
  }

  const trips = [];
  for (const tripId of checkedTripIds) {
    trips.push(await fetchTripDetails(tripId));
  }
  drawDestinationMarkers(buildDestinationEntries(trips));
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
    "記録にチェックを入れると 📍 が地図に表示されます";
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
    const destCount = trip.destination_count || 0;
    body.innerHTML =
      `<strong>${trip.title || trip.trip_date}</strong>` +
      `<div class="trip-meta">${trip.trip_date} · 📍 ${destCount} か所</div>`;

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

async function init() {
  initMap();
  initMapToolbar("destinations");
  try {
    const meta = window.VIEW_DATA?.meta ?? (await fetch("/api/meta").then((res) => res.json()));
    if (meta.segment_colors?.length) {
      tripColors = meta.segment_colors;
    }
    await refreshTrips();
  } catch (err) {
    alert(err.message);
  }
}

init();
