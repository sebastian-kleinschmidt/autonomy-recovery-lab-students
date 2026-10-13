"""Objektliste eines Tracking-Stacks mit Messunsicherheit (Wahrnehmungsmodell ``tracked``).

Das Format lehnt sich an ``TrackedObjects`` aus Autoware (autoware_perception_msgs) an:
Existenzwahrscheinlichkeit, Klassenverteilung, Pose und Geschwindigkeit mit
Standardabweichung, Form und Track-Zustand. Koordinaten stehen im Fahrzeugsystem
``base_link``: Ursprung Fahrzeugmitte, x nach vorn, y nach links.

Anders als die Lagewerkzeuge des Modells ``exact`` liefert dieses Modul **Messungen,
keine Urteile**: kein ``conflict``, keine Zeit bis zum Zusammenstoss, kein noetiger
Seitenabstand. Daraus rechnet der Agent selbst (oder ``recovery_helpers``).

Das Rauschen ist ueber Szenario-Seed, Objekt und Zeitpunkt deterministisch: Zwei Aufrufe
zum selben Zeitpunkt liefern dasselbe, zwei Sitzungen zu verschiedenen Zeiten nicht.
Die Track-IDs sind undurchsichtig und verraten nichts ueber das Szenario.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .models import ActorState, EgoState
from .perception import PerceptionModel, PerceptionSnapshot
from .scenario import Scenario

FRAME_ID = "base_link"
PERCEPTION_LATENCY_S = 0.08
MEASUREMENT_PERIOD_S = 0.5
CONFIRMED_AFTER_S = 1.0
STATIONARY_BELOW_MPS = 0.3
MAP_SAMPLE_STEP_M = 10.0
MAP_BEHIND_M = 20.0
MAP_AHEAD_M = 60.0
COVERAGE_STEP_M = 2.0
COVERAGE_BEHIND_M = 30.0
RADAR_RANGE_M = 150.0
RADAR_HINT_PROBABILITY = 0.6

LABELS = {
    "car": "CAR",
    "van": "VAN",
    "truck": "TRUCK",
    "bus": "BUS",
    "bicycle": "BICYCLE",
    "pedestrian": "PEDESTRIAN",
    "animal": "ANIMAL",
    "train": "TRAIN",
    "barrier": "BARRIER",
    "beacon": "TRAFFIC_CONE",
    "trash_bin": "TRASH_BIN",
    "debris": "DEBRIS",
}
# Typische Verwechslungen eines Klassifikators.
CONFUSABLE = {
    "CAR": ("VAN",),
    "VAN": ("CAR", "TRUCK"),
    "TRUCK": ("VAN", "BUS"),
    "BUS": ("TRUCK",),
    "BICYCLE": ("PEDESTRIAN",),
    "PEDESTRIAN": ("BICYCLE",),
    "ANIMAL": ("PEDESTRIAN", "UNKNOWN"),
    "TRAIN": ("TRUCK",),
    "BARRIER": ("TRAFFIC_CONE",),
    "TRAFFIC_CONE": ("TRASH_BIN",),
    "TRASH_BIN": ("TRAFFIC_CONE",),
    "DEBRIS": ("UNKNOWN",),
}
HEIGHTS_M = {
    "car": 1.5, "van": 2.4, "truck": 3.2, "bus": 3.0, "bicycle": 1.7, "pedestrian": 1.75,
    "animal": 1.2, "train": 3.8, "barrier": 1.0, "beacon": 1.0, "trash_bin": 1.1, "debris": 0.3,
}
RADAR_KINDS = frozenset({"car", "van", "truck", "bus", "train"})


@dataclass
class TrackingModel:
    """Langlebiger Teil des Trackers: wann ein Objekt zuerst erfasst wurde."""

    scenario: Scenario
    first_seen_s: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        base = random.Random(f"track-ids:{self.scenario.seed}").randint(100, 899)
        self._ids = {
            actor.actor_id: f"trk_{base + 7 * index:04d}"
            for index, actor in enumerate(self.scenario.actors)
        }

    def track_id(self, actor_id: str) -> str:
        return self._ids.get(actor_id) or f"trk_{random.Random(actor_id).randint(1000, 8999):04d}"

    def update(self, time_s: float, snapshot: PerceptionSnapshot) -> None:
        current = {actor.spec.actor_id for actor in snapshot.actors}
        for actor_id in current:
            self.first_seen_s.setdefault(actor_id, time_s)
        for actor_id in list(self.first_seen_s):
            if actor_id not in current:
                del self.first_seen_s[actor_id]


@dataclass(frozen=True)
class TrackingAccess:
    """Sicht des Agenten auf Tracker, Sichtabdeckung und Karte zu einem Zeitpunkt."""

    model: TrackingModel
    perception_model: PerceptionModel
    time_s: float
    ego: EgoState
    ego_pose: tuple[float, float, float]
    snapshot: PerceptionSnapshot
    # Physische Lage aller Akteure (fuer Sichtlinien und Radar durch Verdeckungen).
    all_actors: tuple[ActorState, ...]
    sensors: tuple[dict[str, object], ...] = ()

    @property
    def scenario(self) -> Scenario:
        return self.model.scenario

    # ------------------------------------------------------------ Hilfen

    def _rng(self, *key: object) -> random.Random:
        bucket = round(self.time_s / MEASUREMENT_PERIOD_S)
        return random.Random(":".join(str(item) for item in (self.scenario.seed, bucket, *key)))

    def _to_ego(self, x_m: float, y_m: float) -> tuple[float, float]:
        ego_x, ego_y, yaw = self.ego_pose
        dx, dy = x_m - ego_x, y_m - ego_y
        return (
            math.cos(yaw) * dx + math.sin(yaw) * dy,
            -math.sin(yaw) * dx + math.cos(yaw) * dy,
        )

    def _route_point_ego(self, s_m: float, d_m: float) -> tuple[float, float, float]:
        x_m, y_m, heading = self.scenario.road.to_xy(s_m, d_m)
        local_x, local_y = self._to_ego(x_m, y_m)
        return local_x, local_y, heading

    def _lanes(self) -> list[tuple[str, str, float, float]]:
        """(Name, Richtung, Mitte d, Breite) aller befahrbaren Flaechen."""
        width = self.scenario.road.lane_width_m
        own = self.scenario.ego_lane_d_m
        lanes = [("EGO_LANE", "SAME", own, width), ("ONCOMING_LANE", "OPPOSITE", -own, width)]
        if self.scenario.shoulder_m > 0:
            shoulder = self.scenario.shoulder_m
            lanes.append(("SHOULDER", "SAME", own - width / 2.0 - shoulder / 2.0, shoulder))
        return lanes

    def _lane_association(self, d_m: float, std_m: float) -> dict[str, object]:
        """Wahrscheinlichste Spur aus der verrauschten Querlage (Kartenabgleich)."""
        best: tuple[str, float] = ("OFF_ROAD", 0.0)
        for name, _, center, width in self._lanes():
            margin = width / 2.0 - abs(d_m - center)
            probability = 0.5 * (1.0 + math.erf(margin / (math.sqrt(2.0) * max(std_m, 0.05))))
            if probability > best[1]:
                best = (name, probability)
        if best[1] < 0.5:
            return {"lane": "OFF_ROAD", "probability": round(1.0 - best[1], 2)}
        return {"lane": best[0], "probability": round(best[1], 2)}

    # ------------------------------------------------------- Objektliste

    def _classification(self, actor: ActorState, visible_fraction: float) -> list[dict[str, object]]:
        true_label = LABELS.get(actor.spec.kind, "UNKNOWN")
        noise = self.scenario.perception_noise
        # Eine Verwechslung bleibt fuer den ganzen Track bestehen (nicht je Messung).
        persistent = random.Random(f"class:{self.scenario.seed}:{actor.spec.actor_id}")
        top = true_label
        if true_label in CONFUSABLE and persistent.random() < noise.class_confusion:
            top = persistent.choice(CONFUSABLE[true_label])
        quality = actor.spec.confidence * (0.6 + 0.4 * visible_fraction)
        rng = self._rng("class", actor.spec.actor_id)
        top_probability = max(0.34, min(0.97, quality - rng.uniform(0.0, 0.12)))
        others = [label for label in (true_label, *CONFUSABLE.get(true_label, ())) if label != top]
        others = list(dict.fromkeys(others + ["UNKNOWN"]))[:2]
        rest = 1.0 - top_probability
        split = rng.uniform(0.55, 0.8)
        result = [{"label": top, "probability": round(top_probability, 2)}]
        result.append({"label": others[0], "probability": round(rest * split, 2)})
        if len(others) > 1:
            result.append({"label": others[1], "probability": round(rest * (1.0 - split), 2)})
        return result

    def _object(
        self,
        actor: ActorState,
        status: str,
        visible_fraction: float,
        last_update_s: float,
        *,
        radar_only: bool = False,
    ) -> dict[str, object]:
        noise = self.scenario.perception_noise
        rng = self._rng("pose", actor.spec.actor_id, radar_only)
        true_x, true_y, road_heading = self._route_point_ego(actor.s_m, actor.d_m)
        distance = math.hypot(true_x, true_y)
        position_std = noise.position_std_m + noise.position_std_per_10m_m * distance / 10.0
        std_x, std_y = position_std, position_std * 0.7
        if radar_only:
            std_x, std_y = 1.5 + 0.02 * distance, 0.6
        elif status == "tracked":
            std_x, std_y = std_x + 0.5 * last_update_s, std_y + 0.3 * last_update_s
        x_m = true_x + rng.gauss(0.0, std_x)
        y_m = true_y + rng.gauss(0.0, std_y)
        _, _, ego_yaw = self.ego_pose
        heading = road_heading + (0.0 if actor.spec.direction == 1 else math.pi)
        yaw = math.atan2(math.sin(heading - ego_yaw), math.cos(heading - ego_yaw))
        velocity_std = noise.velocity_std_mps * (3.0 if radar_only else 1.0)
        speed = actor.speed_mps
        velocity = (
            speed * math.cos(yaw) + rng.gauss(0.0, velocity_std),
            speed * math.sin(yaw) + rng.gauss(0.0, velocity_std),
        )
        measured_speed = math.hypot(*velocity)
        # Der Bewegungszustand stammt aus dem ueber viele Messungen gefilterten Track;
        # nur die einzelne Geschwindigkeitsmessung ist verrauscht.
        if radar_only:
            motion = "UNKNOWN"
        elif speed < STATIONARY_BELOW_MPS:
            motion = "STATIONARY"
        else:
            motion = "MOVING"
        actor_id = actor.spec.actor_id
        kinematics: dict[str, object] = {
            "position_m": [round(x_m, 2), round(y_m, 2)],
            "position_std_m": [round(std_x, 2), round(std_y, 2)],
            "velocity_mps": [round(velocity[0], 2), round(velocity[1], 2)],
            "velocity_std_mps": [round(velocity_std, 2), round(velocity_std, 2)],
            "motion_state": motion,
        }
        if not radar_only:
            yaw_std = noise.yaw_std_rad * (1.0 + 2.0 * (1.0 - visible_fraction))
            kinematics["yaw_rad"] = round(yaw + rng.gauss(0.0, yaw_std), 3)
            kinematics["yaw_std_rad"] = round(yaw_std, 3)
        length, width = actor.spec.length_m, actor.spec.width_m
        height = HEIGHTS_M.get(actor.spec.kind, 1.5)
        complete = radar_only or visible_fraction >= 0.6 or status == "tracked"
        if radar_only:
            shape: dict[str, object] = {"type": "BOUNDING_BOX", "dimensions_m": [4.5, 1.8, 1.5], "extent": "ASSUMED"}
        elif not complete:
            seen = 0.5 + 0.5 * visible_fraction
            shape = {
                "type": "BOUNDING_BOX",
                "dimensions_m": [round(length * seen, 1), round(width, 1), round(height, 1)],
                "extent": "PARTIAL",
            }
        else:
            shape = {
                "type": "BOUNDING_BOX",
                "dimensions_m": [
                    round(length + rng.gauss(0.0, 0.1), 1),
                    round(width + rng.gauss(0.0, 0.05), 1),
                    round(height, 1),
                ],
                "extent": "FULL",
            }
        if radar_only:
            existence = rng.uniform(0.3, 0.5)
            classification = [
                {"label": "UNKNOWN", "probability": 0.55},
                {"label": "CAR", "probability": 0.45},
            ]
            track_status, sensors = "TENTATIVE", ["radar"]
            age_s = rng.uniform(0.5, 2.5)
        else:
            classification = self._classification(actor, max(visible_fraction, 0.3))
            age_s = self.time_s - self.model.first_seen_s.get(actor_id, self.time_s)
            if status == "tracked":
                existence = max(0.2, 0.85 * math.exp(-last_update_s / 2.0))
                track_status, sensors = "COASTING", []
            else:
                existence = min(0.99, actor.spec.confidence * (0.85 + 0.14 * visible_fraction))
                track_status = "CONFIRMED" if age_s >= CONFIRMED_AFTER_S else "TENTATIVE"
                sensors = ["lidar", "camera_front_wide" if x_m >= 0 else "camera_rear"]
                if actor.spec.kind in RADAR_KINDS or measured_speed >= 1.0:
                    sensors.insert(1, "radar")
        world_x, world_y = self._from_ego(x_m, y_m)
        _, d_m, _, _ = self.scenario.road.project(world_x, world_y)
        return {
            "track_id": self.model.track_id(actor_id),
            "existence_probability": round(existence, 2),
            "classification": classification,
            "kinematics": kinematics,
            "shape": shape,
            "lane_association": self._lane_association(d_m, std_y),
            "track": {
                "age_s": round(age_s, 1),
                "last_update_s_ago": round(last_update_s if not radar_only else rng.uniform(0.2, 1.5), 1),
                "status": track_status,
                "contributing_sensors": sensors,
            },
        }

    def _from_ego(self, x_m: float, y_m: float) -> tuple[float, float]:
        ego_x, ego_y, yaw = self.ego_pose
        return (
            ego_x + math.cos(yaw) * x_m - math.sin(yaw) * y_m,
            ego_y + math.sin(yaw) * x_m + math.cos(yaw) * y_m,
        )

    def _ghosts(self) -> list[dict[str, object]]:
        rate = self.scenario.perception_noise.false_positive_rate_hz
        if rate <= 0:
            return []
        # Ein Geist lebt eine Sekunde; je Sekundenfenster entscheidet der Zufall einmal.
        window = math.floor(self.time_s)
        rng = random.Random(f"ghost:{self.scenario.seed}:{window}")
        if rng.random() >= min(1.0, rate):
            return []
        x_m, y_m = rng.uniform(8.0, 45.0), rng.uniform(-5.0, 5.0)
        world_x, world_y = self._from_ego(x_m, y_m)
        _, d_m, _, _ = self.scenario.road.project(world_x, world_y)
        return [
            {
                "track_id": f"trk_9{rng.randint(0, 999):03d}",
                "existence_probability": round(rng.uniform(0.15, 0.45), 2),
                "classification": [
                    {"label": "UNKNOWN", "probability": 0.7},
                    {"label": rng.choice(("CAR", "PEDESTRIAN", "DEBRIS")), "probability": 0.3},
                ],
                "kinematics": {
                    "position_m": [round(x_m, 2), round(y_m, 2)],
                    "position_std_m": [0.8, 0.5],
                    "velocity_mps": [round(rng.gauss(0.0, 0.5), 2), round(rng.gauss(0.0, 0.3), 2)],
                    "velocity_std_mps": [0.8, 0.8],
                    "motion_state": "UNKNOWN",
                },
                "shape": {"type": "BOUNDING_BOX", "dimensions_m": [round(rng.uniform(0.4, 2.0), 1), round(rng.uniform(0.4, 1.5), 1), 0.8], "extent": "PARTIAL"},
                "lane_association": self._lane_association(d_m, 0.5),
                "track": {
                    "age_s": round(self.time_s - window, 1),
                    "last_update_s_ago": 0.0,
                    "status": "TENTATIVE",
                    "contributing_sensors": [rng.choice(("radar", "camera_front_wide", "lidar"))],
                },
            }
        ]

    def tracked_objects(self) -> dict[str, object]:
        noise = self.scenario.perception_noise
        objects = []
        perceived_ids = set()
        for actor in self.snapshot.actors:
            actor_id = actor.spec.actor_id
            perceived_ids.add(actor_id)
            metadata = self.snapshot.actor_metadata.get(actor_id, {})
            status = str(metadata.get("status", "visible"))
            if status != "tracked" and self._rng("dropout", actor_id).random() < noise.dropout_probability:
                continue
            objects.append(
                self._object(
                    actor,
                    status,
                    float(metadata.get("visible_fraction", 1.0)),
                    float(metadata.get("track_age_s", 0.0)),
                )
            )
        if noise.radar_through_occluders:
            for actor in self.all_actors:
                actor_id = actor.spec.actor_id
                if actor_id in perceived_ids or actor.spec.kind not in RADAR_KINDS:
                    continue
                if self.snapshot.ground_truth_status.get(actor_id) != "occluded":
                    continue
                local_x, local_y, _ = self._route_point_ego(actor.s_m, actor.d_m)
                if math.hypot(local_x, local_y) > RADAR_RANGE_M or local_x <= 0:
                    continue
                if self._rng("radar", actor_id).random() < RADAR_HINT_PROBABILITY:
                    objects.append(self._object(actor, "occluded", 0.0, 0.0, radar_only=True))
        objects.extend(self._ghosts())
        objects.sort(key=lambda item: math.hypot(*item["kinematics"]["position_m"]))  # type: ignore[index]
        return {
            "header": {
                "stamp_s": round(self.time_s, 1),
                "frame_id": FRAME_ID,
                "source": "fusion_tracker",
                "latency_s": PERCEPTION_LATENCY_S,
            },
            "ego": {
                "speed_mps": round(self.ego.speed_mps, 2),
                "dimensions_m": [self.ego.length_m, self.ego.width_m],
            },
            "objects": objects,
        }

    # -------------------------------------------------- Sichtabdeckung

    def sensor_coverage(self) -> dict[str, object]:
        scenario = self.scenario
        effective = min(scenario.perception_range_m, scenario.visibility_m)
        by_name = {str(item["name"]): item for item in self.sensors}

        def status(name: str) -> str:
            item = by_name.get(name)
            if item is None:
                return "OK"
            if item.get("status") != "AVAILABLE":
                return "NO_DATA"
            return "DEGRADED" if float(item.get("quality", 1.0)) < 0.5 else "OK"  # type: ignore[arg-type]

        regions = []
        for lane, _, center, _ in self._lanes():
            for interval in self.perception_model.lane_occlusions(
                self.ego,
                self.all_actors,
                center,
                -COVERAGE_BEHIND_M,
                effective,
                COVERAGE_STEP_M,
            ):
                cause = str(interval["cause"])
                if cause != "BUILDING":
                    # Nur erfasste Objekte haben eine Track-ID; der Rest ist "etwas".
                    cause = (
                        self.model.track_id(cause)
                        if any(actor.spec.actor_id == cause for actor in self.snapshot.actors)
                        else "UNTRACKED_OBJECT"
                    )
                regions.append(
                    {
                        "lane": lane,
                        "from_m": interval["from_m"],
                        "to_m": interval["to_m"],
                        "caused_by": cause,
                    }
                )
        return {
            "header": {"stamp_s": round(self.time_s, 1), "frame_id": FRAME_ID},
            "sensors": {
                "lidar": status("LIDAR"),
                "radar": "OK",
                "camera": status("CAMERA"),
            },
            "max_reliable_range_m": {
                "lidar": round(effective, 1),
                "camera": round(effective, 1),
                "radar": RADAR_RANGE_M,
            },
            "atmospheric_visibility_m": scenario.visibility_m,
            "horizontal_fov_deg": scenario.perception_horizontal_fov_deg,
            "occluded_regions": regions,
            "note": (
                "from_m/to_m: longitudinal distance along the lane from the vehicle center, negative = "
                "behind the vehicle. Nothing is covered beyond max_reliable_range_m."
            ),
        }

    # ----------------------------------------------------------- Karte

    def map_context(self) -> dict[str, object]:
        scenario = self.scenario
        lanes = []
        for name, direction, center, width in self._lanes():
            samples = []
            offset = -MAP_BEHIND_M
            while offset <= MAP_AHEAD_M + 1e-6:
                x_m, y_m, _ = self._route_point_ego(self.ego.s_m + offset, center)
                samples.append([round(x_m, 2), round(y_m, 2)])
                offset += MAP_SAMPLE_STEP_M
            entry: dict[str, object] = {
                "lane": name,
                "direction": direction,
                "width_m": round(width, 2),
                "centerline_m": samples,
            }
            if name == "SHOULDER":
                entry["passing_allowed"] = scenario.allow_shoulder_passing
                entry["stopping_allowed"] = True
            lanes.append(entry)
        controls = []
        for control in scenario.traffic_controls:
            distance = float(control["s_m"]) - self.ego.s_m
            if 0.0 <= distance <= 120.0:
                controls.append(
                    {"id": control["id"], "type": control["type"], "stop_line_distance_m": round(distance, 1)}
                )
        return {
            "header": {"stamp_s": round(self.time_s, 1), "frame_id": FRAME_ID, "source": "hd_map"},
            "road_name": scenario.road.name,
            "traffic_side": scenario.traffic_side,
            "center_marking": {
                "type": scenario.road.center_marking,
                "crossing_allowed_by_exception": scenario.allow_solid_exception,
            },
            "lanes": lanes,
            "ego_lateral_offset_m": round(self.ego.d_m - scenario.ego_lane_d_m, 2),
            "stop_lines": controls,
            "note": "Map knowledge; reality may differ.",
        }
