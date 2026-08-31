"""OBJ + STL mesh writer/reader (ASCII & binary) — pure stdlib.

The digital structure is triangulated once here and shared by the OBJ
and STL exporters; the readers extract vertex clouds + bounding boxes
for CAD ingestion (units assumed metres — stated in the API response).
"""
from __future__ import annotations

import struct

from src.cad.dxf import box_faces


def box_triangles(box) -> list[tuple]:
    """12 triangles per box, outward winding (CCW viewed from outside)."""
    tris = []
    for quad in box_faces(box):
        tris.append((quad[0], quad[1], quad[2]))
        tris.append((quad[0], quad[2], quad[3]))
    return tris


def write_obj(design: dict, components: list[dict]) -> str:
    lines = [f"# SIH26051 Shelter digital structure",
             f"# {design.get('length_m', 3)} x {design.get('width_m', 3)} x "
             f"{design.get('height_m', 2.6)} m, orientation "
             f"{design.get('orientation_deg', 0)} deg (units: metres)"]
    base = 0
    for c in components:
        lines.append(f"o {c['id']}")
        verts = _unique_verts(c["box"])
        idx = {v: base + i for i, v in enumerate(verts)}
        for v in verts:
            lines.append(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}")
        for tri in box_triangles(c["box"]):
            lines.append(f"f {idx[tri[0]] + 1} {idx[tri[1]] + 1} "
                         f"{idx[tri[2]] + 1}")
        base += len(verts)
    return "\n".join(lines) + "\n"


def _unique_verts(box):
    x0, y0, z0, x1, y1, z1 = map(float, box)
    return [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]


def write_stl(design: dict, components: list[dict]) -> str:
    out = [f"solid sih26051_shelter_{design.get('length_m', 3)}x"
           f"{design.get('width_m', 3)}x{design.get('height_m', 2.6)}"]
    for c in components:
        for tri in box_triangles(c["box"]):
            a, b, d = tri
            ux, uy, uz = _normal(a, b, d)
            out.append(f"  facet normal {ux:.6e} {uy:.6e} {uz:.6e}")
            out.append("    outer loop")
            for p in (a, b, d):
                out.append(f"      vertex {p[0]:.6e} {p[1]:.6e} {p[2]:.6e}")
            out.append("    endloop")
            out.append("  endfacet")
    out.append("endsolid sih26051_shelter")
    return "\n".join(out) + "\n"


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln < 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / ln, ny / ln, nz / ln)


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------
def read_obj(text: str) -> dict:
    verts: list[tuple[float, float, float]] = []
    faces = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v":
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "f":
            faces += 1
    return _mesh_summary("obj", verts, faces)


def read_stl(data: bytes) -> dict:
    """STL reader: auto-detects binary vs ASCII."""
    if len(data) >= 84 and data[:5] != b"solid":
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 == len(data):
            verts: list[tuple[float, float, float]] = []
            for i in range(n):
                off = 84 + i * 50
                for j in range(3):
                    x, y, z = struct.unpack_from("<3f", data, off + 12 + j * 12)
                    verts.append((float(x), float(y), float(z)))
            return _mesh_summary("stl(binary)", verts, n)
    return _mesh_summary("stl(ascii)", _ascii_stl_verts(data.decode("latin-1")),
                         data.count(b"facet normal"))


def _ascii_stl_verts(text: str) -> list[tuple[float, float, float]]:
    verts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "vertex":
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return verts


def _mesh_summary(fmt: str, verts: list, faces: int) -> dict:
    if verts:
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
        bbox = {"x0": min(xs), "y0": min(ys), "z0": min(zs),
                "x1": max(xs), "y1": max(ys), "z1": max(zs)}
        dims = {"length_m": round(max(xs) - min(xs), 4),
                "width_m": round(max(ys) - min(ys), 4),
                "height_m": round(max(zs) - min(zs), 4)}
    else:
        bbox, dims = {}, {}
    return {"format": fmt, "vertices": len(verts), "faces": faces,
            "bbox": bbox, "dimensions_m": dims}
