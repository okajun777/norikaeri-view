const DETAIL_VISIBLE_KEY = "mapDetailVisible";

let homeLayer = null;

function isMapDetailVisible() {
  return localStorage.getItem(DETAIL_VISIBLE_KEY) !== "false";
}

function setMapDetailVisible(visible) {
  localStorage.setItem(DETAIL_VISIBLE_KEY, visible ? "true" : "false");
  updateMapDetailToggleButton();
  const panel = document.querySelector(".segment-detail[data-map-detail]");
  if (!visible && panel) {
    panel.classList.add("hidden");
  } else if (visible && panel?.dataset.hasContent === "true") {
    panel.classList.remove("hidden");
  }
}

function updateMapDetailToggleButton() {
  const btn = document.getElementById("detail-toggle-btn");
  if (!btn) return;
  const visible = isMapDetailVisible();
  btn.setAttribute("aria-pressed", visible ? "true" : "false");
  btn.textContent = visible ? "詳細 ON" : "詳細 OFF";
}

function openMapDetailPanel(panel) {
  if (!panel) return;
  panel.dataset.hasContent = "true";
  if (isMapDetailVisible()) {
    panel.classList.remove("hidden");
  }
}

function closeMapDetailPanel(panel) {
  if (!panel) return;
  panel.classList.add("hidden");
}

function createHomeIcon() {
  return L.divIcon({
    className: "",
    html: '<div class="home-marker" aria-hidden="true">🏠</div>',
    iconSize: [32, 32],
    iconAnchor: [16, 32],
    popupAnchor: [0, -32],
  });
}

function drawHomeMarker(map, home) {
  if (!map || !home?.lat || !home?.lon) return;
  if (!homeLayer) {
    homeLayer = L.layerGroup().addTo(map);
  }
  homeLayer.clearLayers();
  L.marker([home.lat, home.lon], {
    icon: createHomeIcon(),
    zIndexOffset: 2000,
  })
    .bindPopup(
      `<b>🏠 ${home.label || "自宅"}</b>` +
        (home.address ? `<br>${home.address}` : "")
    )
    .addTo(homeLayer);
}

async function loadHomeMarker(map) {
  try {
    if (window.VIEW_DATA?.meta?.home) {
      drawHomeMarker(map, window.VIEW_DATA.meta.home);
      return;
    }
    const meta = await fetch("/api/meta").then((res) => res.json());
    drawHomeMarker(map, meta.home);
  } catch {
    /* ignore */
  }
}

function initMapToolbar(activeView) {
  document.querySelectorAll(".map-view-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === activeView);
  });

  updateMapDetailToggleButton();

  const toggleBtn = document.getElementById("detail-toggle-btn");
  toggleBtn?.addEventListener("click", () => {
    setMapDetailVisible(!isMapDetailVisible());
  });

  document.querySelectorAll(".detail-close-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setMapDetailVisible(false);
    });
  });

  if (!isMapDetailVisible()) {
    document.querySelectorAll(".segment-detail[data-map-detail]").forEach((panel) => {
      panel.classList.add("hidden");
    });
  }
}
