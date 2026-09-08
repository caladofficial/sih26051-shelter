"""Fast lumped-parameter (single-node RC) thermal model of the shelter.

This is the rapid-design model used for sweeps (orientation x material x
insulation). It is NOT a replacement for EnergyPlus — it trades fidelity for
speed so the optimizer can evaluate thousands of designs. Key assumptions
are documented in docs/model_notes.md; EnergyPlus is used to confirm final
designs.

Physics (single indoor air node T, explicit Euler):

    m_eff * cp * dT/dt = sum_surfaces( Q_cond_i ) + Q_vent + Q_int + Q_solar_in

Opaque surfaces use the SOL-AIR temperature method (ASHRAE HOF Ch.18;
ISO 13790): the absorbed solar flux raises the effective outside
temperature by  alpha * POA / h_o, and the heat that actually enters is
    q_i = U_i * A_i * (T_out + alpha_i * POA_i / h_o - T)
i.e. only the (U_i / h_o) fraction of absorbed solar reaches the interior —
the rest is re-radiated/convected away at the outer surface. This removes
the classic lumped-model overheating bias.

Windows:  q = U_win*A*(T_out - T) + POA_win * A * SHGC   (transmitted solar).

Solar geometry (sun position, POA irradiance per tilted surface) is computed
with the NREL SPA algorithm + Erbs/Hay-Davies/Spencer/Kasten-Young models
from the weather file's GHI. The implementations in src/data/solar.py are
verbatim ports of the pvlib 0.15.2 equations (validated numerically over a
full year, see scripts/validate_solar_math.py) so the serverless API does
not need to ship pvlib/scipy/h5py.
"""
import numpy as np
import pandas as pd

from src.data import solar
from src.geometry.shelter import Shelter

R_SE = 0.04          # exterior surface film resistance (m2K/W) — ASHRAE HOF
R_SI = 0.13          # interior surface film resistance (m2K/W) — ASHRAE HOF
H_O = 1.0 / R_SE     # exterior film coefficient used by the sol-air method
RHO_AIR = 1.2        # kg/m3
CP_AIR = 1005.0      # J/kgK
MASS_FRACTION = 0.5  # fraction of envelope mass participating in the node


def load_materials(path: str | None = None) -> pd.DataFrame:
    """Load materials.csv, indexed by material name."""
    if path is None:
        from src.paths import MATERIALS_FILE
        path = MATERIALS_FILE
    df = pd.read_csv(path, comment="#")   # skip the documentation footer
    df = df.set_index("material")
    return df


def surface_conductance(layers, materials: pd.DataFrame) -> float:
    """U-value (W/m2K) of an opaque surface from its (material, thickness) layers."""
    r = R_SE + R_SI
    for mat, thick in layers:
        r += thick / float(materials.loc[mat, "k_W_mK"])
    return 1.0 / r


def surface_mass(layers, materials: pd.DataFrame) -> float:
    """kg of a surface per m2."""
    m = 0.0
    for mat, thick in layers:
        m += thick * float(materials.loc[mat, "density_kg_m3"])
    return m


def _build_shelter(cfg: dict) -> Shelter:
    sh = cfg["shelter"]
    return Shelter(
        length_m=sh["length_m"], width_m=sh["width_m"], height_m=sh["height_m"],
        orientation_deg=sh["orientation_deg"],
        wall_material=sh["wall_material"], wall_thickness_m=sh["wall_thickness_m"],
        roof_material=sh["roof_material"], roof_thickness_m=sh["roof_thickness_m"],
        roof_pitch_deg=float(sh.get("roof_pitch_deg", 0.0) or 0.0),
        roof_azimuth_deg=float(sh.get("roof_azimuth_deg", 180.0) or 180.0),
        floor_material=sh["floor_material"], floor_thickness_m=sh["floor_thickness_m"],
        window=None if sh.get("window") is None else _win(sh["window"]),
        door=None if sh.get("door") is None else _door(sh["door"]),
        insulation_material=sh["insulation"]["material"],
        insulation_thickness_m=sh["insulation"]["thickness_m"],
    )


def simulate(cfg: dict, weather: pd.DataFrame,
             materials: pd.DataFrame | None = None,
             dt_min: float | None = None) -> pd.DataFrame:
    """Run the RC model over `weather` (hourly, tz-aware local index).

    Returns an hourly DataFrame with indoor_t_c, outdoor_t_c and the heat
    balance terms (W, positive = heat flowing INTO the shelter).
    """
    if materials is None:
        materials = load_materials()

    dt = dt_min or float(cfg["simulation"]["dt_min"])
    dt_s = dt * 60.0
    shelter = _build_shelter(cfg)
    surfaces = shelter.surfaces()

    # ---- conduction / capacitance terms -----------------------------------
    ua_surf, alpha_surf, mass_kg = {}, {}, 0.0
    for s in surfaces:
        if s["type"] == "window":
            ua_surf[s["name"]] = s["area_m2"] * s["u_w_m2k"]
            alpha_surf[s["name"]] = s["shgc"]
        else:
            u = surface_conductance(s["layers"], materials)
            ua_surf[s["name"]] = s["area_m2"] * u
            alpha_surf[s["name"]] = float(
                materials.loc[s["layers"][0][0], "solar_absorptance"])
            mass_kg += surface_mass(s["layers"], materials) * s["area_m2"] * MASS_FRACTION

    ach = float(cfg["simulation"]["ventilation_ach"])
    ua_vent = RHO_AIR * CP_AIR * shelter.volume_m3 * ach / 3600.0
    # node capacitance = participating envelope mass + indoor air mass
    c_eff = mass_kg * 1000.0 + RHO_AIR * shelter.volume_m3 * CP_AIR
    q_int = float(cfg["simulation"]["internal_gains_w"])
    t_ground = float(cfg["simulation"]["ground_temperature_c"])

    # ---- solar geometry (NREL SPA, pvlib-equivalent) ----------------------
    lat = float(cfg["location"]["latitude"])
    lon = float(cfg["location"]["longitude"])
    solpos = solar.solar_position(weather.index, lat, lon)
    zenith = solpos["apparent_zenith"].clip(upper=89.9)
    azimuth = solpos["azimuth"]

    ghi = weather["ghi"].clip(lower=0.0)
    erbs = solar.erbs(ghi, zenith, weather.index)     # dni/dhi split
    dni = erbs["dni"].clip(lower=0.0).fillna(0.0)
    dhi = erbs["dhi"].clip(lower=0.0).fillna(0.0)
    albedo = float(cfg["simulation"]["albedo"])
    dni_extra = solar.get_extra_radiation(weather.index)
    airmass = solar.get_relative_airmass(zenith)

    poa = {}
    for s in surfaces:
        poa[s["name"]] = solar.get_total_irradiance(
            surface_tilt=s["tilt_deg"], surface_azimuth=s["azimuth_deg"],
            solar_zenith=zenith, solar_azimuth=azimuth,
            dni=dni, ghi=ghi, dhi=dhi,
            dni_extra=dni_extra, airmass=airmass,
            albedo=albedo).fillna(0.0).clip(lower=0.0)

    # ---- assemble & integrate ---------------------------------------------
    t_out = weather["t2m"].interpolate().ffill().bfill().to_numpy(dtype=float)
    n = len(weather)
    n_steps = n * int(round(3600.0 / dt_s))
    t_in = np.full(n_steps + 1, t_out[0], dtype=float)

    q_solar = np.zeros(n_steps)
    q_cond = np.zeros(n_steps)
    q_vent = np.zeros(n_steps)
    poa_a = {name: poa[name].to_numpy() for name in poa}

    for k in range(n_steps):
        h = int(k * dt_s / 3600.0)
        h = min(h, n - 1)
        t_o = t_out[h]
        q_s = q_c = 0.0
        for s in surfaces:
            name, stype = s["name"], s["type"]
            ua = ua_surf[name]
            if stype == "window":
                gain_sol = poa_a[name][h] * s["area_m2"] * alpha_surf[name]
                q_s += gain_sol
                q_c += ua * (t_o - t_in[k])
            elif stype == "floor":                       # ground-coupled
                q_c += ua * (t_ground - t_in[k])
            else:                                        # sol-air opaque
                alpha = alpha_surf[name]
                t_solair = t_o + alpha * poa_a[name][h] / H_O
                q_c += ua * (t_solair - t_in[k])
                q_s += ua * alpha * poa_a[name][h] / H_O   # entering solar part
        q_v = ua_vent * (t_o - t_in[k])
        q_net = q_s + q_c + q_v + q_int
        q_solar[k], q_cond[k], q_vent[k] = q_s, q_c, q_v
        t_in[k + 1] = t_in[k] + q_net / c_eff * dt_s
        if not np.isfinite(t_in[k + 1]):
            raise FloatingPointError("RC model diverged — reduce dt_min")

    steps_per_hour = int(round(3600.0 / dt_s))
    out = pd.DataFrame({
        "indoor_t_c": t_in[:-1],
        "outdoor_t_c": np.repeat(t_out, steps_per_hour)[:n_steps],
        "q_solar_w": q_solar,
        "q_conduct_w": q_cond,
        "q_vent_w": q_vent,
        "q_internal_w": q_int,
    }, index=pd.date_range(weather.index[0], periods=n_steps,
                           freq=f"{int(dt)}min"))
    out["q_net_w"] = out[["q_solar_w", "q_conduct_w", "q_vent_w",
                          "q_internal_w"]].sum(axis=1)
    return out.resample("h").mean()


def comfort_stats(df: pd.DataFrame, comfort_range=(18.0, 32.0)) -> dict:
    """Thermal-comfort summary used as the optimisation objective basis."""
    lo, hi = comfort_range
    n = len(df)
    hours_in = int(((df["indoor_t_c"] >= lo) & (df["indoor_t_c"] <= hi)).sum())
    night = df[(df.index.hour >= 18) | (df.index.hour < 6)]
    night_loss_kwh = float(night["q_conduct_w"].clip(upper=0).sum() / 1000.0)
    return {
        "mean_indoor_c": float(df["indoor_t_c"].mean()),
        "min_indoor_c": float(df["indoor_t_c"].min()),
        "max_indoor_c": float(df["indoor_t_c"].max()),
        "comfort_hours": hours_in,
        "comfort_fraction": hours_in / n,
        "solar_gain_kwh": float(df["q_solar_w"].clip(lower=0).sum() / 1000.0),
        "night_heat_loss_kwh": night_loss_kwh,
        "total_heat_loss_kwh": float(df["q_conduct_w"].clip(upper=0).sum() / 1000.0),
    }


def _win(w: dict):
    from src.geometry.shelter import WindowSpec
    return WindowSpec(wall=w["wall"], width_m=w["width_m"], height_m=w["height_m"],
                      sill_height_m=w.get("sill_height_m", 0.9),
                      u_w_m2k=w.get("u_w_m2k", 5.8), shgc=w.get("shgc", 0.82))


def _door(d: dict):
    from src.geometry.shelter import DoorSpec
    return DoorSpec(wall=d["wall"], width_m=d["width_m"], height_m=d.get("height_m", 1.95))
