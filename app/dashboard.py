"""SIH26051 Shelter Studio — Streamlit dashboard (Phase 7).

Run:  streamlit run app/dashboard.py
Pages: 1 Location · 2 Climate · 3 Shelter Design · 4 Simulation ·
       5 Optimization · 6 Recommendation

Everything is driven by the same config + data + models as the CLI scripts,
so the dashboard never invents numbers — every chart comes from a simulation.
"""
import copy
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.climate import load_config, load_clean, design_weeks  # noqa: E402
from src.paths import PROCESSED_DIR, RESULTS_DIR  # noqa: E402
from src.thermal.rc_model import (comfort_stats, load_materials,  # noqa: E402
                                  simulate)

st.set_page_config(page_title="SIH26051 Shelter Studio", layout="wide",
                   page_icon="🏕️", initial_sidebar_state="expanded")

T_HOT = "#d62728"
T_COLD = "#1f77b4"
T_INS = "#2ca02c"


# --------------------------------------------------------------------------
# cached data loaders
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_cfg():
    return load_config()


@st.cache_data(show_spinner=False)
def get_weather():
    return load_clean()


@st.cache_data(show_spinner=False)
def get_materials():
    return load_materials()


def active_cfg() -> dict:
    """Config with the user's on-screen design merged in (session state)."""
    cfg = copy.deepcopy(get_cfg())
    design = st.session_state.get("design_cfg")
    if design is not None:
        for k, v in design["shelter"].items():
            if isinstance(v, dict) and isinstance(cfg["shelter"].get(k), dict):
                cfg["shelter"][k].update(v)
            else:
                cfg["shelter"][k] = v
    return cfg


def ensure_data() -> bool:
    """If climate data is missing, offer a one-click download."""
    if (PROCESSED_DIR / "climate_clean.csv").exists():
        return True
    st.warning("Climate data not found for this machine yet.")
    if st.button("⬇️ Download climate data now (NASA POWER + Open-Meteo)"):
        with st.spinner("Downloading ~2 years of hourly data..."):
            subprocess.run([sys.executable, "scripts/fetch_climate.py"],
                           cwd=ROOT, check=False)
        st.rerun()
    return False


def line_fig(df: pd.DataFrame, cols: list[str], title: str, ylabel: str,
             labels: dict | None = None, colors: list[str] | None = None):
    fig = go.Figure()
    default_colors = [T_HOT, T_COLD, T_INS, "#9467bd", "#8c564b"]
    for i, col in enumerate(cols):
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], name=(labels or {}).get(col, col),
            line=dict(color=(colors or default_colors)[i % 5])))
    fig.update_layout(title=title, yaxis_title=ylabel, template="plotly_white",
                      height=380, hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def design_weeks_plot(weather):
    """Return (hot_week, cold_week) DataFrames for the current config year."""
    cfg = get_cfg()
    weeks = design_weeks(weather, int(cfg["climate"]["data_year"]))
    return weeks["hot_week"], weeks["cold_week"]


def run_sim(cfg, weather, materials):
    """Simulate the current session design on hot + cold design weeks."""
    hot, cold = design_weeks_plot(weather)
    return simulate(cfg, hot, materials), simulate(cfg, cold, materials)


# --------------------------------------------------------------------------
# Page 1 — Location
# --------------------------------------------------------------------------
def page_location(cfg):
    st.header(f"📍 Location — {cfg['location']['name']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Latitude", f"{cfg['location']['latitude']}°")
    c2.metric("Longitude", f"{cfg['location']['longitude']}°")
    c3.metric("Elevation", f"{cfg['location']['elevation_m']} m")
    st.caption(f"Timezone {cfg['location']['timezone']} · climate year "
               f"{cfg['climate']['data_year']} (NASA POWER + Open-Meteo cross-checked)")
    site = pd.DataFrame({"lat": [cfg["location"]["latitude"]],
                         "lon": [cfg["location"]["longitude"]]})
    st.map(site, zoom=7)
    st.caption("Change the site in config/config.yaml — every page below "
               "re-runs for the new location.")


# --------------------------------------------------------------------------
# Page 2 — Climate
# --------------------------------------------------------------------------
def page_climate(weather):
    st.header("🌤️ Climate — measured inputs")
    st.caption("Data: NASA POWER hourly (primary), cross-checked against "
               "Open-Meteo; see data/processed/validation_report.json.")
    monthly = weather.resample("ME").mean()

    tab1, tab2 = st.tabs(["Temperature", "Solar / Wind / Humidity"])
    with tab1:
        st.plotly_chart(line_fig(monthly, ["t2m"], "Monthly mean air temperature",
                                 "°C"), use_container_width=True)
    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(line_fig(monthly, ["ghi"], "Monthly mean solar "
                            "irradiance (GHI)", "W/m²"), use_container_width=True)
            st.plotly_chart(line_fig(monthly, ["rh2m"], "Monthly mean relative "
                            "humidity", "%"), use_container_width=True)
        with c2:
            st.plotly_chart(line_fig(monthly, ["ws10m"], "Monthly mean wind "
                            "speed", "m/s"), use_container_width=True)
            st.plotly_chart(line_fig(monthly, ["t2mdew"], "Monthly mean "
                            "dew point", "°C"), use_container_width=True)
    st.caption(f"{len(weather):,} hourly records · temperature range "
               f"{weather['t2m'].min():.1f}–{weather['t2m'].max():.1f} °C · "
               f"peak GHI {weather['ghi'].max():.0f} W/m²")


# --------------------------------------------------------------------------
# Page 3 — Shelter design (writes st.session_state["cfg"])
# --------------------------------------------------------------------------
def page_design():
    st.header("🏗️ Shelter design")
    st.caption("Set the design on the left, then open the Simulation page. "
               "The same values feed the RC model here and the EnergyPlus model.")
    cfg = copy.deepcopy(get_cfg())
    sh = cfg["shelter"]

    with st.sidebar:
        st.subheader("Shelter geometry")
        sh["length_m"] = st.slider("Length (m)", 2.0, 8.0, sh["length_m"], 0.5)
        sh["width_m"] = st.slider("Width (m)", 2.0, 8.0, sh["width_m"], 0.5)
        sh["height_m"] = st.slider("Height (m)", 2.0, 4.0, sh["height_m"], 0.1)
        sh["orientation_deg"] = st.slider("Orientation (0 = south-facing)", 0, 345,
                                          int(sh["orientation_deg"]), 15)
        st.subheader("Envelope")
        mats = get_materials()
        wall_options = list(mats.index)
        sh["wall_material"] = st.selectbox("Wall material", wall_options,
                                           index=wall_options.index(sh["wall_material"]))
        sh["wall_thickness_m"] = st.slider("Wall thickness (m)", 0.05, 0.40,
                                           sh["wall_thickness_m"], 0.01)
        sh["roof_material"] = st.selectbox("Roof material", wall_options,
                                           index=wall_options.index(sh["roof_material"]))
        sh["roof_thickness_m"] = st.slider("Roof thickness (m)", 0.05, 0.30,
                                           sh["roof_thickness_m"], 0.01)
        st.subheader("Insulation")
        ins_options = ["none"] + [m for m in mats.index
                                  if mats.loc[m, "category"] == "insulation"]
        sh["insulation"]["material"] = st.selectbox(
            "Insulation material", ins_options,
            index=ins_options.index(sh["insulation"]["material"])
            if sh["insulation"]["material"] in ins_options else 0)
        sh["insulation"]["thickness_m"] = st.slider(
            "Insulation thickness (mm)", 0, 150,
            int(sh["insulation"]["thickness_m"] * 1000), 5) / 1000.0
        st.subheader("Openings")
        sh["window"]["wall"] = st.selectbox("Window wall", ["south", "east",
                                                            "west", "north"])
        sh["window"]["width_m"] = st.slider("Window width (m)", 0.5, 2.5,
                                            sh["window"]["width_m"], 0.1)
        sh["window"]["height_m"] = st.slider("Window height (m)", 0.5, 1.8,
                                             sh["window"]["height_m"], 0.1)
        st.caption(f"Window area ≈ {sh['window']['width_m'] * sh['window']['height_m']:.2f} m²")

    st.success("Design saved to this session — go to **Simulation**.")
    st.session_state["design_cfg"] = cfg
    with st.expander("Design summary"):
        st.json(sh)


# --------------------------------------------------------------------------
# Page 4 — Simulation
# --------------------------------------------------------------------------
def page_simulation():
    st.header("🌡️ Simulation — indoor vs outdoor")
    cfg = active_cfg()
    weather = get_weather()
    materials = get_materials()
    if not ensure_data():
        return

    with st.spinner("Running the fast thermal model on both design weeks "
                    "(10-min steps)..."):
        hot_res, cold_res = run_sim(cfg, weather, materials)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"Hottest week "
                     f"({hot_res.index[0]:%d %b} – {hot_res.index[-1]:%d %b})")
        st.plotly_chart(line_fig(hot_res, ["outdoor_t_c", "indoor_t_c"],
                                 "Indoor vs outdoor temperature",
                                 "°C",
                                 labels={"indoor_t_c": "Indoor (model)",
                                         "outdoor_t_c": "Outdoor"}),
                        use_container_width=True)
    with c2:
        st.subheader(f"Coldest week "
                     f"({cold_res.index[0]:%d %b} – {cold_res.index[-1]:%d %b})")
        st.plotly_chart(line_fig(cold_res, ["outdoor_t_c", "indoor_t_c"],
                                 "Indoor vs outdoor temperature", "°C",
                                 labels={"indoor_t_c": "Indoor (model)",
                                         "outdoor_t_c": "Outdoor"}),
                        use_container_width=True)

    st.subheader("Heat balance — hottest week")
    cols = ["q_solar_w", "q_conduct_w", "q_vent_w", "q_internal_w"]
    st.plotly_chart(line_fig(hot_res, cols, "Heat flows (W, + = into shelter)",
                             "W",
                             labels={"q_solar_w": "Solar gain",
                                     "q_conduct_w": "Conduction",
                                     "q_vent_w": "Ventilation",
                                     "q_internal_w": "Internal gains"}),
                    use_container_width=True)

    s_hot = comfort_stats(hot_res, cfg["climate"]["comfort_range_c"])
    s_cold = comfort_stats(cold_res, cfg["climate"]["comfort_range_c"])
    st.subheader("Comfort summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hot-week comfort", f"{s_hot['comfort_fraction'] * 100:.0f}%")
    c2.metric("Hot-week peak indoor", f"{s_hot['max_indoor_c']:.1f} °C")
    c3.metric("Cold-week comfort", f"{s_cold['comfort_fraction'] * 100:.0f}%")
    c4.metric("Cold-week night heat loss", f"{s_cold['night_heat_loss_kwh']:.0f} kWh")
    st.caption("Comfort band 18–32 °C (config). All numbers from the RC model "
               "run on real 2024 hourly weather.")


# --------------------------------------------------------------------------
# Page 5 — Optimization
# --------------------------------------------------------------------------
def page_optimization():
    st.header("🎯 Optimization — Optuna search")
    cfg = active_cfg()
    weather = get_weather()
    materials = get_materials()

    n_trials = st.slider("Number of trials", 20, 100, 40, 10)
    if st.button("🚀 Run optimization", type="primary"):
        with st.spinner(f"Searching {n_trials} designs on both design weeks..."):
            from src.optimization.optuna_optimizer import run_study, save_study
            study = run_study(cfg, weather, materials, n_trials=n_trials)
            outdir = RESULTS_DIR / "optimization"
            save_study(study, outdir)
            st.session_state["study"] = study
        st.success(f"Done — best TPI {1.0 - study.best_value:.3f} "
                   f"(trial #{study.best_trial.number})")

    study = st.session_state.get("study")
    trials_csv = RESULTS_DIR / "optimization" / "all_trials.csv"
    if study is not None or trials_csv.exists():
        st.subheader("Top 10 designs")
        top = pd.read_csv(trials_csv).head(10)
        top_display = top[["trial", "tpi", "orientation_deg", "wall_material",
                           "roof_material", "insulation_material",
                           "insulation_thickness_m", "window_wall"]].round(3)
        st.dataframe(top_display, use_container_width=True, hide_index=True)
        if study is not None and study.trials:
            try:
                fig = go.Figure()
                hist = [1.0 - t.value for t in study.trials]
                fig.add_trace(go.Scatter(y=hist, mode="lines+markers",
                                         name="1 − TPI (lower = better)"))
                fig.update_layout(title="Optimization history",
                                  yaxis_title="objective", template="plotly_white",
                                  height=320)
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass
    else:
        st.info("No study yet — click Run optimization above, or run "
                "`python scripts/run_optimization.py --trials 40` in the terminal.")


# --------------------------------------------------------------------------
# Page 6 — Recommendation
# --------------------------------------------------------------------------
def page_recommendation():
    st.header("🏆 Recommendation — best passive shelter")
    cfg = active_cfg()
    weather = get_weather()
    materials = get_materials()

    best_json = RESULTS_DIR / "optimization" / "best_design.json"
    if not best_json.exists():
        st.info("No optimization results yet — running a quick 30-trial study…")
        from src.optimization.optuna_optimizer import run_study, save_study
        with st.spinner("Optimizing..."):
            study = run_study(cfg, weather, materials, n_trials=30)
            save_study(study, RESULTS_DIR / "optimization")
        st.rerun()

    import json
    best = json.loads(best_json.read_text())
    design = best["design"]
    st.subheader("Optimal design")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Orientation", f"{design['orientation_deg']}°")
    c2.metric("Walls", f"{design['wall_material']} "
                       f"({design['wall_thickness_m']:.2f} m)")
    c3.metric("Roof", f"{design['roof_material']} "
                      f"({design['roof_thickness_m']:.2f} m)")
    ins = design.get("insulation_material", "none")
    c4.metric("Insulation", f"{ins}"
              + (f" {design.get('insulation_thickness_m', 0)*1000:.0f} mm"
                 if ins != "none" else ""))

    # run the best design through the RC model and show expected behaviour
    cfg_best = copy.deepcopy(cfg)
    for k, v in design.items():
        if isinstance(v, dict):
            cfg_best["shelter"][k].update(v)
        else:
            cfg_best["shelter"][k] = v
    hot, cold = design_weeks_plot(weather)
    with st.spinner("Simulating the recommended design..."):
        hot_res = simulate(cfg_best, hot, materials)
        cold_res = simulate(cfg_best, cold, materials)

    st.subheader("Expected thermal behaviour")
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(line_fig(hot_res, ["outdoor_t_c", "indoor_t_c"],
                                 f"Hottest week — indoor vs outdoor "
                                 f"(TPI {best['tpi']:.3f})", "°C",
                                 labels={"indoor_t_c": "Indoor",
                                         "outdoor_t_c": "Outdoor"}),
                        use_container_width=True)
    with c2:
        st.plotly_chart(line_fig(cold_res, ["outdoor_t_c", "indoor_t_c"],
                                 "Coldest week — indoor vs outdoor", "°C",
                                 labels={"indoor_t_c": "Indoor",
                                         "outdoor_t_c": "Outdoor"}),
                        use_container_width=True)
    s_hot = comfort_stats(hot_res, cfg["climate"]["comfort_range_c"])
    s_cold = comfort_stats(cold_res, cfg["climate"]["comfort_range_c"])
    st.info(f"**Expected behaviour:** hottest-week peak indoor "
            f"{s_hot['max_indoor_c']:.1f} °C (outdoor "
            f"{hot_res['outdoor_t_c'].max():.1f} °C) · coldest-week minimum "
            f"{s_cold['min_indoor_c']:.1f} °C · night heat loss "
            f"{abs(s_cold['night_heat_loss_kwh']):.0f} kWh/week. "
            f"Comfort band 18–32 °C.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    st.sidebar.title("🏕️ SIH26051 Shelter Studio")
    st.sidebar.caption("Area-specific passive shelter · DRDO PS SIH26051")
    page = st.sidebar.radio("Navigate",
                            ["1 · Location", "2 · Climate", "3 · Shelter design",
                             "4 · Simulation", "5 · Optimization",
                             "6 · Recommendation"])

    if not ensure_data():
        return

    cfg = get_cfg()
    weather = get_weather()

    if page == "1 · Location":
        page_location(cfg)
    elif page == "2 · Climate":
        page_climate(weather)
    elif page == "3 · Shelter design":
        page_design()
    elif page == "4 · Simulation":
        page_simulation()
    elif page == "5 · Optimization":
        page_optimization()
    elif page == "6 · Recommendation":
        page_recommendation()


if __name__ == "__main__":
    main()
