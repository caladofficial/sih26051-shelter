/* SIH26051 Shelter Studio — frontend logic (vanilla JS, no build step) */
"use strict";

const $ = (id) => document.getElementById(id);

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

/* ---------- layout helpers ---------- */
function plot(el, data, layout, config = {}) {
  Plotly.react(el, data, Object.assign({
    template: { layout: { paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      font: { color: "#cbd5e1" }, xaxis: { gridcolor: "#334155" },
      yaxis: { gridcolor: "#334155" } } },
    margin: { l: 50, r: 20, t: 30, b: 40 }, height: 300,
  }, layout), { responsive: true, displayModeBar: false, ...config });
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
  $("loadClimate").disabled = true;
  $("loadClimate").textContent = "Loading…";
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
      name: "t2m", line: { color: "#f87171" },
    }], { title: "Monthly mean temperature (°C)" });
    plot($("chartSolar"), [{
      x: data.monthly.ts, y: data.monthly.ghi, type: "scatter", mode: "lines+markers",
      name: "GHI", line: { color: "#fbbf24" },
    }], { title: "Monthly mean solar irradiance (W/m²)" });
    $("climateSection").hidden = false;
  } catch (err) {
    alert(`Climate load failed: ${err.message}`);
  } finally {
    $("loadClimate").disabled = false;
    $("loadClimate").textContent = "Load climate";
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
  $("simulate").disabled = true;
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
      { x: s.ts, y: s.outdoor_t_c, type: "scatter", name: "Outdoor",
        line: { color: "#94a3b8", width: 1.5 } },
      { x: s.ts, y: s.indoor_t_c, type: "scatter", name: "Indoor",
        line: { color: "#38bdf8", width: 2.5 } },
    ], { title: "Indoor vs outdoor temperature (°C)",
         xaxis: { tickangle: -30 } });
    if (s.q_solar_w) {
      plot($("chartHeat"), [
        { x: s.ts, y: s.q_solar_w, type: "scatter", name: "Solar", line: { color: "#fbbf24" } },
        { x: s.ts, y: s.q_conduct_w, type: "scatter", name: "Conduction", line: { color: "#f87171" } },
        { x: s.ts, y: s.q_vent_w, type: "scatter", name: "Ventilation", line: { color: "#60a5fa" } },
      ], { title: "Heat flows (W, + into shelter)" });
    }
    $("simSection").hidden = false;
  } catch (err) {
    alert(`Simulation failed: ${err.message}`);
  } finally {
    $("simulate").disabled = false;
  }
}

/* ---------- 5 · optimize ---------- */
async function runOptimize() {
  const sel = $("location").selectedOptions[0];
  const n = parseInt($("trials").value, 10);
  $("optimize").disabled = true;
  $("optStatus").textContent = `running ${n} trials… (≈${Math.ceil(n / 8)} s)`;
  try {
    const data = await api("/api/optimize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: parseFloat(sel.dataset.lat),
                             lon: parseFloat(sel.dataset.lon),
                             year: parseInt($("year").value, 10), n_trials: n }),
    });
    $("optStatus").textContent = `best TPI ${data.best.tpi.toFixed(3)}`;
    const b = data.best.design;
    $("bestCard").innerHTML = `<h4>🏆 Best design — TPI ${data.best.tpi.toFixed(3)}</h4>` +
      `<p>Orientation: <b>${b.orientation_deg}°</b></p>` +
      `<p>Walls: <b>${b.wall_material}</b> (${fmt(b.wall_thickness_m, 2)} m)</p>` +
      `<p>Roof: <b>${b.roof_material}</b> (${fmt(b.roof_thickness_m, 2)} m)</p>` +
      `<p>Insulation: <b>${b.insulation_material}</b> ${b.insulation_material !== "none" ? fmt(b.insulation_thickness_m * 1000, 0) + " mm" : ""}</p>` +
      `<p>Window: <b>${b.window.wall}</b> ${fmt(b.window.width_m, 1)}×${fmt(b.window.height_m, 1)} m, SHGC ${fmt(b.window.shgc, 2)}</p>`;
    plot($("chartOpt"), [{
      y: data.history, type: "scatter", mode: "lines+markers",
      name: "1 − TPI (lower better)", line: { color: "#2dd4bf" },
    }], { title: "Optimization history" });
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
    $("optStatus").textContent = `failed: ${err.message}`;
  } finally {
    $("optimize").disabled = false;
  }
}

/* ---------- init ---------- */
document.addEventListener("DOMContentLoaded", async () => {
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
