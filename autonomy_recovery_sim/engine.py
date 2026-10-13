from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

from typing import Any

from .agent_contract import (
    AgentPolicy,
    contract_to_payload,
)
from .agent_session import AgentBudgetError, AgentSession, AgentSessionError, AgentToolError
from .assessments import Assessments
from .commands import COMMANDS, CommandError
from .costs import assess_episode
from .dynamics import Control, Vehicle, boxes_overlap, clamp, oriented_corners, step_vehicle, wrap_angle
from .incidents import Impact, IncidentLog, direction_of, severity_of
from .models import ActorState, CommandRequest, EgoState, Event, WorldFacts
from .perception import PerceptionModel, PerceptionSnapshot
from .rules import (
    LOW_RISK_MATERIALS,
    LOW_RISK_MAX_HEIGHT_M,
    PassPlan,
    plan_pass,
    pull_over_target_d,
    u_turn_width_m,
)
from .scenario import Scenario
from .tools import SituationTools
from .tracking import TrackingAccess, TrackingModel
from .triggers import conditions_met
from .vla_mock import PlannerIntent, VlaAccess, VlaMock

EGO_MINIBUS_LENGTH_M = 4.71
EGO_MINIBUS_WIDTH_M = 1.99
EGO_MINIBUS_WHEELBASE_M = 2.99


@dataclass
class TrafficVehicle:
    actor: ActorState
    vehicle: Vehicle
    # Nach einem Aufprall (MODERATE/SEVERE) bleibt der Beteiligte stehen.
    halted: bool = False
    # Von einem Ausloeser gesetztes Verhalten: {"target_speed_mps": v} oder {"cross": (exit_d, v)}.
    override: dict[str, Any] | None = None
    fired: set[int] = field(default_factory=set)


@dataclass
class ActiveCommand:
    """Laufender, vom zertifizierten Ausfuehrer gesteuerter Befehl (hoechstens einer)."""

    request: CommandRequest
    action_id: str
    start_time_s: float
    start_s_m: float
    start_xy: tuple[float, float]
    source: str = "agent"
    plan: PassPlan | None = None
    blocker_s_m: float | None = None
    blocker_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


PASS_COMMANDS = frozenset({"PASS_OBSTACLE"})
HOLD_COMMANDS = frozenset({"WAIT", "REQUEST_REMOTE_ASSISTANCE"})
# Nach so langer Beobachtung im Stand sind Geisterobjekte, veraltete Daten und Kartenfehler erkannt.
WAIT_REFRESH_S = 3.0
VULNERABLE_KINDS = frozenset({"bicycle", "pedestrian"})
CAUTION_KINDS = VULNERABLE_KINDS | {"animal"}
PASS_VALID_FOR_S = 20.0
# Feste Kriechgeschwindigkeit ueber ein validiertes Niedrigrisiko-Objekt.
CROSS_SPEED_MPS = 1.0
PASS_LOOKAHEAD_M = 3.0
STOP_MARGIN_M = 1.0
VULNERABLE_BUFFER_M = 1.5
CAUTION_DISTANCE_M = 35.0
CAUTION_LATERAL_M = 12.0
CAUTION_SPEED_MPS = 3.0
CONTROL_LOOKAHEAD_M = 80.0
HOLDING_STATES = frozenset({"RED", "CLOSED", "DARK"})  # der Planer wartet an der Haltelinie
LEGAL_STOP_STATES = frozenset({"RED", "CLOSED"})  # regulaerer Halt: kein Deadlock, keine Standzeitkosten
PASS_CLEARANCE_M = 3.4
MAX_COMPLETION_SPEED_MPS = 15.0 / 3.6
FALLBACK_WAIT_S = 5.0
PULL_OVER_SPEED_MPS = 1.0
PULL_OVER_TIMEOUT_S = 25.0
REVERSE_SPEED_MPS = 1.0
REVERSE_TIMEOUT_S = 15.0
LOW_RISK_CROSS_TIMEOUT_S = 20.0
LOW_RISK_CONTACT_GAP_M = 0.35
LOW_RISK_CLEARANCE_M = 1.0
# Notbremse: Verzoegerung und Sicherheitsabstand zum wahrgenommenen Objekt im Fahrweg.
AEB_DECELERATION_MPS2 = 6.0
AEB_MARGIN_M = 0.5
# Steht das Fahrzeug in einem Manoever so lange still (Notbremse, Hindernis), bricht der
# Ausfuehrer es ab, und der Agent entscheidet am Kontrollpunkt neu.
MANEUVER_STALL_ABORT_S = 3.0
# Manoever, die der Ausfuehrer abbricht, wenn das Fahrzeug darin festsitzt.
MANEUVER_COMMANDS = PASS_COMMANDS | {"CROSS_LOW_RISK_OBJECT", "REVERSE_SHORT", "PULL_OVER", "ABORT_TO_LANE"}
PULL_OUT_SPEED_MPS = 1.5
# PULLED_OUT: so weit ausgeschert, dass die Sensoren am Hindernis vorbeisehen. Die linke
# Fahrzeugkante bleibt nahe der Mitte; Gegenverkehr kommt noch vorbei.
PULL_OUT_PEEK_OFFSET_M = 1.0
# ... und die vordere linke Ecke ragt hoechstens so weit ueber die Mittellinie.
PULL_OUT_MAX_CORNER_D_M = 0.5
# So viel Anlauf braucht das Ausscheren; fehlt er, setzt das Fahrzeug erst gerade zurueck.
PULL_OUT_ROOM_M = 5.0
# Vorbeifahren an stehenden Objekten, zwischen denen keine Luecke zum Einscheren ist.
CHAIN_GAP_M = 12.0
# Ausgeschert auf eine Luecke im sichtbaren Gegen- und Folgeverkehr warten.
GAP_HORIZON_S = 8.0
GAP_CONFIRM_S = 0.5
GAP_TIMEOUT_S = 30.0
ABORT_SPEED_MPS = 1.0
ABORT_MERGE_ROOM_M = 12.0
ABORT_MAX_REVERSE_M = 20.0
ABORT_TIMEOUT_S = 45.0
# Hinter dem Hindernis genuegt diese Restablage zur Spurmitte.
ABORT_LANE_TOLERANCE_M = 0.6
# Wenden fuer eine Umleitung: Tempo, Abstand zu den Fahrbahnraendern, Zuege und Zeitgrenze.
TURN_SPEED_MPS = 1.2
TURN_EDGE_MARGIN_M = 0.4
TURN_DONE_RAD = math.radians(170.0)
TURN_STRAIGHTEN_M = 6.0
TURN_MAX_MOVES = 7
TURN_TIMEOUT_S = 90.0
# Eine Anweisung der Leitstelle gilt, bis das Fahrzeug wieder zuegig faehrt.
INSTRUCTION_DONE_SPEED_MPS = 1.0
# Reaktion auf ein Schallzeichen beginnt fruehestens nach dieser Zeit.
HORN_DURATION_S = 1.0
# Am Checkpoint laeuft die Uhr: so lange steht das Fahrzeug, waehrend die Welt weiterfaehrt.
DECISION_S_PER_TOOL_CALL = 0.2
DECISION_S_PER_MODEL_ROUND = 2.0


class SimulationEngine:
    """Schrittweise Simulation fuer Batchlauf und Live-Oberflaeche."""

    def __init__(
        self,
        scenario: Scenario,
        agent: AgentPolicy | None = None,
        *,
        agent_name: str | None = None,
        console: bool = False,
    ):
        self.scenario = scenario
        self.agent = agent
        # Recovery Console: Ohne Agent uebernimmt ein Mensch die Sitzung (nur Live-Demonstration).
        self.console = console and agent is None
        self.pending_session: AgentSession | None = None
        self.console_error: str | None = None
        self._pending_flushed = 0
        self.agent_name = agent_name or ("none" if agent is None else "custom")
        self.agent_status = "inactive"
        x, y, heading = scenario.road.to_xy(scenario.ego_start_s_m, scenario.ego_lane_d_m)
        self.ego = Vehicle(
            x,
            y,
            heading,
            0.0,
            length_m=EGO_MINIBUS_LENGTH_M,
            width_m=EGO_MINIBUS_WIDTH_M,
            wheelbase_m=EGO_MINIBUS_WHEELBASE_M,
        )
        self.traffic: list[TrafficVehicle] = []
        for spec in scenario.actors:
            ax, ay, road_heading = scenario.road.to_xy(spec.s_m, spec.d_m)
            yaw = road_heading if spec.direction == 1 else wrap_angle(road_heading + math.pi)
            state = ActorState.from_spec(spec)
            self.traffic.append(TrafficVehicle(
                actor=state,
                vehicle=Vehicle(ax, ay, yaw, spec.speed_mps, spec.length_m, spec.width_m),
            ))
        self.time_s = 0.0
        self.perception_model = PerceptionModel(scenario)
        # Objektliste mit Messunsicherheit fuer den Agenten (Wahrnehmungsmodell ``tracked``).
        self.tracking_model = TrackingModel(scenario)
        self.perception_snapshot: PerceptionSnapshot | None = None
        self._last_perception_time_s = float("-inf")
        self.mode = "autopilot"
        self.lane_d_m = scenario.ego_lane_d_m
        # Befehle und Verlauf
        self.active: ActiveCommand | None = None
        self.commands: list[CommandRequest] = []
        self.command_history: list[dict[str, Any]] = []
        self.additional_information: list[dict[str, object]] = []
        self.action_counter = 0
        self.session_counter = 0
        self.agent_observation: dict[str, object] | None = None
        self.agent_trace: list[dict[str, object]] = []
        self.agent_error: str | None = None
        self.events: list[Event] = []
        self.trajectory: list[dict[str, float]] = []
        self.stopped_for_s = 0.0
        self._retrigger_blocked = False
        self._has_moved = False
        # Manoeverzustand (Vorbeifahren)
        self.liberated = False
        self.maneuver_aborted = False
        self.abort_reason: str | None = None
        self.abort_recovery_active = False
        self.abort_stabilized = False
        self.minimum_risk_completion = False
        self.pulled_over = False
        # Mission und Kosten
        self.mission_state = "ACTIVE"
        self.passengers_on_board = scenario.mission.passengers
        self.initial_passengers = scenario.mission.passengers
        self.passengers_injured = 0
        self.passengers_dropped_safely = 0
        self.passengers_returned_home = 0
        self.terminal_outcome: str | None = None
        self.remote_resolved = False
        self.route_blocked = scenario.route.blocked
        # Was Planer und Agent ueber die Route wissen (kann hinter der Wirklichkeit zurueckliegen).
        self.route_known_blocked = scenario.route.blocked and not scenario.route.map_stale
        self.tool_fault_counts: dict[str, int] = {}
        self._stale_snapshot: tuple[PerceptionSnapshot, float] | None = None
        self.stale_cleared = False
        self.planned_stop_started_s: float | None = None
        self.planned_stop_done = scenario.mission.planned_stop_s_m is None
        self.removed_controls: set[str] = set()
        self.reversed_m = 0.0
        self.stationary_s = 0.0
        self.extra_time_s = 0.0
        self.remote_assistance_calls = 0
        # Anweisung der Leitstelle (type, message, ggf. side) und daraus folgende Ausnahme.
        self.remote_instruction: dict[str, object] | None = None
        self.center_line_exception = False
        # Von der Leitstelle organisierte Raeumung: (Zeitpunkt, Akteur-IDs).
        self._scheduled_clear: tuple[float, frozenset[str]] | None = None
        self.horn_time_s: float | None = None
        # Nach einem beendeten Befehl sofort neu entscheiden, falls das Fahrzeug festsitzt.
        self._recheck_after_command = False
        self.rejected_commands = 0
        self.stopped_in_travel_lane = False
        self.collision = False
        # Angenommene Sicherheitsbefunde: Der Agent verantwortet seine Entscheidung.
        self.safety_warnings: list[str] = []
        # Ein Aufprall wird protokolliert; der Agent wickelt ihn ab.
        self.incidents = IncidentLog()
        self.incident_in_travel_lane = False
        self._pending_trigger: dict[str, object] | None = None
        self.emergency_brake_active = False
        self._maneuver_stalled_s = 0.0
        # Entscheidung vom Checkpoint, die erst nach der Bedenkzeit ausgefuehrt wird.
        self._deferred: tuple[CommandRequest, float] | None = None
        self._first_session_s: float | None = None
        self.collision_with_vulnerable = False
        self.reached_goal = False
        self.minimum_clearance_m = float("inf")
        self.done = False
        # VLA-Fahrstack-Mock: nur Beobachter, er veraendert das Fahrverhalten nicht.
        self.vla = VlaMock(scenario, scenario.vla) if scenario.vla is not None else None
        self._refresh_perception()
        self._update_vla()

    # ------------------------------------------------------------------ Zustand

    @property
    def first_command(self) -> CommandRequest | None:
        return self.commands[0] if self.commands else None

    @property
    def ego_route_state(self) -> tuple[float, float, float, float]:
        return self.scenario.road.project(self.ego.x_m, self.ego.y_m)

    def set_mode(self, mode: str) -> None:
        if mode not in {"autopilot", "manual"}:
            raise ValueError("mode must be 'autopilot' or 'manual'")
        if self.mode != mode:
            self.mode = mode
            self.events.append(Event(self.time_s, "mode_changed", {"mode": mode}))

    def _facts(self) -> WorldFacts:
        scenario = self.scenario
        return WorldFacts(
            mission_state=self.mission_state,
            mission_type=scenario.mission.mission_type,
            passengers_on_board=self.passengers_on_board,
            hub_available=scenario.mission.hub_available,
            hub_return_s=scenario.mission.hub_return_s,
            route_blocked=self.route_known_blocked,
            alternative_route_available=scenario.route.alternative_available,
            detour_cost_s=scenario.route.detour_cost_s,
            reverse_required_m=scenario.route.reverse_required_m,
            turn_required=scenario.route.turn_required,
            reversed_m=self.reversed_m,
            remote_assistance_available=scenario.remote_assistance.available,
            remote_assistance_calls=self.remote_assistance_calls,
            lane_d_m=self.lane_d_m,
            shoulder_m=scenario.shoulder_m,
            localization_quality=next(
                (item["quality"] for item in scenario.sensors if item["name"] == "LOCALIZATION"), 1.0
            ),
            sensors=self._sensors(),
            traffic_controls=tuple(self._controls_ahead(self.ego_route_state[0])),
            history=tuple(self.command_history),
            incident_detected=self.incidents.detected,
            impact_recorded=self.incidents.recorded,
            center_line_exception=self.center_line_exception,
            remote_instruction=dict(self.remote_instruction) if self.remote_instruction else None,
        )

    def _sensors(self) -> tuple[dict[str, object], ...]:
        """Sensoren laut Szenario, nach einem Aufprall ggf. beschaedigt."""
        faults = {fault["sensor"]: fault for fault in self.incidents.sensor_faults()}
        if not faults:
            return self.scenario.sensors
        sensors = {str(item["name"]): dict(item) for item in self.scenario.sensors}
        for name, fault in faults.items():
            sensors[str(name)] = {"name": name, "status": fault["status"], "quality": fault["quality"]}
        return tuple(sensors.values())

    def _actor_states(self) -> list[ActorState]:
        states: list[ActorState] = []
        for traffic in self.traffic:
            s_m, d_m, _, _ = self.scenario.road.project(traffic.vehicle.x_m, traffic.vehicle.y_m)
            traffic.actor.s_m = s_m
            traffic.actor.d_m = d_m
            traffic.actor.speed_mps = traffic.vehicle.speed_mps
            states.append(traffic.actor)
        return states

    def _ego_state(self) -> EgoState:
        ego_s, ego_d, _, _ = self.ego_route_state
        return EgoState(
            ego_s,
            ego_d,
            self.ego.speed_mps,
            self.ego.length_m,
            self.ego.width_m,
        )

    def _refresh_perception(self, *, force: bool = False) -> None:
        if not force and self.time_s - self._last_perception_time_s < 0.5 - 1e-9:
            return
        self.perception_snapshot = self.perception_model.observe(
            self.time_s,
            self._ego_state(),
            tuple(self._actor_states()),
        )
        self.tracking_model.update(self.time_s, self.perception_snapshot)
        self._last_perception_time_s = self.time_s

    def _situation_tools(self, *, force_perception: bool = False) -> SituationTools:
        if self.perception_snapshot is None or force_perception:
            self._refresh_perception(force=True)
        assert self.perception_snapshot is not None
        return SituationTools(
            self.scenario,
            self._ego_state(),
            self.perception_snapshot.actors,
            self.perception_snapshot,
            self._facts(),
            vla=self._vla_access(self.perception_snapshot),
            tracking=self._tracking_access(self.perception_snapshot),
            incidents=self.incidents,
        )

    def _tracking_access(self, perception: PerceptionSnapshot) -> TrackingAccess:
        return TrackingAccess(
            self.tracking_model,
            self.perception_model,
            self.time_s,
            self._ego_state(),
            (self.ego.x_m, self.ego.y_m, self.ego.yaw_rad),
            perception,
            tuple(self._actor_states()),
            self._sensors(),
        )

    # ------------------------------------------------------- VLA-Fahrstack

    def _vla_pose(self) -> tuple[float, float, float, float, float, float]:
        ego_s, ego_d, _, _ = self.ego_route_state
        return (self.ego.x_m, self.ego.y_m, self.ego.yaw_rad, ego_s, ego_d, self.ego.speed_mps)

    def _vla_access(self, perception: PerceptionSnapshot | None) -> VlaAccess | None:
        if self.vla is None:
            return None
        return VlaAccess(
            self.vla,
            self.time_s,
            self._vla_pose(),
            perception,
            tuple(self._controls_ahead(self.ego_route_state[0])),
        )

    def _update_vla(self) -> None:
        if self.vla is None or not self.vla.due(self.time_s):
            return
        self.vla.update(self.time_s, self._planner_intent(), self._vla_pose(), self.perception_snapshot)

    def _blocker_context(self, *, passing: bool = False) -> tuple[str, ...]:
        """Was der Fahrstack ueber das Vorbeifahren sagt - nur aus wahrgenommenen Objekten."""
        ego_s = self.ego_route_state[0]
        perceived = self.perception_snapshot.actors if self.perception_snapshot else ()
        reasons: list[str] = []
        kinds = {
            actor.spec.kind
            for actor in perceived
            if actor.spec.kind in CAUTION_KINDS
            and -3.0 <= actor.s_m - ego_s <= CAUTION_DISTANCE_M
            and abs(actor.d_m) <= self.scenario.road.lane_width_m
        }
        if kinds:
            names = {"bicycle": "a cyclist", "pedestrian": "pedestrians", "animal": "an animal"}
            reasons.append(f"I also see {' and '.join(names[kind] for kind in sorted(kinds))} near the passing path.")
        opposite = [
            actor for actor in perceived
            if actor.spec.direction == -1 and 0.0 < actor.s_m - ego_s <= 80.0 and actor.d_m > 0
        ]
        if any(actor.speed_mps >= 0.3 for actor in opposite):
            reasons.append("Oncoming traffic in the opposite lane leaves no gap to pass.")
        elif opposite:
            reasons.append("A stationary vehicle in the opposite lane blocks the passing path.")
        if self.ego.speed_mps < 0.3 and self.perception_snapshot is not None:
            # Nur im Stand: Die Sichtpruefung ist teurer als die uebrigen Saetze.
            # Hoechstens einmal pro Sekunde neu berechnen.
            cached = getattr(self, "_sight_cache", None)
            if cached is None or self.time_s - cached[0] >= 1.0:
                cached = (self.time_s, Assessments(self._tracking_access(self.perception_snapshot)).oncoming())
                self._sight_cache = cached
            sight = cached[1]
            if not sight["sight_sufficient"]:
                reasons.append(f"I can see only about {sight['visible_m']:.0f} m along the opposite lane.")
        route = self.scenario.route
        if route.blocked and route.reverse_required_m > self.reversed_m + 1e-6:
            # Hinweis auf REVERSE_SHORT vor der Umleitung: hier ist kein Platz zum Wenden.
            reasons.append("The road ahead is blocked; there is not enough room to turn around here.")
        if self.scenario.road.center_marking == "solid" and not passing:
            if self.center_line_exception or self.scenario.allow_solid_exception:
                reasons.append("The control centre has approved crossing the solid center line here.")
            else:
                reasons.append("A solid center line does not allow passing.")
        if not reasons and not passing:
            reasons.append("Passing would require leaving the lane, which my nominal driving policy does not do.")
        return tuple(reasons)

    def _nominal_intent(self, ego_s: float, ego_d: float) -> PlannerIntent:
        """Fahrabsicht des Autopiloten ohne Recovery-Befehl (Spiegel von _autopilot_control)."""
        cruise = self.scenario.ego_cruise_speed_mps
        if self.pulled_over and self._path_blocked():
            return PlannerIntent("STOP", "pulled_over", 0.0, stop_distance_m=0.0)
        stop_s = self.scenario.mission.planned_stop_s_m
        if stop_s is not None and not self.planned_stop_done:
            distance = stop_s - ego_s
            if self.planned_stop_started_s is not None or distance <= 30.0:
                return PlannerIntent("STOP", "planned_stop", 0.0, stop_distance_m=max(0.0, distance))
        for control in self._controls_ahead(ego_s):
            if control["state"] in HOLDING_STATES and control["stop_line_distance_m"] <= 50.0:
                gap = max(0.0, control["stop_line_distance_m"] - STOP_MARGIN_M)
                return PlannerIntent(
                    "STOP", "traffic_control", 0.0, stop_distance_m=gap, control=control
                )
        blocker = self._blocking_actor(ego_s, ego_d)
        if blocker is not None and blocker.s_m - ego_s <= 40.0:
            gap = blocker.s_m - ego_s - (self.ego.length_m + blocker.spec.length_m) / 2.0
            stop = 0.0 if self.ego.speed_mps < 0.3 else max(0.0, gap - 5.0)
            if blocker.spec.kind in CAUTION_KINDS:
                return PlannerIntent(
                    "YIELD", "vulnerable", 0.0, stop_distance_m=stop, actor=blocker, distance_m=gap
                )
            return PlannerIntent(
                "STOP", "blocker", 0.0, stop_distance_m=stop, actor=blocker, distance_m=gap,
                context=self._blocker_context(),
            )
        for actor in self._actor_states():
            if actor.spec.kind not in CAUTION_KINDS:
                continue
            ahead = -5.0 <= actor.s_m - ego_s <= CAUTION_DISTANCE_M
            near = min(abs(actor.d_m - ego_d), abs(actor.d_m - self.lane_d_m)) <= CAUTION_LATERAL_M
            if ahead and near:
                return PlannerIntent(
                    "YIELD", "vulnerable", CAUTION_SPEED_MPS, actor=actor,
                    distance_m=max(0.0, actor.s_m - ego_s - self.ego.length_m / 2.0),
                )
        lead = self._lead_vehicle(ego_s, ego_d)
        if lead is not None and lead.s_m - ego_s <= 40.0:
            return PlannerIntent(
                "FOLLOW_VEHICLE", "lead_vehicle", min(cruise, lead.speed_mps), actor=lead,
                distance_m=lead.s_m - ego_s - (self.ego.length_m + lead.spec.length_m) / 2.0,
            )
        return PlannerIntent("FOLLOW_LANE", "clear", cruise)

    def _planner_intent(self) -> PlannerIntent:
        ego_s, ego_d, _, _ = self.ego_route_state
        if self.mode == "manual":
            return PlannerIntent("MANUAL", "manual", 0.0, stop_distance_m=0.0)
        if self.maneuver_aborted:
            return PlannerIntent(
                "STOP", "maneuver_aborted", 0.0, stop_distance_m=0.0, target_d_m=self.lane_d_m
            )
        active = self.active
        if active is None:
            return self._nominal_intent(ego_s, ego_d)
        name = active.request.command
        params = active.request.parameters
        if name == "PASS_OBSTACLE" and active.data.get("phase") == "PULLED_OUT":
            # Ausgeschert, aber noch nicht vorbei: Der Stack steht und sagt, worauf er wartet.
            nominal = self._nominal_intent(ego_s, ego_d)
            reason = (
                "Holding pulled out: the situation changed and a decision is pending."
                if active.data.get("paused")
                else "Waiting pulled out for a gap in the oncoming traffic."
            )
            return replace(
                nominal, meta_action="STOP" if active.data.get("paused") else "YIELD", target_speed_mps=0.0,
                stop_distance_m=0.0, command=name, context=self._blocker_context(passing=True) + (reason,),
            )
        if name in PASS_COMMANDS and active.plan is not None:
            side = params.get("side") or ("LEFT" if active.plan.target_d_m > ego_d else "RIGHT")
            meta = f"NUDGE_{side}"
            context = (
                ("Pulling out slowly to look past the obstacle.",)
                if active.data.get("phase") == "PULL_OUT"
                else ()
            )
            return PlannerIntent(
                meta, "recovery_command", active.plan.speed_mps, command=name, target_d_m=active.plan.target_d_m,
                context=context,
            )
        if name == "REVERSE_SHORT":
            return PlannerIntent(
                "REVERSE", "recovery_command", REVERSE_SPEED_MPS,
                stop_distance_m=float(params["max_distance_m"]), command=name,
            )
        if name == "PULL_OVER":
            return PlannerIntent(
                "PULL_OVER", "recovery_command", PULL_OVER_SPEED_MPS, command=name,
                target_d_m=pull_over_target_d(self.scenario, self.ego.width_m),
            )
        if name == "CROSS_LOW_RISK_OBJECT":
            return PlannerIntent("CREEP", "recovery_command", CROSS_SPEED_MPS, command=name)
        if name == "REPLAN_ROUTE":
            return PlannerIntent("CREEP", "recovery_command", TURN_SPEED_MPS, command=name)
        if name == "ABORT_TO_LANE":
            meta = "REVERSE" if active.data.get("phase", "REVERSE") == "REVERSE" else f"NUDGE_{'RIGHT' if ego_d > self.lane_d_m else 'LEFT'}"
            return PlannerIntent(meta, "recovery_command", ABORT_SPEED_MPS, command=name, target_d_m=self.lane_d_m)
        # Halte-Befehle: Die eigentliche Ursache bleibt sichtbar, ergaenzt um die Anweisung.
        nominal = self._nominal_intent(ego_s, ego_d)
        return replace(
            nominal,
            meta_action="STOP",
            target_speed_mps=0.0,
            stop_distance_m=0.0,
            command=name,
            context=nominal.context + (f"Holding position as instructed ({name}).",),
        )

    def _at_road_edge(self) -> bool:
        _, ego_d, _, _ = self.ego_route_state
        return ego_d + self.ego.width_m / 2.0 <= -self.scenario.road.lane_width_m + 0.05

    # ------------------------------------------------------------------ Fahrer

    def _route_control(
        self,
        vehicle: Vehicle,
        direction: int,
        lane_d_m: float,
        target_speed: float,
        *,
        min_lookahead_m: float = 5.0,
    ) -> Control:
        s_m, _, _, _ = self.scenario.road.project(vehicle.x_m, vehicle.y_m)
        lookahead = max(min_lookahead_m, vehicle.speed_mps * 1.15)
        tx, ty, _ = self.scenario.road.to_xy(s_m + direction * lookahead, lane_d_m)
        desired_heading = math.atan2(ty - vehicle.y_m, tx - vehicle.x_m)
        error = wrap_angle(desired_heading - vehicle.yaw_rad)
        steering = clamp(error / math.radians(28.0), -1.0, 1.0)
        speed_error = target_speed - vehicle.speed_mps
        return Control(
            throttle=clamp(speed_error * 0.55, 0.0, 1.0),
            brake=clamp(-speed_error * 0.45, 0.0, 1.0),
            steering=steering,
        )

    def _blocking_actor(self, ego_s: float, ego_d: float) -> ActorState | None:
        """Naechster Aktor, der den eigenen Fahrstreifen voraus versperrt (Ground Truth)."""
        candidates = []
        for actor in self._actor_states():
            if actor.spec.direction != 1:
                continue
            # Akteure, die neben dem Ego stehen (Fussgaenger im Fahrkorridor), zaehlen mit.
            if actor.s_m + actor.spec.length_m / 2.0 < ego_s - self.ego.length_m / 2.0:
                continue
            if actor.speed_mps >= 0.3 and actor.spec.kind not in CAUTION_KINDS:
                continue
            reach = (actor.spec.width_m + self.ego.width_m) / 2.0
            if actor.spec.kind in CAUTION_KINDS:
                reach += VULNERABLE_BUFFER_M  # Menschen, Radverkehr und Tiere: Sicherheitsabstand
            if min(abs(actor.d_m - ego_d), abs(actor.d_m - self.lane_d_m)) < reach:
                candidates.append(actor)
        return min(candidates, key=lambda actor: actor.s_m, default=None)

    def _apply_planned_stop(self, ego_s: float, target_speed: float) -> float:
        """Geplanter Halt (Fahrgastwechsel): kein Fehler, obwohl das Fahrzeug steht."""
        stop_s = self.scenario.mission.planned_stop_s_m
        if stop_s is None or self.planned_stop_done:
            return target_speed
        distance = stop_s - ego_s
        if self.planned_stop_started_s is None:
            if distance <= 0.5 and self.ego.speed_mps < 0.3:
                self.planned_stop_started_s = self.time_s
                self.mission_state = "PLANNED_STOP"
                self.events.append(Event(self.time_s, "planned_stop_started", {"s_m": round(ego_s, 2)}))
            return min(target_speed, max(0.0, 1.2 * distance))
        if self.time_s - self.planned_stop_started_s >= self.scenario.mission.planned_stop_duration_s:
            self.planned_stop_done = True
            self.mission_state = "ACTIVE"
            self.events.append(Event(self.time_s, "planned_stop_finished", {}))
            return target_speed
        return 0.0

    def _apply_vulnerable_caution(self, ego_s: float, ego_d: float, target_speed: float) -> float:
        """Vorsichtig an Fuss- und Radverkehr in der Naehe der Fahrspur heranfahren."""
        for actor in self._actor_states():
            if actor.spec.kind not in CAUTION_KINDS:
                continue
            ahead = -5.0 <= actor.s_m - ego_s <= CAUTION_DISTANCE_M
            near = min(abs(actor.d_m - ego_d), abs(actor.d_m - self.lane_d_m)) <= CAUTION_LATERAL_M
            if ahead and near:
                return min(target_speed, CAUTION_SPEED_MPS)
        return target_speed

    def _lead_vehicle(self, ego_s: float, ego_d: float) -> ActorState | None:
        """Naechster fahrender Aktor voraus im eigenen Fahrstreifen (fuer das Folgen)."""
        candidates = []
        for actor in self._actor_states():
            if actor.spec.direction != 1 or actor.s_m <= ego_s or actor.speed_mps < 0.3:
                continue
            reach = (actor.spec.width_m + self.ego.width_m) / 2.0
            if min(abs(actor.d_m - ego_d), abs(actor.d_m - self.lane_d_m)) < reach:
                candidates.append(actor)
        return min(candidates, key=lambda actor: actor.s_m, default=None)

    def _control_state(self, control: dict[str, Any]) -> tuple[str, float]:
        """Zustand einer Ampel bzw. eines Bahnuebergangs und der Beginn dieser Phase."""
        started = 0.0
        for phase in control["schedule"]:
            if self.time_s < phase["until_s"]:
                return phase["state"], started
            started = phase["until_s"]
        return control["schedule"][-1]["state"], started

    def _controls_ahead(self, ego_s: float, max_distance_m: float = CONTROL_LOOKAHEAD_M) -> list[dict[str, Any]]:
        """Verkehrssteuerungen voraus (Haltelinie noch nicht ueberfahren), nach Abstand sortiert."""
        front = ego_s + self.ego.length_m / 2.0
        ahead = []
        for control in self.scenario.traffic_controls:
            distance = control["s_m"] - front
            if control["id"] in self.removed_controls or distance < -0.5 or distance > max_distance_m:
                continue
            state, since = self._control_state(control)
            ahead.append(
                {
                    "id": control["id"],
                    "type": control["type"],
                    "state": state,
                    "stop_line_distance_m": round(distance, 2),
                    "changed_s_ago": round(self.time_s - since, 1),
                }
            )
        return sorted(ahead, key=lambda item: item["stop_line_distance_m"])

    def _apply_controls(self, ego_s: float, target_speed: float) -> float:
        """Haelt vor roter Ampel, geschlossenem oder ausgefallenem Signal an der Haltelinie."""
        for control in self._controls_ahead(ego_s):
            if control["state"] in HOLDING_STATES:
                gap = control["stop_line_distance_m"] - STOP_MARGIN_M
                target_speed = min(target_speed, max(0.0, 1.2 * gap))
        return target_speed

    def _held_by_control(self) -> bool:
        ego_s = self.ego_route_state[0]
        return self.ego.speed_mps < 0.3 and any(
            control["state"] in LEGAL_STOP_STATES and control["stop_line_distance_m"] <= 15.0
            for control in self._controls_ahead(ego_s)
        )

    def _path_blocked(self) -> bool:
        if self.mission_state == "PLANNED_STOP":
            return True
        if any(
            control["state"] in HOLDING_STATES and control["stop_line_distance_m"] <= 40.0
            for control in self._controls_ahead(self.ego_route_state[0])
        ):
            return True
        ego_s, ego_d, _, _ = self.ego_route_state
        blocker = self._blocking_actor(ego_s, ego_d)
        return blocker is not None and blocker.s_m - ego_s <= 25.0

    def _would_collide(self, advance_m: float = 0.3) -> bool:
        """Beruehrt der Ego-Kasten nach ``advance_m`` Fahrt in Blickrichtung einen Aktor?"""
        probe = Vehicle(
            self.ego.x_m + math.cos(self.ego.yaw_rad) * advance_m,
            self.ego.y_m + math.sin(self.ego.yaw_rad) * advance_m,
            self.ego.yaw_rad,
            0.0,
            self.ego.length_m,
            self.ego.width_m,
        )
        return any(
            boxes_overlap(probe, traffic.vehicle)
            for traffic in self.traffic
            if not traffic.actor.spec.phantom
        )

    def _hold(self, lane_d_m: float | None = None) -> Control:
        return self._route_control(self.ego, 1, self.lane_d_m if lane_d_m is None else lane_d_m, 0.0)

    def _autopilot_control(self) -> Control:
        ego_s, ego_d, _, _ = self.ego_route_state
        target_lane = self.lane_d_m
        target_speed = self.scenario.ego_cruise_speed_mps

        if self.maneuver_aborted and self.active is not None and self.active.source == "executor":
            return self._run_active(self.active, ego_s, ego_d)
        if self.maneuver_aborted:
            safely_inside_lane = ego_d + self.ego.width_m / 2.0 <= 0.1
            target_speed = 0.0 if safely_inside_lane else 1.0
            if (
                self.abort_recovery_active
                and safely_inside_lane
                and self.ego.speed_mps < 0.15
            ):
                self.abort_recovery_active = False
                self.abort_stabilized = True
                self.events.append(
                    Event(
                        self.time_s,
                        "abort_stabilized",
                        {"s_m": round(ego_s, 2), "d_m": round(ego_d, 2)},
                    )
                )
                # Die Lage kann sich aendern; der Agent entscheidet wieder.
                self.maneuver_aborted = False
                self._retrigger_blocked = False
                self.events.append(Event(self.time_s, "abort_released", {}))
            return self._route_control(self.ego, 1, target_lane, target_speed)

        if self.active is not None:
            return self._run_active(self.active, ego_s, ego_d)

        if self.incidents.detected or self.incidents.scene_secured:
            # Nach einem erkannten Unfall oder dem Sichern der Stelle bleibt das Fahrzeug stehen.
            return self._route_control(self.ego, 1, ego_d, 0.0)

        if self.pulled_over:
            if self._path_blocked():
                return self._route_control(self.ego, 1, ego_d, 0.0)
            self.pulled_over = False
            self.events.append(Event(self.time_s, "pull_over_released", {"s_m": round(ego_s, 2)}))

        target_speed = self._apply_planned_stop(ego_s, target_speed)
        target_speed = self._apply_controls(ego_s, target_speed)
        target_speed = self._apply_vulnerable_caution(ego_s, ego_d, target_speed)
        blocker = self._blocking_actor(ego_s, ego_d)
        if blocker:
            bumper_gap = blocker.s_m - ego_s - (self.ego.length_m + blocker.spec.length_m) / 2.0
            braking_distance = self.ego.speed_mps ** 2 / 8.0 + 5.0
            if bumper_gap <= braking_distance:
                target_speed = 0.0
        lead = self._lead_vehicle(ego_s, ego_d)
        if lead is not None:
            gap = lead.s_m - ego_s - (self.ego.length_m + lead.spec.length_m) / 2.0
            follow_speed = lead.speed_mps + 0.5 * (gap - (4.0 + 1.2 * self.ego.speed_mps))
            target_speed = min(target_speed, max(0.0, follow_speed))
        return self._route_control(self.ego, 1, target_lane, target_speed)

    # ------------------------------------------------------ Befehlsausfuehrung

    def _run_active(self, active: ActiveCommand, ego_s: float, ego_d: float) -> Control:
        name = active.request.command
        elapsed = self.time_s - active.start_time_s
        params = active.request.parameters

        if name == "WAIT":
            if elapsed >= WAIT_REFRESH_S and "verdicts" not in active.data:
                active.data["verdicts"] = self._reassess_scene()
            if elapsed >= float(params["duration_s"]):
                self._complete_wait(active, "WAIT_ELAPSED")
            elif elapsed >= 1.0 and not self._path_blocked():
                self._complete_wait(active, "PATH_CLEARED")
            return self._hold()

        if name == "REQUEST_REMOTE_ASSISTANCE":
            if elapsed >= self.scenario.remote_assistance.response_s:
                self._complete_remote_assistance(active)
            return self._hold()

        if name == "SAFE_STOP":
            if self.ego.speed_mps < 0.15:
                self._complete_safe_stop(active)
            return self._route_control(self.ego, 1, ego_d, 0.0)

        if name == "REVERSE_SHORT":
            return self._run_reverse(active, ego_s, elapsed)

        if name == "REPLAN_ROUTE":
            return self._run_turn_around(active, ego_s, ego_d, elapsed)

        if name == "PULL_OVER":
            return self._run_pull_over(active, ego_s, ego_d, elapsed)

        if name == "CROSS_LOW_RISK_OBJECT":
            return self._run_cross_low_risk_object(active, ego_s, ego_d, elapsed)

        if active.data.get("paused"):
            # Neue Lage: anhalten und auf die Entscheidung des Agenten warten.
            return self._route_control(self.ego, 1, ego_d, 0.0)

        if name == "ABORT_TO_LANE":
            return self._run_abort_to_lane(active, ego_s, ego_d, elapsed)

        if name == "PASS_OBSTACLE":
            phase = active.data.get("phase")
            if phase in {"PULLED_OUT", "PASSING"} and self._overtaken_traffic_moves(active):
                return self._yield_to_moving_traffic(active, ego_s, ego_d)
            if phase == "PULL_OUT":
                return self._run_pull_out(active, ego_s, ego_d)
            if phase == "PULLED_OUT":
                return self._run_wait_for_gap(active, ego_s, ego_d)

        if name in PASS_COMMANDS:
            return self._run_pass(active, ego_s, ego_d)

        raise AssertionError(f"no executor for {name}")

    # ------------------------------------------------------- Vorbeifahren in Phasen

    def _pause_at_checkpoint(
        self,
        active: ActiveCommand,
        phase: str,
        new_actor_ids: set[str],
        *,
        oncoming: bool = False,
        limited_sight: bool = False,
    ) -> None:
        """Haelt das Manoever an einer sicheren Stelle an und ruft den Agenten: die Lage ist neu."""
        active.data["paused"] = True
        active.data["phase"] = phase
        track_ids = sorted(self.tracking_model.track_id(actor_id) for actor_id in new_actor_ids)
        reasons = (
            (["NEW_OBJECTS"] if track_ids else [])
            + (["ONCOMING_TRAFFIC"] if oncoming else [])
            + (["LIMITED_SIGHT"] if limited_sight else [])
        )
        self._pending_trigger = {
            "type": "SITUATION_CHANGED",
            "command": active.request.command,
            "phase": phase,
            "reasons": reasons,
            "new_track_ids": track_ids,
        }
        self.events.append(
            Event(
                self.time_s,
                "situation_changed",
                {"action_id": active.action_id, "phase": phase, "reasons": reasons, "new_track_ids": track_ids},
            )
        )

    def _relevant_actor_ids(self, active: ActiveCommand) -> set[str]:
        """Was beim Vorbeifahren zaehlt: Gegenverkehr, Objekte vor dem Hindernis, Fuss- und Radverkehr."""
        self._refresh_perception(force=True)
        assert self.perception_snapshot is not None
        ego_s, _, _, _ = self.ego_route_state
        blocker_s = active.blocker_s_m if active.blocker_s_m is not None else ego_s
        road_half = self.scenario.road.lane_width_m + 1.0
        found = set()
        for actor in self.perception_snapshot.actors:
            if actor.spec.actor_id == active.blocker_id or actor.s_m < ego_s - 5.0:
                continue
            if actor.spec.kind in CAUTION_KINDS:
                if abs(actor.d_m) <= road_half:
                    found.add(actor.spec.actor_id)
            elif actor.spec.direction == -1 or actor.s_m > blocker_s:
                found.add(actor.spec.actor_id)
        return found

    def _travelled(self, active: ActiveCommand) -> float:
        return math.hypot(self.ego.x_m - active.start_xy[0], self.ego.y_m - active.start_xy[1])

    def _run_pull_out(self, active: ActiveCommand, ego_s: float, ego_d: float) -> Control:
        """PASS_OBSTACLE, Phase 1: hinter dem Hindernis so weit ausscheren, dass die Sicht frei wird."""
        plan = active.plan
        assert plan is not None and active.blocker_s_m is not None
        blocker = next((a for a in self._actor_states() if a.spec.actor_id == active.blocker_id), None)
        blocker_s = blocker.s_m if blocker is not None else active.blocker_s_m
        blocker_length = blocker.spec.length_m if blocker is not None else 4.5
        blocker_rear = blocker_s - blocker_length / 2.0
        front = ego_s + self.ego.length_m / 2.0
        peeked = (
            abs(ego_d - self.lane_d_m) >= PULL_OUT_PEEK_OFFSET_M or self._corner_d()[1] >= PULL_OUT_MAX_CORNER_D_M
        )
        if active.data.get("make_room", front > blocker_rear - PULL_OUT_ROOM_M and not peeked):
            # Zu dicht am Hindernis: erst gerade zuruecksetzen, um Anlauf zu gewinnen.
            active.data["make_room"] = True
            done = front <= blocker_rear - PULL_OUT_ROOM_M - 1.0 or self._rear_gap(ego_s, ego_d) < 0.6
            if done:
                if abs(self.ego.speed_mps) < 0.05:
                    active.data["make_room"] = False
                return Control(brake=1.0, reverse=True)
            return self._reverse_control(ego_s, ego_d)
        near_rear = front >= blocker_rear - 0.5
        blocked = self._would_collide(0.5)
        if (peeked or near_rear or blocked) and self.ego.speed_mps < 0.05:
            if not peeked:
                self._finish(active, "FAILED", "NO_ROOM_TO_PULL_OUT")
                return self._route_control(self.ego, 1, ego_d, 0.0)
            active.data["phase"] = "PULLED_OUT"
            active.data["gap_start_s"] = self.time_s
            new = self._relevant_actor_ids(active) - set(active.data.get("known", ()))
            oncoming = bool(self._situation_tools().oncoming_conflict(GAP_HORIZON_S)["conflict"])
            assert self.perception_snapshot is not None
            sight = Assessments(self._tracking_access(self.perception_snapshot)).oncoming()
            limited = not sight["sight_sufficient"]
            if new or oncoming or limited:
                # Neues sichtbar, Gegenverkehr naht oder die Gegenspur bleibt verdeckt:
                # Der Agent entscheidet, ob es weitergeht.
                active.data["known"] = set(active.data.get("known", ())) | new
                self._pause_at_checkpoint(active, "PULLED_OUT", new, oncoming=oncoming, limited_sight=limited)
            return self._route_control(self.ego, 1, ego_d, 0.0)
        speed = 0.0 if peeked or near_rear or blocked else PULL_OUT_SPEED_MPS
        return self._route_control(self.ego, 1, plan.target_d_m, speed, min_lookahead_m=PASS_LOOKAHEAD_M)

    def _overtaken_traffic_moves(self, active: ActiveCommand) -> bool:
        watched = {active.blocker_id, *active.data.get("chain", ())}
        return active.data.get("yielding", False) or any(
            actor.spec.actor_id in watched and actor.speed_mps >= 1.0 for actor in self._actor_states()
        )

    def _yield_to_moving_traffic(self, active: ActiveCommand, ego_s: float, ego_d: float) -> Control:
        """Neben anfahrendem Verkehr: abbremsen, ziehen lassen, dahinter einscheren."""
        if not active.data.get("yielding"):
            active.data["yielding"] = True
            active.data["yield_start_s"] = self.time_s
        watched = {active.blocker_id, *active.data.get("chain", ())}
        alongside = any(
            actor.spec.actor_id in watched
            and actor.s_m - actor.spec.length_m / 2.0 < ego_s + self.ego.length_m / 2.0 + 2.0
            and actor.s_m + actor.spec.length_m / 2.0 > ego_s - self.ego.length_m / 2.0 - 2.0
            # Nur wer seitlich noch im Weg ist; wer die Fahrbahn verlassen hat, zaehlt nicht.
            and abs(actor.d_m - ego_d) < (actor.spec.width_m + self.ego.width_m) / 2.0 + 1.0
            for actor in self._actor_states()
        )
        back_in_lane = abs(ego_d - self.lane_d_m) < 0.3
        if back_in_lane and not alongside:
            # Wieder hinter dem anfahrenden Verkehr in der Spur: Der Fahrstack folgt ihm.
            self._finish(active, "SUCCEEDED", "TRAFFIC_RESUMED")
            return self._hold()
        if self.time_s - float(active.data["yield_start_s"]) > GAP_TIMEOUT_S:
            # Sicherheitsnetz: Kommt die Lage nicht in Gang, entscheidet wieder der Agent.
            self._finish(active, "FAILED", "YIELD_TIMEOUT")
            return self._hold()
        speed = 0.0 if alongside or self._would_collide(0.5) else ABORT_SPEED_MPS
        return self._route_control(self.ego, 1, self.lane_d_m, speed, min_lookahead_m=PASS_LOOKAHEAD_M)

    def _stationary_chain(self, blocker: ActorState) -> tuple[str, ...]:
        """Stehende Objekte in der eigenen Spur direkt vor dem Hindernis, ohne Luecke zum Einscheren."""
        chain: list[str] = []
        front = blocker.s_m + blocker.spec.length_m / 2.0
        others = sorted(
            (
                actor
                for actor in self._actor_states()
                if actor.spec.actor_id != blocker.spec.actor_id
                and actor.spec.direction == 1
                and actor.speed_mps < 0.3
                and actor.s_m > blocker.s_m
                and abs(actor.d_m - self.lane_d_m) < (actor.spec.width_m + self.ego.width_m) / 2.0
            ),
            key=lambda actor: actor.s_m,
        )
        for actor in others:
            if actor.s_m - actor.spec.length_m / 2.0 - front > CHAIN_GAP_M:
                break
            chain.append(actor.spec.actor_id)
            front = actor.s_m + actor.spec.length_m / 2.0
        return tuple(chain)

    def _lane_blocker(self, ego_s: float) -> ActorState | None:
        """Naechstes stehendes Objekt in der eigenen Spur, auch wenn das Ego daneben steht."""
        candidates = [
            actor
            for actor in self._actor_states()
            if actor.spec.direction == 1
            and actor.speed_mps < 0.3
            and abs(actor.d_m - self.lane_d_m) < (actor.spec.width_m + self.ego.width_m) / 2.0
            and actor.s_m + actor.spec.length_m / 2.0 > ego_s - self.ego.length_m / 2.0
        ]
        return min(candidates, key=lambda actor: actor.s_m, default=None)

    def _run_abort_to_lane(self, active: ActiveCommand, ego_s: float, ego_d: float, elapsed: float) -> Control:
        """Zurueck in die eigene Spur: wenn noetig gerade zuruecksetzen, dann einscheren."""
        if elapsed > ABORT_TIMEOUT_S:
            self._finish(active, "FAILED", "TIMEOUT")
            return self._hold(ego_d)
        if "blocker" not in active.data:
            active.data["blocker"] = self._lane_blocker(ego_s)
        blocker = active.data["blocker"]
        front = ego_s + self.ego.length_m / 2.0
        blocker_rear = blocker.s_m - blocker.spec.length_m / 2.0 if blocker is not None else math.inf
        phase = active.data.setdefault("phase", "REVERSE" if front > blocker_rear - ABORT_MERGE_ROOM_M else "MERGE")
        if phase == "REVERSE":
            done = front <= blocker_rear - ABORT_MERGE_ROOM_M or self._travelled(active) >= ABORT_MAX_REVERSE_M
            if done or self._rear_gap(ego_s, ego_d) < 0.6:
                if abs(self.ego.speed_mps) < 0.05:
                    active.data["phase"] = "MERGE"
                return Control(brake=1.0, reverse=True)
            return self._reverse_control(ego_s, ego_d)
        near_blocker = front >= blocker_rear - 1.5
        in_lane = abs(ego_d - self.lane_d_m) < (ABORT_LANE_TOLERANCE_M if near_blocker else 0.3)
        if in_lane and self.ego.speed_mps < 0.05:
            self._finish(active, "SUCCEEDED", "BACK_IN_LANE")
            return self._hold()
        speed = 0.0 if near_blocker or in_lane or self._would_collide(0.5) else ABORT_SPEED_MPS
        if near_blocker and not in_lane and self.ego.speed_mps < 0.05:
            # Kein Platz zum Einscheren: noch einmal zuruecksetzen.
            active.data["phase"] = "REVERSE"
            active.start_xy = (self.ego.x_m, self.ego.y_m)
        return self._route_control(self.ego, 1, self.lane_d_m, speed, min_lookahead_m=PASS_LOOKAHEAD_M)

    def _corner_d(self) -> tuple[float, float]:
        """Kleinste und groesste Querlage der vier Fahrzeugecken."""
        values = [self.scenario.road.project(x, y)[1] for x, y in oriented_corners(self.ego)]
        return min(values), max(values)

    def _swept_collision(self, distance_m: float) -> bool:
        """Beruehrt der Fahrzeugkasten nach ``distance_m`` entlang der Blickrichtung ein Objekt?"""
        return distance_m != 0.0 and self._would_collide(distance_m)

    def _run_turn_around(self, active: ActiveCommand, ego_s: float, ego_d: float, elapsed: float) -> Control:
        """REPLAN_ROUTE mit Wende: abwechselnd vorwaerts links und rueckwaerts rechts eingeschlagen."""
        if elapsed > TURN_TIMEOUT_S:
            self._finish(active, "FAILED", "TIMEOUT")
            return Control(brake=1.0)
        data = active.data
        _, _, road_heading = self.scenario.road.to_xy(ego_s, ego_d)
        turned = abs(wrap_angle(self.ego.yaw_rad - road_heading))
        if data.setdefault("phase", "FORWARD") != "STRAIGHTEN" and turned >= TURN_DONE_RAD:
            data["phase"] = "STRAIGHTEN"
            data["straighten_xy"] = (self.ego.x_m, self.ego.y_m)
        if data["phase"] == "STRAIGHTEN":
            start = data["straighten_xy"]
            if math.hypot(self.ego.x_m - start[0], self.ego.y_m - start[1]) >= TURN_STRAIGHTEN_M:
                self._finish(active, "SUCCEEDED", "TURNED_AROUND", moves=data.get("moves", 1))
                self._complete_turn_around()
                return Control(brake=1.0)
            return self._route_control(self.ego, -1, -self.scenario.ego_lane_d_m, TURN_SPEED_MPS)
        lowest, highest = self._corner_d()
        left_edge = self.scenario.road.lane_width_m - TURN_EDGE_MARGIN_M
        right_edge = -(self.scenario.road.lane_width_m + self.scenario.shoulder_m) + TURN_EDGE_MARGIN_M
        forward = data["phase"] == "FORWARD"
        limit_reached = (
            (highest >= left_edge or self._swept_collision(0.6))
            if forward
            else (lowest <= right_edge or self._swept_collision(-0.6))
        )
        if limit_reached:
            if abs(self.ego.speed_mps) > 0.05:
                return Control(brake=1.0, reverse=not forward)
            if active.data["method"] == "U_TURN":
                self._finish(active, "FAILED", "ROAD_EDGE_REACHED")
                return Control(brake=1.0)
            data["moves"] = int(data.get("moves", 1)) + 1
            if data["moves"] > TURN_MAX_MOVES:
                self._finish(active, "FAILED", "TOO_MANY_MOVES")
                return Control(brake=1.0)
            data["phase"] = "REVERSE" if forward else "FORWARD"
            return Control(brake=1.0, reverse=forward)
        speed_error = TURN_SPEED_MPS - abs(self.ego.speed_mps)
        return Control(
            throttle=clamp(speed_error * 0.55, 0.0, 1.0),
            brake=clamp(-speed_error * 0.45, 0.0, 1.0),
            steering=1.0 if forward else -1.0,
            reverse=not forward,
        )

    def _complete_turn_around(self) -> None:
        route = self.scenario.route
        self.extra_time_s += route.detour_cost_s
        self.route_blocked = False
        self.route_known_blocked = False
        self._terminate("rerouted", detour_cost_s=route.detour_cost_s, turned_around=True)

    def _rear_gap(self, ego_s: float, ego_d: float) -> float:
        """Abstand zum naechsten Objekt hinter dem Heck in der aktuellen Querlage."""
        gap = math.inf
        for actor in self._actor_states():
            if actor.s_m < ego_s and abs(actor.d_m - ego_d) < (actor.spec.width_m + self.ego.width_m) / 2.0 + 0.3:
                gap = min(gap, ego_s - self.ego.length_m / 2.0 - (actor.s_m + actor.spec.length_m / 2.0))
        return gap

    def _reverse_control(self, ego_s: float, ego_d: float) -> Control:
        """Gerades Zuruecksetzen entlang der Strasse mit Kriechtempo."""
        _, _, road_heading = self.scenario.road.to_xy(ego_s, ego_d)
        error = wrap_angle(road_heading - self.ego.yaw_rad)
        speed_error = ABORT_SPEED_MPS - abs(self.ego.speed_mps)
        return Control(
            throttle=clamp(speed_error * 0.55, 0.0, 1.0),
            brake=clamp(-speed_error * 0.45, 0.0, 1.0),
            steering=clamp(-error / math.radians(28.0), -1.0, 1.0),
            reverse=True,
        )

    def _gap_open(self, horizon_s: float) -> bool:
        """Sieht die Wahrnehmung auf der Gegenspur genug Luecke (und nichts von hinten)?"""
        tools = self._situation_tools()
        return not tools.oncoming_conflict(horizon_s)["conflict"] and not tools.rear_conflict(horizon_s)["conflict"]

    def _run_wait_for_gap(self, active: ActiveCommand, ego_s: float, ego_d: float) -> Control:
        """Ausgeschert: auf eine Luecke im sichtbaren Verkehr warten, dann vorbeifahren."""
        if self._gap_open(GAP_HORIZON_S):
            active.data["open_s"] = float(active.data.get("open_s", 0.0)) + self.scenario.dt_s
        else:
            active.data["open_s"] = 0.0
        if float(active.data["open_s"]) >= GAP_CONFIRM_S:
            # Vertrag (Strecke, Zeit) gilt ab dem eigentlichen Vorbeifahren.
            active.data["phase"] = "PASSING"
            active.start_time_s = self.time_s
            active.start_s_m = ego_s
        elif self.time_s - float(active.data.get("gap_start_s", self.time_s)) > GAP_TIMEOUT_S:
            self._finish(active, "FAILED", "NO_GAP")
            self._start_command(
                CommandRequest("ABORT_TO_LANE", {}, "executor: no gap in oncoming traffic"), source="executor"
            )
        return self._route_control(self.ego, 1, ego_d, 0.0)

    def _run_reverse(self, active: ActiveCommand, ego_s: float, elapsed: float) -> Control:
        limit = float(active.request.parameters["max_distance_m"])
        travelled = math.hypot(
            self.ego.x_m - active.start_xy[0], self.ego.y_m - active.start_xy[1]
        )
        rear_gap = float("inf")
        _, ego_d, _, _ = self.ego_route_state
        for actor in self._actor_states():
            reach = (actor.spec.width_m + self.ego.width_m) / 2.0 + 0.3
            if actor.s_m < ego_s and abs(actor.d_m - ego_d) < reach:
                rear_gap = min(
                    rear_gap,
                    ego_s - self.ego.length_m / 2.0 - (actor.s_m + actor.spec.length_m / 2.0),
                )
        finished_reason = None
        if travelled >= limit - 0.05:
            finished_reason = "DISTANCE_REACHED"
        elif rear_gap < 0.6:
            finished_reason = "REAR_OBSTACLE"
        elif elapsed > REVERSE_TIMEOUT_S:
            finished_reason = "TIMEOUT"
        if finished_reason is not None:
            if abs(self.ego.speed_mps) < 0.05:
                self.reversed_m += travelled
                self._finish(
                    active,
                    "FAILED" if finished_reason == "TIMEOUT" else "SUCCEEDED",
                    finished_reason,
                    travelled_m=round(travelled, 2),
                )
                return self._hold()
            return Control(brake=1.0, reverse=True)
        speed_error = -REVERSE_SPEED_MPS - self.ego.speed_mps
        return Control(throttle=clamp(-speed_error * 0.6, 0.0, 1.0), reverse=True)

    def _run_pull_over(
        self, active: ActiveCommand, ego_s: float, ego_d: float, elapsed: float
    ) -> Control:
        target_d = pull_over_target_d(self.scenario, self.ego.width_m)
        if abs(ego_d - target_d) < 0.15:
            if self.ego.speed_mps < 0.1:
                self.pulled_over = True
                self._finish(active, "SUCCEEDED", "PULLED_OVER", d_m=round(ego_d, 2))
            return self._route_control(self.ego, 1, target_d, 0.0)
        if elapsed > PULL_OVER_TIMEOUT_S:
            self._finish(active, "FAILED", "PULL_OVER_BLOCKED")
            return self._hold(ego_d)
        speed = 0.0 if self._would_collide() else PULL_OVER_SPEED_MPS
        return self._route_control(self.ego, 1, target_d, speed, min_lookahead_m=PASS_LOOKAHEAD_M)

    def _run_cross_low_risk_object(
        self, active: ActiveCommand, ego_s: float, ego_d: float, elapsed: float
    ) -> Control:
        """Zertifizierter Geradeaus-Skill fuer ein zuvor validiertes, weiches Niedrigobjekt."""
        speed = CROSS_SPEED_MPS
        crossed = bool(active.data.get("object_crossed", False))
        if not crossed:
            blocker = next(
                (
                    item
                    for item in self.traffic
                    if item.actor.spec.actor_id == active.blocker_id
                ),
                None,
            )
            if blocker is None:
                self._finish(active, "FAILED", "OBJECT_LOST")
                return self._hold(ego_d)
            blocker_s = self.scenario.road.project(
                blocker.vehicle.x_m, blocker.vehicle.y_m
            )[0]
            contact_gap = (
                blocker_s
                - blocker.actor.spec.length_m / 2.0
                - (ego_s + self.ego.length_m / 2.0)
            )
            if contact_gap <= LOW_RISK_CONTACT_GAP_M:
                self._record_underbody(blocker.actor)
                self.traffic.remove(blocker)
                self.perception_model.forget(blocker.actor.spec.actor_id)
                active.data["object_crossed"] = True
                self.events.append(
                    Event(
                        self.time_s,
                        "low_risk_object_crossed",
                        {
                            "actor_id": blocker.actor.spec.actor_id,
                            "speed_mps": round(self.ego.speed_mps, 2),
                        },
                    )
                )
            elif elapsed > LOW_RISK_CROSS_TIMEOUT_S:
                self._finish(active, "FAILED", "CROSSING_TIMEOUT")
                return self._hold(ego_d)
            return self._route_control(
                self.ego, 1, self.lane_d_m, speed, min_lookahead_m=PASS_LOOKAHEAD_M
            )

        assert active.blocker_s_m is not None
        blocker_length = float(active.data["blocker_length_m"])
        clear_s = (
            active.blocker_s_m
            + blocker_length / 2.0
            + self.ego.length_m / 2.0
            + LOW_RISK_CLEARANCE_M
        )
        if ego_s >= clear_s:
            self.liberated = True
            self.events.append(
                Event(self.time_s, "liberated", {"s_m": round(ego_s, 2)})
            )
            self._finish(active, "SUCCEEDED", "LOW_RISK_OBJECT_CROSSED")
        return self._route_control(
            self.ego, 1, self.lane_d_m, speed, min_lookahead_m=PASS_LOOKAHEAD_M
        )

    def _run_pass(self, active: ActiveCommand, ego_s: float, ego_d: float) -> Control:
        plan = active.plan
        assert plan is not None and active.blocker_s_m is not None
        # Rueckkehr in die Spur erst, wenn das Ego das (ggf. inzwischen anfahrende) Hindernis
        # mit Sicherheitsabstand hinter sich gelassen hat.
        blocker = next(
            (item for item in self._actor_states() if item.spec.actor_id == active.blocker_id),
            None,
        )
        blocker_s = blocker.s_m if blocker is not None else active.blocker_s_m
        blocker_length = blocker.spec.length_m if blocker is not None else 4.5
        clear_s = blocker_s + blocker_length / 2.0 + self.ego.length_m / 2.0 + PASS_CLEARANCE_M
        for actor_id in active.data.get("chain", ()):
            # Erst hinter dem letzten Objekt der Kette einscheren.
            other = next((a for a in self._actor_states() if a.spec.actor_id == actor_id), None)
            if other is not None and other.speed_mps < 0.3:
                clear_s = max(clear_s, other.s_m + other.spec.length_m / 2.0 + self.ego.length_m / 2.0 + PASS_CLEARANCE_M)
        final_lane = plan.target_d_m if plan.keeps_new_lane else self.lane_d_m
        target_lane = plan.target_d_m if ego_s < clear_s else final_lane
        speed = plan.speed_mps
        if blocker is not None and blocker.speed_mps >= 0.3:
            # Minimum-Risk-Abschluss neben einem anfahrenden Hindernis: zuegig vorbeiziehen.
            speed = max(speed, min(blocker.speed_mps + 1.2, MAX_COMPLETION_SPEED_MPS))
        # Nie in ein Hindernis hineinschieben: stehen bleiben, die Freigabe laeuft dann aus.
        if self._would_collide():
            speed = 0.0
        if ego_s > clear_s + 7.0 and abs(ego_d - final_lane) < 0.35:
            self.liberated = True
            if plan.keeps_new_lane:
                self.lane_d_m = plan.target_d_m
            self.events.append(Event(self.time_s, "liberated", {"s_m": round(ego_s, 2)}))
            self._finish(active, "SUCCEEDED", "OBSTACLE_PASSED")
            if self.remote_instruction is not None and self.remote_instruction["type"] == "PASS_OBSTACLE":
                self.remote_instruction = None
                self.center_line_exception = False
            return self._route_control(self.ego, 1, self.lane_d_m, plan.speed_mps)
        # Kurzer Vorausschaupunkt: der Seitenversatz muss im Reststueck vor dem Hindernis gelingen.
        return self._route_control(self.ego, 1, target_lane, speed, min_lookahead_m=PASS_LOOKAHEAD_M)

    def _request_maneuver_abort(
        self,
        condition: str,
        evidence: dict[str, object],
    ) -> None:
        active = self.active
        if active is None or active.plan is None or self.maneuver_aborted:
            return
        ego_s, _, _, _ = self.ego_route_state
        blocker = next(
            (
                actor
                for actor in self._actor_states()
                if actor.spec.actor_id == active.blocker_id
            ),
            None,
        )
        blocker_rear_s = (
            blocker.s_m - blocker.spec.length_m / 2.0
            if blocker is not None
            else active.blocker_s_m
        )
        ego_front_s = ego_s + self.ego.length_m / 2.0
        phase = (
            "approach"
            if blocker_rear_s is None or ego_front_s < blocker_rear_s - 0.5
            else "committed"
        )
        details = {"condition": condition, "phase": phase, "evidence": evidence}
        if phase == "committed":
            if not self.minimum_risk_completion:
                self.minimum_risk_completion = True
                self.events.append(Event(self.time_s, "minimum_risk_completion", details))
            return

        self.maneuver_aborted = True
        self.abort_reason = condition
        self.abort_recovery_active = True
        self.events.append(Event(self.time_s, "maneuver_aborted", details))
        self._finish(active, "ABORTED", condition.upper())
        if active.plan is not None and active.plan.crosses_center:
            # Dicht hinter dem Hindernis hilft nur der sichere Rueckweg: gerade zuruecksetzen, dann einscheren.
            self._start_command(
                CommandRequest("ABORT_TO_LANE", {}, f"executor: aborted because of {condition}"), source="executor"
            )

    def _monitor_maneuver(self) -> None:
        active = self.active
        if active is None or active.plan is None or self.maneuver_aborted:
            return
        plan = active.plan
        ego_s, _, _, _ = self.ego_route_state
        progress = ego_s - active.start_s_m
        elapsed = self.time_s - active.start_time_s
        if active.data.get("paused") or active.data.get("phase") in {"PULL_OUT", "PULLED_OUT"}:
            elapsed = 0.0  # vor dem eigentlichen Vorbeifahren laeuft der Vertrag nicht
            progress = 0.0
        if progress > plan.max_distance_m or elapsed > PASS_VALID_FOR_S:
            self._request_maneuver_abort(
                "contract_expired",
                {"distance_m": round(progress, 2), "elapsed_s": round(elapsed, 2)},
            )
            return

    # ------------------------------------------------------- Befehlsverwaltung

    def _finish(
        self, active: ActiveCommand, status: str, outcome: str, **details: object
    ) -> None:
        elapsed = self.time_s - active.start_time_s
        entry = {
            "action_id": active.action_id,
            "command": active.request.command,
            "parameters": dict(active.request.parameters),
            "status": status,
            "outcome": outcome,
            "elapsed_s": round(elapsed, 2),
            "source": active.source,
        }
        self.command_history.append(entry)
        self.events.append(
            Event(self.time_s, "command_finished", {**entry, **details})
        )
        if self.active is active:
            self.active = None
        self.stopped_for_s = 0.0
        if outcome != "SUPERSEDED":
            # Sitzt das Fahrzeug danach noch fest, entscheidet der Agent sofort neu.
            self._recheck_after_command = True

    def _terminate(self, outcome: str, **details: object) -> None:
        self.terminal_outcome = outcome
        self.done = True
        self.events.append(Event(self.time_s, "episode_terminated", {"outcome": outcome, **details}))

    def _instant_finish(
        self, request: CommandRequest, action_id: str, source: str, status: str, outcome: str, **details: object
    ) -> None:
        ego_s, _, _, _ = self.ego_route_state
        self._finish(
            ActiveCommand(request, action_id, self.time_s, ego_s, (self.ego.x_m, self.ego.y_m), source),
            status,
            outcome,
            **details,
        )

    def _start_command(self, request: CommandRequest, *, source: str = "agent") -> None:
        self.action_counter += 1
        action_id = f"act-{self.action_counter}"
        self.commands.append(request)
        self.events.append(
            Event(
                self.time_s,
                "command_started",
                {"action_id": action_id, "source": source, **contract_to_payload(request)},
            )
        )
        name = request.command
        self._recheck_after_command = False
        ego_s, ego_d, _, _ = self.ego_route_state
        paused = self.active if self.active is not None and self.active.data.get("paused") else None
        if paused is not None and name != "RESUME":
            # Eine andere Entscheidung am Checkpoint beendet das angehaltene Manoever.
            self._finish(paused, "INTERRUPTED", "SUPERSEDED", by=name)
        instant = {
            "REPLAN_ROUTE": self._do_replan_route,
            "RETURN_HOME": self._do_return_home,
            "DROP_PASSENGER": self._do_drop_passenger,
            "HONK": self._do_honk,
            "RESUME": self._do_resume,
            "SECURE_SCENE": self._do_secure_scene,
            "REPORT_INCIDENT": self._do_report_incident,
        }.get(name)
        if instant is not None:
            instant(request, action_id, source)
            return

        active = ActiveCommand(
            request, action_id, self.time_s, ego_s, (self.ego.x_m, self.ego.y_m), source
        )
        if name in PASS_COMMANDS:
            tools = self._situation_tools()
            blocker = tools.blocker_ahead()
            if blocker is None:
                self._instant_finish(request, action_id, source, "FAILED", "NO_STATIONARY_BLOCKER")
                return
            try:
                active.plan = plan_pass(
                    request, self.scenario, self._ego_state(), blocker, self._facts()
                )
            except CommandError as exc:
                self._instant_finish(request, action_id, source, "FAILED", exc.code)
                return
            active.blocker_s_m = blocker.s_m
            active.blocker_id = blocker.spec.actor_id
            if name == "PASS_OBSTACLE":
                active.data["chain"] = self._stationary_chain(blocker)
                if active.plan.crosses_center:
                    # Ueber die Mittellinie: erst zum Schauen ausscheren, dann auf eine Luecke warten.
                    active.data["phase"] = "PULL_OUT"
                    active.data["known"] = self._relevant_actor_ids(active)
        elif name == "CROSS_LOW_RISK_OBJECT":
            tools = self._situation_tools()
            blocker = tools.blocker_ahead()
            if blocker is None:
                self._instant_finish(request, action_id, source, "FAILED", "NO_STATIONARY_BLOCKER")
                return
            active.blocker_s_m = blocker.s_m
            active.blocker_id = blocker.spec.actor_id
            active.data["blocker_length_m"] = blocker.spec.length_m
        elif name == "REQUEST_REMOTE_ASSISTANCE":
            self.remote_assistance_calls += 1
            self.events.append(
                Event(
                    self.time_s,
                    "remote_assistance_requested",
                    {"assistance_id": f"ra-{self.remote_assistance_calls}", **request.parameters},
                )
            )
        elif name == "SAFE_STOP":
            self.mission_state = "SAFE_STOPPING"
        self.active = active

    def _do_resume(self, request: CommandRequest, action_id: str, source: str) -> None:
        if self.remote_instruction is not None and self.remote_instruction["type"] == "PROCEED":
            self.remote_instruction = None  # Freigabe umgesetzt
        paused = self.active if self.active is not None and self.active.data.get("paused") else None
        if paused is None:
            self._instant_finish(request, action_id, source, "SUCCEEDED", "RESUMED")
            return
        self._instant_finish(request, action_id, source, "SUCCEEDED", "MANEUVER_CONTINUED")
        paused.data["paused"] = False
        # Weiter mit dem Warten auf eine Luecke; der Vertrag beginnt erst beim Vorbeifahren.
        paused.data["gap_start_s"] = self.time_s
        paused.data["open_s"] = 0.0

    def _do_honk(self, request: CommandRequest, action_id: str, source: str) -> None:
        if self.horn_time_s is None:
            self.horn_time_s = self.time_s
        self.events.append(Event(self.time_s, "horn_sounded", {"s_m": round(self.ego_route_state[0], 2)}))
        self._instant_finish(request, action_id, source, "SUCCEEDED", "HORN_SOUNDED")

    def _do_secure_scene(self, request: CommandRequest, action_id: str, source: str) -> None:
        self.incidents.hazard_lights = True
        self.incidents.scene_secured = True
        self.events.append(Event(self.time_s, "scene_secured", {"s_m": round(self.ego_route_state[0], 2)}))
        self._instant_finish(request, action_id, source, "SUCCEEDED", "SCENE_SECURED")

    def _do_report_incident(self, request: CommandRequest, action_id: str, source: str) -> None:
        log = self.incidents
        log.reported = True
        log.injured_reported = bool(request.parameters["injured_persons_suspected"])
        log.report_time_s = self.time_s
        self.incident_in_travel_lane = not self._at_road_edge()
        self.events.append(
            Event(
                self.time_s,
                "incident_reported",
                {
                    "injured_persons_suspected": log.injured_reported,
                    "impacts": len(log.third_party),
                },
            )
        )
        self._instant_finish(request, action_id, source, "SUCCEEDED", "INCIDENT_REPORTED")
        self._terminate("incident_reported")

    def _do_replan_route(self, request: CommandRequest, action_id: str, source: str) -> None:
        route = self.scenario.route
        failure = None
        # Wer selbst sieht, dass es vorne nicht weitergeht, darf wenden, auch wenn die Karte es nicht weiss.
        if not self.route_known_blocked and not (route.turn_required and self.route_blocked):
            failure = "ROUTE_NOT_BLOCKED"
        elif not route.alternative_available:
            failure = "NO_ALTERNATIVE_ROUTE"
        elif self.reversed_m < route.reverse_required_m - 1e-6:
            failure = "NO_MANEUVER_SPACE"
        if failure is not None:
            self._instant_finish(request, action_id, source, "FAILED", failure)
            return
        if route.turn_required:
            # Die Umleitung beginnt mit einer Wende: in einem Zug, wenn die Strasse breit genug ist.
            ego_s, _, _, _ = self.ego_route_state
            active = ActiveCommand(request, action_id, self.time_s, ego_s, (self.ego.x_m, self.ego.y_m), source)
            road_width = 2.0 * self.scenario.road.lane_width_m + self.scenario.shoulder_m
            active.data["method"] = "U_TURN" if road_width >= u_turn_width_m(self._ego_state()) else "THREE_POINT"
            self.active = active
            return
        self.extra_time_s += route.detour_cost_s
        self.route_blocked = False
        self.route_known_blocked = False
        self._instant_finish(
            request, action_id, source, "SUCCEEDED", "ROUTE_REPLANNED", detour_cost_s=route.detour_cost_s
        )
        self._terminate("rerouted", detour_cost_s=route.detour_cost_s)

    def _do_return_home(self, request: CommandRequest, action_id: str, source: str) -> None:
        self.extra_time_s += self.scenario.mission.hub_return_s
        self.passengers_returned_home = self.passengers_on_board
        self.mission_state = "RETURNED_HOME"
        self._instant_finish(request, action_id, source, "SUCCEEDED", "RETURNING_TO_HUB")
        self._terminate("returned_home", passengers=self.passengers_on_board)

    def _do_drop_passenger(self, request: CommandRequest, action_id: str, source: str) -> None:
        passengers = self.passengers_on_board
        safe = self._at_road_edge() and self.ego.speed_mps < 0.3
        if safe:
            self.passengers_dropped_safely = passengers
        else:
            self.passengers_injured = passengers
        self.passengers_on_board = 0
        self.mission_state = "PASSENGERS_DROPPED"
        self.events.append(
            Event(
                self.time_s,
                "passengers_dropped",
                {"passengers": passengers, "safe": safe},
            )
        )
        self._instant_finish(
            request, action_id, source, "SUCCEEDED", "PASSENGERS_DROPPED_SAFE" if safe else "PASSENGERS_ENDANGERED"
        )
        self._terminate("passenger_dropped", safe=safe)

    def _reassess_scene(self) -> list[dict[str, object]]:
        """Laengeres Beobachten im Stand: Geisterobjekte verschwinden, veraltete Daten werden
        erneuert und eine Sperrung, die nicht in der Karte steht, wird erkannt."""
        verdicts: list[dict[str, object]] = []
        for ghost in [item for item in self.traffic if item.actor.spec.phantom]:
            verdicts.append(
                {
                    "track_id": self.tracking_model.track_id(ghost.actor.spec.actor_id),
                    "verdict": "NO_PHYSICAL_OBJECT",
                }
            )
            self.traffic.remove(ghost)
            self.perception_model.forget(ghost.actor.spec.actor_id)
        self.stale_cleared = True
        if self.route_blocked and not self.route_known_blocked:
            self.route_known_blocked = True  # Die Karte ist jetzt aktualisiert.
            verdicts.append({"type": "UNMAPPED_ROAD_CLOSURE", "confidence": 0.9})
        return verdicts

    def _complete_wait(self, active: ActiveCommand, outcome: str) -> None:
        """WAIT endet mit einem Beobachtungsbericht unter additional_information."""
        tools = self._agent_tools()
        found = active.data.get("verdicts", [])
        result: dict[str, object] = {
            "observed_s": round(self.time_s - active.start_time_s, 1),
            "objects": [
                {
                    "track_id": self.tracking_model.track_id(actor.spec.actor_id),
                    "distance_m": round(actor.s_m - tools.ego.s_m, 1),
                    "speed_mps": round(actor.speed_mps, 2),
                    "moving": actor.speed_mps >= 0.3,
                }
                for actor in tools.actors
            ],
            "blocker_still_present": tools.blocker_ahead() is not None,
            "map_consistent": not any("type" in item for item in found),
            "map_mismatches": [item for item in found if "type" in item],
        }
        # Das Fahrzeug spuert einen Stoss, auch wenn er keine eigene Runde ausloest.
        felt = [impact.status_entry() for impact in self.incidents.impacts if impact.time_s >= active.start_time_s]
        if felt:
            result["impacts_registered"] = felt
        ghosts = [item for item in found if "track_id" in item]
        if ghosts:
            result["reassessed_tracks"] = ghosts
        self.additional_information.append({"time_s": round(self.time_s, 2), "result": result})
        self._finish(active, "SUCCEEDED", outcome)

    def _complete_remote_assistance(self, active: ActiveCommand) -> None:
        """Die Leitstelle antwortet mit einer Anweisung, die der Agent befolgen muss."""
        assistance = self.scenario.remote_assistance
        if not assistance.available:
            self._finish(active, "FAILED", "REMOTE_ASSISTANCE_UNAVAILABLE")
            return
        instruction = (
            dict(assistance.instruction) if isinstance(assistance.instruction, dict) else self._auto_instruction()
        )
        kind = str(instruction["type"])
        actor_ids = frozenset(str(item) for item in instruction.get("clears", ()))
        control_ids = {str(item) for item in instruction.get("control_ids", ())}
        if kind == "PROCEED":
            # Freigabe: ausgefallenes Signal ueberbruecken oder Weg ist inzwischen geraeumt.
            self.removed_controls |= control_ids
            self._clear_actors(actor_ids)
        elif kind == "WAIT" and actor_ids:
            self._scheduled_clear = (self.time_s + float(instruction.get("after_s", 30.0)), actor_ids)
        elif kind == "PASS_OBSTACLE":
            self.center_line_exception = True
        elif kind == "REPLAN_ROUTE":
            self.route_known_blocked = True
        self.remote_resolved = kind in {"PROCEED", "PASS_OBSTACLE", "WAIT"}
        self.remote_instruction = {
            "type": kind,
            "message": str(instruction.get("message", "")),
            **({"side": instruction["side"]} if instruction.get("side") else {}),
            **({"expected_wait_s": float(instruction.get("after_s", 30.0))} if kind == "WAIT" and actor_ids else {}),
        }
        self.events.append(Event(self.time_s, "remote_instruction", dict(self.remote_instruction)))
        self._finish(active, "SUCCEEDED", f"INSTRUCTION_{kind}")
        self._pending_trigger = {"type": "REMOTE_INSTRUCTION", "instruction": kind}

    def _clear_actors(self, actor_ids: frozenset[str] | set[str]) -> None:
        removed = [item for item in self.traffic if item.actor.spec.actor_id in actor_ids]
        for item in removed:
            self.traffic.remove(item)
            self.perception_model.forget(item.actor.spec.actor_id)
        if removed:
            self.events.append(
                Event(self.time_s, "remote_assistance_cleared", {"actor_ids": sorted(actor_ids)})
            )

    def _auto_instruction(self) -> dict[str, object]:
        """Was eine Leitstelle aus Kamerabild und Lage anweist, wenn das Szenario nichts vorgibt."""
        ego_s, ego_d, _, _ = self.ego_route_state
        route = self.scenario.route
        blocker = self._blocking_actor(ego_s, ego_d)
        controls = self._controls_ahead(ego_s, 40.0)
        if any(control["state"] in LEGAL_STOP_STATES for control in controls):
            # Ueber Rot oder eine geschlossene Schranke gibt die Leitstelle nie frei.
            return {"type": "WAIT", "message": "Regular stop at a traffic light or barrier. Wait."}
        dark = [control["id"] for control in controls if control["state"] == "DARK"]
        if blocker is None and dark:
            return {
                "type": "PROCEED",
                "control_ids": dark,
                "message": "Signal out of order; the intersection is clear. Proceed carefully.",
            }
        if self.route_blocked or (blocker is not None and blocker.spec.kind == "barrier"):
            if route.alternative_available:
                return {"type": "REPLAN_ROUTE", "message": "Road closed. Take the detour."}
            return {"type": "END_TRIP", "message": "Road closed, no detour. End the trip safely."}
        tools = self._situation_tools()
        if blocker is None or blocker.spec.kind in CAUTION_KINDS:
            if tools.vulnerable_road_users()["conflict"]:
                return {"type": "WAIT", "message": "People or animals on the road. Wait until the path is clear."}
            return {"type": "PROCEED", "message": "No obstacle visible. Proceed."}
        request = CommandRequest("PASS_OBSTACLE", {"side": "LEFT"}, "Leitstelle")
        try:
            plan_pass(request, self.scenario, self._ego_state(), blocker, replace(self._facts(), center_line_exception=True))
        except CommandError:
            if route.alternative_available:
                return {"type": "REPLAN_ROUTE", "message": "Passing is not possible. Take the detour."}
            return {"type": "END_TRIP", "message": "Passing is not possible. End the trip safely."}
        message = "Pass on the left; watch for oncoming traffic."
        if self.scenario.road.center_marking == "solid" and not self.scenario.allow_solid_exception:
            message = "Exception granted: the solid line may be crossed to pass. Watch for oncoming traffic."
        return {"type": "PASS_OBSTACLE", "side": "LEFT", "message": message}

    def _complete_safe_stop(self, active: ActiveCommand) -> None:
        self.mission_state = "SAFE_STOPPED"
        self.stopped_in_travel_lane = not self._at_road_edge()
        self._finish(
            active, "SUCCEEDED", "SAFE_STOP_REACHED", in_travel_lane=self.stopped_in_travel_lane
        )
        self._terminate("safe_stopped", in_travel_lane=self.stopped_in_travel_lane)

    # ----------------------------------------------------- Autonomy Recovery

    def _stale_fault(self) -> dict[str, object] | None:
        return next((f for f in self.scenario.faults if f["type"] == "stale_data"), None)

    def _tool_fault(self, name: str) -> None:
        for fault in self.scenario.faults:
            if fault["type"] == "tool_timeout" and fault["tool"] == name:
                used = self.tool_fault_counts.get(name, 0)
                if used < int(fault.get("failures", 1)):
                    self.tool_fault_counts[name] = used + 1
                    raise AgentToolError(
                        "TIMEOUT", f"{name} did not answer in time; calling it again is possible"
                    )

    def _agent_tools(self) -> SituationTools:
        """Werkzeugsicht des Agenten; kann veraltet sein (Szenariofehler ``stale_data``)."""
        tools = self._situation_tools(force_perception=True)
        if self._stale_snapshot is None or self.stale_cleared:
            return tools
        snapshot, taken_s = self._stale_snapshot
        return replace(
            tools,
            actors=snapshot.actors,
            perception=snapshot,
            observation_age_s=self.time_s - taken_s,
            vla=self._vla_access(snapshot),
            tracking=self._tracking_access(snapshot),
        )

    def _update_recovery(self) -> None:
        ego_s, ego_d, _, _ = self.ego_route_state
        if self.mode != "autopilot":
            self.stopped_for_s = 0.0
            return
        if self.ego.speed_mps < 0.3 and ego_s < self.scenario.ego_target_s_m - 1.0:
            self.stopped_for_s += self.scenario.dt_s
        else:
            self.stopped_for_s = 0.0
        if self._deferred is not None and not self.done:
            request, ready_s = self._deferred
            if self.time_s + 1e-9 < ready_s:
                return
            self._deferred = None
            if self.maneuver_aborted:
                # Der Ausfuehrer hat inzwischen selbst abgebrochen; die Entscheidung kommt zu spaet.
                self.events.append(Event(self.time_s, "decision_dropped", contract_to_payload(request)))
                return
            self._start_command(request)
            return
        paused_checkpoint = (
            self.active is not None
            and self.active.data.get("paused")
            and self._pending_trigger is not None
            and self._pending_trigger.get("type") == "SITUATION_CHANGED"
        )
        if self.done or (self.active is not None and not paused_checkpoint):
            return
        if self.agent_status == "waiting_for_agent":
            return
        if self.remote_instruction is not None and self.ego.speed_mps >= INSTRUCTION_DONE_SPEED_MPS:
            # Das Fahrzeug faehrt wieder: Die Anweisung ist erledigt, eine Ausnahme erlischt.
            self.remote_instruction = None
            self.center_line_exception = False
        if self._recheck_after_command and self.active is None:
            if self.ego.speed_mps >= 0.3:
                self._recheck_after_command = False
            elif self._pending_trigger is None and self._stuck_now():
                self._recheck_after_command = False
                if not self.maneuver_aborted:
                    last = self.command_history[-1] if self.command_history else {}
                    self._pending_trigger = {
                        "type": "DEADLOCK",
                        "after_command": last.get("command"),
                        "outcome": last.get("outcome"),
                    }
        trigger = self._pending_trigger
        if trigger is not None:
            # Unfall oder Kontrollpunkt nach einem Manoever: sofort, ohne Standzeit abzuwarten.
            self._pending_trigger = None
            if len(self.commands) < self.scenario.max_commands:
                self._open_session(trigger)
            return
        if self._retrigger_blocked:
            return
        if self.stopped_for_s < self.scenario.stuck_after_s:
            return
        if len(self.commands) >= self.scenario.max_commands:
            self._retrigger_blocked = True
            self.events.append(
                Event(self.time_s, "command_budget_exhausted", {"max_commands": self.scenario.max_commands})
            )
            return
        if self.maneuver_aborted:
            # Nach einem sicherheitsbedingten Abbruch ist die Episode fuer den Agenten beendet.
            self._retrigger_blocked = True
            return
        self._open_session({"type": "DEADLOCK"})

    def _stuck_now(self) -> bool:
        """Steht das Fahrzeug fest, ohne dass ein regulaerer Halt es erklaert?"""
        if self.pulled_over:
            return True
        if self.mission_state == "PLANNED_STOP" or not self._path_blocked():
            return False
        return not any(
            control["state"] in LEGAL_STOP_STATES and control["stop_line_distance_m"] <= 40.0
            for control in self._controls_ahead(self.ego_route_state[0])
        )

    def _apply_scheduled_clear(self) -> None:
        if self._scheduled_clear is not None and self.time_s >= self._scheduled_clear[0]:
            _, actor_ids = self._scheduled_clear
            self._scheduled_clear = None
            self._clear_actors(actor_ids)

    def _open_session(self, trigger: dict[str, object] | None) -> None:
        if self._first_session_s is None:
            self._first_session_s = self.time_s
        tools = self._agent_tools()
        self.session_counter += 1
        session = AgentSession(
            tools,
            time_s=self.time_s,
            stopped_for_s=self.stopped_for_s,
            max_tool_calls=self.scenario.max_tool_calls,
            additional_information=self.additional_information,
            commands_used=len(self.commands),
            tool_fault=self._tool_fault,
            trigger=trigger,
            remote_instruction=self.remote_instruction,
        )
        self.agent_observation = session.context
        self.events.append(
            Event(
                self.time_s,
                "recovery_requested",
                {"stopped_for_s": round(self.stopped_for_s, 2), **({"trigger": trigger} if trigger else {})},
            )
        )
        self.events.append(Event(self.time_s, "agent_observation", session.context))
        if self.agent is None:
            self.agent_status = "waiting_for_agent"
            self.events.append(
                Event(
                    self.time_s,
                    "agent_requested",
                    {"message": "no decision agent connected"},
                )
            )
            if self.console:
                self.pending_session = session
                self._pending_flushed = 0
                self.console_error = None
            return

        self.agent_status = "active"
        self.events.append(Event(self.time_s, "agent_activated", {"name": self.agent_name}))
        try:
            raw_decision = self.agent(session)
            request = session.complete(raw_decision)
        except Exception as exc:  # Agent code is an untrusted laboratory extension point.
            self.agent_status = "error"
            self.agent_error = str(exc)
            request = CommandRequest(
                "WAIT",
                {"duration_s": FALLBACK_WAIT_S},
                f"Fail-safe: ungueltige Agentenausgabe ({exc})",
            )
            self.events.append(Event(self.time_s, "agent_contract_error", {"message": str(exc)}))
        else:
            self.agent_status = "decided"
        self._record_trace(session, 0)
        self.events.append(Event(self.time_s, "agent_decision", contract_to_payload(request)))
        if trigger is not None and trigger.get("type") == "SITUATION_CHANGED":
            # Am Checkpoint laeuft die Uhr: Bedenkzeit aus Werkzeugaufrufen und Modellrunden.
            latency = DECISION_S_PER_TOOL_CALL * session.tool_call_count + DECISION_S_PER_MODEL_ROUND * sum(
                entry["type"] == "model_round" for entry in session.trace
            )
            self.events.append(Event(self.time_s, "decision_latency", {"seconds": round(latency, 2)}))
            if latency > 0:
                self._deferred = (request, self.time_s + latency)
                return
        self._start_command(request)

    def _record_trace(self, session: AgentSession, start: int) -> None:
        for trace_entry in session.trace[start:]:
            entry = {**trace_entry, "session": self.session_counter}
            self.agent_trace.append(entry)
            event_name = {
                "tool_call": "agent_tool_call",
                "decision_attempt": "agent_decision_attempt",
                "model_round": "agent_model_round",
            }.get(str(trace_entry["type"]), "agent_trace")
            self.events.append(Event(self.time_s, event_name, entry))
            if trace_entry["type"] == "decision_attempt" and not trace_entry.get("accepted"):
                self.rejected_commands += 1
            if trace_entry["type"] == "decision_attempt" and trace_entry.get("warnings"):
                self.safety_warnings.extend(trace_entry["warnings"])  # type: ignore[arg-type]

    # ------------------------------------------------------- Recovery Console

    def _flush_pending_trace(self) -> None:
        session = self.pending_session
        if session is not None:
            self._record_trace(session, self._pending_flushed)
            self._pending_flushed = len(session.trace)

    def console_tool(self, name: str, arguments: dict[str, object] | None = None) -> None:
        """Fragt ein Lagewerkzeug fuer die offene Konsolensitzung ab (Fehler landen in ``console_error``)."""
        session = self.pending_session
        if session is None:
            return
        try:
            session.call_tool(name, arguments)
            self.console_error = None
        except AgentSessionError as exc:
            self.console_error = str(exc)
        self._flush_pending_trace()

    def console_gather_evidence(self) -> None:
        """Ruft alle noch fehlenden Lagewerkzeuge auf, die Manoever als Evidenz verlangen."""
        session = self.pending_session
        if session is None:
            return
        required = COMMANDS["PASS_OBSTACLE"].evidence_tools
        for name in sorted(required - session.called_tools):
            self.console_tool(name)

    def console_submit(self, payload: dict[str, object]) -> None:
        """Reicht einen Befehl ein; er durchlaeuft dieselbe Pruefkette wie der eines Agenten."""
        session = self.pending_session
        if session is None:
            return
        try:
            request = session.submit_decision(payload)
        except AgentBudgetError as exc:
            # Wie beim Agenten: erschoepftes Entscheidungsbudget fuehrt fail-safe zu WAIT.
            self.console_error = None
            self.agent_error = str(exc)
            request = CommandRequest(
                "WAIT", {"duration_s": FALLBACK_WAIT_S}, f"fail-safe: decision budget exhausted ({exc})"
            )
            self.events.append(Event(self.time_s, "agent_contract_error", {"message": str(exc)}))
        except (AgentSessionError, CommandError) as exc:
            self.console_error = str(exc)
            self._flush_pending_trace()
            return
        self.console_error = None
        self._flush_pending_trace()
        self.pending_session = None
        self.agent_name = "manual"
        self.agent_status = "manual"
        self.events.append(Event(self.time_s, "agent_decision", contract_to_payload(request)))
        self._start_command(request, source="manual")

    def console_snapshot(self) -> dict[str, object]:
        session = self.pending_session
        results: dict[str, object] = {}
        if session is not None:
            for entry in session.trace:
                if entry["type"] == "tool_call" and "result" in entry:
                    results[str(entry["name"])] = entry["result"]
        return {
            "enabled": self.console,
            "pending": session is not None,
            "incident_pending": self.incidents.detected and not self.incidents.reported,
            "session": self.session_counter,
            "tool_calls": session.tool_call_count if session else 0,
            "max_tool_calls": session.max_tool_calls if session else 0,
            "decision_attempts": session.decision_attempt_count if session else 0,
            "max_decision_attempts": session.max_decision_attempts if session else 0,
            "called_tools": sorted(session.called_tools) if session else [],
            "tool_results": results,
            "error": self.console_error,
            "remote_instruction": dict(self.remote_instruction) if self.remote_instruction else None,
        }

    def _update_safety(self) -> None:
        for traffic in self.traffic:
            if traffic.actor.spec.phantom:
                continue  # Geisterobjekt: fuer Wahrnehmung vorhanden, physisch nicht
            center_distance = math.hypot(
                self.ego.x_m - traffic.vehicle.x_m,
                self.ego.y_m - traffic.vehicle.y_m,
            )
            radii = math.hypot(self.ego.length_m, self.ego.width_m) / 2.0 + math.hypot(
                traffic.vehicle.length_m, traffic.vehicle.width_m
            ) / 2.0
            if traffic.actor.spec.width_m < 20.0:  # ein 60 m langer Zug verfaelscht die Naeherung
                self.minimum_clearance_m = min(self.minimum_clearance_m, center_distance - radii)
            if boxes_overlap(self.ego, traffic.vehicle):
                self._record_impact(traffic)

    def _record_impact(self, traffic: TrafficVehicle) -> None:
        """Aufprall: protokollieren, je nach Schwere anhalten, Sitzung ausloesen."""
        actor_id = traffic.actor.spec.actor_id
        if any(impact.actor_id == actor_id for impact in self.incidents.impacts):
            return  # dieselbe Beruehrung, solange sich die Kaesten ueberlappen
        other = traffic.vehicle
        closing = math.hypot(
            self.ego.speed_mps * math.cos(self.ego.yaw_rad) - other.speed_mps * math.cos(other.yaw_rad),
            self.ego.speed_mps * math.sin(self.ego.yaw_rad) - other.speed_mps * math.sin(other.yaw_rad),
        )
        vulnerable = traffic.actor.spec.kind in VULNERABLE_KINDS
        # Steht das Ego still, faehrt kein eigenes Manoever und kommt der andere angefahren, ist er aufgefahren.
        ego_still = self.ego.speed_mps < 0.2 and (self.active is None or self.active.request.command == "WAIT")
        at_fault = not (ego_still and other.speed_mps >= 0.2)
        impact = Impact(
            time_s=self.time_s,
            actor_id=actor_id,
            kind="COLLISION",
            severity=severity_of(closing, vulnerable),
            direction=direction_of((self.ego.x_m, self.ego.y_m), self.ego.yaw_rad, (other.x_m, other.y_m)),
            closing_speed_mps=closing,
            vulnerable=vulnerable,
            position_xy=(self.ego.x_m, self.ego.y_m),
            ego_at_fault=at_fault,
        )
        self.incidents.impacts.append(impact)
        if at_fault:
            self.collision = True
            self.collision_with_vulnerable = self.collision_with_vulnerable or vulnerable
        else:
            # Der Unfallgegner haelt Stossstange an Stossstange an.
            other.speed_mps = 0.0
            traffic.halted = True
            ego_s, _, _, _ = self.ego_route_state
            other_s, other_d, _, _ = self.scenario.road.project(other.x_m, other.y_m)
            if other_s < ego_s:
                behind = ego_s - (self.ego.length_m + other.length_m) / 2.0 - 0.1
                other.x_m, other.y_m, _ = self.scenario.road.to_xy(min(other_s, behind), other_d)
        self.events.append(
            Event(
                self.time_s,
                "collision",
                {
                    "actor_id": actor_id,
                    "vulnerable": vulnerable,
                    "severity": impact.severity,
                    "direction": impact.direction,
                    "closing_speed_mps": round(closing, 2),
                    "ego_at_fault": at_fault,
                },
            )
        )
        if not impact.detected:
            return  # leichte Beruehrung: Das Fahrzeug merkt es nicht und faehrt weiter
        self.ego.speed_mps = 0.0
        other.speed_mps = 0.0
        traffic.halted = True
        if self.active is not None:
            self._finish(self.active, "ABORTED", "IMPACT")
        self._pending_trigger = {
            "type": "IMPACT_DETECTED",
            "severity": impact.severity,
            "direction": impact.direction,
        }

    def _record_underbody(self, actor: ActorState) -> None:
        """Ueberfahren eines Objekts, das die Pruefung nicht bestanden haette: eigener Schaden."""
        profile = actor.spec.obstacle_profile
        if profile is not None and not (
            profile.height_m > LOW_RISK_MAX_HEIGHT_M
            or profile.material not in LOW_RISK_MATERIALS
            or profile.deformability != "HIGH"
            or profile.sharp_edges
            or profile.liquid
        ):
            return
        rigid = (
            profile is None
            or profile.height_m > LOW_RISK_MAX_HEIGHT_M
            or profile.material not in LOW_RISK_MATERIALS
            or profile.deformability != "HIGH"
        )
        impact = Impact(
            time_s=self.time_s,
            actor_id=actor.spec.actor_id,
            kind="UNDERBODY",
            severity="MODERATE" if rigid else "LIGHT",
            direction="FRONT",
            closing_speed_mps=self.ego.speed_mps,
            vulnerable=False,
            position_xy=(self.ego.x_m, self.ego.y_m),
        )
        self.incidents.impacts.append(impact)
        self.events.append(
            Event(self.time_s, "underbody_impact", {"actor_id": actor.spec.actor_id, "severity": impact.severity})
        )

    def _emergency_brake_needed(self) -> bool:
        """Notbremse: ein wahrgenommenes Objekt im Fahrweg innerhalb des Anhaltewegs.

        Sie sieht nur, was die Wahrnehmung sieht; was hinter einer Verdeckung hervorkommt,
        erfasst sie zu spaet. Beruehrte Objekte und Phantome im eigenen Kasten zaehlen nicht.
        """
        speed = self.ego.speed_mps
        if abs(speed) < 0.3 or self.perception_snapshot is None:
            return False
        reach = speed * speed / (2.0 * AEB_DECELERATION_MPS2) + AEB_MARGIN_M
        sign = 1.0 if speed > 0 else -1.0
        probe = Vehicle(
            self.ego.x_m + sign * math.cos(self.ego.yaw_rad) * reach,
            self.ego.y_m + sign * math.sin(self.ego.yaw_rad) * reach,
            self.ego.yaw_rad,
            0.0,
            self.ego.length_m,
            self.ego.width_m,
        )
        for actor in self.perception_snapshot.actors:
            if any(impact.actor_id == actor.spec.actor_id for impact in self.incidents.impacts):
                continue
            x_m, y_m, heading = self.scenario.road.to_xy(actor.s_m, actor.d_m)
            box = Vehicle(x_m, y_m, heading, 0.0, actor.spec.length_m, actor.spec.width_m)
            if boxes_overlap(probe, box) and not boxes_overlap(self.ego, box):
                return True
        return False

    def _abort_stalled_maneuver(self, dt_s: float) -> None:
        """Ein festgefahrenes Manoever endet; der Agent entscheidet am Kontrollpunkt neu."""
        active = self.active
        if (
            active is None
            or active.request.command not in MANEUVER_COMMANDS
            or active.data.get("paused")
            or active.data.get("phase") == "PULLED_OUT"  # wartet ausgeschert auf eine Luecke
            or self.maneuver_aborted
        ):
            self._maneuver_stalled_s = 0.0
            return
        if abs(self.ego.speed_mps) >= 0.1 or self.time_s - active.start_time_s < MANEUVER_STALL_ABORT_S:
            self._maneuver_stalled_s = 0.0
            return
        self._maneuver_stalled_s += dt_s
        if self._maneuver_stalled_s >= MANEUVER_STALL_ABORT_S:
            self._maneuver_stalled_s = 0.0
            self._finish(
                active, "ABORTED", "EMERGENCY_BRAKE" if self.emergency_brake_active else "STALLED"
            )
            _, ego_d, _, _ = self.ego_route_state
            if active.request.command == "PASS_OBSTACLE" and abs(ego_d - self.lane_d_m) > 0.5:
                # Nie halb auf der Gegenspur stehen bleiben: zurueck hinter das Hindernis.
                self._start_command(
                    CommandRequest("ABORT_TO_LANE", {}, "executor: maneuver stuck"), source="executor"
                )

    def _trigger_state(self) -> dict[str, object]:
        ego_s, ego_d, _, _ = self.ego_route_state
        phases: set[str] = set()
        if self.active is not None:
            name = self.active.request.command
            phases.add(name)
            if self.active.data.get("phase"):
                phases.add(f"{name}.{self.active.data['phase']}")
        return {
            "time_s": self.time_s,
            "ego_s_m": ego_s,
            "ego_lateral_offset_m": ego_d - self.lane_d_m,
            "since_first_session_s": (
                None if self._first_session_s is None else self.time_s - self._first_session_s
            ),
            "since_horn_s": None if self.horn_time_s is None else self.time_s - self.horn_time_s,
            "ego_phase": phases,
        }

    def _evaluate_triggers(self) -> None:
        state: dict[str, object] | None = None
        for traffic in self.traffic:
            for index, trigger in enumerate(traffic.actor.spec.triggers):
                if index in traffic.fired:
                    continue
                state = state or self._trigger_state()
                if not conditions_met(trigger["when"], state):
                    continue
                traffic.fired.add(index)
                effect, settings = next(iter(trigger["then"].items()))
                if effect == "start_motion":
                    traffic.override = {"target_speed_mps": float(settings["target_speed_mps"])}
                elif effect == "stop":
                    traffic.override = {"target_speed_mps": 0.0}
                else:
                    traffic.override = {"cross": (float(settings["exit_d_m"]), float(settings["speed_mps"]))}
                self.events.append(
                    Event(self.time_s, "actor_triggered", {"actor_id": traffic.actor.spec.actor_id, "effect": effect})
                )

    def _move_overridden(self, traffic: TrafficVehicle, dt_s: float) -> None:
        override = traffic.override
        assert override is not None
        if "cross" in override:
            exit_d, speed = override["cross"]
            s_m, d_m, _, _ = self.scenario.road.project(traffic.vehicle.x_m, traffic.vehicle.y_m)
            remaining = exit_d - d_m
            if abs(remaining) < 0.02:
                traffic.vehicle.speed_mps = 0.0
                return
            step = min(abs(remaining), speed * dt_s)
            traffic.vehicle.x_m, traffic.vehicle.y_m, _ = self.scenario.road.to_xy(s_m, d_m + math.copysign(step, remaining))
            traffic.vehicle.speed_mps = speed
            return
        spec = traffic.actor.spec
        target_speed = float(override["target_speed_mps"])
        if target_speed <= 0.0 and traffic.vehicle.speed_mps <= 0.0:
            return
        step_vehicle(traffic.vehicle, self._route_control(traffic.vehicle, spec.direction, spec.d_m, target_speed), dt_s)

    def _move_crossing(self, traffic: TrafficVehicle, dt_s: float) -> None:
        spec = traffic.actor.spec
        vehicle = traffic.vehicle
        assert spec.exit_d_m is not None and spec.motion_start_time_s is not None
        if self.time_s < spec.motion_start_time_s:
            vehicle.speed_mps = 0.0
            return
        assert spec.target_speed_mps is not None
        s_m, d_m, _, _ = self.scenario.road.project(vehicle.x_m, vehicle.y_m)
        remaining = spec.exit_d_m - d_m
        if abs(remaining) < 0.02:
            vehicle.speed_mps = 0.0
            return
        step = min(abs(remaining), spec.target_speed_mps * dt_s)
        new_d = d_m + math.copysign(step, remaining)
        vehicle.x_m, vehicle.y_m, _ = self.scenario.road.to_xy(s_m, new_d)
        vehicle.speed_mps = spec.target_speed_mps

    def step(self, control: Control | None = None) -> None:
        # Waehrend ein Mensch in der Konsole entscheidet, steht die Simulationszeit still.
        if self.done or self.pending_session is not None:
            return
        dt_s = self.scenario.dt_s
        self._apply_scheduled_clear()
        self._evaluate_triggers()
        for traffic in self.traffic:
            spec = traffic.actor.spec
            if traffic.halted:
                traffic.vehicle.speed_mps = 0.0
                continue
            if traffic.override is not None:
                self._move_overridden(traffic, dt_s)
                continue
            if spec.behavior == "crossing" and spec.exit_d_m is not None:
                self._move_crossing(traffic, dt_s)
                continue
            target_speed = spec.speed_mps
            if spec.motion_start_time_s is not None and self.time_s >= spec.motion_start_time_s:
                assert spec.target_speed_mps is not None
                target_speed = spec.target_speed_mps
            if target_speed <= 0.0 and traffic.vehicle.speed_mps <= 0.0:
                continue
            command = self._route_control(
                traffic.vehicle,
                spec.direction,
                spec.d_m,
                target_speed,
            )
            step_vehicle(traffic.vehicle, command, dt_s)

        self._monitor_maneuver()
        command = control if self.mode == "manual" and control is not None else self._autopilot_control()
        if self._emergency_brake_needed():
            if not self.emergency_brake_active:
                self.events.append(Event(self.time_s, "emergency_brake", {"speed_mps": round(self.ego.speed_mps, 2)}))
            self.emergency_brake_active = True
            command = Control(brake=1.0, reverse=self.ego.speed_mps < 0)
        else:
            self.emergency_brake_active = False
        step_vehicle(self.ego, command, dt_s)
        self._abort_stalled_maneuver(dt_s)
        self.incidents.check_left_scene((self.ego.x_m, self.ego.y_m))
        self.time_s += dt_s
        if abs(self.ego.speed_mps) >= 0.3:
            self._has_moved = True
        elif (
            self._has_moved
            and self.terminal_outcome is None
            and not self.reached_goal
            and self.mission_state != "PLANNED_STOP"
            and not self._held_by_control()
        ):
            self.stationary_s += dt_s
        self._refresh_perception()
        stale = self._stale_fault()
        if (
            stale is not None
            and self._stale_snapshot is None
            and not self.stale_cleared
            and self.time_s >= float(stale["frozen_at_s"])
            and self.perception_snapshot is not None
        ):
            self._stale_snapshot = (self.perception_snapshot, self.time_s)
        self._update_safety()
        self._update_vla()
        self._update_recovery()

        ego_s, ego_d, _, _ = self.ego_route_state
        self.trajectory.append({
            "time_s": round(self.time_s, 3),
            "s_m": round(ego_s, 3),
            "d_m": round(ego_d, 3),
            "speed_mps": round(self.ego.speed_mps, 3),
            "x_m": round(self.ego.x_m, 3),
            "y_m": round(self.ego.y_m, 3),
            "yaw_rad": round(self.ego.yaw_rad, 5),
        })
        if self.done:
            return
        if ego_s >= self.scenario.ego_target_s_m:
            self.reached_goal = True
            self.done = True
            self.events.append(Event(self.time_s, "goal_reached"))
        elif self.time_s >= self.scenario.duration_s:
            self.done = True

    def _control_snapshot(self) -> list[dict[str, object]]:
        snapshot = []
        for control in self.scenario.traffic_controls:
            if control["id"] in self.removed_controls:
                continue
            x_m, y_m, heading = self.scenario.road.to_xy(control["s_m"], 0.0)
            state, _ = self._control_state(control)
            snapshot.append(
                {
                    "id": control["id"],
                    "type": control["type"],
                    "state": state,
                    "x_m": x_m,
                    "y_m": y_m,
                    "yaw_rad": heading,
                }
            )
        return snapshot

    def costs(self) -> dict[str, object]:
        return assess_episode(self)

    def snapshot(self) -> dict[str, object]:
        ego_s, ego_d, _, _ = self.ego_route_state
        actors = []
        ground_truth_status = (
            self.perception_snapshot.ground_truth_status
            if self.perception_snapshot is not None
            else {}
        )
        perception_metadata = (
            self.perception_snapshot.actor_metadata
            if self.perception_snapshot is not None
            else {}
        )
        for traffic in self.traffic:
            actor_id = traffic.actor.spec.actor_id
            metadata = perception_metadata.get(actor_id, {})
            ground_truth = ground_truth_status.get(actor_id, "unknown")
            actors.append({
                "id": actor_id,
                "kind": traffic.actor.spec.kind,
                "direction": traffic.actor.spec.direction,
                "x_m": traffic.vehicle.x_m,
                "y_m": traffic.vehicle.y_m,
                "yaw_rad": traffic.vehicle.yaw_rad,
                "speed_mps": traffic.vehicle.speed_mps,
                "length_m": traffic.vehicle.length_m,
                "width_m": traffic.vehicle.width_m,
                # The UI gets both layers explicitly: the status available to
                # the agent and the simulator-only ground truth used for the
                # teaching overlay. A short-lived track therefore stays a
                # "tracked" object even while its real body is occluded.
                "perception_status": metadata.get("status", ground_truth),
                "ground_truth_perception_status": ground_truth,
                "visible_fraction": metadata.get("visible_fraction", 0.0),
                "track_age_s": metadata.get("track_age_s"),
                "detected": actor_id in perception_metadata,
            })
        last_command = self.commands[-1] if self.commands else None
        return {
            "scenario": {
                "id": self.scenario.scenario_id,
                "road_name": self.scenario.road.name,
                "road_source": self.scenario.road.source,
                "source_url": (
                    self.scenario.road.context.get("source_url", self.scenario.road.source_url)
                    if self.scenario.road.context
                    else self.scenario.road.source_url
                ),
            },
            "road": {
                "points_xy": self.scenario.road.points_xy,
                "lane_width_m": self.scenario.road.lane_width_m,
                "center_marking": self.scenario.road.center_marking,
            },
            "context": self.scenario.road.context,
            "ego": {
                "kind": "minibus",
                "x_m": self.ego.x_m,
                "y_m": self.ego.y_m,
                "yaw_rad": self.ego.yaw_rad,
                "speed_mps": self.ego.speed_mps,
                "length_m": self.ego.length_m,
                "width_m": self.ego.width_m,
                "s_m": ego_s,
                "d_m": ego_d,
            },
            "actors": actors,
            "controls": self._control_snapshot(),
            "time_s": self.time_s,
            "mode": self.mode,
            "command": contract_to_payload(last_command) if last_command else None,
            "commands": [item["command"] for item in self.command_history],
            "active_command": self.active.request.command if self.active else None,
            "command_history": self.command_history[-10:],
            "mission_state": self.mission_state,
            "passengers_on_board": self.passengers_on_board,
            "terminal_outcome": self.terminal_outcome,
            "costs": self.costs(),
            "console": self.console_snapshot(),
            "agent_observation": self.agent_observation,
            "agent_trace": self.agent_trace,
            "perception": (
                {
                    "visibility": self.perception_snapshot.visibility,
                    "update_rate_hz": 2.0,
                    "detected_actor_ids": [
                        actor.spec.actor_id for actor in self.perception_snapshot.actors
                    ],
                }
                if self.perception_snapshot is not None
                else None
            ),
            "vla": self.vla.snapshot(self.time_s) if self.vla is not None else None,
            "agent_error": self.agent_error,
            "agent": {
                "name": self.agent_name,
                "connected": self.agent is not None,
                "status": self.agent_status,
            },
            "maneuver_aborted": self.maneuver_aborted,
            "abort_reason": self.abort_reason,
            "abort_recovery_active": self.abort_recovery_active,
            "abort_stabilized": self.abort_stabilized,
            "minimum_risk_completion": self.minimum_risk_completion,
            "collision": self.collision,
            "difficulty": self.scenario.difficulty.to_dict(),
            "safety_warnings": self.safety_warnings[-10:],
            "liberated": self.liberated,
            "reached_goal": self.reached_goal,
            "done": self.done,
            "minimum_clearance_m": None if math.isinf(self.minimum_clearance_m) else self.minimum_clearance_m,
            "trajectory": self.trajectory[-240:],
            "events": [event.to_dict() for event in self.events[-20:]],
        }
