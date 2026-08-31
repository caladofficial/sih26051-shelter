"""CAD / digital-structure tests — geometry kernel, DXF/OBJ/STL
round-trips, ingestion and the /api/cad/* endpoints.

Run:  python -m pytest tests/test_cad.py -v
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.cad import dxf, ingest, mesh, model  # noqa: E402
from src.db.store import Store  # noqa: E402

DESIGN = {
    "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
    "orientation_deg": 0, "wall_material": "brick", "wall_thickness_m": 0.2,
    "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
    "floor_material": "concrete", "floor_thickness_m": 0.1,
    "insulation_material": "eps", "insulation_thickness_m": 0.05,
    "window_wall": "south", "window_width_m": 1.2, "window_height_m": 1.2,
    "window_sill_m": 0.9, "window_shgc": 0.82, "window_u_w_m2k": 5.8,
}


@pytest.fixture(scope="module")
def materials():
    store = Store()
    # canonical key names (store returns PostgREST-folded keys)
    canon = {}
    for m in store.list_materials():
        c = dict(m)
        c["k_W_mK"] = c.pop("k_w_mk", None)
        c["cp_J_kgK"] = c.pop("cp_j_kgk", None)
        canon[c["material"]] = c
    return canon


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


# ---------------------------------------------------------------- structure
def test_components_basic(materials):
    comps = model.build_components(DESIGN, materials)
    types = [c["type"] for c in comps]
    assert types.count("wall") == 4
    assert types.count("insulation") == 5        # 4 walls + roof
    assert types.count("floor") == 1
    assert types.count("roof") == 1
    assert types.count("window") == 1
    # window sits on the south wall plane (y = -width/2)
    win = next(c for c in comps if c["id"] == "window")
    assert win["box"][1] == pytest.approx(-1.5, abs=0.02)
    # every component carries sourced material properties
    wall = next(c for c in comps if c["id"] == "wall_south")
    assert wall["properties"]["k_W_mK"] is not None


def test_components_no_insulation(materials):
    d = dict(DESIGN, insulation_material="none", insulation_thickness_m=0.0)
    comps = model.build_components(d, materials)
    assert all(c["type"] != "insulation" for c in comps)


def test_bounding_and_surfaces(materials):
    comps = model.build_components(DESIGN, materials)
    bb = model.bounding_box(comps)
    assert bb["length_m"] == pytest.approx(3.0, abs=1e-6)
    assert bb["width_m"] == pytest.approx(3.0, abs=1e-6)
    assert bb["height_m"] == pytest.approx(2.6 + 0.1 + 0.12 + 0.05, abs=1e-6)
    s = model.surfaces(DESIGN, comps)
    assert s["floor_m2"] == pytest.approx(9.0)
    assert s["window_m2"] == pytest.approx(1.44, abs=1e-6)
    assert s["net_opaque_wall_m2"] == pytest.approx(31.2 - 1.44, abs=1e-6)


def test_assembly_physics(materials):
    a = model.assembly(DESIGN, materials)
    wall = a["wall"]
    k_brick = materials["brick"]["k_W_mK"]
    k_eps = materials["eps"]["k_W_mK"]
    r_expected = 0.2 / k_brick + 0.05 / k_eps
    assert wall["total_r_m2K_W"] == pytest.approx(r_expected, rel=1e-3)
    assert wall["u_w_m2k"] == pytest.approx(1.0 / r_expected, rel=1e-3)


# ------------------------------------------------------------- round-trips
def test_dxf_roundtrip():
    comps = model.build_components(DESIGN, {})
    text = dxf.write_dxf(DESIGN, comps)
    assert "SECTION" in text and "EOF" in text
    s = dxf.read_dxf(text)
    assert s["dimensions_m"]["length_m"] == pytest.approx(3.0, abs=1e-3)
    assert s["dimensions_m"]["width_m"] == pytest.approx(3.0, abs=1e-3)
    assert s["dimensions_m"]["height_m"] == pytest.approx(2.87, abs=1e-3)
    assert s["format"] == "dxf"
    assert any(k.startswith(("WALL", "INSULATION", "ROOF")) for k in s["layers"])


def test_obj_roundtrip():
    comps = model.build_components(DESIGN, {})
    text = mesh.write_obj(DESIGN, comps)
    s = mesh.read_obj(text)
    assert s["faces"] == len(comps) * 12
    assert s["dimensions_m"]["length_m"] == pytest.approx(3.0, abs=1e-3)
    assert s["dimensions_m"]["height_m"] == pytest.approx(2.87, abs=1e-3)


def test_stl_ascii_roundtrip():
    comps = model.build_components(DESIGN, {})
    text = mesh.write_stl(DESIGN, comps)
    s = mesh.read_stl(text.encode())
    assert s["format"] == "stl(ascii)"
    assert s["faces"] == len(comps) * 12
    assert s["dimensions_m"]["width_m"] == pytest.approx(3.0, abs=1e-3)


def test_stl_binary_read():
    # tiny binary STL: one triangle 0,0,0 / 3,0,0 / 0,3,0
    tris = [(0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 3.0, 0.0)]
    data = b"x" * 80 + struct.pack("<I", 1)
    for x1, y1, z1, x2, y2, z2, x3, y3, z3 in tris:
        data += struct.pack("<3f", 0, 0, 1)
        data += struct.pack("<9f", x1, y1, z1, x2, y2, z2, x3, y3, z3)
        data += struct.pack("<H", 0)
    s = mesh.read_stl(data)
    assert s["format"] == "stl(binary)"
    assert s["dimensions_m"]["length_m"] == pytest.approx(3.0)
    assert s["dimensions_m"]["width_m"] == pytest.approx(3.0)


# ---------------------------------------------------------------- ingestion
def test_ingest_dxf_and_suggest():
    comps = model.build_components(DESIGN, {})
    summary = ingest.ingest("shelter.dxf", dxf.write_dxf(DESIGN, comps).encode())
    assert summary["dimensions_m"]["height_m"] == pytest.approx(2.87, abs=1e-3)
    sug = ingest.suggest_design(summary)
    assert sug["length_m"] == pytest.approx(3.0, abs=1e-3)
    assert "units" in sug["units_note"].lower()


def test_ingest_obj_and_stl():
    comps = model.build_components(DESIGN, {})
    s1 = ingest.ingest("s.obj", mesh.write_obj(DESIGN, comps).encode())
    assert s1["dimensions_m"]["length_m"] == pytest.approx(3.0, abs=1e-3)
    s2 = ingest.ingest("s.stl", mesh.write_stl(DESIGN, comps).encode())
    assert s2["dimensions_m"]["height_m"] == pytest.approx(2.87, abs=1e-3)


def test_ingest_rejects():
    with pytest.raises(ValueError):
        ingest.ingest("x.png", b"not a cad file")
    with pytest.raises(ValueError):
        ingest.ingest("x.dxf", b"")
    with pytest.raises(ValueError):
        ingest.ingest("x.dxf", b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF")


# ---------------------------------------------------------------- endpoints
def test_api_structure():
    r = client.post("/api/cad/structure", json={"length_m": 3.0, "width_m": 3.0})
    assert r.status_code == 200
    j = r.json()
    assert j["generated"] is True
    assert len(j["components"]) >= 6
    assert j["surfaces"]["floor_m2"] == pytest.approx(9.0)
    assert j["assembly"]["wall"]["u_w_m2k"] is not None


def test_api_export_all_formats():
    for fmt, ctype in (("dxf", "application/dxf"), ("obj", "model/obj"),
                       ("stl", "model/stl")):
        r = client.get(f"/api/cad/export?format={fmt}")
        assert r.status_code == 200, fmt
        assert r.headers["content-type"].startswith(ctype)
        assert "attachment" in r.headers.get("content-disposition", "")
        assert r.content
    r = client.get("/api/cad/export?format=iges")
    assert r.status_code == 400


def test_api_import_roundtrip():
    comps = model.build_components(DESIGN, {})
    obj = mesh.write_obj(DESIGN, comps).encode()
    r = client.post("/api/cad/import",
                    files={"file": ("shelter.obj", obj, "text/plain")})
    assert r.status_code == 200
    j = r.json()
    assert j["format"] == "obj"
    assert j["import_id"].startswith("cad_")
    assert j["suggested_design"]["length_m"] == pytest.approx(3.0, abs=1e-3)
    # it must be recorded in the ingestion log
    r2 = client.get("/api/cad/imports?limit=5")
    assert r2.status_code == 200
    assert any(i["import_id"] == j["import_id"] for i in r2.json()["imports"])


def test_api_import_rejects_bad():
    r = client.post("/api/cad/import",
                    files={"file": ("bad.dxf", b"garbage", "text/plain")})
    assert r.status_code == 400
