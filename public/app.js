/* SIH26051 · Shelter-Studio — "Command Deck" frontend logic (vanilla JS, no build step) */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------- api ---------- */
async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));

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
    v.classList.add("ready");
    renderStruct3D();
    renderStructSheet(data);
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
        `<td>${L.u_w_m2k != null ? L.u_w_m2k.toFixed(3) : "—"}</td>`;
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

  await buildStructure();
  loadCadRecent();
});
