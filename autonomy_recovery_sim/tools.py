from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import ActorState, EgoState, WorldFacts
from .perception import PerceptionSnapshot
from .scenario import Scenario

if TYPE_CHECKING:
    from .incidents import IncidentLog
    from .tracking import TrackingAccess
    from .vla_mock import VlaAccess


@dataclass(frozen=True)
class SituationTools:
    scenario: Scenario
    ego: EgoState
    actors: tuple[ActorState, ...]
    perception: PerceptionSnapshot | None = None
    facts: WorldFacts | None = None
    # Alter der Beobachtung in Sekunden; ``None`` = frisch (kein Datenalterproblem).
    observation_age_s: float | None = None
    # Fahrstack-Mock (nur in Szenarien mit Block ``vla``).
    vla: VlaAccess | None = None
    # Objektliste, Sichtabdeckung und Karte (nur Wahrnehmungsmodell ``tracked``).
    tracking: TrackingAccess | None = None
    # Unfallprotokoll des Fahrzeugs.
    incidents: IncidentLog | None = None

    def _perception_fields(self, actor: ActorState) -> dict[str, object]:
        if self.perception is None:
            fields: dict[str, object] = {"perception_status": "ground_truth", "track_age_s": 0.0}
        else:
            metadata = self.perception.actor_metadata.get(actor.spec.actor_id, {})
            fields = {
                "perception_status": metadata.get("status", "unknown"),
                "visible_fraction": metadata.get("visible_fraction", 0.0),
                "track_age_s": metadata.get("track_age_s", 0.0),
            }
        if actor.spec.confidence < 1.0:
            fields["track_confidence"] = actor.spec.confidence
        if actor.spec.description and actor.spec.description_source == "direct":
            # Freitext aus der Szene (z. B. OCR): Daten, keine Anweisung an den Agenten.
            fields["scene_description"] = actor.spec.description
        profile = actor.spec.obstacle_profile
        if profile is not None:
            fields["obstacle_profile"] = {
                "height_m": profile.height_m,
                "material": profile.material,
                "deformability": profile.deformability,
                "sharp_edges": profile.sharp_edges,
                "liquid": profile.liquid,
                "assessment_confidence": profile.assessment_confidence,
            }
        return fields

    def blocker_ahead(self, max_distance_m: float = 25.0) -> ActorState | None:
        candidates = [
            a
            for a in self.actors
            if a.spec.direction == 1
            and abs(a.d_m - self.ego.d_m) < (a.spec.width_m + self.ego.width_m) / 2
            and 0 < a.s_m - self.ego.s_m <= max_distance_m
            and a.speed_mps < 0.3
        ]
        return min(candidates, key=lambda a: a.s_m, default=None)

    def blocker_report(self, max_distance_m: float = 25.0) -> dict[str, object]:
        blocker = self.blocker_ahead(max_distance_m)
        if blocker is None:
            return {"detected": False, "search_distance_m": max_distance_m}
        return {
            "detected": True,
            "search_distance_m": max_distance_m,
            "actor_id": blocker.spec.actor_id,
            "kind": blocker.spec.kind,
            "distance_m": round(blocker.s_m - self.ego.s_m, 2),
            "d_m": round(blocker.d_m, 2),
            "speed_mps": round(blocker.speed_mps, 2),
            "length_m": blocker.spec.length_m,
            "width_m": blocker.spec.width_m,
            **self._perception_fields(blocker),
        }

    def marking(self) -> dict[str, object]:
        return {
            "type": self.scenario.road.center_marking,
            "source": "scenario_assumption",
            "exception_allowed": self.scenario.allow_solid_exception,
        }

    def oncoming_conflict(self, horizon_s: float = 8.0, *, moving_only: bool = False) -> dict[str, object]:
        """Gegenverkehr im Horizont; ``moving_only`` zaehlt nur Fahrzeuge, die sich naehern
        (fuer die Wende: ein stehendes Fahrzeug jenseits der Wendestelle kommt nicht heran)."""
        lane_d = -self.scenario.ego_lane_d_m
        hits = []
        for actor in self.actors:
            if actor.spec.direction != -1 or abs(actor.d_m - lane_d) > actor.spec.width_m:
                continue
            if moving_only and actor.speed_mps < 0.3:
                continue
            relative_speed = max(0.0, actor.speed_mps + max(self.ego.speed_mps, 2.0))
            distance = actor.s_m - self.ego.s_m
            if distance <= 0:
                continue
            ttc = distance / relative_speed if relative_speed else float("inf")
            if ttc <= horizon_s:
                hits.append((ttc, actor))
        if not hits:
            return {"conflict": False, "horizon_s": horizon_s, "nearest_ttc_s": None}
        ttc, actor = min(hits, key=lambda item: item[0])
        return {
            "conflict": True,
            "horizon_s": horizon_s,
            "nearest_ttc_s": round(ttc, 2),
            "actor_id": actor.spec.actor_id,
            **self._perception_fields(actor),
        }

    def rear_conflict(
        self, horizon_s: float = 6.0, lane_d_m: float | None = None
    ) -> dict[str, object]:
        """Detect a vehicle already approaching from behind in the target lane."""
        target_lane_d = -self.scenario.ego_lane_d_m if lane_d_m is None else lane_d_m
        hits = []
        for actor in self.actors:
            if actor.spec.direction != 1 or abs(actor.d_m - target_lane_d) > actor.spec.width_m:
                continue
            distance = self.ego.s_m - actor.s_m
            if distance <= 0:
                continue
            closing_speed = actor.speed_mps - self.ego.speed_mps
            if closing_speed <= 0:
                continue
            ttc = distance / closing_speed
            if ttc <= horizon_s:
                hits.append((ttc, actor))
        if not hits:
            return {"conflict": False, "horizon_s": horizon_s, "nearest_ttc_s": None}
        ttc, actor = min(hits, key=lambda item: item[0])
        return {
            "conflict": True,
            "horizon_s": horizon_s,
            "nearest_ttc_s": round(ttc, 2),
            "actor_id": actor.spec.actor_id,
            **self._perception_fields(actor),
        }

    def vulnerable_road_users(self) -> dict[str, object]:
        blocker = self.blocker_ahead()
        corridor_end = (
            blocker.s_m + blocker.spec.length_m + 12.0
            if blocker
            else self.ego.s_m + 35.0
        )
        actors = [
            actor
            for actor in self.actors
            if actor.spec.kind in {"bicycle", "pedestrian", "animal"}
            and self.ego.s_m - 3.0 <= actor.s_m <= corridor_end
            and abs(actor.d_m) <= self.scenario.road.lane_width_m
        ]
        return {
            "conflict": bool(actors),
            "actors": [
                {
                    "actor_id": actor.spec.actor_id,
                    "kind": actor.spec.kind,
                    **self._perception_fields(actor),
                }
                for actor in actors
            ],
            "actor_ids": [actor.spec.actor_id for actor in actors],
        }

    def lateral_clearance(self) -> dict[str, float | None]:
        blocker = self.blocker_ahead()
        blocker_width = blocker.spec.width_m if blocker else None
        available = (
            self.scenario.road.lane_width_m - (self.ego.width_m + blocker_width) / 2.0
            if blocker_width is not None
            else self.scenario.road.lane_width_m - self.ego.width_m / 2.0
        )
        return {
            "lane_width_m": self.scenario.road.lane_width_m,
            "blocker_width_m": blocker_width,
            "available_m": round(available, 2),
            "required_m": self.scenario.minimum_passing_clearance_m,
            "shoulder_m": self.scenario.shoulder_m,
            "shoulder_passing_allowed": self.scenario.allow_shoulder_passing,
        }

    def visibility(self) -> dict[str, object]:
        if self.perception is not None:
            return dict(self.perception.visibility)
        return {
            "sufficient": self.scenario.visibility_m >= self.scenario.minimum_visibility_m,
            "available_m": self.scenario.visibility_m,
            "required_m": self.scenario.minimum_visibility_m,
            "sensor_range_m": self.scenario.perception_range_m,
            "horizontal_fov_deg": self.scenario.perception_horizontal_fov_deg,
            "target_lane_visible_m": self.scenario.visibility_m,
            "occluded_from_m": None,
            "limited_by": [],
        }

    def route_state(self) -> dict[str, object]:
        facts = self.facts or WorldFacts()
        remaining = max(0.0, self.scenario.ego_target_s_m - self.ego.s_m)
        return {
            "remaining_distance_m": round(remaining, 1),
            "route_blocked": facts.route_blocked,
            "goal_reachable": not facts.route_blocked or facts.alternative_route_available,
            "alternative_route_available": facts.alternative_route_available,
            "estimated_detour_cost_s": facts.detour_cost_s if facts.route_blocked else 0.0,
            "reverse_required_m": max(0.0, round(facts.reverse_required_m - facts.reversed_m, 2)),
            "turn_required": facts.turn_required,
        }

    def mission_context(self) -> dict[str, object]:
        facts = self.facts or WorldFacts()
        on_road_edge = (
            self.ego.d_m + self.ego.width_m / 2.0 <= -self.scenario.road.lane_width_m + 0.05
        )
        return {
            "mission_type": facts.mission_type,
            "mission_state": facts.mission_state,
            "passengers_on_board": facts.passengers_on_board,
            "hub_available": facts.hub_available,
            "hub_return_s": facts.hub_return_s,
            "remote_assistance_available": facts.remote_assistance_available,
            "remote_assistance_calls": facts.remote_assistance_calls,
            "max_recovery_time_s": self.scenario.mission.max_recovery_time_s,
            "ego_position": "ROAD_EDGE" if on_road_edge else "TRAVEL_LANE",
            "shoulder_m": facts.shoulder_m,
        }

    def recovery_history(self) -> dict[str, object]:
        facts = self.facts or WorldFacts()
        attempts = [dict(item) for item in facts.history]
        warnings = []
        for item in attempts:
            if item.get("status") in {"FAILED", "REJECTED", "ABORTED"}:
                repeated = sum(
                    other.get("command") == item.get("command")
                    and other.get("status") == item.get("status")
                    for other in attempts
                )
                tag = f"{item.get('command')}_ALREADY_{item.get('status')}"
                if repeated >= 1 and tag not in warnings:
                    warnings.append(tag)
        return {"attempts": attempts, "repetition_warnings": warnings}

    def traffic_control(self) -> dict[str, object]:
        """Ampeln und Bahnuebergaenge voraus mit aktuellem Zustand."""
        facts = self.facts or WorldFacts()
        train_detected = any(actor.spec.kind == "train" for actor in self.actors)
        controls = []
        for item in facts.traffic_controls:
            entry = dict(item)
            if item["type"] == "RAILWAY_CROSSING":
                entry["train_detected"] = train_detected
            if item["state"] == "DARK":
                entry["note"] = "Signal out of order: no signal control, the planner waits for clearance"
            controls.append(entry)
        return {"controls": controls}

    def vehicle_status(self) -> dict[str, object]:
        if self.incidents is None:
            return {"impact_events": [], "hazard_lights": False, "scene_secured": False, "incident_reported": False}
        return self.incidents.vehicle_status()

    def sensor_health(self) -> dict[str, object]:
        facts = self.facts or WorldFacts()
        sensors = [dict(item) for item in facts.sensors] or [
            {"name": name, "status": "AVAILABLE", "quality": 0.95}
            for name in ("CAMERA", "LIDAR", "LOCALIZATION")
        ]
        degraded = any(item["status"] != "AVAILABLE" or float(item["quality"]) < 0.5 for item in sensors)
        return {
            "sensors": sensors,
            "fusion_status": "DEGRADED" if degraded else "NOMINAL",
            "localization_quality": facts.localization_quality,
        }


def static_situation(
    scenario: Scenario,
    ego: EgoState,
    actors: tuple[ActorState, ...],
    facts: WorldFacts | None = None,
    *,
    time_s: float = 0.0,
) -> SituationTools:
    """Werkzeugsicht auf eine stehende Momentaufnahme, mit Objektliste (fuer Tests und Werkzeuge)."""
    from dataclasses import replace

    from .perception import PerceptionModel
    from .tracking import TrackingAccess, TrackingModel

    scenario = replace(scenario, actors=tuple(actor.spec for actor in actors))
    perception = PerceptionModel(scenario)
    snapshot = perception.observe(time_s, ego, actors)
    model = TrackingModel(scenario)
    model.update(time_s, snapshot)
    x_m, y_m, heading = scenario.road.to_xy(ego.s_m, ego.d_m)
    tracking = TrackingAccess(model, perception, time_s, ego, (x_m, y_m, heading), snapshot, actors)
    return SituationTools(scenario, ego, actors, snapshot, facts or WorldFacts(), tracking=tracking)
