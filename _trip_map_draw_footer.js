
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
