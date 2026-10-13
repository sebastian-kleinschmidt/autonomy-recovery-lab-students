from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .models import ActorState, EgoState
from .scenario import Scenario


Point = tuple[float, float]
Polygon = tuple[Point, ...]


@dataclass(frozen=True)
class PerceivedActor:
    state: ActorState
    status: str
    visible_fraction: float
    track_age_s: float

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "visible_fraction": round(self.visible_fraction, 2),
            "track_age_s": round(self.track_age_s, 2),
        }


@dataclass(frozen=True)
class PerceptionSnapshot:
    actors: tuple[ActorState, ...]
    actor_metadata: dict[str, dict[str, object]]
    ground_truth_status: dict[str, str]
    visibility: dict[str, object]


@dataclass
class _Track:
    state: ActorState
    last_seen_s: float
    visible_fraction: float


def _copy_actor(actor: ActorState, *, age_s: float = 0.0) -> ActorState:
    return ActorState(
        spec=actor.spec,
        s_m=actor.s_m + actor.spec.direction * actor.speed_mps * age_s,
        d_m=actor.d_m,
        speed_mps=actor.speed_mps,
    )


def _segment_intersection_parameter(start: Point, end: Point, a: Point, b: Point) -> float | None:
    ray = (end[0] - start[0], end[1] - start[1])
    edge = (b[0] - a[0], b[1] - a[1])
    denominator = ray[0] * edge[1] - ray[1] * edge[0]
    if abs(denominator) < 1e-9:
        return None
    offset = (a[0] - start[0], a[1] - start[1])
    ray_parameter = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
    edge_parameter = (offset[0] * ray[1] - offset[1] * ray[0]) / denominator
    if 1e-5 < ray_parameter < 1.0 - 1e-5 and -1e-9 <= edge_parameter <= 1.0 + 1e-9:
        return ray_parameter
    return None


def _polygon_hit(start: Point, end: Point, polygon: Polygon) -> float | None:
    """Kleinster Strahlparameter, an dem die Sichtlinie das Polygon schneidet."""
    hits = [
        parameter
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
        if (parameter := _segment_intersection_parameter(start, end, a, b)) is not None
    ]
    return min(hits) if hits else None


def _point_inside(point: Point, polygon: Polygon) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > point[1]) != (y2 > point[1]):
            if point[0] < x1 + (point[1] - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def _line_blocked(start: Point, end: Point, polygons: Iterable[Polygon]) -> bool:
    for polygon in polygons:
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            if _segment_intersection_parameter(start, end, a, b) is not None:
                return True
    return False


def _silhouette_points(center: Point, polygon: Polygon, samples_per_edge: int = 4) -> Polygon:
    """Abtastpunkte der Objektsilhouette: Mittelpunkt, Ecken und Kantenstuetzen.

    Nur Mittelpunkt und vier Ecken zu pruefen, macht den Sichtbarkeitsanteil
    fuenfstufig; eine einzelne streifende Sichtlinie auf genau eine Ecke ergibt
    dann bereits 20 Prozent. Mit Stuetzpunkten entlang der Kanten beschreibt der
    Anteil tatsaechlich, wie viel der Silhouette frei liegt.
    """
    points = [center]
    for index, corner in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        for step in range(samples_per_edge):
            share = step / samples_per_edge
            points.append(
                (
                    corner[0] + (following[0] - corner[0]) * share,
                    corner[1] + (following[1] - corner[1]) * share,
                )
            )
    return tuple(points)


def _rectangle(center: Point, heading: float, length_m: float, width_m: float) -> Polygon:
    half_length = length_m / 2.0
    half_width = width_m / 2.0
    cos_heading = math.cos(heading)
    sin_heading = math.sin(heading)
    result = []
    for longitudinal, lateral in (
        (half_length, half_width),
        (half_length, -half_width),
        (-half_length, -half_width),
        (-half_length, half_width),
    ):
        result.append(
            (
                center[0] + longitudinal * cos_heading - lateral * sin_heading,
                center[1] + longitudinal * sin_heading + lateral * cos_heading,
            )
        )
    return tuple(result)


class PerceptionModel:
    """Deterministic 2D line-of-sight model with short-lived object tracks."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self._tracks: dict[str, _Track] = {}
        self._buildings = self._building_polygons()

    def _building_polygons(self) -> tuple[Polygon, ...]:
        context = self.scenario.road.context or {}
        buildings = context.get("buildings", [])
        result: list[Polygon] = []
        if not isinstance(buildings, list):
            return ()
        for building in buildings:
            if not isinstance(building, dict):
                continue
            height = building.get("height_m", 0.0)
            min_height = building.get("min_height_m", 0.0)
            points = building.get("points_xy")
            if not isinstance(height, (int, float)) or float(height) <= 1.4:
                continue
            if isinstance(min_height, (int, float)) and float(min_height) > 1.4:
                continue
            if not isinstance(points, list) or len(points) < 3:
                continue
            polygon = tuple(
                (float(point[0]), float(point[1]))
                for point in points
                if isinstance(point, list) and len(point) >= 2
            )
            if len(polygon) >= 3:
                result.append(polygon)
        return tuple(result)

    def _actor_geometry(self, actor: ActorState) -> tuple[Point, Polygon]:
        x_m, y_m, road_heading = self.scenario.road.to_xy(actor.s_m, actor.d_m)
        heading = road_heading if actor.spec.direction == 1 else road_heading + math.pi
        center = (x_m, y_m)
        return center, _rectangle(
            center,
            heading,
            actor.spec.length_m,
            actor.spec.width_m,
        )

    def _sensor_origins(self, ego: EgoState) -> tuple[Point, ...]:
        """Die vier Ecksensoren des Egos.

        Der seitliche Versatz ist kein Detail: Nur weil die Sensoren an den
        Karosserieecken sitzen, kann das Ego an einem stehenden Fahrzeug vorbei
        auf die Gegenfahrbahn blicken - genau darauf beruht die Freigabe- und
        Ablehnungsentscheidung der Szenariomatrix. Wer die Urspruenge auf die
        Fahrzeugmitte zusammenzieht, macht die Gegenfahrbahn hinter jedem
        Blocker blind. Der Preis dieses Modells: Ein Ecksensor sieht mehr als
        die Kamera der 3D-Ansicht, die auf der Mittelachse sitzt. Die
        Sichtflaeche im UI wird deshalb aus denselben Urspruengen gezeichnet.
        """
        x_m, y_m, heading = self.scenario.road.to_xy(ego.s_m, ego.d_m)
        forward = (math.cos(heading), math.sin(heading))
        left = (-math.sin(heading), math.cos(heading))
        longitudinal = ego.length_m * 0.43
        lateral = ego.width_m * 0.42
        return (
            (x_m + forward[0] * longitudinal + left[0] * lateral,
             y_m + forward[1] * longitudinal + left[1] * lateral),
            (x_m + forward[0] * longitudinal - left[0] * lateral,
             y_m + forward[1] * longitudinal - left[1] * lateral),
            (x_m - forward[0] * longitudinal + left[0] * lateral,
             y_m - forward[1] * longitudinal + left[1] * lateral),
            (x_m - forward[0] * longitudinal - left[0] * lateral,
             y_m - forward[1] * longitudinal - left[1] * lateral),
        )

    def _nearby_buildings(self, origins: tuple[Point, ...], range_m: float) -> tuple[Polygon, ...]:
        center_x = sum(point[0] for point in origins) / len(origins)
        center_y = sum(point[1] for point in origins) / len(origins)
        margin = range_m + 20.0
        return tuple(
            polygon
            for polygon in self._buildings
            if min(point[0] for point in polygon) <= center_x + margin
            and max(point[0] for point in polygon) >= center_x - margin
            and min(point[1] for point in polygon) <= center_y + margin
            and max(point[1] for point in polygon) >= center_y - margin
        )

    @staticmethod
    def _inside_fov(
        point: Point,
        origins: tuple[Point, ...],
        heading: float,
        horizontal_fov_deg: float,
    ) -> bool:
        half_fov = math.radians(horizontal_fov_deg) / 2.0
        for origin in origins:
            if horizontal_fov_deg >= 360.0:
                return True
            bearing = math.atan2(point[1] - origin[1], point[0] - origin[0])
            delta = (bearing - heading + math.pi) % (2.0 * math.pi) - math.pi
            if abs(delta) <= half_fov:
                return True
        return False

    @classmethod
    def _point_visible(
        cls,
        point: Point,
        origins: tuple[Point, ...],
        range_m: float,
        occluders: Iterable[Polygon],
        heading: float,
        horizontal_fov_deg: float,
    ) -> bool:
        return any(
            math.dist(origin, point) <= range_m
            and cls._inside_fov(point, (origin,), heading, horizontal_fov_deg)
            and not _line_blocked(origin, point, occluders)
            for origin in origins
        )

    def _visibility_report(
        self,
        ego: EgoState,
        origins: tuple[Point, ...],
        actor_polygons: dict[str, Polygon],
        buildings: tuple[Polygon, ...],
        effective_range_m: float,
        heading: float,
    ) -> dict[str, object]:
        required = self.scenario.minimum_visibility_m
        step_m = 5.0
        sampled_to = min(required, effective_range_m)
        blocked_at: float | None = None
        distance = step_m
        all_occluders = buildings + tuple(actor_polygons.values())
        while distance <= sampled_to + 1e-6:
            point_x, point_y, _ = self.scenario.road.to_xy(
                ego.s_m + distance,
                -self.scenario.ego_lane_d_m,
            )
            if not self._point_visible(
                (point_x, point_y),
                origins,
                effective_range_m,
                all_occluders,
                heading,
                self.scenario.perception_horizontal_fov_deg,
            ):
                blocked_at = distance
                break
            distance += step_m
        lane_visible_m = min(
            effective_range_m,
            required if blocked_at is None else max(0.0, blocked_at - step_m),
        )
        atmospheric_ok = self.scenario.visibility_m >= required
        occlusion_ok = blocked_at is None and effective_range_m >= required
        limitations = []
        if not atmospheric_ok:
            limitations.append("atmosphere")
        if not occlusion_ok:
            limitations.append("occlusion")
        return {
            "sufficient": atmospheric_ok and occlusion_ok,
            "available_m": round(min(self.scenario.visibility_m, lane_visible_m), 2),
            "required_m": required,
            "sensor_range_m": self.scenario.perception_range_m,
            "effective_range_m": round(effective_range_m, 2),
            "horizontal_fov_deg": self.scenario.perception_horizontal_fov_deg,
            "target_lane_visible_m": round(lane_visible_m, 2),
            # Das UI zeichnet die freie Sichtflaeche aus genau diesen Punkten.
            # Sonst zeigt das Overlay die Sicht der Fahrzeugmitte, waehrend die
            # Objektstatus aus der Ecksensorik stammen - und ein Objekt gilt als
            # teilweise sichtbar, obwohl es im Bild im Schatten liegt.
            "sensor_origins_xy": [[round(x, 3), round(y, 3)] for x, y in origins],
            "occluded_from_m": round(blocked_at, 2) if blocked_at is not None else None,
            "limited_by": limitations,
        }

    def lane_occlusions(
        self,
        ego: EgoState,
        actors: tuple[ActorState, ...],
        lane_d_m: float,
        s_from_m: float,
        s_to_m: float,
        step_m: float = 2.0,
    ) -> list[dict[str, object]]:
        """Verdeckte Abschnitte einer Spur (relativ zum Ego) mit dem verdeckenden Objekt.

        Gleiche Sichtlinien wie ``observe``; ein Abschnitt jenseits der Reichweite gilt nicht
        als verdeckt, sondern als nicht abgedeckt (das meldet der Aufrufer getrennt).
        """
        effective_range = min(self.scenario.perception_range_m, self.scenario.visibility_m)
        origins = self._sensor_origins(ego)
        _, _, heading = self.scenario.road.to_xy(ego.s_m, ego.d_m)
        fov = self.scenario.perception_horizontal_fov_deg
        occluders: list[tuple[str, Polygon]] = [
            ("BUILDING", polygon) for polygon in self._nearby_buildings(origins, effective_range)
        ]
        occluders += [(actor.spec.actor_id, self._actor_geometry(actor)[1]) for actor in actors]
        intervals: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        offset = s_from_m
        while offset <= s_to_m + 1e-6:
            x_m, y_m, _ = self.scenario.road.to_xy(ego.s_m + offset, lane_d_m)
            point = (x_m, y_m)
            reachable = [
                origin
                for origin in origins
                if math.dist(origin, point) <= effective_range
                and self._inside_fov(point, (origin,), heading, fov)
            ]
            cause: str | None = None
            if reachable:
                clear = False
                for origin in sorted(reachable, key=lambda item: math.dist(item, point)):
                    blocking = [
                        (parameter, name)
                        for name, polygon in occluders
                        if not _point_inside(point, polygon)
                        and (parameter := _polygon_hit(origin, point, polygon)) is not None
                    ]
                    if not blocking:
                        clear = True
                        break
                    if cause is None:
                        cause = min(blocking)[1]
                if clear:
                    cause = None
            if cause is not None and current is not None and current["cause"] == cause:
                current["to_m"] = offset
            elif cause is not None:
                current = {"from_m": offset, "to_m": offset, "cause": cause}
                intervals.append(current)
            else:
                current = None
            offset += step_m
        return intervals

    def forget(self, actor_id: str) -> None:
        """Vergisst den Track eines Objekts, das als nicht existent erkannt wurde."""
        self._tracks.pop(actor_id, None)

    def observe(
        self,
        time_s: float,
        ego: EgoState,
        actors: tuple[ActorState, ...],
    ) -> PerceptionSnapshot:
        effective_range = min(self.scenario.perception_range_m, self.scenario.visibility_m)
        origins = self._sensor_origins(ego)
        _, _, ego_heading = self.scenario.road.to_xy(ego.s_m, ego.d_m)
        buildings = self._nearby_buildings(origins, effective_range)
        geometries = {actor.spec.actor_id: self._actor_geometry(actor) for actor in actors}
        polygons = {actor_id: geometry[1] for actor_id, geometry in geometries.items()}
        perceived: list[PerceivedActor] = []
        ground_truth_status: dict[str, str] = {}

        for actor in actors:
            actor_id = actor.spec.actor_id
            center, polygon = geometries[actor_id]
            target_points = _silhouette_points(center, polygon)
            other_polygons = buildings + tuple(
                other_polygon
                for other_id, other_polygon in polygons.items()
                if other_id != actor_id
            )
            in_range = min(math.dist(origin, center) for origin in origins) <= effective_range
            inside_fov = any(
                self._inside_fov(
                    point,
                    origins,
                    ego_heading,
                    self.scenario.perception_horizontal_fov_deg,
                )
                for point in target_points
            )
            visible_count = (
                sum(
                    self._point_visible(
                        point,
                        origins,
                        effective_range,
                        other_polygons,
                        ego_heading,
                        self.scenario.perception_horizontal_fov_deg,
                    )
                    for point in target_points
                )
                if in_range and inside_fov
                else 0
            )
            fraction = visible_count / len(target_points)
            # Ein freigelegter Splitter der Silhouette ist noch keine Detektion:
            # unterhalb der Mindestsichtflaeche bleibt das Objekt verdeckt und
            # lebt hoechstens als kurzlebiger Track weiter.
            detectable = fraction >= self.scenario.perception_min_detectable_fraction
            if fraction >= 0.6:
                status = "visible"
            elif detectable:
                status = "partially_visible"
            elif in_range:
                status = "occluded" if inside_fov else "outside_fov"
            else:
                status = "out_of_range"
            ground_truth_status[actor_id] = status

            if detectable:
                copied = _copy_actor(actor)
                self._tracks[actor_id] = _Track(copied, time_s, fraction)
                perceived.append(PerceivedActor(copied, status, fraction, 0.0))
                continue
            track = self._tracks.get(actor_id)
            if track is None:
                continue
            age_s = max(0.0, time_s - track.last_seen_s)
            if age_s <= self.scenario.perception_track_memory_s:
                perceived.append(
                    PerceivedActor(
                        _copy_actor(track.state, age_s=age_s),
                        "tracked",
                        0.0,
                        age_s,
                    )
                )
            else:
                self._tracks.pop(actor_id, None)

        metadata = {item.state.spec.actor_id: item.metadata() for item in perceived}
        return PerceptionSnapshot(
            actors=tuple(item.state for item in perceived),
            actor_metadata=metadata,
            ground_truth_status=ground_truth_status,
            visibility=self._visibility_report(
                ego,
                origins,
                polygons,
                buildings,
                effective_range,
                ego_heading,
            ),
        )
