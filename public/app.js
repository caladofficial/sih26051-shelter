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
});
