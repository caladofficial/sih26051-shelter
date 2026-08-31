"""CAD ingestion — accept geometry from any of the supported channels.

Supported source channels (all open formats, parsed in pure Python):
    .dxf — AutoCAD R12/R2000 drawing exchange (LINE / LWPOLYLINE /
           POLYLINE / 3DFACE / POINT geometry is dimensioned)
    .obj — Wavefront mesh (exported by Blender, SketchUp, Fusion 360…)
    .stl — 3D-print mesh, ASCII or binary (CAD/CAM slicers)

Every channel yields the same summary: entity counts, bounding box and
suggested shelter dimensions. DXF files carry no units — metres are
assumed and stated explicitly in the response (never silently guessed).
"""
from __future__ import annotations

from src.cad import dxf, mesh

MAX_FILE_BYTES = 8 * 1024 * 1024

# sanity envelope for shelter-scale geometry (metres)
MIN_DIM = 0.1
MAX_DIM = 60.0


def ingest(filename: str, data: bytes) -> dict:
    """Parse a CAD file and return the dimension summary dict."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            f"file too large ({len(data) / 1e6:.1f} MB) — max 8 MB")
    if len(data) == 0:
        raise ValueError("empty file")
    name = (filename or "upload").lower()
    if name.endswith(".dxf"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        summary = dxf.read_dxf(text)
    elif name.endswith(".obj"):
        summary = mesh.read_obj(data.decode("utf-8-sig", "replace"))
    elif name.endswith(".stl"):
        summary = mesh.read_stl(data)
    else:
        raise ValueError("unsupported format — use .dxf, .obj or .stl")

    dims = summary.get("dimensions_m") or {}
    if not dims:
        raise ValueError("no drawable geometry found in the file")
    for k in ("length_m", "width_m", "height_m"):
        v = dims.get(k)
        if v is None or not (MIN_DIM <= v <= MAX_DIM):
            raise ValueError(
                f"bounding dimension {k}={v} m outside the shelter sanity "
                f"envelope [{MIN_DIM}, {MAX_DIM}] m — units are assumed metres")
    return summary


def suggest_design(summary: dict) -> dict:
    """Map a parsed CAD bounding box onto the shelter design parameters.

    Height comes from the vertical axis; the two horizontal axes are
    ordered length >= width so the footprint is unambiguous.
    """
    d = summary.get("dimensions_m") or {}
    horiz = sorted([d.get("length_m", 0.0), d.get("width_m", 0.0)],
                   reverse=True)
    return {
        "length_m": round(horiz[0], 3) if horiz else None,
        "width_m": round(horiz[1], 3) if len(horiz) > 1 else None,
        "height_m": round(d.get("height_m", 0.0), 3),
        "units_note": "DXF/OBJ/STL carry no units — dimensions read as metres",
    }
