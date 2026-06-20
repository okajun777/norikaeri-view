/* Shared trip map drawing */
window.TripMapDraw = (function () {
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
  layerGroup,
  onSegmentDetail
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
    line.on("click", () => onSegmentDetail && onSegmentDetail(seg, segIndex, segments));
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

  function drawTrips({
    map,
    layerGroup,
    tripEntries,
    palette,
    lineColorMap,
    jrLineColorMap,
    privateLineColorMap,
    createDestinationIcon,
    onSegmentDetail,
    onDestinationDetail,
    onTransferDetail,
    fitBounds = true,
  }) {
    layerGroup.clearLayers();
    const bounds = [];
    for (const entry of tripEntries) {
      const b = drawTripLayers({
        trip: entry.trip,
        tripLabel: entry.tripLabel,
        tripColor: entry.tripColor,
        layerGroup,
        palette,
        lineColorMap,
        jrLineColorMap,
        privateLineColorMap,
        createDestinationIcon,
        onSegmentDetail,
        onDestinationDetail,
        onTransferDetail,
      });
      bounds.push(...b);
    }
    if (fitBounds && bounds.length) {
      map.fitBounds(bounds, { padding: [48, 48], maxZoom: 14 });
    }
    return bounds.length;
  }

  function drawTripLayers(opts) {
    const trip = opts.trip;
    const bounds = [];
    const destinations = trip.destinations || [];
    const segments = trip.segments || [];
    const lineColors = assignLineColors(segments, opts.palette);
    const lineOffsets = assignOverlapLineOffsets(segments);
    const drawGeometries = buildDrawGeometries(segments, lineOffsets);
    const markerPoints = [];
    const transfers = collectTransferPoints(segments, destinations);

    segments.forEach((seg, i) => {
      const color = lineColors[i];
      const drawGeom = drawGeometries[i];
      for (const run of getSegmentUnderlayRuns(drawGeom, i, segments)) {
        const latlngs = geometryToLatLngs(run.geom);
        if (latlngs.length < 2) return;
        L.polyline(latlngs, {
          color,
          weight: LINE_WEIGHT * LINE_OVERLAP_BOTTOM_FACTOR,
          opacity: 1,
          lineCap: "round",
          lineJoin: "round",
        }).addTo(opts.layerGroup);
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
        opts.layerGroup,
        opts.onSegmentDetail
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
        tripLabel: opts.tripLabel,
        tripColor: opts.tripColor,
      });
    });

    applyMarkerSpread(markerPoints).forEach((marker) => {
      const pos = [marker.displayLat, marker.displayLon];
      bounds.push(pos);
      if (marker.kind === "station") {
        const { seg, endpoint, color } = marker;
        const label = endpoint === "from" ? seg.from_station : seg.to_station;
        const time = endpoint === "from" ? seg.depart_time || "" : seg.arrive_time || "";
        const timeLabel = endpoint === "from" ? "発" : "着";
        L.circleMarker(pos, {
          radius: 6,
          color: "#fff",
          weight: 2,
          fillColor: color,
          fillOpacity: 1,
        })
          .bindPopup(`<b>${label}</b><br>${timeLabel} ${time}`)
          .addTo(opts.layerGroup);
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
              `区間 ${transfer.fromSegment}→${transfer.toSegment}`
          )
          .on("click", () => opts.onTransferDetail && opts.onTransferDetail(transfer))
          .addTo(opts.layerGroup);
        return;
      }
      if (marker.kind === "dest") {
        const dest = marker.dest;
        const i = marker.index;
        const icon = opts.createDestinationIcon
          ? opts.createDestinationIcon(i + 1, marker.tripColor)
          : createDestinationIcon(i + 1);
        const time =
          dest.arrive_time && dest.depart_time
            ? `${dest.arrive_time} - ${dest.depart_time}`
            : dest.arrive_time || dest.depart_time || "";
        const tripNote = marker.tripLabel ? `<br><small>${marker.tripLabel}</small>` : "";
        L.marker(pos, { icon, zIndexOffset: 1000 })
          .bindPopup(`<b>📍 ${dest.name}</b>${time ? `<br>${time}` : ""}${tripNote}`)
          .on("click", () =>
            opts.onDestinationDetail &&
            opts.onDestinationDetail({ dest, index: i, tripLabel: marker.tripLabel })
          )
          .addTo(opts.layerGroup);
      }
    });
    return bounds;
  }

  return { drawTrips };
})();
