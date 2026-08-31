"""Minimal DXF (R12) writer + reader — no third-party dependencies.

Writes the digital structure as LINE (wireframe) + 3DFACE (surfaces)
entities on named layers (WALL-SOUTH, INSULATION, ROOF, ...) so the
file opens cleanly in AutoCAD, LibreCAD, QCAD, Fusion 360, etc.

The reader extracts geometry from the subset that matters for shelter
dimensioning: LINE, LWPOLYLINE, POLYLINE, 3DFACE, POINT (all other
entities are counted but skipped). Units are not stored in DXF — the
API assumes metres and says so explicitly in the response.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------
ACI = {  # AutoCAD colour index per component type (1-9 safe everywhere)
    "floor": 8, "wall": 6, "insulation": 2, "roof": 4, "window": 5, "door": 3,
}


def _f(v: float) -> str:
    return f"{float(v):.4f}"


def _line(layer: str, color: int, p1, p2) -> str:
    return ("0\nLINE\n8\n%s\n62\n%d\n10\n%s\n20\n%s\n30\n%s\n"
            "11\n%s\n21\n%s\n31\n%s\n" % (layer, color, _f(p1[0]), _f(p1[1]),
                                          _f(p1[2]), _f(p2[0]), _f(p2[1]),
                                          _f(p2[2])))


def _face(layer: str, color: int, pts) -> str:
    out = ["0\n3DFACE", "8\n" + layer, "62\n%d" % color]
    names = ["10", "20", "30", "11", "21", "31", "12", "22", "32", "13", "23", "33"]
    for i, p in enumerate(pts[:4]):
        out.append(f"{names[i * 3]}\n{_f(p[0])}\n{names[i * 3 + 1]}\n{_f(p[1])}\n{names[i * 3 + 2]}\n{_f(p[2])}")
    return "\n".join(out) + "\n"


def box_edges(box) -> list[tuple]:
    x0, y0, z0, x1, y1, z1 = map(float, box)
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return [(v[0], v[1]), (v[1], v[2]), (v[2], v[3]), (v[3], v[0]),
            (v[4], v[5]), (v[5], v[6]), (v[6], v[7]), (v[7], v[4]),
            (v[0], v[4]), (v[1], v[5]), (v[2], v[6]), (v[3], v[7])]


def box_faces(box) -> list[list]:
    """Outward-facing quads: [bottom, top, south, north, west, east]."""
    x0, y0, z0, x1, y1, z1 = map(float, box)
    return [
        [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],  # bottom
        [(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)],  # top
        [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],  # -y south
        [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)],  # +y north
        [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],  # -x west
        [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],  # +x east
    ]


def write_dxf(design: dict, components: list[dict]) -> str:
    """R12 DXF text: title TEXT + per-component LINE/3DFACE on named layers."""
    layername = lambda c: f"{c['type'].upper()}-{c['id'].upper()}"
    out = ["0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES"]
    title = (f"SIH26051 SHELTER-STRUCTURE "
             f"{design.get('length_m', 3)}x{design.get('width_m', 3)}"
             f"x{design.get('height_m', 2.6)} m "
             f"ORIENT {design.get('orientation_deg', 0)} deg")
    out.append("0\nTEXT\n8\nTITLE\n10\n0\n20\n0\n30\n0\n40\n0.2\n1\n" + title)
    for c in components:
        layer = layername(c)
        color = ACI.get(c["type"], 7)
        for p1, p2 in box_edges(c["box"]):
            out.append(_line(layer, color, p1, p2))
        for pts in box_faces(c["box"]):
            out.append(_face(layer, color, pts))
    out.append("0\nENDSEC\n0\nEOF")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# reader (dimension extraction)
# ---------------------------------------------------------------------------
_GROUP = re.compile(r"(\d+)\s*\n(.*?)(?=\n\d+\s*\n|\Z)", re.S)


def _parse_entities(text: str) -> list[list[str]]:
    """Split into [code, value, code, value, ...] pairs per entity."""
    lines = text.splitlines()
    entities: list[list[str]] = []
    cur: list[str] | None = None
    i = 0
    while i < len(lines):
        code = lines[i].strip()
        if code.isdigit():
            value = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if code == "0":
                if cur is not None:
                    entities.append(cur)
                cur = [code, value]
            else:
                if cur is not None:
                    cur += [code, value]
            i += 2
        else:
            i += 1
    if cur is not None:
        entities.append(cur)
    return entities


def _get(ent: list[str], code: str, default: str | None = None) -> str | None:
    for i in range(0, len(ent) - 1, 2):
        if ent[i] == code:
            return ent[i + 1]
    return default


def read_dxf(text: str) -> dict:
    """Extract geometry summary: entity counts, layers, bounding box."""
    entities = _parse_entities(text)
    counts: dict[str, int] = {}
    layers: dict[str, int] = {}
    pts: list[tuple[float, float, float]] = []

    for ent in entities:
        etype = ent[1] if len(ent) > 1 else "?"
        counts[etype] = counts.get(etype, 0) + 1
        layer = _get(ent, "8") or "0"
        layers[layer] = layers.get(layer, 0) + 1
        if etype == "LINE":
            pts.append((float(_get(ent, "10", "0") or 0),
                        float(_get(ent, "20", "0") or 0),
                        float(_get(ent, "30", "0") or 0)))
            pts.append((float(_get(ent, "11", "0") or 0),
                        float(_get(ent, "21", "0") or 0),
                        float(_get(ent, "31", "0") or 0)))
        elif etype in ("3DFACE", "SOLID"):
            for i in (10, 11, 12, 13):
                x = _get(ent, str(i))
                y = _get(ent, str(i + 10))
                z = _get(ent, str(i + 20))
                if x is not None and y is not None and z is not None:
                    pts.append((float(x), float(y), float(z)))
        elif etype == "LWPOLYLINE":
            xs, ys = [], []
            for j in range(0, len(ent) - 1, 2):
                if ent[j] == "10":
                    xs.append(float(ent[j + 1]))
                elif ent[j] == "20":
                    ys.append(float(ent[j + 1]))
            for x, y in zip(xs, ys):
                pts.append((x, y, 0.0))
        elif etype == "POINT":
            pts.append((float(_get(ent, "10", "0") or 0),
                        float(_get(ent, "20", "0") or 0),
                        float(_get(ent, "30", "0") or 0)))

    return _summary("dxf", counts, layers, pts)


def _summary(fmt: str, counts: dict, layers: dict,
             pts: list[tuple[float, float, float]]) -> dict:
    if pts:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        bbox = {"x0": min(xs), "y0": min(ys), "z0": min(zs),
                "x1": max(xs), "y1": max(ys), "z1": max(zs)}
        dims = {"length_m": round(max(xs) - min(xs), 4),
                "width_m": round(max(ys) - min(ys), 4),
                "height_m": round(max(zs) - min(zs), 4)}
    else:
        bbox, dims = {}, {}
    return {"format": fmt, "entity_counts": counts,
            "layers": dict(sorted(layers.items(), key=lambda kv: -kv[1])),
            "bbox": bbox, "dimensions_m": dims}
