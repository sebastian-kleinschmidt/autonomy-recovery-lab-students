from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import COMMAND_NAMES
from .models import ActorSpec, ObstacleProfile
from .road import Road, load_road


DEFAULT_BUDGET_EUR = 100.0
DEFAULT_TOOL_BUDGET = 14  # gleich agent_session.DEFAULT_MAX_TOOL_CALLS (per Test abgesichert)
FAULT_TYPES = frozenset({"tool_timeout", "stale_data"})
SENSOR_NAMES = frozenset({"CAMERA", "LIDAR", "LOCALIZATION"})
# Fahrstack-Mock nach dem Vorbild eines Vision-Language-Action-Modells (siehe vla_mock.py).
VLA_FAULT_TYPES = frozenset(
    {"HALLUCINATED_CAUSE", "REASONING_ACTION_MISMATCH", "VAGUE_CAUSE", "OVERCONFIDENT"}
)
PERCEPTION_INTERFACES = frozenset({"structured", "vla"})
# Schwierigkeit (Block ``difficulty``): Stufe; die Ausfuehrung ist fuer alle Szenarien gleich.
LEVELS = ("A", "B", "C", "D")
# Anweisungen der Leitstelle (Block ``remote_assistance.instruction``).
INSTRUCTION_TYPES = frozenset({"PROCEED", "PASS_OBSTACLE", "WAIT", "REPLAN_ROUTE", "END_TRIP"})
CONTROL_STATES = {
    "TRAFFIC_LIGHT": frozenset({"RED", "YELLOW", "GREEN", "DARK"}),
    "RAILWAY_CROSSING": frozenset({"OPEN", "CLOSED"}),
}


@dataclass(frozen=True)
class MissionSpec:
    mission_type: str = "PASSENGER_TRANSPORT"
    passengers: int = 1
    hub_available: bool = True
    hub_return_s: float = 150.0
    max_recovery_time_s: float | None = None
    # Geplanter Halt (z. B. Fahrgastwechsel): Kein Deadlock, obwohl das Fahrzeug steht.
    planned_stop_s_m: float | None = None
    planned_stop_duration_s: float = 0.0


@dataclass(frozen=True)
class RouteSpec:
    blocked: bool = False
    alternative_available: bool = True
    detour_cost_s: float = 180.0
    # Ein Umplanen gelingt erst, wenn das Fahrzeug so weit zurueckgesetzt hat (Sackgasse).
    reverse_required_m: float = 0.0
    # Die Karte kennt die Sperrung noch nicht: get_route_state meldet "frei", bis ein
    # Kartenabgleich beim Warten (WAIT) sie aufdeckt.
    map_stale: bool = False
    # Die Alternativroute beginnt hinter dem Fahrzeug: REPLAN_ROUTE wendet erst.
    turn_required: bool = False


@dataclass(frozen=True)
class AssistanceSpec:
    available: bool = True
    response_s: float = 15.0
    # "auto": die Leitstelle beurteilt die Lage selbst; sonst eine feste Anweisung
    # {"type", "message", optional "side", "clears" (Akteur-IDs), "control_ids", "after_s"}.
    instruction: dict[str, Any] | str = "auto"


@dataclass(frozen=True)
class VlaSpec:
    """Konfiguration des VLA-Fahrstack-Mocks (Block ``vla`` im Szenario).

    ``perception_interface="structured"`` laesst alle Lagewerkzeuge unveraendert und
    ergaenzt nur die VLA-Werkzeuge. ``"vla"`` entfernt zusaetzlich die semantischen
    Felder (Objektklasse, Hindernisprofil, Szenentext, Signalzustand) aus den
    Lagewerkzeugen: Semantik gibt es dann nur noch als Text des Fahrstacks.
    """

    seed: int = 0
    latency_ms: float = 100.0
    perception_interface: str = "structured"
    faults: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class NoiseSpec:
    """Messunsicherheit der Objektliste (Block ``perception.noise``, nur Modell ``tracked``).

    Die Standardwerte entsprechen leichtem Rauschen bei gutem Wetter (Stufe B).
    """

    position_std_m: float = 0.10
    position_std_per_10m_m: float = 0.05
    velocity_std_mps: float = 0.15
    yaw_std_rad: float = 0.03
    # Wahrscheinlichkeit, dass ein Objekt dauerhaft der falschen Klasse zugeordnet wird.
    class_confusion: float = 0.10
    # Wahrscheinlichkeit je Messzyklus, dass ein sichtbares Objekt fehlt.
    dropout_probability: float = 0.0
    # Erwartete Fehlalarme (Geisterobjekte) je Sekunde.
    false_positive_rate_hz: float = 0.0
    # Radar sieht verdeckte Fahrzeuge gelegentlich als schwachen Track.
    radar_through_occluders: bool = True


@dataclass(frozen=True)
class DifficultySpec:
    """Block ``difficulty``; ohne Block gilt Stufe A."""

    level: str = "A"

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level}


@dataclass(frozen=True)
class SolutionPath:
    """Ein vollstaendiger, szenariospezifischer Weg mit passendem Endzustand."""

    path_id: str
    sequence: tuple[tuple[str, ...], ...]
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.path_id,
            "sequence": [list(step) for step in self.sequence],
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    seed: int
    duration_s: float
    dt_s: float
    road: Road
    ego_start_s_m: float
    ego_lane_d_m: float
    ego_target_s_m: float
    ego_cruise_speed_mps: float
    actors: tuple[ActorSpec, ...]
    stuck_after_s: float
    allow_solid_exception: bool
    allow_shoulder_passing: bool
    traffic_side: str
    visibility_m: float
    perception_range_m: float
    perception_horizontal_fov_deg: float
    perception_track_memory_s: float
    perception_min_detectable_fraction: float
    minimum_visibility_m: float
    minimum_passing_clearance_m: float
    expected_outcome: str
    expected_reason: str
    source_path: Path
    expected_commands: tuple[str, ...] = ()
    # Loesungsweg: geordnete Schritte, je Schritt eine Menge gleichwertiger Befehle.
    # Ohne sequence oder solution_paths gilt die bisherige Einzelreferenz.
    expected_sequence: tuple[tuple[str, ...], ...] = ()
    solution_paths: tuple[SolutionPath, ...] = ()
    shoulder_m: float = 3.0
    mission: MissionSpec = MissionSpec()
    route: RouteSpec = RouteSpec()
    remote_assistance: AssistanceSpec = AssistanceSpec()
    max_commands: int = 8
    budget_eur: float = DEFAULT_BUDGET_EUR
    faults: tuple[dict[str, Any], ...] = ()
    sensors: tuple[dict[str, Any], ...] = ()
    max_tool_calls: int = DEFAULT_TOOL_BUDGET
    # Ampeln und Bahnuebergaenge: {"id", "type", "s_m", "schedule": [{"state", "until_s"}, ...]}
    traffic_controls: tuple[dict[str, Any], ...] = ()
    # Ohne Block ``vla`` gibt es keinen VLA-Mock und keine VLA-Werkzeuge (Standard).
    vla: VlaSpec | None = None
    difficulty: DifficultySpec = DifficultySpec()
    perception_noise: NoiseSpec = NoiseSpec()

    @property
    def expected_outcomes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(path.outcome for path in self.solution_paths)) or (self.expected_outcome,)


OUTCOMES = frozenset(
    {
        "not_triggered",
        "waiting",
        "resolved",
        "liberated",
        "aborted",
        "rerouted",
        "safe_stopped",
        "returned_home",
        "passenger_dropped",
        "remote_resolved",
        "incident_reported",
    }
)


def _number(data: dict[str, Any], name: str, *, positive: bool = False) -> float:
    value = float(data[name])
    if positive and value <= 0:
        raise ValueError(f"{name} muss positiv sein")
    return value


def _load_sequence(value: Any, field: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or any(not isinstance(step, list) or not step for step in value):
        raise ValueError(f"{field} muss eine Liste nicht leerer Befehlslisten sein")
    sequence = tuple(tuple(str(name) for name in step) for step in value)
    unknown = sorted({name for step in sequence for name in step} - set(COMMAND_NAMES))
    if unknown:
        raise ValueError(f"Unbekannte {field}-Befehle: {', '.join(unknown)}")
    return sequence


def _load_solution_paths(reference: dict[str, Any]) -> tuple[SolutionPath, ...]:
    if "paths" not in reference:
        return ()
    if any(key in reference for key in ("command", "commands", "sequence", "outcome")):
        raise ValueError("reference.paths ersetzt command, commands, sequence und outcome")
    raw = reference["paths"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("reference.paths muss eine nicht leere Liste sein")
    paths = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"id", "sequence", "outcome"}:
            raise ValueError("Jeder reference.paths-Eintrag braucht genau id, sequence und outcome")
        path_id = entry["id"]
        if not isinstance(path_id, str) or not path_id.strip():
            raise ValueError("reference.paths.id muss ein nicht leerer Text sein")
        sequence = _load_sequence(entry["sequence"], "reference.paths.sequence")
        if not sequence:
            raise ValueError("reference.paths.sequence darf nicht leer sein")
        if not isinstance(entry["outcome"], str) or entry["outcome"] not in OUTCOMES:
            raise ValueError("Unbekannter reference.paths.outcome")
        paths.append(SolutionPath(path_id, sequence, entry["outcome"]))
    if len({path.path_id for path in paths}) != len(paths):
        raise ValueError("reference.paths.id muss eindeutig sein")
    return tuple(paths)


def _with_default_vla(scenario: Scenario) -> Scenario:
    """Jedes Fahrzeug hat einen Fahrstack, der sein Verhalten begruendet: ohne Block ``vla``
    ein fehlerfreier Mock. Fehlerbilder kommen nur aus einem ausdruecklichen Block."""
    from dataclasses import replace

    if scenario.vla is not None:
        return scenario
    return replace(scenario, vla=VlaSpec(seed=scenario.seed))


def _load_instruction(cfg: Mapping[str, Any]) -> dict[str, Any] | str:
    if "clears" in cfg:
        raise ValueError(
            "remote_assistance.clears wurde ersetzt: die Leitstelle gibt eine Anweisung "
            "(remote_assistance.instruction)"
        )
    unknown = set(cfg) - {"available", "response_s", "instruction"}
    if unknown:
        raise ValueError(f"Unbekannte remote_assistance-Felder: {', '.join(sorted(unknown))}")
    instruction = cfg.get("instruction", "auto")
    if instruction == "auto":
        return "auto"
    if not isinstance(instruction, Mapping):
        raise ValueError('remote_assistance.instruction muss "auto" oder ein Objekt sein')
    unknown = set(instruction) - {"type", "message", "side", "clears", "control_ids", "after_s"}
    if unknown:
        raise ValueError(f"Unbekannte Felder in remote_assistance.instruction: {', '.join(sorted(unknown))}")
    if instruction.get("type") not in INSTRUCTION_TYPES:
        raise ValueError(f"remote_assistance.instruction.type muss eines von {', '.join(sorted(INSTRUCTION_TYPES))} sein")
    if instruction.get("side") not in (None, "LEFT", "RIGHT"):
        raise ValueError("remote_assistance.instruction.side muss LEFT oder RIGHT sein")
    if not str(instruction.get("message", "")).strip():
        raise ValueError("remote_assistance.instruction.message fehlt")
    return {
        "type": str(instruction["type"]),
        "message": str(instruction["message"]),
        **({"side": str(instruction["side"])} if instruction.get("side") else {}),
        "clears": tuple(str(item) for item in instruction.get("clears", ())),
        "control_ids": tuple(str(item) for item in instruction.get("control_ids", ())),
        "after_s": float(instruction.get("after_s", 30.0)),
    }


def _load_noise(cfg: Any) -> NoiseSpec:
    if cfg is None:
        return NoiseSpec()
    if not isinstance(cfg, dict):
        raise ValueError("perception.noise muss ein Objekt sein")
    defaults = NoiseSpec()
    known = set(defaults.__dataclass_fields__)
    unknown = set(cfg) - known
    if unknown:
        raise ValueError(f"Unbekannte perception.noise-Felder: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for name in known:
        if name not in cfg:
            continue
        if name == "radar_through_occluders":
            if not isinstance(cfg[name], bool):
                raise ValueError("perception.noise.radar_through_occluders muss true oder false sein")
            values[name] = cfg[name]
            continue
        value = float(cfg[name])
        if value < 0:
            raise ValueError(f"perception.noise.{name} darf nicht negativ sein")
        if name in {"class_confusion", "dropout_probability"} and value > 1:
            raise ValueError(f"perception.noise.{name} muss zwischen 0 und 1 liegen")
        values[name] = value
    return NoiseSpec(**values)


def _load_vla(cfg: Any) -> VlaSpec | None:
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise ValueError("vla muss ein Objekt sein")
    spec = VlaSpec(
        seed=int(cfg.get("seed", 0)),
        latency_ms=float(cfg.get("latency_ms", 100.0)),
        perception_interface=str(cfg.get("perception_interface", "structured")),
        faults=tuple(dict(fault) for fault in cfg.get("faults", [])),
    )
    if spec.perception_interface not in PERCEPTION_INTERFACES:
        raise ValueError(
            f"vla.perception_interface muss eines von {', '.join(sorted(PERCEPTION_INTERFACES))} sein"
        )
    if not 0.0 <= spec.latency_ms <= 2000.0:
        raise ValueError("vla.latency_ms muss zwischen 0 und 2000 liegen")
    for fault in spec.faults:
        if fault.get("type") not in VLA_FAULT_TYPES:
            raise ValueError(f"vla.faults[].type muss eines von {', '.join(sorted(VLA_FAULT_TYPES))} sein")
        if fault["type"] == "HALLUCINATED_CAUSE" and not str(fault.get("text", "")).strip():
            raise ValueError("HALLUCINATED_CAUSE braucht einen nicht leeren text")
        start = float(fault.get("from_s", 0.0))
        end = fault.get("until_s")
        if start < 0 or (end is not None and float(end) <= start):
            raise ValueError("vla.faults[]: from_s >= 0 und until_s > from_s verwenden")
    return spec


def _load_triggers(actor: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = actor.get("triggers")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{actor.get('id')}: triggers muss eine Liste sein")
    from .agent_session import TOOL_CATALOG, VLA_TOOL_CATALOG
    from .triggers import validate_triggers

    return validate_triggers(
        str(actor.get("id")),
        str(actor.get("kind", "car")),
        bool(actor.get("phantom", False)),
        raw,
        {**TOOL_CATALOG, **VLA_TOOL_CATALOG},
    )


def _load_difficulty(cfg: Any) -> DifficultySpec:
    if cfg is None:
        return DifficultySpec()
    if not isinstance(cfg, dict):
        raise ValueError("difficulty muss ein Objekt sein")
    if "guardrails" in cfg:
        raise ValueError("difficulty.guardrails wurde entfernt; alle Szenarien verwenden dasselbe Ausfuehrungsverhalten. Feld entfernen.")
    if "perception_model" in cfg:
        raise ValueError(
            "difficulty.perception_model wurde entfernt; der Agent arbeitet immer mit der Objektliste. Feld entfernen."
        )
    unknown = set(cfg) - {"level"}
    if unknown:
        raise ValueError(f"Unbekannte difficulty-Felder: {', '.join(sorted(unknown))}")
    spec = DifficultySpec(level=str(cfg.get("level", "A")))
    if spec.level not in LEVELS:
        raise ValueError(f"difficulty.level muss eines von {', '.join(LEVELS)} sein")
    return spec


def load_scenario(path: str | Path) -> Scenario:
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding="utf8"))
    road_cfg = data["road"]
    map_path = (source.parent / road_cfg["map_file"]).resolve()
    context_path = (
        (source.parent / road_cfg["context_file"]).resolve()
        if road_cfg.get("context_file")
        else None
    )
    road = load_road(
        map_path,
        lane_width_m=_number(road_cfg, "lane_width_m", positive=True),
        center_marking=str(road_cfg["center_marking"]),
        context_path=context_path,
    )
    ego = data["ego"]
    actors = tuple(
        ActorSpec(
            actor_id=str(a["id"]),
            kind=str(a.get("kind", "car")),
            s_m=float(a["s_m"]),
            d_m=float(a["d_m"]),
            speed_mps=float(a.get("speed_mps", 0.0)),
            direction=int(a.get("direction", 1)),
            length_m=float(a.get("length_m", 4.5)),
            width_m=float(a.get("width_m", 1.8)),
            behavior=str(a.get("behavior", "lane_follow")),
            motion_start_time_s=(
                float(a["motion_start_time_s"])
                if a.get("motion_start_time_s") is not None
                else None
            ),
            target_speed_mps=(
                float(a["target_speed_mps"])
                if a.get("target_speed_mps") is not None
                else None
            ),
            exit_d_m=float(a["exit_d_m"]) if a.get("exit_d_m") is not None else None,
            confidence=float(a.get("confidence", 1.0)),
            description=str(a["description"]) if a.get("description") is not None else None,
            description_source=str(a.get("description_source", "direct")),
            phantom=bool(a.get("phantom", False)),
            obstacle_profile=(
                ObstacleProfile(
                    height_m=float(a["obstacle_profile"]["height_m"]),
                    material=str(a["obstacle_profile"]["material"]),
                    deformability=str(a["obstacle_profile"]["deformability"]),
                    sharp_edges=bool(a["obstacle_profile"]["sharp_edges"]),
                    liquid=bool(a["obstacle_profile"]["liquid"]),
                    assessment_confidence=float(
                        a["obstacle_profile"]["assessment_confidence"]
                    ),
                )
                if a.get("obstacle_profile") is not None
                else None
            ),
            triggers=_load_triggers(a),
        )
        for a in data.get("actors", [])
    )
    if any(a.direction not in (-1, 1) for a in actors):
        raise ValueError("actor.direction muss -1 oder 1 sein")
    if len({a.actor_id for a in actors}) != len(actors):
        raise ValueError("actor.id muss innerhalb eines Szenarios eindeutig sein")
    if any(a.length_m <= 0 or a.width_m <= 0 for a in actors):
        raise ValueError("Aktorabmessungen muessen positiv sein")
    if any(a.speed_mps < 0 for a in actors):
        raise ValueError("actor.speed_mps darf nicht negativ sein")
    if any(a.motion_start_time_s is not None and a.motion_start_time_s < 0 for a in actors):
        raise ValueError("actor.motion_start_time_s darf nicht negativ sein")
    if any(a.target_speed_mps is not None and a.target_speed_mps < 0 for a in actors):
        raise ValueError("actor.target_speed_mps darf nicht negativ sein")
    if any(
        (a.motion_start_time_s is None) != (a.target_speed_mps is None)
        for a in actors
    ):
        raise ValueError(
            "actor.motion_start_time_s und actor.target_speed_mps muessen gemeinsam gesetzt werden"
        )
    allowed_kinds = {
        "car", "van", "truck", "bus", "bicycle", "pedestrian", "animal",
        "beacon", "trash_bin", "debris", "train", "barrier",
    }
    unknown_kinds = sorted({a.kind for a in actors} - allowed_kinds)
    if unknown_kinds:
        raise ValueError(f"Unbekannte actor.kind: {', '.join(unknown_kinds)}")
    if any((a.exit_d_m is not None) != (a.behavior == "crossing" and a.motion_start_time_s is not None) for a in actors):
        raise ValueError(
            "actor.exit_d_m ist nur mit behavior=crossing und motion_start_time_s zulaessig und dort Pflicht"
        )
    if any(a.description_source not in {"direct", "camera"} for a in actors):
        raise ValueError("actor.description_source muss direct oder camera sein")
    if any(a.description_source == "camera" and not a.description for a in actors):
        raise ValueError("actor.description_source camera braucht eine description")
    allowed_behaviors = {"lane_follow", "parked", "overtaking", "crossing"}
    unknown_behaviors = sorted({a.behavior for a in actors} - allowed_behaviors)
    if unknown_behaviors:
        raise ValueError(f"Unbekannte actor.behavior: {', '.join(unknown_behaviors)}")
    material_types = {"CARDBOARD", "FOAM", "PLASTIC", "RUBBER", "WOOD", "METAL", "GLASS", "MIXED", "UNKNOWN"}
    deformability_types = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
    for actor in actors:
        profile = actor.obstacle_profile
        if profile is None:
            continue
        if actor.kind != "debris":
            raise ValueError("actor.obstacle_profile ist nur fuer actor.kind=debris zulaessig")
        if profile.height_m < 0:
            raise ValueError("actor.obstacle_profile.height_m darf nicht negativ sein")
        if profile.material not in material_types:
            raise ValueError(f"Unbekanntes obstacle_profile.material: {profile.material}")
        if profile.deformability not in deformability_types:
            raise ValueError(f"Unbekannte obstacle_profile.deformability: {profile.deformability}")
        if not 0.0 < profile.assessment_confidence <= 1.0:
            raise ValueError("actor.obstacle_profile.assessment_confidence muss in (0, 1] liegen")
    expected = data["reference"]
    solution_paths = _load_solution_paths(expected)
    raw_commands = expected.get("commands", [expected["command"]] if "command" in expected else None)
    if solution_paths:
        raw_commands = list(dict.fromkeys(name for path in solution_paths for name in path.sequence[0]))
    if raw_commands is None:
        raise ValueError("reference braucht command oder commands")
    expected_commands = tuple(str(name) for name in raw_commands)
    unknown_commands = [name for name in expected_commands if name not in COMMAND_NAMES and name != "NONE"]
    if unknown_commands:
        raise ValueError(f"Unbekannte reference.commands: {', '.join(unknown_commands)}")
    if not expected_commands:
        raise ValueError("reference.commands darf nicht leer sein")
    expected_sequence = _load_sequence(expected.get("sequence", []), "reference.sequence")
    policy = data.get("policy", {})
    environment = data.get("environment", {})
    perception = data.get("perception", {})
    default_outcome = {
        "NONE": "not_triggered",
        "WAIT": "waiting",
        "PASS_OBSTACLE": "liberated",
        "CROSS_LOW_RISK_OBJECT": "liberated",
        "REPLAN_ROUTE": "rerouted",
        "SAFE_STOP": "safe_stopped",
        "RETURN_HOME": "returned_home",
        "DROP_PASSENGER": "passenger_dropped",
        "REQUEST_REMOTE_ASSISTANCE": "remote_resolved",
        "REPORT_INCIDENT": "incident_reported",
    }.get(expected_commands[0], "waiting")
    # Einzelwert fuer bestehende Aufrufer; die Bewertung verwendet expected_outcomes.
    expected_outcome = solution_paths[0].outcome if solution_paths else str(expected.get("outcome", default_outcome))
    if expected_outcome not in OUTCOMES:
        raise ValueError(f"reference.outcome muss eines von {', '.join(sorted(OUTCOMES))} sein")
    mission_cfg = data.get("mission", {})
    mission_type = str(mission_cfg.get("type", "PASSENGER_TRANSPORT"))
    if mission_type not in {"PASSENGER_TRANSPORT", "EMPTY_REPOSITIONING"}:
        raise ValueError("mission.type muss PASSENGER_TRANSPORT oder EMPTY_REPOSITIONING sein")
    mission = MissionSpec(
        mission_type=mission_type,
        passengers=int(mission_cfg.get("passengers", 1 if mission_type == "PASSENGER_TRANSPORT" else 0)),
        hub_available=bool(mission_cfg.get("hub_available", True)),
        hub_return_s=float(mission_cfg.get("hub_return_s", 150.0)),
        max_recovery_time_s=(
            float(mission_cfg["max_recovery_time_s"])
            if mission_cfg.get("max_recovery_time_s") is not None
            else None
        ),
        planned_stop_s_m=(
            float(mission_cfg["planned_stop_s_m"])
            if mission_cfg.get("planned_stop_s_m") is not None
            else None
        ),
        planned_stop_duration_s=float(mission_cfg.get("planned_stop_duration_s", 0.0)),
    )
    route_cfg = data.get("route", {})
    route = RouteSpec(
        blocked=bool(route_cfg.get("blocked", False)),
        alternative_available=bool(route_cfg.get("alternative_available", True)),
        detour_cost_s=float(route_cfg.get("detour_cost_s", 180.0)),
        reverse_required_m=float(route_cfg.get("reverse_required_m", 0.0)),
        map_stale=bool(route_cfg.get("map_stale", False)),
        turn_required=bool(route_cfg.get("turn_required", False)),
    )
    assistance_cfg = data.get("remote_assistance", {})
    recovery_cfg = data.get("recovery", {})
    assistance = AssistanceSpec(
        available=bool(assistance_cfg.get("available", True)),
        response_s=float(assistance_cfg.get("response_s", 15.0)),
        instruction=_load_instruction(assistance_cfg),
    )
    if "adjacent_lanes" in data:
        raise ValueError("adjacent_lanes wird nicht mehr unterstuetzt: Spurwechsel uebernimmt der Fahrstack selbst")
    scenario = Scenario(
        scenario_id=str(data["id"]),
        seed=int(data.get("seed", 0)),
        duration_s=_number(data["simulation"], "duration_s", positive=True),
        dt_s=_number(data["simulation"], "dt_s", positive=True),
        road=road,
        ego_start_s_m=float(ego["start_s_m"]),
        ego_lane_d_m=float(ego["lane_d_m"]),
        ego_target_s_m=float(ego["target_s_m"]),
        ego_cruise_speed_mps=float(ego["cruise_speed_mps"]),
        actors=actors,
        stuck_after_s=float(recovery_cfg.get("stuck_after_s", 3.0)),
        allow_solid_exception=bool(policy.get("allow_solid_exception", False)),
        allow_shoulder_passing=bool(policy.get("allow_shoulder_passing", False)),
        traffic_side=str(data.get("traffic_side", "right")),
        visibility_m=float(environment.get("visibility_m", 120.0)),
        perception_range_m=float(perception.get("range_m", 60.0)),
        perception_horizontal_fov_deg=float(
            perception.get("horizontal_fov_deg", 360.0)
        ),
        perception_track_memory_s=float(perception.get("track_memory_s", 2.0)),
        perception_min_detectable_fraction=float(
            perception.get("min_detectable_fraction", 0.3)
        ),
        minimum_visibility_m=float(policy.get("minimum_visibility_m", 60.0)),
        minimum_passing_clearance_m=float(policy.get("minimum_passing_clearance_m", 0.5)),
        expected_outcome=expected_outcome,
        expected_reason=str(expected.get("reason", "")),
        source_path=source,
        expected_commands=expected_commands,
        expected_sequence=expected_sequence,
        solution_paths=solution_paths,
        shoulder_m=float(data["road"].get("shoulder_m", 3.0)),
        mission=mission,
        route=route,
        remote_assistance=assistance,
        max_commands=int(recovery_cfg.get("max_commands", 8)),
        budget_eur=float(expected.get("budget_eur", DEFAULT_BUDGET_EUR)),
        faults=tuple(dict(fault) for fault in data.get("faults", [])),
        sensors=tuple(
            {"name": str(name), "status": str(cfg.get("status", "AVAILABLE")), "quality": float(cfg.get("quality", 1.0))}
            for name, cfg in data.get("sensors", {}).items()
        ),
        max_tool_calls=int(recovery_cfg.get("max_tool_calls", DEFAULT_TOOL_BUDGET)),
        traffic_controls=tuple(
            {
                "id": str(control["id"]),
                "type": str(control["type"]),
                "s_m": float(control["s_m"]),
                "schedule": tuple(
                    {"state": str(phase["state"]), "until_s": float(phase["until_s"])}
                    for phase in control["schedule"]
                ),
            }
            for control in data.get("traffic_controls", [])
        ),
        vla=_load_vla(data.get("vla")),
        difficulty=_load_difficulty(data.get("difficulty")),
        perception_noise=_load_noise(perception.get("noise")),
    )
    if scenario.ego_target_s_m > road.length_m:
        raise ValueError(
            f"Ego-Ziel {scenario.ego_target_s_m:.1f} m liegt hinter dem Kartenende "
            f"({road.length_m:.1f} m)"
        )
    if scenario.ego_start_s_m < 0 or scenario.ego_start_s_m >= scenario.ego_target_s_m:
        raise ValueError("ego.start_s_m muss vor ego.target_s_m auf der Route liegen")
    if scenario.dt_s > scenario.duration_s:
        raise ValueError("simulation.dt_s darf duration_s nicht uebersteigen")
    if scenario.road.center_marking not in {"dashed", "solid"}:
        raise ValueError("road.center_marking muss dashed oder solid sein")
    if scenario.traffic_side != "right":
        raise ValueError("Der Laboraufbau unterstuetzt derzeit nur Rechtsverkehr")
    if scenario.ego_lane_d_m >= 0:
        raise ValueError("Im Rechtsverkehr muss ego.lane_d_m negativ sein")
    if scenario.ego_cruise_speed_mps <= 0:
        raise ValueError("ego.cruise_speed_mps muss positiv sein")
    if scenario.visibility_m <= 0 or scenario.minimum_visibility_m <= 0:
        raise ValueError("Sichtweiten muessen positiv sein")
    if scenario.perception_range_m <= 0:
        raise ValueError("perception.range_m muss positiv sein")
    if not 0 < scenario.perception_horizontal_fov_deg <= 360:
        raise ValueError("perception.horizontal_fov_deg muss zwischen 0 und 360 liegen")
    if scenario.perception_track_memory_s < 0:
        raise ValueError("perception.track_memory_s darf nicht negativ sein")
    if not 0 < scenario.perception_min_detectable_fraction <= 1:
        raise ValueError(
            "perception.min_detectable_fraction muss zwischen 0 und 1 liegen"
        )
    if scenario.shoulder_m < 0:
        raise ValueError("road.shoulder_m darf nicht negativ sein")
    if mission.passengers < 0 or route.detour_cost_s < 0 or assistance.response_s < 0:
        raise ValueError("mission.passengers, route.detour_cost_s und remote_assistance.response_s duerfen nicht negativ sein")
    from .agent_session import TOOL_CATALOG, VLA_TOOL_CATALOG

    for fault in scenario.faults:
        if fault.get("type") not in FAULT_TYPES:
            raise ValueError(f"faults[].type muss eines von {', '.join(sorted(FAULT_TYPES))} sein")
        if fault["type"] == "tool_timeout" and fault.get("tool") not in {
            **TOOL_CATALOG, **VLA_TOOL_CATALOG
        }:
            raise ValueError(f"faults[].tool ist kein bekanntes Werkzeug: {fault.get('tool')!r}")
        if fault["type"] == "tool_timeout" and int(fault.get("failures", 1)) < 1:
            raise ValueError("faults[].failures muss mindestens 1 sein")
        if fault["type"] == "stale_data" and float(fault.get("frozen_at_s", -1.0)) < 0:
            raise ValueError("stale_data braucht frozen_at_s >= 0")
    for sensor in scenario.sensors:
        if sensor["name"] not in SENSOR_NAMES or not 0.0 <= sensor["quality"] <= 1.0:
            raise ValueError("sensors: bekannte Namen (CAMERA, LIDAR, LOCALIZATION) und quality in [0, 1] verwenden")
    control_ids = [control["id"] for control in scenario.traffic_controls]
    if len(set(control_ids)) != len(control_ids):
        raise ValueError("traffic_controls[].id muss eindeutig sein")
    for control in scenario.traffic_controls:
        allowed = CONTROL_STATES.get(control["type"])
        if allowed is None:
            raise ValueError(f"traffic_controls[].type muss eines von {', '.join(sorted(CONTROL_STATES))} sein")
        if not 0 <= control["s_m"] <= road.length_m:
            raise ValueError(f"{control['id']}: s_m liegt ausserhalb der Route")
        if not control["schedule"]:
            raise ValueError(f"{control['id']}: schedule darf nicht leer sein")
        until = [phase["until_s"] for phase in control["schedule"]]
        if any(phase["state"] not in allowed for phase in control["schedule"]) or until != sorted(until) or until[0] <= 0:
            raise ValueError(
                f"{control['id']}: schedule braucht Zustaende aus {', '.join(sorted(allowed))} "
                "und aufsteigende, positive until_s"
            )
    if scenario.max_tool_calls <= 0:
        raise ValueError("recovery.max_tool_calls muss positiv sein")
    if any(not 0.0 < a.confidence <= 1.0 for a in actors) or any(
        a.description is not None and len(a.description) > 400 for a in actors
    ):
        raise ValueError("actor.confidence muss in (0, 1] liegen, actor.description hoechstens 400 Zeichen")
    if scenario.budget_eur <= 0:
        raise ValueError("reference.budget_eur muss positiv sein")
    if scenario.max_commands <= 0:
        raise ValueError("recovery.max_commands muss positiv sein")
    if scenario.minimum_passing_clearance_m < 0:
        raise ValueError("minimum_passing_clearance_m darf nicht negativ sein")
    if scenario.vla is not None:
        actor_ids = {actor.actor_id for actor in actors}
        for fault in scenario.vla.faults:
            if fault.get("actor_id") is not None and fault["actor_id"] not in actor_ids:
                raise ValueError(f"vla.faults[].actor_id ist kein Akteur: {fault['actor_id']!r}")
    for actor in scenario.actors:
        if not 0 <= actor.s_m <= road.length_m:
            raise ValueError(f"{actor.actor_id}: s_m liegt ausserhalb der Route")
        if actor.kind in {"car", "van", "truck", "bus", "bicycle"}:
            wrong_side = actor.direction == -1 and actor.d_m <= 0
            overtaking = actor.direction == 1 and actor.d_m > 0 and actor.behavior == "overtaking"
            if wrong_side and actor.behavior != "crossing":
                raise ValueError(
                    f"{actor.actor_id}: Gegenverkehr muss im Rechtsverkehr d_m > 0 haben"
                )
            if actor.direction == 1 and actor.d_m > 0 and not overtaking:
                raise ValueError(
                    f"{actor.actor_id}: Verkehr in Fahrtrichtung braucht d_m < 0 oder behavior=overtaking"
                )
    scenario = _with_default_vla(scenario)
    return scenario
