/* SIH26051 · Shelter-Studio — "Command Deck" frontend logic (vanilla JS, no build step) */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------- api (auth-aware; falls back to plain fetch if auth.js absent) ---------- */
async function api(path, options) {
  if (window.SHI && SHI.apiFetch) return SHI.apiFetch(path, options);
  const res = await fetch(path, options);
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

const fmt = (v, d = 1) => {
  const n = parseFloat(v);
  return (v === null || v === undefined || isNaN(n)) ? "—" : n.toFixed(d);
};

/* ---------- toast (replaces browser alert) ---------- */
function toast(msg, isErr = false) {
  let t = $("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = `▲ ${msg}`;
  t.className = isErr ? "show err" : "show";
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.className = ""; }, 4200);
}

/* ---------- theme ---------- */
const THEME_COLORS = {
  dark:  { font: "#c3cad2", grid: "#2b3036", zero: "#333940" },
  light: { font: "#4a4f57", grid: "#e0ddd5", zero: "#c9c5bc" },
};

function currentTheme() {
  return document.documentElement.dataset.theme || "dark";
}

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("shl-theme", t); } catch (e) {}
  updateThemeToggle();
  lastPlots.forEach((p) => renderPlot(p.el, p.data, p.layout, p.config));
}

function updateThemeToggle() {
  const dark = currentTheme() === "dark";
  const icon = $("themeIcon"), label = $("themeLabel");
  if (!icon || !label) return;
  label.textContent = dark ? "LIGHT" : "DARK";
  icon.innerHTML = dark
    ? '<circle cx="12" cy="12" r="4.5"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6"/>'
    : '<path d="M20.4 14.2A8.2 8.2 0 0 1 9.8 3.6a8.2 8.2 0 1 0 10.6 10.6Z"/>';
}

/* ---------- plot (matte command-deck theme) ---------- */
const lastPlots = []; // registry so charts re-theme on toggle

function renderPlot(el, data, layout, config = {}) {
  const c = THEME_COLORS[currentTheme()] || THEME_COLORS.dark;
  Plotly.react(el, data, Object.assign({
    template: { layout: {
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: c.font, family: "'Cascadia Mono','Consolas',monospace" },
      xaxis: { gridcolor: c.grid, zerolinecolor: c.zero, linecolor: c.zero },
      yaxis: { gridcolor: c.grid, zerolinecolor: c.zero, linecolor: c.zero },
    } },
    margin: { l: 54, r: 16, t: 40, b: 46 }, height: 320,
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: c.font, family: "'Cascadia Mono','Consolas',monospace" },
  }, layout), { responsive: true, displayModeBar: false, ...config });
}

function plot(el, data, layout, config = {}) {
  if (typeof Plotly === "undefined") {
    el.classList.add("has-data");
    el.textContent = "CHART FEED UNAVAILABLE — OFFLINE PREVIEW";
    return;
  }
  el.classList.add("has-data");
  lastPlots.push({ el, data, layout, config });
  if (lastPlots.length > 8) lastPlots.shift();
  renderPlot(el, data, layout, config);
}

function metric(value, label) {
  const d = document.createElement("div");
  d.className = "metric";
  d.innerHTML = `<b>${value}</b><span>${label}</span>`;
  return d;
}

/* ---------- state ---------- */
const state = { locations: [], materials: [], climate: null };

/* ---------- 1 · locations ---------- */
async function loadLocations() {
  const data = await api("/api/locations");
  state.locations = data.locations;
  const sel = $("location");
  sel.innerHTML = "";
  state.locations.forEach((l) => {
    const opt = document.createElement("option");
    opt.value = l.location_id || l.name;
    opt.textContent = `${l.name} (${l.latitude.toFixed(3)}, ${l.longitude.toFixed(3)})`;
    opt.dataset.lat = l.latitude; opt.dataset.lon = l.longitude;
    sel.appendChild(opt);
  });
}

async function loadMaterials() {
  const data = await api("/api/materials");
  state.materials = data.materials;
  const fill = (id, names) => {
    const sel = $(id);
    sel.innerHTML = "";
    names.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      sel.appendChild(opt);
    });
  };
  fill("wallMat", state.materials.map((m) => m.material));
  fill("roofMat", state.materials.map((m) => m.material));
  const ins = state.materials.filter((m) => m.category === "insulation")
    .map((m) => m.material);
  $("insMat").innerHTML = "<option value='none'>none</option>" +
    ins.map((n) => `<option>${n}</option>`).join("");
}

/* ---------- 2 · climate ---------- */
async function loadClimate() {
  const sel = $("location").selectedOptions[0];
  const btn = $("loadClimate");
  btn.disabled = true; btn.classList.add("busy");
  btn.textContent = "◈ Fetching…";
  try {
    const data = await api(`/api/climate?lat=${sel.dataset.lat}&lon=${sel.dataset.lon}` +
      `&year=${$("year").value}`);
    state.climate = data;
    $("climateSource").textContent = data.source;
    const s = data.summary;
    const wrap = $("climateSummary");
    wrap.innerHTML = "";
    wrap.append(metric(fmt(s.t2m_mean_c), "mean temp °C"),
                metric(`${fmt(s.t2m_min_c)} … ${fmt(s.t2m_max_c)}`, "temp range °C"),
                metric(fmt(s.ghi_mean_w_m2, 0), "mean solar W/m²"),
                metric(fmt(s.rh2m_mean_pct, 0), "mean RH %"),
                metric(fmt(data.n_hours / 24, 0), "days of data"));
    plot($("chartTemp"), [{
      x: data.monthly.ts, y: data.monthly.t2m, type: "scatter", mode: "lines+markers",
      name: "t2m", line: { color: "#ff5d5d", width: 2 },
      marker: { size: 5, color: "#ff5d5d" },
    }], { title: "MONTHLY MEAN TEMPERATURE · °C" });
    plot($("chartSolar"), [{
      x: data.monthly.ts, y: data.monthly.ghi, type: "scatter", mode: "lines+markers",
      name: "GHI", line: { color: "#ffb25e", width: 2 },
      marker: { size: 5, color: "#ffb25e" },
    }], { title: "MONTHLY MEAN SOLAR IRRADIANCE · W/m²" });
    $("climateSection").hidden = false;
    loadProfile();            // MOD·02C — location characteristics
  } catch (err) {
    toast(`Climate load failed: ${err.message}`, true);
  } finally {
    btn.disabled = false; btn.classList.remove("busy");
    btn.textContent = "◈ Load Climate";
  }
}

/* ---------- 3/4 · simulate ---------- */
function designPayload() {
  const [ww, wh] = $("winSize").value.split(/[x×]/).map((v) => parseFloat(v) || 1.2);
  return {
    length_m: parseFloat($("length").value), width_m: parseFloat($("width").value),
    height_m: parseFloat($("height").value),
    orientation_deg: parseInt($("orientation").value, 10),
    wall_material: $("wallMat").value, wall_thickness_m: parseFloat($("wallThick").value),
    roof_material: $("roofMat").value, roof_thickness_m: parseFloat($("roofThick").value),
    insulation_material: $("insMat").value,
    insulation_thickness_m: parseFloat($("insThick").value) / 1000,
    window_wall: $("winWall").value, window_width_m: ww, window_height_m: wh,
    period: $("period").value,
  };
}

async function runSimulate() {
  const sel = $("location").selectedOptions[0];
  const body = { lat: parseFloat(sel.dataset.lat), lon: parseFloat(sel.dataset.lon),
                 year: parseInt($("year").value, 10), ...designPayload() };
  const btn = $("simulate");
  btn.disabled = true; btn.classList.add("busy");
  try {
    const data = await api("/api/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("simEngine").textContent = `RC model · ${data.weather_source}`;
    const m = data.metrics;
    const wrap = $("simMetrics");
    wrap.innerHTML = "";
    wrap.append(metric(fmt(m.mean_indoor_c), "mean indoor °C"),
                metric(`${fmt(m.min_indoor_c)} … ${fmt(m.max_indoor_c)}`, "indoor range °C"),
                metric(`${Math.round(m.comfort_fraction * 100)}%`, "comfort hours"),
                metric(fmt(m.solar_gain_kwh, 0), "solar gain kWh"));
    const s = data.series;
    plot($("chartSim"), [
      { x: s.ts, y: s.outdoor_t_c, type: "scatter", name: "OUTDOOR",
        line: { color: "#98a1ab", width: 1.5 } },
      { x: s.ts, y: s.indoor_t_c, type: "scatter", name: "INDOOR",
        line: { color: "#6ab7ff", width: 2.5 } },
    ], { title: "INDOOR VS OUTDOOR TEMPERATURE · °C",
         xaxis: { tickangle: -30 } });
    if (s.q_solar_w) {
      plot($("chartHeat"), [
        { x: s.ts, y: s.q_solar_w, type: "scatter", name: "SOLAR", line: { color: "#ffb25e", width: 2 } },
        { x: s.ts, y: s.q_conduct_w, type: "scatter", name: "CONDUCTION", line: { color: "#ff5d5d", width: 2 } },
        { x: s.ts, y: s.q_vent_w, type: "scatter", name: "VENTILATION", line: { color: "#5eea8d", width: 2 } },
      ], { title: "HEAT FLOW BUDGET · W (+ INTO SHELTER)" });
    }
    if (data.metrics.monthly_comfort && typeof Plotly !== "undefined") {
      $("monthlyWrap").hidden = false;
      const mc = data.metrics.monthly_comfort;
      plot($("chartMonthly"), [{
        x: mc.map((m) => m.month), y: mc.map((m) => m.comfort_fraction * 100),
        type: "bar",
        marker: { color: mc.map((m) => (m.comfort_fraction >= 0.5 ? "#5fd08a" : "#ff9933")) },
        hovertemplate: "%{y:.0f}% of hours comfortable<extra></extra>",
      }], { title: "MONTHLY COMFORT FRACTION · % HOURS INSIDE 18-32 °C",
            yaxis: { ticksuffix: "%", rangemode: "tozero" },
            xaxis: { dtick: 1, title: "month" },
            margin: { l: 44, r: 16, t: 40, b: 30 } });
    }
    $("simSection").hidden = false;
  } catch (err) {
    toast(`Simulation failed: ${err.message}`, true);
  } finally {
    btn.disabled = false; btn.classList.remove("busy");
  }
}

/* ---------- 5 · optimize ---------- */
async function runOptimize() {
  const sel = $("location").selectedOptions[0];
  const n = parseInt($("trials").value, 10);
  const btn = $("optimize");
  btn.disabled = true; btn.classList.add("busy");
  const st = $("optStatus");
  st.textContent = `running ${n} trials… (≈${Math.ceil(n / 8)} s)`;
  st.classList.remove("err");
  try {
    const data = await api("/api/optimize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: parseFloat(sel.dataset.lat),
                             lon: parseFloat(sel.dataset.lon),
                             year: parseInt($("year").value, 10), n_trials: n }),
    });
    st.textContent = `BEST TPI ${data.best.tpi.toFixed(3)} — ${data.best.design.wall_material} / ${data.best.design.roof_material}`;
    const b = data.best.design;
    $("bestCard").innerHTML = `<h4>★ Best design — TPI ${data.best.tpi.toFixed(3)}</h4>` +
      `<p>Orientation: <b>${b.orientation_deg}°</b></p>` +
      `<p>Walls: <b>${b.wall_material}</b> (${fmt(b.wall_thickness_m, 2)} m)</p>` +
      `<p>Roof: <b>${b.roof_material}</b> (${fmt(b.roof_thickness_m, 2)} m)</p>` +
      `<p>Insulation: <b>${b.insulation_material}</b> ${b.insulation_material !== "none" ? fmt(b.insulation_thickness_m * 1000, 0) + " mm" : ""}</p>` +
      `<p>Window: <b>${b.window.wall}</b> ${fmt(b.window.width_m, 1)}×${fmt(b.window.height_m, 1)} m, SHGC ${fmt(b.window.shgc, 2)}</p>`;
    plot($("chartOpt"), [{
      y: data.history, type: "scatter", mode: "lines+markers",
      name: "1 − TPI (lower better)", line: { color: "#5eea8d", width: 2 },
      marker: { size: 6, color: "#5eea8d" },
    }], { title: "OPTIMIZATION HISTORY · TRIAL PROGRESS" });
    const tb = $("optTable").querySelector("tbody");
    tb.innerHTML = "";
    data.top10.forEach((t, i) => {
      const tr = document.createElement("tr");
      if (i === 0) tr.className = "best";
      const p = t.params;
      tr.innerHTML = `<td>${i + 1}</td><td><b>${t.tpi.toFixed(3)}</b></td>` +
        `<td>${p.orientation_deg}°</td><td>${p.wall_material}</td>` +
        `<td>${p.roof_material}</td>` +
        `<td>${p.insulation_material} ${p.insulation_material !== "none" ? (p.insulation_thickness_m * 1000).toFixed(0) + "mm" : ""}</td>` +
        `<td>${p.window_wall}</td>`;
      tb.appendChild(tr);
    });
    $("optSection").hidden = false;
  } catch (err) {
    st.textContent = `FAILED: ${err.message}`;
    st.classList.add("err");
  } finally {
    btn.disabled = false; btn.classList.remove("busy");
  }
}

/* ---------- HUD extras ---------- */
function tickClock() {
  const f = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false, timeZone: "Asia/Kolkata",
  });
  const c = $("clockIST");
  if (c) c.textContent = f.format(new Date());
}
setInterval(tickClock, 1000);

async function checkHealth() {
  const led = $("sysLed"), link = $("sysLink"), foot = $("footStatus");
  try {
    const h = await api("/api/health");
    led.className = "led ok";
    link.textContent = `DATA-LINK OK · ${h.backend.toUpperCase()} · PY ${h.python}`;
    if (foot) { foot.textContent = "SYS/ONLINE · BACKEND LINKED"; foot.className = "ok"; }
  } catch (err) {
    led.className = "led warn";
    link.textContent = "DATA-LINK DEGRADED — RETRYING";
    if (foot) { foot.textContent = "SYS/ONLINE · BACKEND DEGRADED"; foot.className = "warn"; }
  }
}

function setupTicker() {
  const t = $("tickerTrack");
  if (t) t.innerHTML += t.innerHTML; // duplicate for seamless loop
}

function setupNavSpy() {
  const links = Array.from(document.querySelectorAll(".navbar a"));
  const secs = ["sec1", "sec2", "sec3", "sec4", "sec5"]
    .map((id) => document.getElementById(id)).filter(Boolean);
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        const idx = secs.indexOf(e.target);
        links.forEach((a, i) => a.classList.toggle("active", i === idx));
      }
    });
  }, { rootMargin: "-30% 0px -55% 0px" });
  secs.forEach((s) => io.observe(s));
}

/* ============================================================
   6 · DIGITAL STRUCTURE + CAD CHANNELS
   ============================================================ */
const struct = { data: null, exploded: false, wire: false,
  scene: null, camera: null, renderer: null, controls: null,
  group: null, meshes: [], rotY: 0, selected: null };

function structPayload() {
  const p = designPayload();
  delete p.period;
  for (const k in p) if (p[k] === undefined || Number.isNaN(p[k])) return null;
  return p;
}

let structTimer = null;
function scheduleStructure() {
  clearTimeout(structTimer);
  const p = structPayload();
  if (p) { try { localStorage.setItem("shl-draft", JSON.stringify(p)); } catch (e) {} }
  structTimer = setTimeout(buildStructure, 350);
}

async function buildStructure() {
  const p = structPayload();
  const v = $("view3d");
  if (!p) return;
  try {
    const data = await api("/api/cad/structure", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    });
    struct.data = data;
    const b = data.bounding;
    $("structDims").textContent = `${b.length_m.toFixed(2)} × ${b.width_m.toFixed(2)} × ${b.height_m.toFixed(2)} m`;
    $("structVol").textContent = `V ${data.surfaces.volume_m3.toFixed(2)} m³`;
    const tm = data.thermal_mass || {};
    $("structThermal").textContent =
      `LAG wall ${tm.wall_assembly_lag_hours != null ? tm.wall_assembly_lag_hours.toFixed(1) : "—"} h · DF ${tm.wall_decrement_factor != null ? tm.wall_decrement_factor.toFixed(3) : "—"} | roof ${tm.roof_assembly_lag_hours != null ? tm.roof_assembly_lag_hours.toFixed(1) : "—"} h`;
    v.classList.add("ready");
    renderStruct3D();
    renderStructSheet(data);
    $("structLegend").innerHTML = "";
    renderLegend();
  } catch (err) {
    v.classList.remove("ready");
    v.innerHTML = `<div class="view3d-msg">STRUCTURE FEED FAILED — ${err.message}</div>`;
  }
}

function renderStruct3D() {
  if (typeof THREE !== "undefined") renderThree();
  else renderIsoCanvas();
}

/* ---------------- three.js renderer ---------------- */
function ensureThree() {
  if (struct.scene) return true;
  const el = $("view3d");
  const w = el.clientWidth || 640, h = el.clientHeight || 400;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 0.05, 200);
  camera.position.set(4.6, 3.8, 5.4);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h);
  el.innerHTML = "";
  el.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const d1 = new THREE.DirectionalLight(0xffffff, 0.95);
  d1.position.set(6, 9, 5); scene.add(d1);
  const d2 = new THREE.DirectionalLight(0xffffff, 0.35);
  d2.position.set(-5, 3, -6); scene.add(d2);
  const grid = new THREE.GridHelper(9, 18, 0x3a4047, 0x262b31);
  scene.add(grid);
  const group = new THREE.Group();
  scene.add(group);
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  struct.scene = scene; struct.camera = camera; struct.renderer = renderer;
  struct.controls = controls; struct.group = group;
  const ro = new ResizeObserver(() => {
    const w2 = el.clientWidth, h2 = el.clientHeight;
    if (w2 && h2) { renderer.setSize(w2, h2); camera.aspect = w2 / h2; camera.updateProjectionMatrix(); }
  });
  ro.observe(el);
  renderer.domElement.addEventListener("pointermove", onThreeHover);
  renderer.domElement.addEventListener("click", onThreeClick);
  requestAnimationFrame(function loop() {
    requestAnimationFrame(loop);
    if (struct.controls) { struct.controls.update(); struct.renderer.render(struct.scene, struct.camera); }
  });
  return true;
}

function rebuildMeshes() {
  if (!struct.data || !struct.group) return;
  const group = struct.group;
  while (group.children.length) {
    const c = group.children.pop();
    if (c.geometry) c.geometry.dispose();
    if (c.material) c.material.dispose();
  }
  struct.meshes = [];
  const b = struct.data.bounding;
  const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
  for (const c of struct.data.components) {
    const [x0, y0, z0, x1, y1, z1] = c.box;
    const geo = new THREE.BoxGeometry(x1 - x0, z1 - z0, y1 - y0);
    const mat = new THREE.MeshStandardMaterial({
      color: c.color, roughness: 0.9, metalness: 0.05,
      transparent: true,
      opacity: c.type === "window" ? 0.55 : (c.type === "insulation" ? 0.92 : 1),
    });
    const m = new THREE.Mesh(geo, mat);
    // our frame (x east, y north, z up) -> three (x, y up, z); centre on footprint
    m.position.set((x0 + x1) / 2 - cx, (z0 + z1) / 2, (y0 + y1) / 2 - cy);
    m.userData = { id: c.id, type: c.type };
    group.add(m);
    struct.meshes.push(m);
  }
  // orientation: clockwise viewed from above (matches RC azimuth convention)
  group.rotation.y = -THREE.MathUtils.degToRad(struct.data.design.orientation_deg || 0);
  // frame the whole structure
  const hh = b.z1 - b.z0;
  struct.controls.target.set(0, hh / 2 + 0.2, 0);
  struct.camera.position.set(4.6, 3.8, 5.4);
  applyExplode();
  setWire();
  setCutaway();
  setAutoRot();
}

function applyExplode() {
  if (!struct.meshes.length) return;
  const off = struct.exploded ? 1 : 0;
  for (const m of struct.meshes) {
    const id = m.userData.id, t = m.userData.type;
    let dx = 0, dy = 0, dz = 0;
    if (t === "floor") dy = -0.35;
    else if (t === "roof") dy = 0.35;
    else if (t === "insulation") {
      if (id.includes("_north")) dz = 0.16; else if (id.includes("_south")) dz = -0.16;
      else if (id.includes("_east")) dx = 0.16; else if (id.includes("_west")) dx = -0.16;
      else dy = 0.4;
    } else if (t === "window") {
      const wd = struct.data.design.window_wall || "south";
      if (wd === "south") dz = -0.3; else if (wd === "north") dz = 0.3;
      else if (wd === "east") dx = 0.3; else dx = -0.3;
    } else {
      if (id.includes("_north")) dz = 0.26; else if (id.includes("_south")) dz = -0.26;
      else if (id.includes("_east")) dx = 0.26; else if (id.includes("_west")) dx = -0.26;
    }
    m.position.x += dx * off; m.position.y += dy * off; m.position.z += dz * off;
    m.userData._base = m.position.clone();
  }
}

function setWire() {
  if (!struct.meshes.length) return;
  for (const m of struct.meshes) m.material.wireframe = struct.wire;
}

function clearHighlight() {
  for (const m of struct.meshes) m.material.emissive.setHex(0x000000);
  document.querySelectorAll(".struct-sheet tr.hl").forEach((r) => r.classList.remove("hl"));
}

function highlightIds(ids, on) {
  clearHighlight();
  if (!on) return;
  const set = new Set(ids);
  for (const m of struct.meshes) {
    if (set.has(m.userData.id)) m.material.emissive.setHex(0xff9933);
  }
  document.querySelectorAll(".struct-sheet tr[data-id]").forEach((r) => {
    const rids = (r.dataset.id || "").split(",");
    if (rids.some((i) => set.has(i))) r.classList.add("hl");
  });
}

function onThreeHover(e) {
  if (!struct.scene) return;
  const el = $("view3d");
  const rect = el.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1);
  const rc = new THREE.Raycaster();
  rc.setFromCamera(ndc, struct.camera);
  const hits = rc.intersectObjects(struct.meshes, false);
  highlightIds(hits.length ? [hits[0].object.userData.id] : [], hits.length > 0);
}

function onThreeClick(e) {
  if (measure.on) { measureClick(e); return; }
  const el = $("view3d");
  const rect = el.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1);
  const rc = new THREE.Raycaster();
  rc.setFromCamera(ndc, struct.camera);
  const hits = rc.intersectObjects(struct.meshes, false);
  struct.selected = hits.length ? hits[0].object.userData.id : null;
  highlightIds(struct.selected ? [struct.selected] : [], !!struct.selected);
}

function renderThree() {
  ensureThree();
  rebuildMeshes();
}

/* ---------------- canvas isometric fallback (offline preview) ---------------- */
function renderIsoCanvas() {
  const el = $("view3d");
  let cv = el.querySelector("canvas");
  if (!cv) {
    cv = document.createElement("canvas");
    el.innerHTML = "";
    el.appendChild(cv);
    let dragging = false, sx = 0;
    cv.addEventListener("pointerdown", (e) => { dragging = true; sx = e.clientX; });
    window.addEventListener("pointerup", () => { dragging = false; });
    cv.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      struct.rotY = (struct.rotY + (e.clientX - sx) * 0.5 + 360) % 360;
      sx = e.clientX;
      drawIso();
    });
  }
  const fit = () => {
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(10, el.clientWidth) * dpr;
    cv.height = Math.max(10, el.clientHeight) * dpr;
    cv.style.width = "100%"; cv.style.height = "100%";
  };
  fit();
  new ResizeObserver(fit).observe(el);
  drawIso();
}

function drawIso() {
  const el = $("view3d");
  const cv = el.querySelector("canvas");
  if (!cv || !struct.data) return;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const r = -struct.rotY * Math.PI / 180;
  const cosr = Math.cos(r), sinr = Math.sin(r);
  const faces = [];
  for (const c of struct.data.components) {
    const [x0, y0, z0, x1, y1, z1] = c.box;
    const quad = (ax, ay, az, bx, by, bz, cx, cy, cz, dx, dy, dz) => {
      const pts = [[ax, ay, az], [bx, by, bz], [cx, cy, cz], [dx, dy, dz]]
        .map(([px, py, pz]) => {
          const rx = px * cosr - py * sinr, ry = px * sinr + py * cosr;
          return { x: (rx - ry) * 0.866, y: (rx + ry) * 0.5 - pz, z: rx + ry + pz };
        });
      faces.push({ color: c.color, id: c.id, pts, z: pts.reduce((s, p) => s + p.z, 0) / 4 });
    };
    quad(x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0);            // bottom
    quad(x0, y0, z1, x0, y1, z1, x1, y1, z1, x1, y0, z1);            // top
    quad(x0, y0, z0, x0, y1, z0, x0, y1, z1, x0, y0, z1);            // -x
    quad(x1, y0, z0, x1, y0, z1, x1, y1, z1, x1, y1, z0);            // +x
    quad(x0, y0, z0, x0, y0, z1, x1, y0, z1, x1, y0, z0);            // -y
    quad(x0, y1, z0, x1, y1, z0, x1, y1, z1, x0, y1, z1);            // +y
  }
  faces.sort((a, b) => b.z - a.z);
  // fit
  let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
  for (const f of faces) for (const p of f.pts) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
  }
  const s = Math.min(W / (maxX - minX + 1), H / (maxY - minY + 1)) * 0.78;
  const ox = W / 2 - (minX + maxX) / 2 * s, oy = H / 2 - (minY + maxY) / 2 * s;
  for (const f of faces) {
    ctx.beginPath();
    f.pts.forEach((p, i) => {
      const X = ox + p.x * s, Y = oy + p.y * s;
      if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
    });
    ctx.closePath();
    ctx.fillStyle = f.color; ctx.globalAlpha = 0.88;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "rgba(0,0,0,0.35)"; ctx.lineWidth = 1;
    ctx.stroke();
  }
}

/* ---------------- structure data sheet ---------------- */
function lagCell(L, p) {
  if (L.r_m2K_W == null || !p.density_kg_m3 || !p.cp_J_kgK) return "—";
  const tauH = L.r_m2K_W * p.density_kg_m3 * p.cp_J_kgK * L.thickness_m / 3600;
  if (!isFinite(tauH) || tauH <= 0) return "—";
  return `<span title="Thermal lag = R·C of this layer (sourced k, ρ, cp)">${tauH.toFixed(1)}</span>`;
}

function renderStructSheet(data) {
  const tb = $("structSheet");
  if (!tb) return;
  tb.innerHTML = "";
  const props = {};
  for (const c of data.components) {
    if (c.properties && c.material && !props[c.material]) props[c.material] = c.properties;
  }
  const row = (label, ids, layers, total) => {
    for (const L of layers) {
      const p = props[L.material] || {};
      const tr = document.createElement("tr");
      tr.dataset.id = ids;
      tr.innerHTML = `<td>${label}</td><td>${L.material}</td>` +
        `<td>${L.thickness_m.toFixed(3)}</td>` +
        `<td>${L.k_W_mK != null ? L.k_W_mK.toFixed(3) : "—"}</td>` +
        `<td>${p.density_kg_m3 != null ? p.density_kg_m3.toFixed(0) : "—"}</td>` +
        `<td>${p.cp_J_kgK != null ? p.cp_J_kgK.toFixed(0) : "—"}</td>` +
        `<td>${L.r_m2K_W != null ? L.r_m2K_W.toFixed(3) : "—"}</td>` +
        `<td>${L.u_w_m2k != null ? L.u_w_m2k.toFixed(3) : "—"}</td>` +
        `<td>${lagCell(L, p)}</td>`;
      tb.appendChild(tr);
      label = "";
    }
    if (total) {
      const tr = document.createElement("tr");
      tr.className = "total";
      tr.innerHTML = `<td colspan="6">${total.label} — total resistance</td>` +
        `<td>${total.r != null ? total.r.toFixed(3) : "—"}</td>` +
        `<td>${total.u != null ? total.u.toFixed(3) : "—"}</td>`;
      tb.appendChild(tr);
    }
  };
  const wallIds = "wall_south,wall_north,wall_east,wall_west";
  const insIds = "ins_wall_south,ins_wall_north,ins_wall_east,ins_wall_west";
  const A = data.assembly;
  row("FLOOR", "floor", A.floor.layers, null);
  row("WALL", wallIds, A.wall.layers.filter((l) => l.material !== (data.design.insulation_material || "none") || !data.design.insulation_thickness_m),
      { label: "WALL", r: A.wall.total_r_m2K_W, u: A.wall.u_w_m2k });
  row("WALL INSULATION", insIds, A.wall.layers.filter((l) => l.material === data.design.insulation_material && data.design.insulation_thickness_m), null);
  row("ROOF", "roof", A.roof.layers.filter((l) => l.material !== data.design.insulation_material || !data.design.insulation_thickness_m),
      { label: "ROOF", r: A.roof.total_r_m2K_W, u: A.roof.u_w_m2k });
  row("ROOF INSULATION", "ins_roof", A.roof.layers.filter((l) => l.material === data.design.insulation_material && data.design.insulation_thickness_m), null);
  // glazing
  const tr = document.createElement("tr");
  tr.dataset.id = "window";
  tr.innerHTML = `<td>WINDOW</td><td>glass (clear)</td><td>0.020</td><td>—</td><td>—</td><td>—</td><td>—</td>` +
    `<td>${A.window.u_w_m2k != null ? A.window.u_w_m2k.toFixed(3) : "—"} · SHGC ${A.window.shgc != null ? A.window.shgc.toFixed(2) : "—"}</td>`;
  tb.appendChild(tr);
  // hover wiring
  tb.querySelectorAll("tr[data-id]").forEach((r) => {
    r.addEventListener("mouseenter", () => highlightIds(r.dataset.id.split(","), true));
    r.addEventListener("mouseleave", () => highlightIds([], false));
  });
}

/* ---------------- CAD channels ---------------- */
function exportCad(fmt) {
  const p = structPayload();
  if (!p) { toast("Design parameters incomplete", true); return; }
  const q = new URLSearchParams();
  for (const k in p) q.set(k, p[k]);
  fetch(`/api/cad/export?format=${fmt}&${q.toString()}`)
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="?([^";]+)"?/);
      return r.blob().then((b) => ({ b, name: m ? m[1] : `shelter-structure.${fmt}` }));
    })
    .then(({ b, name }) => {
      const a = document.createElement("a");
      const url = URL.createObjectURL(b);
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast(`CAD export ${fmt.toUpperCase()} — ${(b.size / 1024).toFixed(1)} KB`);
    })
    .catch((err) => toast(`CAD export failed: ${err.message}`, true));
}

async function loadCadRecent() {
  const wrap = $("cadRecent");
  if (!wrap) return;
  try {
    const j = await api("/api/cad/imports?limit=6");
    wrap.innerHTML = "";
    if (!j.imports || !j.imports.length) {
      wrap.innerHTML = `<div class="cad-item">NO INGESTIONS LOGGED — UPLOAD DXF / OBJ / STL ABOVE</div>`;
      return;
    }
    for (const i of j.imports) {
      const d = i.dimensions || {};
      const div = document.createElement("div");
      div.className = "cad-item";
      div.innerHTML = `<b>${i.filename || "—"}</b>` +
        `<span>${(i.format || "").toUpperCase()}</span>` +
        `<span class="dims">${d.length_m ?? "—"}×${d.width_m ?? "—"}×${d.height_m ?? "—"} m</span>` +
        `<span class="when">${(i.created_at || "").slice(0, 19).replace("T", " ")}</span>`;
      wrap.appendChild(div);
    }
  } catch (e) { /* silent */ }
}

/* ---------- init ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  updateThemeToggle();
  const tt = $("themeToggle");
  if (tt) tt.addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark"));
  tickClock();
  setupTicker();
  setupNavSpy();
  checkHealth();
  try {
    await loadLocations();
    await loadMaterials();
  } catch (err) {
    $("loadClimate").textContent = `API unavailable: ${err.message}`;
  }
  $("loadClimate").addEventListener("click", loadClimate);
  $("simulate").addEventListener("click", runSimulate);
  $("optimize").addEventListener("click", runOptimize);

  /* --- digital structure: live rebuild on design changes --- */
  ["length", "width", "height", "orientation", "wallMat", "wallThick",
   "roofMat", "roofThick", "insMat", "insThick", "winWall", "winSize"]
    .forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("input", scheduleStructure);
      el.addEventListener("change", scheduleStructure);
    });
  $("explode").addEventListener("change", () => {
    struct.exploded = $("explode").checked;
    applyExplode();
  });
  $("wire").addEventListener("change", () => {
    struct.wire = $("wire").checked;
    setWire();
  });

  /* --- CAD channels --- */
  $("cadPick").addEventListener("click", () => $("cadFile").click());
  $("cadFile").addEventListener("change", () => {
    const f = $("cadFile").files[0];
    $("cadFileLabel").textContent = f ? `${f.name} (${(f.size / 1024).toFixed(0)} KB)` : "no file selected";
    $("cadImport").disabled = !f;
  });
  $("cadImport").addEventListener("click", async () => {
    const f = $("cadFile").files[0];
    if (!f) return;
    const btn = $("cadImport");
    btn.disabled = true; btn.classList.add("busy");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const j = await api("/api/cad/import", { method: "POST", body: fd });
      const s = j.suggested_design || {};
      if (s.length_m) $("length").value = s.length_m;
      if (s.width_m) $("width").value = s.width_m;
      if (s.height_m) $("height").value = s.height_m;
      $("structSource").textContent = `CAD · ${(j.filename || "").split(".").pop().toUpperCase()}`;
      toast(`CAD ingested: ${j.filename} — ${j.dimensions_m.length_m}×${j.dimensions_m.width_m}×${j.dimensions_m.height_m} m`);
      scheduleStructure();
      loadCadRecent();
    } catch (err) {
      toast(`CAD ingest failed: ${err.message}`, true);
    } finally {
      btn.classList.remove("busy"); btn.disabled = false;
      $("cadFile").value = ""; $("cadFileLabel").textContent = "no file selected";
    }
  });
  $("exportDxf").addEventListener("click", () => exportCad("dxf"));
  $("exportObj").addEventListener("click", () => exportCad("obj"));
  $("exportStl").addEventListener("click", () => exportCad("stl"));
  $("exportJson").addEventListener("click", exportDesignJson);
  $("exportReport").addEventListener("click", exportReport);
  $("importJson").addEventListener("click", () => $("jsonFile").click());
  $("jsonFile").addEventListener("change", () => {
    const f = $("jsonFile").files[0];
    if (f) importDesignJson(f);
    $("jsonFile").value = "";
  });

  /* --- 3D pro tools --- */
  $("zoomIn").addEventListener("click", () => zoomThree(0.85));
  $("zoomOut").addEventListener("click", () => zoomThree(1.18));
  $("viewReset").addEventListener("click", resetView);
  $("full3d").addEventListener("click", toggleFull3D);
  $("autoRot").addEventListener("change", () => {
    struct.autoRot = $("autoRot").checked;
    setAutoRot();
  });
  $("cutaway").addEventListener("change", () => {
    struct.cutaway = $("cutaway").checked;
    setCutaway();
  });
  $("measureBtn").addEventListener("click", () => setMeasure(!measure.on));

  /* --- design library --- */
  $("saveDesign").addEventListener("click", saveCurrentDesign);
  $("libTable").addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const id = act.dataset.id;
    const row = lib.data.find((d) => d.design_id === id);
    if (!row) return;
    if (act.dataset.act === "load") {
      applyDesignToForm(row.design);
      $("designName").value = row.name || "";
      $("structSource").textContent = `LIBRARY · ${(row.name || "").toUpperCase()}`;
      toast(`Loaded design — ${row.name}`);
      scheduleStructure();
    } else if (act.dataset.act === "ren") {
      const name = prompt("Design name:", row.name || "");
      if (name !== null) {
        api(`/api/designs/${id}`, { method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }) }).then(loadDesigns)
          .catch((err) => toast(err.message, true));
      }
    } else if (act.dataset.act === "del") {
      if (act.textContent !== "SURE?") {
        act.textContent = "SURE?";
        act.style.color = "var(--red)";
        setTimeout(() => { act.textContent = "DEL"; act.style.color = ""; }, 3000);
        return;
      }
      api(`/api/designs/${id}`, { method: "DELETE" })
        .then(() => { lib.sel = lib.sel.filter((x) => x !== id); loadDesigns(); loadStats(); toast("Design deleted"); })
        .catch((err) => toast(err.message, true));
    }
  });
  $("libTable").addEventListener("click", (e) => {
    const star = e.target.closest(".fav");
    if (!star) return;
    const tr = star.closest("tr");
    const id = tr.querySelector("[data-act]").dataset.id;
    const row = lib.data.find((d) => d.design_id === id);
    api(`/api/designs/${id}`, { method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorite: !row.favorite }) })
      .then(loadDesigns).catch((err) => toast(err.message, true));
  });
  $("libTable").addEventListener("click", (e) => {
    const tbody = e.target.closest("tbody");
    if (!tbody || e.target.closest("[data-act]") || e.target.closest(".fav")) return;
    const tr = e.target.closest("tr");
    const id = tr.querySelector("[data-act]").dataset.id;
    toggleCompare(id, tr);
  });
  $("compareBtn").addEventListener("click", runCompare);

  /* --- sweep + stats --- */
  $("sweepBtn").addEventListener("click", runSweep);

  /* --- keyboard --- */
  setupShortcuts();

  /* --- first paint: draft -> structure -> library/stats --- */
  const restored = applyDraft();
  if (restored) toast("Draft design restored from this browser");
  await buildStructure();
  loadCadRecent();
  loadDesigns();
  loadStats();

  /* --- accounts + fleet --- */
  initAuthFleet();

  /* --- site adaptation --- */
  initAdapt();
  initAi();
});

/* ============================================================
   7 · 3D PRO TOOLS · DESIGN LIBRARY · SWEEP · STATS · MORE
   ============================================================ */
// hidden file input for JSON design import (created in JS)
(function () {
  const i = document.createElement("input");
  i.type = "file"; i.id = "jsonFile"; i.accept = ".json,application/json"; i.hidden = true;
  document.body.appendChild(i);
})();
const lib = { sel: [], data: [] };
const measure = { on: false, a: null, marker: null, line: null };

/* ---------------- 3D pro tools ---------------- */
function zoomThree(factor) {
  if (!struct.camera || !struct.controls) return;
  const dir = struct.camera.position.clone().sub(struct.controls.target);
  struct.camera.position.copy(struct.controls.target).add(dir.multiplyScalar(factor));
}
function resetView() {
  if (struct.controls && struct.data) {
    const b = struct.data.bounding;
    struct.controls.target.set(0, (b.z1 - b.z0) / 2 + 0.2, 0);
    struct.camera.position.set(4.6, 3.8, 5.4);
  }
  struct.rotY = 0;
  if (typeof THREE === "undefined") drawIso();
}
function toggleFull3D() {
  const holder = $("view3d").closest(".mod");
  holder.classList.toggle("view3d-full");
  if (holder.classList.contains("view3d-full"))
    holder.scrollIntoView({ block: "start" });
  if (typeof THREE !== "undefined" && struct.renderer) {
    setTimeout(() => {
      const el = $("view3d");
      struct.renderer.setSize(el.clientWidth, el.clientHeight);
      struct.camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
      struct.camera.updateProjectionMatrix();
    }, 60);
  }
}
function setCutaway() {
  if (!struct.meshes.length) return;
  const hide = new Set(["roof", "ins_roof", "wall_south", "ins_wall_south"]);
  const ww = struct.data.design.window_wall || "south";
  if (ww === "south") hide.add("window");
  for (const m of struct.meshes)
    m.visible = !(struct.cutaway && hide.has(m.userData.id));
}
function setAutoRot() {
  if (struct.controls) {
    struct.controls.autoRotate = struct.autoRot;
    struct.controls.autoRotateSpeed = 2.0;
  }
}
function measureClick(e) {
  if (!measure.on || typeof THREE === "undefined" || !struct.scene) return;
  const el = $("view3d");
  const rect = el.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1);
  const rc = new THREE.Raycaster();
  rc.setFromCamera(ndc, struct.camera);
  const hits = rc.intersectObjects(struct.meshes, false);
  if (!hits.length) return;
  const p = hits[0].point;
  if (!measure.a) {
    measure.a = p.clone();
    if (measure.marker) struct.scene.remove(measure.marker);
    measure.marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.035, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0x5eea8d }));
    measure.marker.position.copy(p);
    struct.scene.add(measure.marker);
    $("measureOut").textContent = "POINT A SET — CLICK TARGET";
  } else {
    const dist = measure.a.distanceTo(p);
    if (measure.line) struct.scene.remove(measure.line);
    const g = new THREE.BufferGeometry().setFromPoints([measure.a, p]);
    measure.line = new THREE.Line(g,
      new THREE.LineBasicMaterial({ color: 0xff9933, linewidth: 2 }));
    struct.scene.add(measure.line);
    $("measureOut").textContent = `DISTANCE ${dist.toFixed(2)} m`;
    measure.a = null;
  }
}
function setMeasure(on) {
  measure.on = on;
  $("measureBtn").classList.toggle("active", on);
  $("measureBtn").textContent = on ? "📏 MEASURING…" : "📏 MEASURE";
  $("measureOut").textContent = "";
  if (!on && measure.marker) { struct.scene.remove(measure.marker); measure.marker = null; }
  if (!on && measure.line) { struct.scene.remove(measure.line); measure.line = null; }
  measure.a = null;
}

/* ---------------- 3D legend ---------------- */
function renderLegend() {
  const wrap = $("structLegend");
  if (!wrap || !struct.data) return;
  const seen = {};
  for (const c of struct.data.components) {
    if (!seen[c.type]) {
      seen[c.type] = c.color;
      const s = document.createElement("span");
      s.innerHTML = `<i style="background:${c.color}"></i>${c.type.toUpperCase()}`;
      wrap.appendChild(s);
    }
  }
}

/* ---------------- design library ---------------- */
function libDesigns() { return lib.data; }

async function loadDesigns() {
  try {
    const j = await api("/api/designs?limit=100");
    lib.data = j.designs || [];
    renderLibrary();
  } catch (e) { /* silent */ }
}

function renderLibrary() {
  const tb = $("libTable");
  if (!tb) return;
  tb.innerHTML = "";
  if (!lib.data.length) {
    tb.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--dim)">
      NO SAVED DESIGNS YET — CONFIGURE THE MATRIX AND HIT “SAVE DESIGN”</td></tr>`;
    return;
  }
  for (const d of lib.data) {
    const p = d.design || {};
    const tr = document.createElement("tr");
    if (lib.sel.includes(d.design_id)) tr.className = "sel";
    const updated = (d.updated_at || "").replace("T", " ").slice(0, 16);
    const ins = p.insulation_material && p.insulation_material !== "none"
      ? `${p.insulation_material} ${Math.round((p.insulation_thickness_m || 0) * 1000)}mm` : "—";
    tr.innerHTML =
      `<td class="fav ${d.favorite ? "" : "off"}" title="favorite">${d.favorite ? "★" : "☆"}</td>` +
      `<td><b>${d.name || "Design"}</b></td>` +
      `<td>${p.length_m ?? "—"}×${p.width_m ?? "—"}×${p.height_m ?? "—"}</td>` +
      `<td>${p.wall_material || "—"} / ${p.roof_material || "—"}</td>` +
      `<td>${ins}</td><td>—</td><td>—</td><td>${updated}</td>` +
      `<td><div class="row-actions">
         <button data-act="load" data-id="${d.design_id}">LOAD</button>
         <button data-act="ren" data-id="${d.design_id}">REN</button>
         <button data-act="del" data-id="${d.design_id}">DEL</button>
       </div></td>`;
    tb.appendChild(tr);
  }
  // enrich rows with computed U / mass asynchronously (cheap: reuse structure)
  lib.data.forEach((d) => enrichLibraryRow(d));
}

async function enrichLibraryRow(d) {
  try {
    const j = await api("/api/cad/structure", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d.design),
    });
    const rows = [...$("libTable").querySelectorAll("tr")];
    const tr = rows.find((r) => r.querySelector(`[data-id="${d.design_id}"]`));
    if (!tr) return;
    const tds = tr.querySelectorAll("td");
    tds[5].textContent = j.assembly.wall.u_w_m2k != null ? j.assembly.wall.u_w_m2k.toFixed(2) : "—";
    tds[6].textContent = j.mass.total_mass_kg != null ? Math.round(j.mass.total_mass_kg) + " kg" : "—";
  } catch (e) { /* keep placeholders */ }
}

function applyDesignToForm(d) {
  if (!d) return;
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const set = (id, v) => { if (v !== null && $(id)) $(id).value = v; };
  set("length", num(d.length_m)); set("width", num(d.width_m));
  set("height", num(d.height_m));
  if (num(d.orientation_deg) !== null) set("orientation", Math.round(d.orientation_deg));
  set("wallThick", num(d.wall_thickness_m)); set("roofThick", num(d.roof_thickness_m));
  set("insThick", num(d.insulation_thickness_m) !== null
      ? Math.round(d.insulation_thickness_m * 1000) : null);
  if (d.window_width_m && d.window_height_m)
    set("winSize", `${d.window_width_m} × ${d.window_height_m}`);
  const opt = (id, v) => {
    const s = $(id);
    if (v && s && [...s.options].some((o) => o.value === v)) s.value = v;
  };
  opt("wallMat", d.wall_material); opt("roofMat", d.roof_material);
  opt("insMat", d.insulation_material || "none"); opt("winWall", d.window_wall);
}

function currentFlatDesign() {
  return structPayload();
}

async function saveCurrentDesign() {
  const p = currentFlatDesign();
  if (!p) { toast("Design parameters incomplete", true); return; }
  const name = ($("designName").value || "").trim() ||
    `Design ${new Date().toISOString().slice(0, 16).replace("T", " ")}`;
  const btn = $("saveDesign");
  btn.classList.add("busy");
  try {
    const j = await api("/api/designs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, design: p }),
    });
    $("libStatus").textContent = `SAVED · ${j.design_id}`;
    toast(window.SHI && SHI.getUser()
      ? `Design saved to ${SHI.getUser().username}'s library`
      : "Design saved in guest mode — sign in to keep it on your account");
    await loadDesigns();
    loadStats();
  } catch (err) {
    toast(`Save failed: ${err.message}`, true);
  } finally { btn.classList.remove("busy"); }
}

function toggleCompare(id, tr) {
  const i = lib.sel.indexOf(id);
  if (i >= 0) lib.sel.splice(i, 1);
  else if (lib.sel.length < 2) lib.sel.push(id);
  else lib.sel.shift(), lib.sel.push(id);
  renderLibrary();
  $("compareBtn").disabled = lib.sel.length !== 2;
}

async function runCompare() {
  if (lib.sel.length !== 2) return;
  const [a, b] = lib.sel.map((id) => lib.data.find((d) => d.design_id === id));
  const [ja, jb] = await Promise.all([
    api("/api/cad/structure", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a.design) }),
    api("/api/cad/structure", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b.design) }),
  ]);
  const rows = [
    ["Dimensions L×W×H (m)", `${ja.bounding.length_m}×${ja.bounding.width_m}×${ja.bounding.height_m}`,
     `${jb.bounding.length_m}×${jb.bounding.width_m}×${jb.bounding.height_m}`],
    ["Orientation (°)", ja.design.orientation_deg, jb.design.orientation_deg],
    ["Wall", `${ja.design.wall_material} ${ja.design.wall_thickness_m}m`, `${jb.design.wall_material} ${jb.design.wall_thickness_m}m`],
    ["Roof", `${ja.design.roof_material} ${ja.design.roof_thickness_m}m`, `${jb.design.roof_material} ${jb.design.roof_thickness_m}m`],
    ["Insulation", ja.design.insulation_material === "none" ? "none" : `${ja.design.insulation_material} ${Math.round(ja.design.insulation_thickness_m * 1000)}mm`,
     jb.design.insulation_material === "none" ? "none" : `${jb.design.insulation_material} ${Math.round(jb.design.insulation_thickness_m * 1000)}mm`],
    ["U wall (W/m²K)", ja.assembly.wall.u_w_m2k, jb.assembly.wall.u_w_m2k],
    ["U roof (W/m²K)", ja.assembly.roof.u_w_m2k, jb.assembly.roof.u_w_m2k],
    ["U floor (W/m²K)", ja.assembly.floor.u_w_m2k, jb.assembly.floor.u_w_m2k],
    ["Glazing ratio (%)", ja.surfaces.glazing_ratio_pct, jb.surfaces.glazing_ratio_pct],
    ["Volume (m³)", ja.surfaces.volume_m3, jb.surfaces.volume_m3],
    ["Envelope mass (kg)", ja.mass.total_mass_kg, jb.mass.total_mass_kg],
    ["Window U / SHGC", `${ja.assembly.window.u_w_m2k} / ${ja.assembly.window.shgc}`,
     `${jb.assembly.window.u_w_m2k} / ${jb.assembly.window.shgc}`],
  ];
  const head = $("compareHead");
  head.innerHTML = `<tr><th>Property</th><th>${a.name}</th><th>${b.name}</th></tr>`;
  const tb = $("compareBody");
  tb.innerHTML = "";
  for (const [label, va, vb] of rows) {
    const tr = document.createElement("tr");
    const isNum = (typeof va === "number") && (typeof vb === "number");
    let clsA = "", clsB = "";
    if (isNum && va !== vb) {
      if (label.includes("U ") || label.includes("Glazing")) {
        clsA = va < vb ? "hi" : ""; clsB = vb < va ? "hi" : "";
      } else if (label.includes("Mass")) {
        clsA = va < vb ? "hi" : ""; clsB = vb < va ? "hi" : "";
      }
    }
    tr.innerHTML = `<td>${label}</td>` +
      `<td class="${clsA}">${typeof va === "number" ? va.toFixed ? (Math.abs(va) < 100 ? +va.toFixed(3) : Math.round(va)) : va : va}</td>` +
      `<td class="${clsB}">${typeof vb === "number" ? vb.toFixed ? (Math.abs(vb) < 100 ? +vb.toFixed(3) : Math.round(vb)) : vb : vb}</td>`;
    tb.appendChild(tr);
  }
  $("comparePanel").hidden = false;
}

/* ---------------- stats strip ---------------- */
async function loadStats() {
  try {
    const j = await api("/api/stats");
    $("stSims").textContent = j.simulations;
    $("stOpts").textContent = j.optimizations;
    $("stDesigns").textContent = j.designs;
    $("stCad").textContent = j.cad_imports;
  } catch (e) { /* silent */ }
}

/* ---------------- insulation sweep ---------------- */
async function runSweep() {
  const p = structPayload();
  if (!p) { toast("Design parameters incomplete", true); return; }
  const grid = ($("sweepGrid").value || "")
    .split(/[,\s]+/).map((v) => parseFloat(v)).filter((v) => isFinite(v));
  if (!grid.length) { toast("Enter a thickness grid (mm)", true); return; }
  const btn = $("sweepBtn");
  btn.classList.add("busy"); btn.disabled = true;
  const st = $("sweepStatus");
  st.textContent = `running ${grid.length} points…`;
  try {
    const j = await api("/api/sweep", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, thicknesses_mm: grid }),
    });
    const xs = j.points.map((q) => q.thickness_mm);
    plot($("chartSweep"), [
      { x: xs, y: j.points.map((q) => q.mean_indoor_c), type: "scatter", mode: "lines+markers",
        name: "MEAN INDOOR °C", line: { color: "#6ab7ff", width: 2 },
        marker: { size: 7, color: "#6ab7ff" } },
      { x: xs, y: j.points.map((q) => q.max_indoor_c), type: "scatter", mode: "lines+markers",
        name: "MAX INDOOR °C", line: { color: "#ff5d5d", width: 2, dash: "dash" },
        marker: { size: 6, color: "#ff5d5d" } },
    ], { title: `INSULATION SWEEP · ${j.insulation_material.toUpperCase()} · HOT WEEK` });
    const b = j.best;
    st.textContent = `best ${b.thickness_mm} mm → mean ${b.mean_indoor_c.toFixed(1)}°C · max ${b.max_indoor_c.toFixed(1)}°C`;
  } catch (err) {
    st.textContent = `SWEEP FAILED: ${err.message}`;
    st.classList.add("err");
  } finally {
    btn.classList.remove("busy"); btn.disabled = false;
  }
}

/* ---------------- JSON exchange + report ---------------- */
function downloadBlob(content, name, type) {
  const a = document.createElement("a");
  const url = URL.createObjectURL(new Blob([content], { type }));
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function exportDesignJson() {
  const p = currentFlatDesign();
  if (!p) { toast("Design parameters incomplete", true); return; }
  downloadBlob(JSON.stringify(p, null, 2), "shelter-design.json",
               "application/json");
  toast("Design JSON exported");
}

function importDesignJson(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const d = JSON.parse(reader.result);
      if (!d || typeof d !== "object" || !d.length_m)
        throw new Error("not a design file (missing length_m)");
      applyDesignToForm(d);
      $("structSource").textContent = "JSON · IMPORTED";
      toast(`Design imported from ${file.name}`);
      scheduleStructure();
    } catch (err) {
      toast(`JSON import failed: ${err.message}`, true);
    }
  };
  reader.readAsText(file);
}

function exportReport() {
  const p = currentFlatDesign();
  if (!p) { toast("Design parameters incomplete", true); return; }
  const q = new URLSearchParams();
  for (const k in p) q.set(k, p[k]);
  fetch(`/api/cad/report?${q.toString()}`)
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then((b) => {
      const a = document.createElement("a");
      const url = URL.createObjectURL(b);
      a.href = url; a.download = "shelter-report.md";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast(`Report downloaded — ${(b.size / 1024).toFixed(1)} KB`);
    })
    .catch((err) => toast(`Report failed: ${err.message}`, true));
}

/* ---------------- draft autosave + keyboard ---------------- */
function applyDraft() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem("shl-draft") || "null"); } catch (e) {}
  if (!d || !d.length_m) return false;
  applyDesignToForm(d);
  return true;
}

function setupShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const k = e.key.toLowerCase();
    if (k === "s") { e.preventDefault(); $("saveDesign").click(); }
    else if (k === "f") { $("full3d").click(); }
    else if (k === "r") { $("viewReset").click(); }
    else if (k === "m") { $("measureBtn").click(); }
    else if (k >= "1" && k <= "6") {
      const a = document.querySelector(`.navbar a[href="#sec${k}"]`);
      if (a) a.click();
    }
  });
}

/* ============================================================
   8 · ACCOUNTS + SHELTER FLEET MANAGEMENT
============================================================ */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderAuthChip() {
  const chip = $("authChip");
  if (!chip) return;
  const user = window.SHI ? SHI.getUser() : null;
  chip.innerHTML = user
    ? `<span class="who" title="Signed in">◈ ${escapeHtml(user.username)}</span>
       <button id="authLogout" type="button" class="btn-sm">SIGN OUT</button>`
    : `<a class="signin" href="login.html">SIGN IN / REGISTER</a>`;
  const lo = $("authLogout");
  if (lo) lo.addEventListener("click", () => {
    SHI.logout(); toast("Signed out — back to guest mode");
  });
}

/* ---------------- fleet ---------------- */
async function loadShelters() {
  try {
    const j = await api("/api/shelters");
    renderFleet(j.shelters || []);
  } catch (err) { toast("Fleet: " + err.message, true); }
}

function statusClass(s) {
  return ({ planned: "st-planned", deployed: "st-deployed",
            maintenance: "st-maint", retired: "st-retired" })[s] || "";
}

function renderFleet(list) {
  const tb = $("fleetBody");
  if (!tb) return;
  const counts = { planned: 0, deployed: 0, maintenance: 0, retired: 0 };
  const temps = [];
  tb.innerHTML = "";
  for (const s of list) {
    counts[s.status] = (counts[s.status] || 0) + 1;
    const m = s.metrics || {};
    const t = m.mean_indoor_c;
    if (typeof t === "number") temps.push(t);
    const d = s.design || {};
    const dims = d.length_m ? `${fmt(d.length_m, 1)}×${fmt(d.width_m, 1)}×${fmt(d.height_m, 1)} m` : "—";
    const tr = document.createElement("tr");
    tr.dataset.id = s.shelter_id;
    const zone = m.zone_name || "—";
    tr.innerHTML = `
      <td><b>${escapeHtml(s.name)}</b>
        <br><span class="dim">${escapeHtml(s.shelter_id.slice(4))}${s.notes ? " · " + escapeHtml(s.notes) : ""}</span></td>
      <td>${escapeHtml(s.location_name || "—")}
        ${s.latitude != null ? `<br><span class="dim">${fmt(s.latitude, 4)}, ${fmt(s.longitude, 4)}</span>` : ""}</td>
      <td><span class="chip chip-zone" title="NBC 2016-style climate zone">${escapeHtml(zone)}</span></td>
      <td><span class="chip ${statusClass(s.status)}">${s.status.toUpperCase()}</span></td>
      <td>${dims}</td>
      <td>${typeof t === "number"
        ? `<b>${fmt(t, 1)} °C</b><br><span class="dim">peak ${typeof m.max_indoor_c === "number" ? fmt(m.max_indoor_c, 1) + " °C" : "—"}</span>`
        : (m.error ? `<span class="err">${escapeHtml(m.error)}</span>` : "—")}</td>
      <td class="row-actions">
        ${s.status === "planned" ? `<button data-act="deploy" title="Deploy">▶ DEPLOY</button>` : ""}
        <button data-act="maint" title="To maintenance">🛠</button>
        <button data-act="retire" title="Retire">▣</button>
        <button data-act="del" class="danger" title="Remove">✕</button>
      </td>`;
    tb.appendChild(tr);
  }
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set("fsPlanned", counts.planned); set("fsDeployed", counts.deployed);
  set("fsMaint", counts.maintenance); set("fsRetired", counts.retired);
  const avg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;
  set("fsAvg", avg != null ? `${fmt(avg, 1)} °C` : "—");
  set("fleetStatus", `FLEET · ${list.length}`);
  set("stShelters", list.length);
}

function collectCurrentDesign() {
  const flat = {};
  const map = { length: "length_m", width: "width_m", height: "height_m",
                orientation: "orientation_deg", wallMat: "wall_material",
                wallThick: "wall_thickness_m", roofMat: "roof_material",
                roofThick: "roof_thickness_m", insMat: "insulation_material",
                insThick: "insulation_thickness_m", winWall: "window_wall" };
  for (const [id, key] of Object.entries(map)) {
    const el = $(id);
    if (el && el.value !== "" && el.value != null) flat[key] = el.value;
  }
  const ws = $("winSize");
  if (ws && ws.value) {
    const parts = ws.value.split("×").map((v) => parseFloat(v));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      flat.window_width_m = parts[0]; flat.window_height_m = parts[1];
    }
  }
  return flat;
}

async function addShelter() {
  const name = $("shName").value.trim();
  if (!name) { toast("Give the shelter a name first", true); return; }
  const loc = $("shLocation").value;
  const [lat, lon, lname] = loc.split("|");
  const design = $("shUseCurrent").checked ? collectCurrentDesign() : {};
  try {
    await api("/api/shelters", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, location_name: lname || "",
        latitude: parseFloat(lat), longitude: parseFloat(lon),
        design, status: $("shStatus").value,
        notes: $("shNotes").value.trim(), compute: true }) });
    $("shName").value = ""; $("shNotes").value = "";
    toast("Shelter registered — thermal metrics computed");
    loadShelters(); loadStats();
  } catch (err) { toast("Fleet: " + err.message, true); }
}

async function fleetAction(id, act) {
  try {
    if (act === "del") {
      await api(`/api/shelters/${id}`, { method: "DELETE" });
      toast("Shelter removed from fleet");
    } else {
      const status = act === "deploy" ? "deployed"
        : act === "maint" ? "maintenance" : "retired";
      await api(`/api/shelters/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }) });
      toast(`Shelter → ${status.toUpperCase()}`);
    }
    loadShelters(); loadStats();
  } catch (err) { toast("Fleet: " + err.message, true); }
}

function initFleetForm() {
  const sel = $("shLocation");
  if (!sel) return;
  sel.innerHTML = "";
  const locs = (typeof state !== "undefined" && state.locations && state.locations.length)
    ? state.locations
    : [{ name: "Prayagraj", latitude: 25.4358, longitude: 81.8463 }];
  locs.forEach((l) => {
    const o = document.createElement("option");
    o.value = `${l.latitude}|${l.longitude}|${l.name}`;
    o.textContent = `${l.name} (${l.latitude.toFixed(3)}, ${l.longitude.toFixed(3)})`;
    sel.appendChild(o);
  });
  const add = $("shAdd");
  if (add) add.addEventListener("click", addShelter);
  const tb = $("fleetBody");
  if (tb) tb.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    const tr = e.target.closest("tr[data-id]");
    if (!btn || !tr) return;
    if (btn.dataset.act === "del" && !confirm("Remove this shelter from the fleet?")) return;
    fleetAction(tr.dataset.id, btn.dataset.act);
  });
}

function initAuthFleet() {
  renderAuthChip();
  if (window.SHI) {
    SHI.onAuth(() => { renderAuthChip(); loadDesigns(); loadStats(); loadShelters(); });
  }
  initFleetForm();
  loadShelters();
}

/* ============================================================
   9 · LOCATION CHARACTERISTICS + SITE-ADAPTED DESIGN
============================================================ */
let adapt = null;   // last recommendation payload

function renderProfile(p) {
  const zb = $("locZone");
  if (!zb) return;
  zb.textContent = `CLIMATE ZONE · ${p.zone_name}`;
  zb.dataset.zone = p.zone;
  $("locZoneBasis").textContent =
    `hottest month ${fmt(p.t_hottest_month_c, 1)} °C · coldest ${fmt(p.t_coldest_month_c, 1)} °C · RH ${fmt(p.rh_mean_pct, 0)}%`;
  const wrap = $("locMetrics");
  wrap.innerHTML = "";
  const items = [
    [fmt(p.t_mean_c, 1), "annual mean temp °C"],
    [fmt(p.diurnal_range_c, 1), "diurnal range °C"],
    [fmt(p.hdd18, 0), "heating degree-days · 18°C"],
    [fmt(p.cdd18, 0), "cooling degree-days · 18°C"],
    [fmt(p.ghi_mean_w_m2, 0), "mean solar W/m²"],
    [fmt(p.rh_mean_pct, 0), "mean RH %"],
    [fmt(p.wind_mean_ms, 2), "mean wind m/s"],
    [fmt(p.wet_hours_pct, 0), "wet hours %"],
  ];
  items.forEach(([v, l]) => wrap.append(metric(v, l)));
  if (p.wind_rose && p.wind_rose.length && typeof Plotly !== "undefined") {
    plot($("chartWind"), [{
      type: "barpolar", r: p.wind_rose.map((x) => x.freq_pct),
      theta: p.wind_rose.map((x) => x.center_deg),
      hovertemplate: "%{theta}° · %{r}% of hours<extra></extra>",
      marker: { color: p.wind_rose.map((x) => x.freq_pct),
                colorscale: [[0, "#3a4a5e"], [1, "#ff9933"]],
                colorbar: { title: "% of hours", thickness: 8 } },
      width: 0.9,
    }], { title: "WIND DIRECTION FREQUENCY · % HOURS",
           polar: { radialaxis: { showticklabels: false } },
           margin: { l: 40, r: 30, t: 36, b: 20 },
           paper_bgcolor: "transparent", plot_bgcolor: "transparent" });
  }
  if (p.diurnal && p.diurnal.length && typeof Plotly !== "undefined") {
    plot($("chartDiurnal"), [{
      x: p.diurnal.map((x) => x.hour), y: p.diurnal.map((x) => x.mean_c),
      type: "scatter", mode: "lines+markers",
      line: { color: "#ff5d5d", width: 2 }, marker: { size: 4, color: "#ff5d5d" },
      fill: "tozeroy", fillcolor: "rgba(255,93,93,0.10)",
    }], { title: "MEAN TEMPERATURE BY HOUR OF DAY · °C",
          xaxis: { title: "hour", dtick: 3 },
          margin: { l: 44, r: 20, t: 36, b: 30 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent" });
  }
  const st = $("locStrategies");
  st.innerHTML = "";
  (p.guidance || []).forEach((g) => {
    const el = document.createElement("span");
    el.className = "strat-chip";
    el.textContent = g;
    st.appendChild(el);
  });
}

async function loadProfile() {
  const sel = $("location").selectedOptions[0];
  try {
    const p = await api("/api/location/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: parseFloat(sel.dataset.lat),
                             lon: parseFloat(sel.dataset.lon),
                             year: parseInt($("year").value, 10) }),
    });
    renderProfile(p);
  } catch (err) {
    $("locZone").textContent = "PROFILE FAILED";
    toast(`Profile: ${err.message}`, true);
  }
}

function adaptValue(p) {
  const cur = structPayload() || {};
  const v = adapt.recommendation.design[p];
  if (p === "insulation_thickness_m") return `${fmt(cur[p] * 1000, 0)} mm → ${fmt(v * 1000, 0)} mm`;
  if (p === "window_width_m" || p === "window_height_m") return `${fmt(cur[p], 2)} → ${fmt(v, 2)} m`;
  if (p === "wall_thickness_m" || p === "roof_thickness_m") return `${fmt(cur[p], 2)} → ${fmt(v, 2)} m`;
  return `${cur[p] ?? "—"} → ${v}`;
}

async function runAdapt() {
  const sel = $("location").selectedOptions[0];
  const btn = $("adaptBtn");
  btn.disabled = true; $("adaptBusy").hidden = false;
  try {
    const j = await api("/api/location/recommend", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: parseFloat(sel.dataset.lat),
                             lon: parseFloat(sel.dataset.lon),
                             year: parseInt($("year").value, 10),
                             design: structPayload() || undefined }),
    });
    adapt = j;
    renderProfile(j.profile);
    const tb = $("recBody");
    tb.innerHTML = "";
    const labels = {
      orientation_deg: "Orientation °", wall_material: "Wall material",
      wall_thickness_m: "Wall thickness m", roof_material: "Roof material",
      insulation_material: "Insulation", insulation_thickness_m: "Insulation mm",
      window_wall: "Window wall", window_width_m: "Window width m",
      ach: "Ventilation ACH",
    };
    for (const r of j.recommendation.rationale) {
      if (!labels[r.parameter]) continue;
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${labels[r.parameter]}</td>
        <td class="cur">${escapeHtml(adaptValue(r.parameter))}</td>
        <td class="rec">${escapeHtml(r.value)}</td>
        <td class="why">${escapeHtml(r.why)}</td>`;
      tb.appendChild(tr);
    }
    $("recWrap").hidden = false;
    $("applyAdapt").hidden = false;
    const bm = j.baseline_metrics, rm = j.recommended_metrics;
    $("adaptStatus").textContent =
      `PREDICTED HOT WEEK · ${fmt(bm.mean_indoor_c, 1)} °C → ${fmt(rm.mean_indoor_c, 1)} °C mean · peak ${fmt(bm.max_indoor_c, 1)} → ${fmt(rm.max_indoor_c, 1)} °C`;
    plot($("adaptChart"), [{
      x: ["Current design", "Site-adapted"], y: [bm.mean_indoor_c, rm.mean_indoor_c],
      type: "bar", name: "mean indoor °C", marker: { color: ["#8a8177", "#ff9933"] },
    }, {
      x: ["Current design", "Site-adapted"], y: [bm.max_indoor_c, rm.max_indoor_c],
      type: "bar", name: "peak indoor °C",
      marker: { color: ["rgba(138,129,119,0.45)", "rgba(255,153,51,0.45)"] },
    }], { title: "VALIDATED · HOT-WEEK RC SIMULATION", barmode: "group" });
    toast(`Site-adapted design ready — ${j.delta.mean_indoor_c > 0 ? "+" : ""}${fmt(j.delta.mean_indoor_c, 1)} °C mean vs current`);
  } catch (err) {
    toast(`Adapt failed: ${err.message}`, true);
  } finally {
    btn.disabled = false; $("adaptBusy").hidden = true;
  }
}

function applyAdapt() {
  if (!adapt) return;
  applyDesignToForm(adapt.recommendation.design);
  scheduleStructure();
  toast("Adapted design applied — rebuild 3D + run simulation to verify");
  document.getElementById("sec4").scrollIntoView({ behavior: "smooth", block: "start" });
}

function initAdapt() {
  const ab = $("adaptBtn");
  if (ab) ab.addEventListener("click", runAdapt);
  const cb = $("cmpBtn");
  if (cb) cb.addEventListener("click", runCompare);
  renderCmpChips();
  const ap = $("applyAdapt");
  if (ap) ap.addEventListener("click", applyAdapt);
  const gb = $("geoBtn");
  if (gb) gb.addEventListener("click", detectLocation);
}

function detectLocation() {
  if (!navigator.geolocation) { toast("Geolocation not available", true); return; }
  $("geoBtn").textContent = "◎ LOCATING…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const lat = pos.coords.latitude.toFixed(4), lon = pos.coords.longitude.toFixed(4);
    const sel = $("location");
    const opt = document.createElement("option");
    opt.value = "my"; opt.dataset.lat = lat; opt.dataset.lon = lon;
    opt.textContent = `My location (${lat}, ${lon})`;
    sel.appendChild(opt);
    sel.value = "my";
    $("geoBtn").textContent = "◎ DETECT MY LOCATION";
    toast(`Site set to your location (${lat}, ${lon}) — load climate to profile it`);
  }, (err) => {
    $("geoBtn").textContent = "◎ DETECT MY LOCATION";
    toast(`Location denied: ${err.message}`, true);
  }, { timeout: 15000 });
}

/* ============================================================
   10 · MULTI-ZONE VALIDATION (MOD·02E)
============================================================ */
const ZONE_COLORS = {
  composite: "#ff9933", hot_dry: "#ffb25e", warm_humid: "#5fd08a",
  temperate: "#c3b6ff", cold: "#8ecae6",
};

function renderCmpChips(zones) {
  const wrap = $("cmpChips");
  if (!wrap) return;
  const names = zones || ["Prayagraj", "Jaisalmer", "Chennai", "Bengaluru", "Leh"];
  wrap.innerHTML = "";
  names.forEach((n) => {
    const el = document.createElement("span");
    el.className = "zone-chip";
    el.textContent = n;
    wrap.appendChild(el);
  });
}

async function runCompare() {
  const btn = $("cmpBtn");
  btn.disabled = true; $("cmpStatus").textContent = "RUNNING 5 SITE SIMULATIONS…";
  try {
    const j = await api("/api/location/compare", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ design: structPayload() || undefined }),
    });
    const sites = j.sites;
    renderCmpChips(sites.map((x) => x.site));
    sites.forEach((s, i) => {
      const el = $("cmpChips").children[i];
      if (el) {
        el.textContent = `${s.site} · ${s.zone_name}`;
        el.style.borderColor = ZONE_COLORS[s.zone] || "var(--line)";
        el.style.color = ZONE_COLORS[s.zone] || "var(--txt)";
        if (s.error) el.textContent += " · ERR";
      }
    });
    const tb = $("cmpBody");
    tb.innerHTML = "";
    for (const s of sites) {
      if (s.error) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(s.site)}</td><td colspan="6" class="err">${escapeHtml(s.error)}</td>`;
        tb.appendChild(tr);
        continue;
      }
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(s.site)}</td>
        <td><span class="chip chip-zone">${escapeHtml(s.zone_name)}</span></td>
        <td>${fmt(s.t_hottest_month_c, 1)}</td>
        <td>${fmt(s.diurnal_range_c, 1)}</td>
        <td class="rec"><b>${fmt(s.mean_indoor_c, 1)} °C</b></td>
        <td>${fmt(s.max_indoor_c, 1)} °C</td>
        <td>${fmt(s.comfort_fraction * 100, 0)}%</td>`;
      tb.appendChild(tr);
    }
    $("cmpWrap").hidden = false;
    if (j.best) {
      $("cmpStatus").textContent =
        `BEST SITE FOR THIS DESIGN · ${j.best.site} (${j.best.zone_name}) · ${fmt(j.best.mean_indoor_c, 1)} °C mean indoor`;
    } else {
      $("cmpStatus").textContent = "comparison incomplete — see table";
    }
    const valid = sites.filter((x) => !x.error);
    if (valid.length && typeof Plotly !== "undefined") {
      plot($("cmpChart"), [{
        x: valid.map((x) => x.site), y: valid.map((x) => x.mean_indoor_c),
        type: "bar",
        marker: { color: valid.map((x) => ZONE_COLORS[x.zone] || "#9aa3ad") },
        hovertemplate: "%{y:.1f} °C mean indoor · hot week<extra></extra>",
      }, {
        x: valid.map((x) => x.site), y: valid.map((x) => x.max_indoor_c),
        type: "bar", name: "peak",
        marker: { color: valid.map((x) => "rgba(255,255,255,0.25)") },
        hovertemplate: "%{y:.1f} °C peak<extra></extra>",
      }], { title: "SAME DESIGN · PREDICTED HOT-WEEK INDOOR TEMPERATURE BY CLIMATE ZONE",
            barmode: "group", yaxis: { title: "°C" },
            margin: { l: 44, r: 16, t: 44, b: 30 } });
    }
    toast("Cross-zone validation complete — same design, five real climates");
  } catch (err) {
    $("cmpStatus").textContent = "FAILED";
    toast(`Compare failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}


/* ============================================================
   11 · AI ASSIST — optional accelerator (engine stays truth)
   ============================================================ */
const aiState = { on: true, info: null, last: null, suggest: null, timer: null };

function aiLocation() {
  const sel = $("location");
  if (!sel || !sel.selectedOptions || !sel.selectedOptions[0]) return null;
  const o = sel.selectedOptions[0];
  const lat = parseFloat(o.dataset.lat), lon = parseFloat(o.dataset.lon);
  if (!isFinite(lat) || !isFinite(lon)) return null;
  return { lat, lon, name: (o.textContent || "").split(" ")[0] };
}

function aiScoreLine(est, bars) {
  return `±${fmt(bars && bars.hot_mean_c, 1)} °C mean · ±${fmt(bars && bars.hot_max_c, 1)} °C peak`;
}

async function aiPredict() {
  if (!aiState.on || !aiState.info) return;
  const loc = aiLocation();
  const p = structPayload();
  if (!loc || !p) return;
  const wrap = $("aiMetrics");
  if (!wrap) return;
  try {
    const j = await api("/api/ai/predict", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: loc.lat, lon: loc.lon, name: loc.name, design: p }),
    });
    aiState.last = j;
    const e = j.estimates;
    wrap.innerHTML = "";
    wrap.append(metric(`${fmt(e.hot_mean_c, 1)} °C`, "hot-week mean · AI est."));
    wrap.append(metric(`${fmt(e.hot_max_c, 1)} °C`, "hot-week peak · AI est."));
    wrap.append(metric(`${Math.round(e.hot_comfort_fraction * 100)}%`, "comfort hrs in band · est."));
    wrap.append(metric(`${fmt(e.cold_min_c, 1)} °C`, "cold-week min · AI est."));
    const zb = document.createElement("span");
    zb.className = "zone-badge";
    zb.textContent = `CLIMATE ZONE · ${(j.profile && j.profile.zone_name) || "—"}`;
    wrap.prepend(zb);
    $("aiEstNote").textContent =
      `Estimates refresh as you edit · model ${j.model.n_samples.toLocaleString()} samples · ` +
      `MAE ${aiScoreLine(e, j.error_bars_c)} · source: ${(j.profile && j.profile.weather_source) || "—"}`;
  } catch (err) {
    $("aiEstNote").textContent = `AI estimate unavailable: ${err.message}`;
  }
}

function scheduleAiPredict() {
  if (!aiState.on) return;
  clearTimeout(aiState.timer);
  aiState.timer = setTimeout(aiPredict, 450);
}

async function aiSuggest() {
  const btn = $("aiSuggestBtn");
  if (!btn) return;
  const loc = aiLocation();
  if (!loc) { toast("Pick a location first", true); return; }
  btn.disabled = true;
  $("aiSuggestStatus").textContent = "RANKING + ENGINE-VERIFYING…";
  try {
    const j = await api("/api/ai/suggest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: loc.lat, lon: loc.lon, name: loc.name,
                             objective: $("aiObjective").value }),
    });
    aiState.suggest = j;
    const d = j.design, v = j.verified;
    const body = $("aiSuggestBody");
    body.hidden = false;
    body.innerHTML = `
      <div class="rec-grid">
        <span class="tag">wall ${escapeHtml(d.wall_material)} ${d.wall_thickness_m} m</span>
        <span class="tag">roof ${escapeHtml(d.roof_material)} ${d.roof_thickness_m} m</span>
        <span class="tag">ins ${escapeHtml(d.insulation_material)} ${d.insulation_thickness_m} m</span>
        <span class="tag">win ${d.window_width_m}×${d.window_height_m} m ${escapeHtml(d.window_wall)} · SHGC ${d.window_shgc}</span>
        <span class="tag">orient ${Math.round(d.orientation_deg)}°</span>
      </div>
      <div class="tblwrap">
        <table class="rec-table">
          <thead><tr><th></th><th>Mean indoor °C</th><th>Peak indoor °C</th><th>Comfort %</th></tr></thead>
          <tbody>
            <tr><td><b>AI estimate</b></td>
                <td>${fmt(j.estimates.hot_mean_c, 1)}</td>
                <td>${fmt(j.estimates.hot_max_c, 1)}</td>
                <td>${Math.round(j.estimates.hot_comfort_fraction * 100)}</td></tr>
            <tr><td><b>Engine verified</b> <span class="tag">TRUTH</span></td>
                <td>${fmt(v.mean_indoor_c, 1)}</td>
                <td>${fmt(v.max_indoor_c, 1)}</td>
                <td>${Math.round(v.comfort_fraction * 100)}</td></tr>
          </tbody>
        </table>
      </div>
      <p class="hint">Top ${(j.alternatives || []).length + 1} candidates were re-run on the real RC engine; numbers above are engine truth.</p>`;
    $("aiSuggestStatus").textContent = `BEST ${j.objective.replace(/_/g, " ").toUpperCase()} · peak ${fmt(v.max_indoor_c, 1)} °C`;
    $("aiApplyBtn").hidden = false;
    $("aiVerifyBtn").hidden = false;
    $("aiCompare").hidden = true;
  } catch (err) {
    $("aiSuggestStatus").textContent = `suggestion failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

function aiApply() {
  if (!aiState.suggest) return;
  applyDesignToForm(aiState.suggest.design);
  scheduleStructure();
  toast("AI suggestion applied — rebuild 3D + run simulation to verify");
}

async function aiVerify() {
  const loc = aiLocation();
  const p = structPayload();
  if (!loc || !p) return;
  const cmp = $("aiCompare");
  cmp.hidden = false;
  cmp.innerHTML = `<p class="hint">Running the real engine on the current design…</p>`;
  try {
    const j = await api("/api/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, lat: loc.lat, lon: loc.lon, period: "hot_week" }),
    });
    const m = j.metrics, e = aiState.last ? aiState.last.estimates : null;
    cmp.innerHTML = `
      <div class="mod-hd"><i></i>AI ESTIMATE vs ENGINE · HOT WEEK · CURRENT DESIGN</div>
      <div class="tblwrap">
        <table class="rec-table">
          <thead><tr><th></th><th>Mean indoor °C</th><th>Peak indoor °C</th><th>Comfort %</th></tr></thead>
          <tbody>
            <tr><td><b>AI estimate</b></td>
                <td>${e ? fmt(e.hot_mean_c, 1) : "—"}</td>
                <td>${e ? fmt(e.hot_max_c, 1) : "—"}</td>
                <td>${e ? Math.round(e.hot_comfort_fraction * 100) : "—"}</td></tr>
            <tr><td><b>Engine</b> <span class="tag">TRUTH</span></td>
                <td>${fmt(m.mean_indoor_c, 1)}</td>
                <td>${fmt(m.max_indoor_c, 1)}</td>
                <td>${Math.round(m.comfort_fraction * 100)}</td></tr>
          </tbody>
        </table>
      </div>`;
  } catch (err) {
    cmp.innerHTML = `<p class="hint">Engine verify failed: ${escapeHtml(err.message)}</p>`;
  }
}

function initAi() {
  const tog = $("aiToggle");
  if (!tog) return;
  tog.addEventListener("change", () => {
    aiState.on = tog.checked;
    $("aiToggleLabel").textContent = aiState.on
      ? "AI ASSIST ON — instant estimates as you edit"
      : "AI ASSIST OFF — classic manual workflow";
    if ($("aiBody")) $("aiBody").style.display = aiState.on ? "" : "none";
    if (aiState.on) aiPredict();
  });
  if ($("aiSuggestBtn")) $("aiSuggestBtn").addEventListener("click", aiSuggest);
  if ($("aiApplyBtn")) $("aiApplyBtn").addEventListener("click", aiApply);
  if ($("aiVerifyBtn")) $("aiVerifyBtn").addEventListener("click", aiVerify);
  if ($("location")) $("location").addEventListener("change", scheduleAiPredict);
  ["length", "width", "height", "orientation", "wallMat", "wallThick",
   "roofMat", "roofThick", "insMat", "insThick", "winWall", "winSize"]
    .forEach((id) => { const el = $(id); if (el) el.addEventListener("input", scheduleAiPredict); });
  (async () => {
    try {
      aiState.info = await api("/api/ai/info");
      const m = aiState.info.model;
      $("aiModelTag").textContent =
        `${m.name} · ${Number(m.n_samples).toLocaleString()} samples · ${m.n_sites} cities · ` +
        `MAE hot-mean ${fmt(aiState.info.accuracy.hot_mean_c.mae_c, 2)} °C`;
      $("aiNote").textContent = aiState.info.note;
    } catch (err) {
      $("aiModelTag").textContent = `model unavailable: ${err.message}`;
    }
    aiPredict();
  })();
}
