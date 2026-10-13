from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Road:
    name: str
    points_xy: tuple[tuple[float, float], ...]
    lane_width_m: float
    center_marking: str
    source: str
    source_url: str
    osm_way_id: int | None = None
    context: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if len(self.points_xy) < 2:
            raise ValueError("Eine Strasse braucht mindestens zwei Punkte")
        if self.lane_width_m <= 0:
            raise ValueError("lane_width_m muss positiv sein")

    @property
    def length_m(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points_xy, self.points_xy[1:]))

    def to_xy(self, s_m: float, d_m: float = 0.0) -> tuple[float, float, float]:
        """Route-Koordinaten (Laenge/Quer) in lokales XY und Richtung umrechnen."""
        remaining = min(max(s_m, 0.0), self.length_m)
        for a, b in zip(self.points_xy, self.points_xy[1:]):
            segment = math.dist(a, b)
            if segment < 1e-9:
                continue
            if remaining <= segment:
                f = remaining / segment
                x = a[0] + f * (b[0] - a[0])
                y = a[1] + f * (b[1] - a[1])
                heading = math.atan2(b[1] - a[1], b[0] - a[0])
                return x - math.sin(heading) * d_m, y + math.cos(heading) * d_m, heading
            remaining -= segment
        a, b = self.points_xy[-2], self.points_xy[-1]
        heading = math.atan2(b[1] - a[1], b[0] - a[0])
        return b[0] - math.sin(heading) * d_m, b[1] + math.cos(heading) * d_m, heading

    def project(self, x_m: float, y_m: float) -> tuple[float, float, float, float]:
        """XY auf die Referenzlinie projizieren: s, d, Richtung, Abstand."""
        best: tuple[float, float, float, float] | None = None
        traversed = 0.0
        for a, b in zip(self.points_xy, self.points_xy[1:]):
            vx, vy = b[0] - a[0], b[1] - a[1]
            length_sq = vx * vx + vy * vy
            if length_sq < 1e-12:
                continue
            fraction = min(1.0, max(0.0, ((x_m - a[0]) * vx + (y_m - a[1]) * vy) / length_sq))
            px, py = a[0] + fraction * vx, a[1] + fraction * vy
            heading = math.atan2(vy, vx)
            nx, ny = -math.sin(heading), math.cos(heading)
            lateral = (x_m - px) * nx + (y_m - py) * ny
            distance = math.hypot(x_m - px, y_m - py)
            along = traversed + fraction * math.sqrt(length_sq)
            candidate = (along, lateral, heading, distance)
            if best is None or distance < best[3]:
                best = candidate
            traversed += math.sqrt(length_sq)
        if best is None:
            raise ValueError("Strasse enthaelt keine projizierbaren Segmente")
        return best


def _project_coordinates(coordinates: list[list[float]]) -> tuple[tuple[float, float], ...]:
    """Kleine WGS84-Ausschnitte ausreichend genau in lokale Meter projizieren."""
    if len(coordinates) < 2:
        raise ValueError("LineString enthaelt zu wenige Koordinaten")
    lon0, lat0 = coordinates[0]
    lat_ref = math.radians(sum(p[1] for p in coordinates) / len(coordinates))
    points = []
    for lon, lat in coordinates:
        x = (lon - lon0) * 111_320.0 * math.cos(lat_ref)
        y = (lat - lat0) * 110_540.0
        points.append((x, y))
    return tuple(points)


def load_road(
    path: Path,
    *,
    lane_width_m: float,
    center_marking: str,
    context_path: Path | None = None,
) -> Road:
    data = json.loads(path.read_text(encoding="utf8"))
    feature = data["features"][0] if data.get("type") == "FeatureCollection" else data
    if feature.get("geometry", {}).get("type") != "LineString":
        raise ValueError(f"{path}: Es wird ein GeoJSON-LineString erwartet")
    props = feature.get("properties", {})
    return Road(
        name=str(props.get("name", path.stem)),
        points_xy=_project_coordinates(feature["geometry"]["coordinates"]),
        lane_width_m=lane_width_m,
        center_marking=center_marking,
        source=str(props.get("source", "unknown")),
        source_url=str(props.get("source_url", "")),
        osm_way_id=int(props["osm_way_id"]) if props.get("osm_way_id") else None,
        context=json.loads(context_path.read_text(encoding="utf8")) if context_path else None,
    )
