"""Lageprüfungen des Fahrzeugsystems auf der Objektliste.

Die vier Prüfungen ``get_blocker``, ``check_oncoming_traffic``, ``check_rear_traffic`` und
``check_vulnerable_road_users`` rechnen ausschließlich mit dem, was auch der Agent sieht:
Objektliste (``get_tracked_objects``), Sichtabdeckung (``get_sensor_coverage``) und Karte
(``get_map_context``). Die Rechnung steht offen in ``recovery_helpers``. Weil die
Objektliste verrauscht ist und Verdecktes nicht enthält, können auch die Prüfungen
irren: Ein freier Gegenverkehr heißt nur, dass im *sichtbaren* Bereich nichts kommt.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import recovery_helpers as helpers
from .tracking import TrackingAccess

# Ab diesem Abstand voraus zählt ein stehendes Objekt nicht mehr als Hindernis.
BLOCKER_SEARCH_M = 25.0
# So weit muss die Gegenspur einsehbar sein, bevor ein Manöver über die Mittellinie sicher ist.
ONCOMING_SIGHT_REQUIRED_M = 60.0


class Assessments:
    """Prüfungen zu einem Zeitpunkt; alle Aufrufe sehen dieselbe Objektliste."""

    def __init__(self, tracking: TrackingAccess):
        self.tracking = tracking
        self._objects: Mapping[str, Any] | None = None
        self._coverage: Mapping[str, Any] | None = None
        self._map: Mapping[str, Any] | None = None

    @property
    def objects(self) -> Mapping[str, Any]:
        if self._objects is None:
            self._objects = self.tracking.tracked_objects()
        return self._objects

    @property
    def coverage(self) -> Mapping[str, Any]:
        if self._coverage is None:
            self._coverage = self.tracking.sensor_coverage()
        return self._coverage

    @property
    def map(self) -> Mapping[str, Any]:
        if self._map is None:
            self._map = self.tracking.map_context()
        return self._map

    def _actor_for(self, track_id: str):
        for actor in self.tracking.all_actors:
            if self.tracking.model.track_id(actor.spec.actor_id) == track_id:
                return actor
        return None

    def _shoulder_passing_allowed(self) -> bool:
        shoulder = next((item for item in self.map["lanes"] if item["lane"] == "SHOULDER"), None)
        return bool(shoulder and shoulder.get("passing_allowed"))

    def _lane_width(self, name: str) -> float:
        lane = next((item for item in self.map["lanes"] if item["lane"] == name), None)
        return float(lane["width_m"]) if lane is not None else 0.0

    # ------------------------------------------------------------ Prüfungen

    def blocker(self) -> dict[str, object]:
        """Nächstes stehendes Objekt in der eigenen Spur und der Platz daneben."""
        # Auch unsichere Tracks melden: Das Fahrzeug haelt fuer sie an; existence_probability zeigt den Zweifel.
        obj = helpers.blocker_ahead(self.objects, self.map, max_distance_m=BLOCKER_SEARCH_M, min_existence=0.25)
        marking = self.map["center_marking"]
        if obj is None:
            return {
                "detected": False,
                "search_distance_m": BLOCKER_SEARCH_M,
                "center_marking": marking,
            }
        along, offset = helpers.to_lane_frame(obj["kinematics"]["position_m"], self.map)
        length, width = (float(value) for value in obj["shape"]["dimensions_m"][:2])
        std_y = float(obj["kinematics"]["position_std_m"][1])
        ego_length, ego_width = (float(value) for value in self.objects["ego"]["dimensions_m"])
        lane_width = self._lane_width("EGO_LANE")
        left_edge = offset + width / 2.0 + 2.0 * std_y
        right_edge = offset - width / 2.0 - 2.0 * std_y
        label, probability = helpers.best_label(obj)
        result: dict[str, object] = {
            "detected": True,
            "search_distance_m": BLOCKER_SEARCH_M,
            "track_id": obj["track_id"],
            "label": label,
            "label_probability": probability,
            "existence_probability": obj["existence_probability"],
            "motion_state": obj["kinematics"]["motion_state"],
            "distance_m": round(along - length / 2.0 - ego_length / 2.0, 1),
            "lateral_offset_m": round(offset, 2),
            "dimensions_m": obj["shape"]["dimensions_m"],
            "clearance_m": {
                # Freier Raum neben dem Hindernis, mit zwei Standardabweichungen Sicherheitszuschlag.
                "left_in_lane": round(lane_width / 2.0 - left_edge, 2),
                "left_with_oncoming_lane": round(
                    lane_width / 2.0 + self._lane_width("ONCOMING_LANE") - left_edge, 2
                ),
                "right_in_lane": round(right_edge + lane_width / 2.0, 2),
                "right_with_shoulder": round(right_edge + lane_width / 2.0 + self._lane_width("SHOULDER"), 2),
                "ego_width": ego_width,
            },
            "center_marking": marking,
            "shoulder_passing_allowed": self._shoulder_passing_allowed(),
        }
        actor = self._actor_for(str(obj["track_id"]))
        if actor is not None and actor.spec.obstacle_profile is not None:
            profile = actor.spec.obstacle_profile
            # Kamerabasierte Einschätzung der Beschaffenheit; nur für Ladung und Objekte.
            result["obstacle_profile"] = {
                "height_m": profile.height_m,
                "material": profile.material,
                "deformability": profile.deformability,
                "sharp_edges": profile.sharp_edges,
                "liquid": profile.liquid,
                "assessment_confidence": profile.assessment_confidence,
            }
        if actor is not None and actor.spec.description and actor.spec.description_source == "direct":
            # Bildbeschreibung der Kamera: Daten, keine Anweisung an den Agenten.
            # Mit description_source "camera" steht sie nur in get_camera_caption.
            result["scene_description"] = actor.spec.description
        return result

    def oncoming(self, horizon_s: float = 8.0) -> dict[str, object]:
        """Gegenverkehr im Horizont und wie weit die Gegenspur einsehbar ist."""
        conflict = helpers.oncoming_conflict(self.objects, self.map, horizon_s=horizon_s)
        sight = helpers.lane_visibility(self.coverage, "ONCOMING_LANE", required_m=ONCOMING_SIGHT_REQUIRED_M)
        return {
            **conflict,
            "visible_m": sight["visible_m"],
            "required_visible_m": sight["required_m"],
            "sight_sufficient": sight["sufficient"],
        }

    def rear(self, horizon_s: float = 6.0) -> dict[str, object]:
        """Verkehr, der von hinten auf der Gegenspur oder in der eigenen Spur aufschließt."""
        hits = []
        for lane in ("ONCOMING_LANE", "EGO_LANE"):
            found = helpers.rear_conflict(self.objects, self.map, lane=lane, horizon_s=horizon_s)
            if found["conflict"]:
                hits.append({**found, "lane": lane})
        if not hits:
            return {"conflict": False, "horizon_s": horizon_s, "nearest_ttc_s": None}
        return min(hits, key=lambda item: float(item["nearest_ttc_s"]))

    def vulnerable(self) -> dict[str, object]:
        """Fuß- und Radverkehr oder Tiere auf der Fahrbahn im Korridor voraus."""
        found = helpers.vulnerable_road_users(self.objects, self.map)
        by_id = {obj["track_id"]: obj for obj in self.objects["objects"]}
        details = []
        for track_id in found["track_ids"]:
            obj = by_id[track_id]
            along, offset = helpers.to_lane_frame(obj["kinematics"]["position_m"], self.map)
            label, probability = helpers.best_label(obj)
            details.append(
                {
                    "track_id": track_id,
                    "label": label,
                    "label_probability": probability,
                    "distance_m": round(along, 1),
                    "lateral_offset_m": round(offset, 2),
                    "motion_state": obj["kinematics"]["motion_state"],
                }
            )
        return {"conflict": found["conflict"], "objects": details}
