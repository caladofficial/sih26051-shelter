"""Shelter geometry — surfaces with area, azimuth, tilt and construction.

Wall naming convention: a wall is named by the compass direction its outward
normal points (in the shelter's rotated frame). With orientation_deg = 0 the
"front" wall faces south (normal azimuth 180 deg). orientation_deg rotates the
whole shelter clockwise (viewed from above).
"""
from dataclasses import dataclass, field


@dataclass
class WindowSpec:
    wall: str            # which wall carries the window
    width_m: float
    height_m: float
    sill_height_m: float = 0.9
    u_w_m2k: float = 5.8     # single clear glazing (ASHRAE typical)
    shgc: float = 0.82

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m


@dataclass
class DoorSpec:
    wall: str
    width_m: float
    height_m: float = 1.95

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m


@dataclass
class Shelter:
    length_m: float
    width_m: float
    height_m: float
    orientation_deg: float
    wall_material: str
    wall_thickness_m: float
    roof_material: str
    roof_thickness_m: float
    floor_material: str
    floor_thickness_m: float
    window: WindowSpec | None = None
    door: DoorSpec | None = None
    insulation_material: str = "none"
    insulation_thickness_m: float = 0.0

    def _wall_areas(self) -> dict:
        """area per wall: front/back walls are `width` wide, side walls `length`."""
        return {
            "south": self.width_m * self.height_m,
            "north": self.width_m * self.height_m,
            "east": self.length_m * self.height_m,
            "west": self.length_m * self.height_m,
        }

    def _wall_azimuth(self, wall: str) -> float:
        """azimuth (deg clockwise from north) of the outward normal."""
        base = {"south": 180.0, "north": 0.0, "east": 90.0, "west": 270.0}
        return (base[wall] + self.orientation_deg) % 360.0

    def surfaces(self) -> list[dict]:
        """Return a list of surface dicts used by both the fast model and IDF builder.

        Each dict: name, type (wall/roof/floor/window/door), area_m2,
        azimuth_deg, tilt_deg (0 = horizontal facing up), layers
        [(material, thickness_m), ...], plus u_w_m2k/shgc for glazing.
        """
        surfaces = []
        wall_areas = self._wall_areas()
        op = {"south": (self.wall_material, self.wall_thickness_m),
              "north": (self.wall_material, self.wall_thickness_m),
              "east": (self.wall_material, self.wall_thickness_m),
              "west": (self.wall_material, self.wall_thickness_m)}
        for wall, area in wall_areas.items():
            mat, thick = op[wall]
            layers = [(mat, thick)]
            if self.insulation_material != "none" and self.insulation_thickness_m > 0:
                layers.append((self.insulation_material, self.insulation_thickness_m))
            surfaces.append({
                "name": f"{wall}_wall", "type": "wall",
                "area_m2": area, "azimuth_deg": self._wall_azimuth(wall),
                "tilt_deg": 90.0, "layers": layers,
            })
        # openings cut out of their wall
        for opening, spec in (("window", self.window), ("door", self.door)):
            if spec is None:
                continue
            for s in surfaces:
                if s["name"] == f"{spec.wall}_wall":
                    s["area_m2"] = max(0.0, s["area_m2"] - spec.area_m2)
                    break
            if opening == "window":
                surfaces.append({
                    "name": "window", "type": "window",
                    "area_m2": spec.area_m2,
                    "azimuth_deg": self._wall_azimuth(spec.wall),
                    "tilt_deg": 90.0, "layers": [],
                    "u_w_m2k": spec.u_w_m2k, "shgc": spec.shgc,
                })
            else:
                surfaces.append({
                    "name": "door", "type": "door",
                    "area_m2": spec.area_m2,
                    "azimuth_deg": self._wall_azimuth(spec.wall),
                    "tilt_deg": 90.0,
                    "layers": [(self.wall_material, self.wall_thickness_m)],
                })
        surfaces.append({
            "name": "roof", "type": "roof",
            "area_m2": self.length_m * self.width_m, "azimuth_deg": 180.0,
            "tilt_deg": 0.0,
            "layers": [(self.roof_material, self.roof_thickness_m)]
                      + ([(self.insulation_material, self.insulation_thickness_m)]
                         if self.insulation_material != "none" and self.insulation_thickness_m > 0
                         else []),
        })
        surfaces.append({
            "name": "floor", "type": "floor",
            "area_m2": self.length_m * self.width_m, "azimuth_deg": 180.0,
            "tilt_deg": 180.0,   # faces downward
            "layers": [(self.floor_material, self.floor_thickness_m)],
        })
        return surfaces

    @property
    def volume_m3(self) -> float:
        return self.length_m * self.width_m * self.height_m

    def summary(self) -> dict:
        s = self.surfaces()
        return {
            "dimensions_m": [self.length_m, self.width_m, self.height_m],
            "orientation_deg": self.orientation_deg,
            "volume_m3": self.volume_m3,
            "surface_count": len(s),
            "opaque_area_m2": sum(x["area_m2"] for x in s if x["type"] in ("wall", "roof", "floor")),
            "glazing_area_m2": sum(x["area_m2"] for x in s if x["type"] == "window"),
            "window_to_floor_ratio": (
                sum(x["area_m2"] for x in s if x["type"] == "window") / (self.length_m * self.width_m)
                if self.window else 0.0),
        }
