"""Digital structure kernel — the shelter as a set of 3D component boxes.

Single source of truth for the "digital structure" shown in the UI
(three.js / canvas render), exported as CAD (DXF / OBJ / STL) and used
for CAD ingestion. The JS mirror lives in `public/app.js` (buildStructure3D)
and must stay geometrically identical — covered by tests in test_cad.py.

Coordinate frame (shelter-local, right-handed, z up):
    +x east, +y north, +z up; floor slab bottom sits at z = 0;
    origin at the footprint centre.
    With orientation_deg = 0 the "south" wall (normal azimuth 180 deg)
    is the one at y = -width/2. orientation_deg rotates the whole
    shelter clockwise viewed from above (matches src/geometry/shelter.py).

All numbers are derived from the design parameters (config defaults or
user input) — nothing invented.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# colour mapping — one hue per construction role (used by UI + DXF layers)
# ---------------------------------------------------------------------------
ROLE_COLORS = {
    "floor": "#7d848e",
    "wall": "#b0784a",          # terracotta/brick family
    "insulation": "#f2c14e",    # amber
    "roof": "#8f9aa6",
    "window": "#5ab8d8",        # glass blue
    "door": "#6e6358",
}


def component_color(role: str) -> str:
    return ROLE_COLORS.get(role, "#9aa3ad")


# ---------------------------------------------------------------------------
# component builder
# ---------------------------------------------------------------------------
def _box(x0, y0, z0, x1, y1, z1) -> list[float]:
    """Normalise box to [x0, y0, z0, x1, y1, z1]."""
    return [float(min(x0, x1)), float(min(y0, y1)), float(min(z0, z1)),
            float(max(x0, x1)), float(max(y0, y1)), float(max(z0, z1))]


def build_components(design: dict, materials: dict[str, dict] | None = None) -> list[dict]:
    """Return the component list of the digital structure.

    design keys (flat): length_m, width_m, height_m, orientation_deg,
        wall_material, wall_thickness_m, roof_material, roof_thickness_m,
        floor_material, floor_thickness_m, insulation_material (or 'none'),
        insulation_thickness_m, window_wall, window_width_m, window_height_m,
        window_sill_m, window_shgc, door_wall, door_width_m, door_height_m
    materials: {material_name: {k_W_mK, density_kg_m3, cp_J_kgK, ...}}
    """
    materials = materials or {}
    L = float(design.get("length_m", 3.0))
    W = float(design.get("width_m", 3.0))
    H = float(design.get("height_m", 2.6))
    tw = float(design.get("wall_thickness_m", 0.2))
    tr = float(design.get("roof_thickness_m", 0.12))
    tf = float(design.get("floor_thickness_m", 0.1))
    ins_mat = design.get("insulation_material") or "none"
    ti = float(design.get("insulation_thickness_m", 0.0))
    has_ins = ins_mat != "none" and ti > 0

    def prop(mat: str) -> dict:
        m = materials.get(mat) or {}
        return {
            "k_W_mK": m.get("k_W_mK"),
            "density_kg_m3": m.get("density_kg_m3"),
            "cp_J_kgK": m.get("cp_J_kgK"),
            "solar_absorptance": m.get("solar_absorptance"),
            "emissivity": m.get("emissivity"),
        }

    hx, hy = L / 2.0, W / 2.0
    comps: list[dict] = []

    # --- floor slab -------------------------------------------------------
    comps.append({
        "id": "floor", "name": "Floor slab", "type": "floor",
        "material": design.get("floor_material", "concrete"),
        "thickness_m": tf, "color": ROLE_COLORS["floor"],
        "box": _box(-hx, -hy, 0.0, hx, hy, tf),
        "properties": prop(design.get("floor_material", "concrete")),
    })

    # --- walls (outer face on the perimeter line, thickness inward) ------
    walls = [
        ("south", -hy, -hy + tw),   # normal -y
        ("north", hy - tw, hy),     # normal +y
        ("east", hx - tw, hx),      # normal +x
        ("west", -hx, -hx + tw),    # normal -x
    ]
    for name, y0, y1 in walls:
        if name in ("south", "north"):
            bx = _box(-hx, y0, tf, hx, y1, H + tf)
        else:
            bx = _box(y0, -hy, tf, y1, hy, H + tf)   # x0..x1 swapped in
        comps.append({
            "id": f"wall_{name}", "name": f"{name.title()} wall",
            "type": "wall", "material": design.get("wall_material", "brick"),
            "thickness_m": tw, "color": ROLE_COLORS["wall"], "box": bx,
            "properties": prop(design.get("wall_material", "brick")),
        })
        if has_ins:
            if name in ("south", "north"):
                # insulation just inside the inner wall face
                iy0 = -hy + tw if name == "south" else hy - tw - ti
                bx_i = _box(-hx + 0.01, iy0, tf + 0.01,
                            hx - 0.01, iy0 + ti, H + tf - 0.01)
            else:
                ix0 = hx - tw - ti if name == "east" else -hx + tw
                bx_i = _box(ix0, -hy + 0.01, tf + 0.01,
                            ix0 + ti, hy - 0.01, H + tf - 0.01)
            comps.append({
                "id": f"ins_wall_{name}", "name": f"{name.title()} wall insulation",
                "type": "insulation", "material": ins_mat,
                "thickness_m": ti, "color": ROLE_COLORS["insulation"],
                "box": bx_i, "properties": prop(ins_mat),
            })

    # --- roof slab + roof insulation -------------------------------------
    comps.append({
        "id": "roof", "name": "Roof slab", "type": "roof",
        "material": design.get("roof_material", "rcc_slab"),
        "thickness_m": tr, "color": ROLE_COLORS["roof"],
        "box": _box(-hx, -hy, H + tf, hx, hy, H + tf + tr),
        "properties": prop(design.get("roof_material", "rcc_slab")),
    })
    if has_ins:
        comps.append({
            "id": "ins_roof", "name": "Roof insulation",
            "type": "insulation", "material": ins_mat,
            "thickness_m": ti, "color": ROLE_COLORS["insulation"],
            "box": _box(-hx + 0.01, -hy + 0.01, H + tf + tr,
                        hx - 0.01, hy - 0.01, H + tf + tr + ti),
            "properties": prop(ins_mat),
        })

    # --- window (glass box set in its wall, sill height above floor) ------
    win_wall = design.get("window_wall", "south")
    ww = float(design.get("window_width_m", 1.2))
    wh = float(design.get("window_height_m", 1.2))
    sill = float(design.get("window_sill_m", 0.9))
    z0 = tf + sill
    z1 = z0 + wh
    tg = 0.02  # visual glazing thickness (flush with the outer wall face)
    if win_wall == "south":
        bx = _box(-ww / 2.0, -hy, z0, ww / 2.0, -hy + tg, z1)
    elif win_wall == "north":
        bx = _box(-ww / 2.0, hy - tg, z0, ww / 2.0, hy, z1)
    elif win_wall == "east":
        bx = _box(hx - tg, -ww / 2.0, z0, hx, ww / 2.0, z1)
    else:
        bx = _box(-hx, -ww / 2.0, z0, -hx + tg, ww / 2.0, z1)
    comps.append({
        "id": "window", "name": "Window (glazing)", "type": "window",
        "material": "glass", "thickness_m": tg, "color": ROLE_COLORS["window"],
        "box": bx,
        "properties": {"u_w_m2k": design.get("window_u_w_m2k", 5.8),
                       "shgc": design.get("window_shgc", 0.82)},
    })

    # --- door (optional; not used by the web UI but supported) ------------
    if design.get("door_width_m"):
        dw = float(design["door_width_m"])
        dh = float(design.get("door_height_m", 1.95))
        d_wall = design.get("door_wall", "west")
        z0d, z1d = tf, tf + dh
        if d_wall == "south":
            bx = _box(-dw / 2.0, -hy, z0d, dw / 2.0, -hy + tg, z1d)
        elif d_wall == "north":
            bx = _box(-dw / 2.0, hy - tg, z0d, dw / 2.0, hy, z1d)
        elif d_wall == "east":
            bx = _box(hx - tg, -dw / 2.0, z0d, hx, dw / 2.0, z1d)
        else:
            bx = _box(-hx, -dw / 2.0, z0d, -hx + tg, dw / 2.0, z1d)
        comps.append({
            "id": "door", "name": "Door", "type": "door",
            "material": design.get("wall_material", "brick"),
            "thickness_m": tw, "color": ROLE_COLORS["door"], "box": bx,
            "properties": prop(design.get("wall_material", "brick")),
        })

    return comps


# ---------------------------------------------------------------------------
# bounding box + surface accounting
# ---------------------------------------------------------------------------
def bounding_box(comps: list[dict]) -> dict:
    xs, ys, zs = [], [], []
    for c in comps:
        x0, y0, z0, x1, y1, z1 = c["box"]
        xs += [x0, x1]; ys += [y0, y1]; zs += [z0, z1]
    return {"x0": min(xs), "y0": min(ys), "z0": min(zs),
            "x1": max(xs), "y1": max(ys), "z1": max(zs),
            "length_m": max(xs) - min(xs), "width_m": max(ys) - min(ys),
            "height_m": max(zs) - min(zs)}


def surfaces(design: dict, comps: list[dict]) -> dict:
    """Area accounting: gross/net opaque wall, window, roof, floor."""
    L = float(design.get("length_m", 3.0))
    W = float(design.get("width_m", 3.0))
    H = float(design.get("height_m", 2.6))
    win_area = 0.0
    door_area = 0.0
    for c in comps:
        if c["type"] == "window":
            win_area += (c["box"][3] - c["box"][0]) * (c["box"][5] - c["box"][2])
        elif c["type"] == "door":
            door_area += (c["box"][3] - c["box"][0]) * (c["box"][5] - c["box"][2])
    gross_wall = 2.0 * (L + W) * H
    return {
        "floor_m2": round(L * W, 3),
        "roof_m2": round(L * W, 3),
        "gross_wall_m2": round(gross_wall, 3),
        "window_m2": round(win_area, 3),
        "door_m2": round(door_area, 3),
        "net_opaque_wall_m2": round(gross_wall - win_area - door_area, 3),
        "glazing_ratio_pct": round(100.0 * win_area / max(gross_wall, 1e-9), 2),
        "volume_m3": round(L * W * H, 3),
    }


# ---------------------------------------------------------------------------
# envelope assembly (R / U) — standard physics: R = t/k, U = 1 / sum(R).
# Conductive only (no film/surface resistances) — documented, not invented.
# ---------------------------------------------------------------------------
def assembly(design: dict, materials: dict[str, dict] | None = None) -> dict:
    materials = materials or {}
    k_of = lambda m: (materials.get(m) or {}).get("k_W_mK")

    def envelope(layers: list[tuple[str, float]]) -> dict:
        rs: list[dict] = []
        for mat, t in layers:
            k = k_of(mat)
            rs.append({"material": mat, "thickness_m": round(t, 4),
                       "k_W_mK": k, "r_m2K_W": round(t / k, 4) if k else None})
        r_total = sum(r["r_m2K_W"] for r in rs if r["r_m2K_W"])
        return {"layers": rs,
                "total_r_m2K_W": round(r_total, 4) if rs else None,
                "u_w_m2k": round(1.0 / r_total, 4) if r_total else None}

    ti = float(design.get("insulation_thickness_m", 0.0))
    ins = design.get("insulation_material", "none")
    ins_layers = [(ins, ti)] if ins != "none" and ti > 0 else []
    return {
        "wall": envelope([(design.get("wall_material", "brick"),
                           float(design.get("wall_thickness_m", 0.2)))] + ins_layers),
        "roof": envelope([(design.get("roof_material", "rcc_slab"),
                           float(design.get("roof_thickness_m", 0.12)))] + ins_layers),
        "floor": envelope([(design.get("floor_material", "concrete"),
                            float(design.get("floor_thickness_m", 0.1)))]),
        "window": {"u_w_m2k": design.get("window_u_w_m2k", 5.8),
                   "shgc": design.get("window_shgc", 0.82)},
    }


def rotate_deg(deg: float) -> float:
    return math.radians(float(deg))
