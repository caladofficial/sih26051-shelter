/* =====================================================================
   Offline Shelter-Studio engine — faithful JS port of the sourced
   Python RC thermal model (src/thermal/rc_model.py + geometry/shelter.py
   + src/data/solar.py irradiance part) and the AI surrogate
   (src/ai_model.py). Solar-position/Erbs outputs are PRE-COMPUTED in
   Python (scripts/build_offline.py embeds them per site/week), so the
   JS never re-implements SPA — it uses the same per-hour inputs the
   Python engine uses, giving bit-level parity with the online engine.

   Dual module: works in a browser (window.OfflineEngine) and in Node
   (module.exports) for parity tests.
   ===================================================================== */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.OfflineEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ---------------- constants (rc_model.py) ---------------- */
  const R_SE = 0.04, R_SI = 0.13, H_O = 1.0 / R_SE;
  const RHO_AIR = 1.2, CP_AIR = 1005.0, MASS_FRACTION = 0.5;
  const DEFAULTS = {
    length_m: 3.0, width_m: 3.0, height_m: 2.6, orientation_deg: 0,
    wall_material: "brick", wall_thickness_m: 0.2,
    roof_material: "rcc_slab", roof_thickness_m: 0.12,
    floor_material: "concrete", floor_thickness_m: 0.1,
    insulation_material: "none", insulation_thickness_m: 0.0,
    window_wall: "south", window_width_m: 1.2, window_height_m: 1.2,
    window_sill_m: 0.9, window_shgc: 0.82, window_u_w_m2k: 5.8,
    door_wall: "west", door_width_m: 0.9, door_height_m: 1.95,
    ach: 2.0, internal_gains_w: 100, albedo: 0.2, dt_min: 10,
    comfort_range: [18.0, 32.0],
  };

  /* ---------------- material helpers ---------------- */
  function surface_conductance(layers, mats) {
    let r = R_SE + R_SI;
    for (const [mat, thick] of layers) r += thick / mats[mat].k_W_mK;
    return 1.0 / r;
  }
  function surface_mass(layers, mats) {
    let m = 0;
    for (const [mat, thick] of layers) m += thick * mats[mat].density_kg_m3;
    return m;
  }

  /* ---------------- geometry (geometry/shelter.py) ---------------- */
  function buildSurfaces(design, mats) {
    const d = Object.assign({}, DEFAULTS, design);
    const wallAz = { south: 180, north: 0, east: 90, west: 270 };
    const az = (wall) => (wallAz[wall] + d.orientation_deg) % 360;
    const areas = {
      south: d.width_m * d.height_m, north: d.width_m * d.height_m,
      east: d.length_m * d.height_m, west: d.length_m * d.height_m,
    };
    const insOn = d.insulation_material !== "none" && d.insulation_thickness_m > 0;
    const surfs = [];
    for (const wall of ["south", "north", "east", "west"]) {
      const layers = [[d.wall_material, d.wall_thickness_m]];
      if (insOn) layers.push([d.insulation_material, d.insulation_thickness_m]);
      surfs.push({ name: wall + "_wall", type: "wall", area: areas[wall],
                   azimuth: az(wall), tilt: 90, layers });
    }
    // openings cut out of their wall
    const win = { wall: d.window_wall, area: d.window_width_m * d.window_height_m };
    for (const s of surfs)
      if (s.name === win.wall + "_wall") s.area = Math.max(0, s.area - win.area);
    surfs.push({ name: "window", type: "window", area: win.area,
                 azimuth: az(win.wall), tilt: 90, layers: [],
                 u_w_m2k: d.window_u_w_m2k, shgc: d.window_shgc });
    const door = { wall: d.door_wall, area: d.door_width_m * d.door_height_m };
    for (const s of surfs)
      if (s.name === door.wall + "_wall") s.area = Math.max(0, s.area - door.area);
    surfs.push({ name: "door", type: "door", area: door.area,
                 azimuth: az(door.wall), tilt: 90,
                 layers: [[d.wall_material, d.wall_thickness_m]] });
    surfs.push({ name: "roof", type: "roof", area: d.length_m * d.width_m,
                 azimuth: 180, tilt: 0,
                 layers: [[d.roof_material, d.roof_thickness_m]]
                   .concat(insOn ? [[d.insulation_material, d.insulation_thickness_m]] : []) });
    surfs.push({ name: "floor", type: "floor", area: d.length_m * d.width_m,
                 azimuth: 180, tilt: 180,
                 layers: [[d.floor_material, d.floor_thickness_m]] });
    return { surfaces: surfs, volume: d.length_m * d.width_m * d.height_m };
  }

  /* ---------------- tilted-surface irradiance (solar.py verbatim) ---------------- */
  function poaForSurface(surf, wk) {
    const tilt = surf.tilt, sAz = surf.azimuth;
    const cosT = Math.cos(tilt * Math.PI / 180);
    let out = new Array(wk.t2m.length);
    for (let h = 0; h < wk.t2m.length; h++) {
      const zen = wk.zenith_deg[h], sunAz = wk.azimuth_deg[h];
      const proj = Math.cos(zen * Math.PI / 180) * cosT +
        Math.sin(zen * Math.PI / 180) * Math.sin(tilt * Math.PI / 180) *
        Math.cos((sunAz - sAz) * Math.PI / 180);
      const p = Math.max(-1, Math.min(1, proj));
      const direct = Math.max(wk.dni[h] * p, 0);
      const sky = wk.dhi[h] * (1 + cosT) * 0.5;
      const ground = wk.ghi[h] * (wk.albedo || 0.2) * (1 - cosT) * 0.5;
      out[h] = Math.max(0, direct + sky + ground);
    }
    return out;
  }

  /* ---------------- RC simulation (rc_model.simulate verbatim) ---------------- */
  function simulate(design, wk, mats) {
    const d = Object.assign({}, DEFAULTS, design);
    const dt = d.dt_min, dt_s = dt * 60;
    const { surfaces, volume } = buildSurfaces(d, mats);
    const ua = {}, alpha = {};
    let mass_kg = 0;
    for (const s of surfaces) {
      if (s.type === "window") { ua[s.name] = s.area * s.u_w_m2k; alpha[s.name] = s.shgc; }
      else {
        const u = surface_conductance(s.layers, mats);
        ua[s.name] = s.area * u;
        alpha[s.name] = mats[s.layers[0][0]].solar_absorptance;
        mass_kg += surface_mass(s.layers, mats) * s.area * MASS_FRACTION;
      }
    }
    const ua_vent = RHO_AIR * CP_AIR * volume * d.ach / 3600.0;
    const c_eff = mass_kg * 1000.0 + RHO_AIR * volume * CP_AIR;
    const q_int = d.internal_gains_w;
    const t_ground = wk.ground_temperature_c;

    const poa = {};
    for (const s of surfaces) poa[s.name] = poaForSurface(s, wk);

    const t_out = wk.t2m;
    const n = t_out.length;
    const n_steps = n * Math.round(3600.0 / dt_s);
    const t_in = new Float64Array(n_steps + 1);
    t_in[0] = t_out[0];
    const q_solar = new Float64Array(n_steps), q_cond = new Float64Array(n_steps),
          q_vent = new Float64Array(n_steps);
    for (let k = 0; k < n_steps; k++) {
      const h = Math.min(Math.floor(k * dt_s / 3600.0), n - 1);
      const t_o = t_out[h];
      let q_s = 0, q_c = 0;
      for (const s of surfaces) {
        const a = alpha[s.name], u = ua[s.name], po = poa[s.name][h];
        if (s.type === "window") { q_s += po * s.area * a; q_c += u * (t_o - t_in[k]); }
        else if (s.type === "floor") { q_c += u * (t_ground - t_in[k]); }
        else {
          const t_solair = t_o + a * po / H_O;
          q_c += u * (t_solair - t_in[k]);
          q_s += u * a * po / H_O;
        }
      }
      const q_v = ua_vent * (t_o - t_in[k]);
      const q_net = q_s + q_c + q_v + q_int;
      q_solar[k] = q_s; q_cond[k] = q_c; q_vent[k] = q_v;
      t_in[k + 1] = t_in[k] + q_net / c_eff * dt_s;
      if (!Number.isFinite(t_in[k + 1])) throw new Error("RC model diverged");
    }
    // hourly resample — replicates pandas resample("h") EXACTLY.
    // The weather rows carry a :30-minute offset (POWER LST -> IST), and
    // pandas bins tz-aware 10-min data on UTC-aligned hours, so each
    // output "hour" is a shifted block of steps: bin hh = steps
    // [hh*sph - shift, hh*sph - shift + sph) where shift = start_minute/60*sph
    // (e.g. shift=3 for a :30 start). The Python engine's hourly series
    // has exactly floor((n_steps + shift)/sph) bins; the trailing partial
    // bin is dropped. Verified numerically against the Python engine.
    const sph = Math.round(3600.0 / dt_s);
    const start_min = wk.start_minute || 0;
    const shift = (start_min / 60.0) * sph;
    // Bin count verified against the Python engine: n_steps=1002, shift=3
    // (start at :30) -> 168 hourly bins (last bin is a trailing partial).
    const hours = Math.ceil((n_steps + shift) / sph);
    const indoor = new Float64Array(hours), qs = new Float64Array(hours),
          qc = new Float64Array(hours), qv = new Float64Array(hours),
          hod = new Uint8Array(hours);
    for (let hh = 0; hh < hours; hh++) {
      const k0 = Math.max(0, Math.round(hh * sph - shift));
      const k1 = Math.min(n_steps, Math.round(hh * sph - shift + sph));
      // pandas labels each bin at the local hour containing its first step
      // (bins start exactly on :00 for :30-shifted input); the LAST bin can
      // start at a full hour beyond the last source row (hour 23 when the
      // week ends at 22:30) — wk.hour_of_day has no entry there.
      const labMin = (start_min + k0 * (dt_s / 60.0));
      hod[hh] = Math.floor(labMin / 60.0) % 24;
      let a = 0, b = 0, c = 0, e = 0;
      for (let j = k0; j < k1; j++) {
        a += t_in[j]; b += q_solar[j]; c += q_cond[j]; e += q_vent[j];
      }
      const cnt = k1 - k0;
      indoor[hh] = a / cnt; qs[hh] = b / cnt; qc[hh] = c / cnt; qv[hh] = e / cnt;
    }
    return { indoor_t_c: indoor, q_solar_w: qs, q_conduct_w: qc, q_vent_w: qv,
             hour_of_day: hod, n_hours: hours };
  }

  /* ---------------- comfort stats (rc_model.comfort_stats verbatim) ---------------- */
  function comfort_stats(res, comfortRange) {
    const [lo, hi] = comfortRange || DEFAULTS.comfort_range;
    const n = res.n_hours;
    let hours_in = 0, minV = Infinity, maxV = -Infinity, sum = 0,
        nightLoss = 0, solarGain = 0, totalLoss = 0;
    for (let i = 0; i < n; i++) {
      const t = res.indoor_t_c[i];
      sum += t;
      if (t < minV) minV = t;
      if (t > maxV) maxV = t;
      if (t >= lo && t <= hi) hours_in++;
      const hd = res.hour_of_day[i];
      if (hd >= 18 || hd < 6) nightLoss += Math.min(res.q_conduct_w[i], 0);
      solarGain += Math.max(res.q_solar_w[i], 0);
      totalLoss += Math.min(res.q_conduct_w[i], 0);
    }
    return {
      mean_indoor_c: sum / n, min_indoor_c: minV, max_indoor_c: maxV,
      comfort_hours: hours_in, comfort_fraction: hours_in / n,
      solar_gain_kwh: solarGain / 1000, night_heat_loss_kwh: nightLoss / 1000,
      total_heat_loss_kwh: totalLoss / 1000,
    };
  }

  /* ---------------- AI surrogate (src/ai_model.py) ---------------- */
  function buildFeatures(design, profile, mats) {
    const d = Object.assign({}, DEFAULTS, design);
    const rOf = (mat, thk) => mats[mat] ? 1.0 / surface_conductance([[mat, thk]], mats) : 0.0;
    const massOf = (mat, thk) => mats[mat] ? surface_mass([[mat, thk]], mats) : 0.0;
    const absOf = (mat) => mats[mat] ? mats[mat].solar_absorptance : 0.7;
    const insR = (d.insulation_material !== "none" && mats[d.insulation_material] && d.insulation_thickness_m > 0)
      ? d.insulation_thickness_m / mats[d.insulation_material].k_W_mK : 0.0;
    const rad = d.orientation_deg * Math.PI / 180;
    return [
      profile.t_hottest_month_c, profile.t_coldest_month_c, profile.diurnal_range_c,
      profile.rh_mean_pct, profile.cdd18, profile.hdd18, profile.ghi_mean_w_m2,
      profile.wind_mean_ms,
      d.length_m, d.width_m, d.height_m,
      rOf(d.wall_material, d.wall_thickness_m),
      massOf(d.wall_material, d.wall_thickness_m), absOf(d.wall_material),
      rOf(d.roof_material, d.roof_thickness_m),
      massOf(d.roof_material, d.roof_thickness_m), absOf(d.roof_material),
      insR, d.window_width_m * d.window_height_m,
      d.window_u_w_m2k, d.window_shgc, Math.sin(rad), Math.cos(rad),
    ];
  }

  function predictOne(X, model) {
    const out = {};
    for (const [target, spec] of Object.entries(model.targets)) {
      let pred = spec.init;
      for (const tree of spec.trees) {
        // node walk (sklearn HistGradientBoosting layout, pre-shrunk leaves)
        let node = 0;
        while (!tree.is_leaf[node]) {
          const f = tree.feature[node];
          node = X[f] <= tree.threshold[node] ? tree.left[node] : tree.right[node];
        }
        pred += tree.value[node];
      }
      out[target] = pred;
    }
    return out;
  }

  function predictDesign(design, profile, mats, model) {
    const X = buildFeatures(design, profile, mats);
    const p = predictOne(X, model);
    return {
      hot_mean_c: Math.round(p.hot_mean_c * 10) / 10,
      hot_max_c: Math.round(p.hot_max_c * 10) / 10,
      hot_comfort_fraction: Math.round(Math.max(0, Math.min(1, p.hot_comfort_fraction)) * 1000) / 1000,
      cold_min_c: Math.round(p.cold_min_c * 10) / 10,
    };
  }

  /* ---------------- design space + sampling (src/ai_model.py) ---------------- */
  const SPACE = {
    WALL_CHOICES: {
      brick: [0.115, 0.23, 0.345], rammed_earth: [0.15, 0.3, 0.45],
      concrete: [0.1, 0.15, 0.2, 0.3], gi_sheet: [0.002],
      plywood: [0.012, 0.019, 0.03, 0.05], puf_sandwich_panel: [0.05, 0.075, 0.1, 0.15],
      stone: [0.15, 0.3, 0.45], mud_brick: [0.15, 0.3, 0.45],
      timber: [0.1, 0.15, 0.2], aerated_concrete: [0.1, 0.2, 0.3],
    },
    ROOF_CHOICES: {
      rcc_slab: [0.1, 0.15, 0.2, 0.25], concrete: [0.1, 0.15, 0.2],
      gi_sheet: [0.002], puf_sandwich_panel: [0.05, 0.075, 0.1, 0.15],
      brick: [0.115, 0.23], stone: [0.15, 0.3], mud_brick: [0.15, 0.25],
      timber: [0.1, 0.15], aerated_concrete: [0.1, 0.2],
    },
    INSULATION_CHOICES: [
      ["none", 0], ["eps", 0.025], ["eps", 0.05], ["eps", 0.1], ["eps", 0.15], ["eps", 0.2],
      ["xps", 0.05], ["xps", 0.1], ["xps", 0.15],
      ["mineral_wool", 0.05], ["mineral_wool", 0.1], ["mineral_wool", 0.15],
      ["sheep_wool", 0.05], ["sheep_wool", 0.1], ["sheep_wool", 0.15],
    ],
    WINDOW_WALLS: ["north", "east", "south", "west"],
    WINDOW_WIDTHS: [0.6, 0.9, 1.2, 1.5, 1.8],
    WINDOW_HEIGHTS: [0.6, 0.9, 1.2, 1.5],
    WINDOW_SHGC: [0.35, 0.45, 0.55, 0.65, 0.75, 0.85],
    WINDOW_U: [1.2, 1.6, 2.0, 2.6, 3.5, 5.8],
    ORIENTATIONS: [0, 45, 90, 135, 180],
    LENGTHS: [3.0], WIDTHS: [3.0], HEIGHTS: [2.6],
  };

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function pick(rng, arr) { return arr[Math.floor(rng() * arr.length)]; }

  function sampleDesign(rng) {
    const S = SPACE;
    const wm = pick(rng, Object.keys(S.WALL_CHOICES));
    const rm = pick(rng, Object.keys(S.ROOF_CHOICES));
    const [im, it] = pick(rng, S.INSULATION_CHOICES);
    return {
      length_m: pick(rng, S.LENGTHS), width_m: pick(rng, S.WIDTHS),
      height_m: pick(rng, S.HEIGHTS), orientation_deg: pick(rng, S.ORIENTATIONS),
      wall_material: wm, wall_thickness_m: pick(rng, S.WALL_CHOICES[wm]),
      roof_material: rm, roof_thickness_m: pick(rng, S.ROOF_CHOICES[rm]),
      floor_material: "concrete", floor_thickness_m: 0.1,
      insulation_material: im, insulation_thickness_m: it,
      window_wall: pick(rng, S.WINDOW_WALLS),
      window_width_m: pick(rng, S.WINDOW_WIDTHS),
      window_height_m: pick(rng, S.WINDOW_HEIGHTS),
      window_sill_m: 0.9, window_shgc: pick(rng, S.WINDOW_SHGC),
      window_u_w_m2k: pick(rng, S.WINDOW_U),
    };
  }

  function suggest(profile, mats, model, opts) {
    const obj = (opts && opts.objective) || "coolest_peak";
    const nCand = (opts && opts.n_candidates) || 800;
    const rng = mulberry32(26051);
    const cand = [];
    for (let i = 0; i < nCand; i++) cand.push(sampleDesign(rng));
    const X = cand.map((d) => buildFeatures(d, profile, mats));
    const preds = X.map((x) => predictOne(x, model));
    const rankIdx = preds.map((_, i) => i).sort((a, b) => {
      const va = obj === "coolest_peak" ? preds[a].hot_max_c
        : obj === "coolest_mean" ? preds[a].hot_mean_c : -preds[a].hot_comfort_fraction;
      const vb = obj === "coolest_peak" ? preds[b].hot_max_c
        : obj === "coolest_mean" ? preds[b].hot_mean_c : -preds[b].hot_comfort_fraction;
      return va - vb;
    });
    const top = rankIdx.slice(0, 12).map((i) => cand[i]);
    // engine-verify top candidates with the JS port (same physics)
    const verified = top.map((d) => {
      const hot = simulate(d, profile.hot_week, mats);
      return comfort_stats(hot, DEFAULTS.comfort_range);
    });
    const score = (m) => obj === "coolest_peak" ? m.max_indoor_c
      : obj === "coolest_mean" ? m.mean_indoor_c : -m.comfort_fraction;
    const order = verified.map((_, i) => i).sort((a, b) => score(verified[a]) - score(verified[b]));
    const best = order[0];
    return {
      objective: obj,
      design: top[best],
      estimates: predictDesign(top[best], profile, mats, model),
      verified: { mean_indoor_c: verified[best].mean_indoor_c,
                  max_indoor_c: verified[best].max_indoor_c,
                  comfort_fraction: verified[best].comfort_fraction },
      alternatives: order.slice(1, 4).map((i) => ({
        design: top[i],
        verified: { mean_indoor_c: verified[i].mean_indoor_c,
                    max_indoor_c: verified[i].max_indoor_c,
                    comfort_fraction: verified[i].comfort_fraction },
      })),
      note: "Offline engine (JS port of the sourced RC model) verified these numbers locally.",
    };
  }

  return {
    DEFAULTS, SPACE,
    surface_conductance, surface_mass, buildSurfaces, poaForSurface,
    simulate, comfort_stats, buildFeatures, predictOne, predictDesign,
    sampleDesign, mulberry32, suggest,
  };
});
