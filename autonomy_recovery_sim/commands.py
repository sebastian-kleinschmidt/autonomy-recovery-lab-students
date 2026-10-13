"""Registry der High-Level-Befehle, die ein Agent anfordern darf.

Ein Befehl ist nur ein *Kandidat*: Schema (dieses Modul), Regel- und
Sicherheitspruefung (``rules.py``) und der zertifizierte Ausfuehrer (Engine)
muessen ihn freigeben. Es gibt bewusst keine Befehle fuer Lenkwinkel, Pedale
oder freie Trajektorien.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import CommandRequest


class CommandError(ValueError):
    """Ein Befehl ist ungueltig, unzulaessig oder nicht verfuegbar.

    ``code`` folgt den Standardfehlern der Schnittstellenspezifikation
    (INVALID_ARGUMENT, NOT_AVAILABLE, RULE_REJECTED, SAFETY_REJECTED, ...).
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


# Kategorien: was der Befehl im Fahrzeug bzw. in der Mission bewirkt.
MOTION = "motion"
ROUTE = "route"
MISSION = "mission"
ESCALATION = "escalation"
INFORMATION = "information"

# Lagepruefungen, ohne die kein Manoever ueber die Fahrbahn freigegeben wird.
PASS_EVIDENCE_TOOLS = frozenset(
    {"get_blocker", "check_oncoming_traffic", "check_rear_traffic", "check_vulnerable_road_users"}
)
CROSS_EVIDENCE_TOOLS = frozenset({"get_blocker", "check_vulnerable_road_users"})


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str  # "number" | "enum" | "bool" | "string"
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    default: Any = None
    max_length: int = 200

    def schema(self) -> dict[str, Any]:
        if self.kind == "number":
            result: dict[str, Any] = {"type": "number"}
            if self.minimum is not None:
                result["minimum"] = self.minimum
            if self.maximum is not None:
                result["maximum"] = self.maximum
        elif self.kind == "enum":
            result = {"enum": list(self.choices)}
        elif self.kind == "bool":
            result = {"type": "boolean"}
        else:
            result = {"type": "string", "maxLength": self.max_length}
        if self.default is not None:
            result["default"] = self.default
        return result

    def normalize(self, value: object) -> Any:
        if self.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CommandError("INVALID_ARGUMENT", f"{self.name} must be a number")
            number = float(value)
            if not math.isfinite(number):
                raise CommandError("INVALID_ARGUMENT", f"{self.name} must be finite")
            if self.minimum is not None and number < self.minimum:
                raise CommandError(
                    "INVALID_ARGUMENT", f"{self.name} must be at least {self.minimum:g}"
                )
            if self.maximum is not None and number > self.maximum:
                raise CommandError(
                    "INVALID_ARGUMENT", f"{self.name} must not exceed {self.maximum:g}"
                )
            return number
        if self.kind == "enum":
            if value not in self.choices:
                raise CommandError(
                    "INVALID_ARGUMENT", f"{self.name} must be one of {', '.join(self.choices)}"
                )
            return value
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise CommandError("INVALID_ARGUMENT", f"{self.name} must be true or false")
            return value
        if not isinstance(value, str) or len(value) > self.max_length:
            raise CommandError(
                "INVALID_ARGUMENT",
                f"{self.name} must be a text of at most {self.max_length} characters",
            )
        return value


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    category: str
    params: tuple[ParamSpec, ...] = ()
    evidence_tools: frozenset[str] = frozenset()
    # Terminale Befehle beenden die Episode (Wiederaufnahme der Fahrt ausserhalb der Simulation).
    terminal: bool = False
    cost_note: str = ""

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "command": self.name,
            "category": self.category,
            "description": self.summary,
            "parameter_schema": {param.name: param.schema() for param in self.params},
            "required_parameters": [param.name for param in self.params if param.required],
            "required_tools": sorted(self.evidence_tools),
            "ends_episode": self.terminal,
            "cost_note": self.cost_note,
        }


def _spec(*items: CommandSpec) -> dict[str, CommandSpec]:
    return {item.name: item for item in items}


SIDE = ("LEFT", "RIGHT")

COMMANDS: dict[str, CommandSpec] = _spec(
    CommandSpec(
        "WAIT",
        "Wait and observe while standing still. Ends early as soon as the path is clear. After 3 s "
        "ghost objects disappear, stale data is refreshed and the map is cross-checked. "
        "The report appears in the next session under additional_information.",
        MOTION,
        (ParamSpec("duration_s", "number", minimum=1.0, maximum=30.0),),
        cost_note="standing time",
    ),
    CommandSpec(
        "HONK",
        "Short horn signal. Under StVO only as a warning when a person or animal on the road "
        "is in danger; not to send an obstacle away.",
        MOTION,
        cost_note="none; a rule violation without danger",
    ),
    CommandSpec(
        "PASS_OBSTACLE",
        "Passes a stationary obstacle slowly. The executor chooses the lateral offset (within "
        "the lane, otherwise across the center line or onto the shoulder), pulls out to look, "
        "waits for a gap in visible oncoming traffic and only stops if something new appears "
        "(trigger SITUATION_CHANGED).",
        MOTION,
        (ParamSpec("side", "enum", choices=SIDE),),
        evidence_tools=PASS_EVIDENCE_TOOLS,
        cost_note="collision risk",
    ),
    CommandSpec(
        "ABORT_TO_LANE",
        "Returns from a lateral position behind the obstacle into the own lane, reversing "
        "straight if necessary.",
        MOTION,
        cost_note="standing time",
    ),
    CommandSpec(
        "CROSS_LOW_RISK_OBJECT",
        "Drives straight over a reliably classified, very flat and highly deformable object at "
        "limited speed. Unknown, rigid, sharp-edged or liquid obstacles are rejected.",
        MOTION,
        (),
        evidence_tools=CROSS_EVIDENCE_TOOLS,
        cost_note="slow driving; only for a validated low-risk object",
    ),
    CommandSpec(
        "REVERSE_SHORT",
        "Limited straight reversing to gain room to maneuver. Only with a clear rear.",
        MOTION,
        (ParamSpec("max_distance_m", "number", minimum=0.5, maximum=5.0),),
        cost_note="standing time",
    ),
    CommandSpec(
        "REPLAN_ROUTE",
        "Computes an alternative route when the planned route is blocked (e.g. road works). "
        "The new route is longer and causes detour costs; turns around if required.",
        ROUTE,
        (),
        terminal=True,
        cost_note="detour (time)",
    ),
    CommandSpec(
        "PULL_OVER",
        "Drives to the safe right road edge (shoulder) and stops there.",
        MOTION,
        (),
        cost_note="standing time",
    ),
    CommandSpec(
        "DROP_PASSENGER",
        "Lets passengers get out and ends the trip. At a safe spot (vehicle at the road edge) "
        "only a small refund; in the driving lane people may get hurt.",
        MISSION,
        (),
        terminal=True,
        cost_note="refund; personal injury at an unsafe spot",
    ),
    CommandSpec(
        "SAFE_STOP",
        "Ends the trip with a minimal-risk stop; the vehicle is recovered. A stop in the "
        "driving lane obstructs traffic; after PULL_OVER it is cheaper.",
        MISSION,
        (),
        terminal=True,
        cost_note="recovery; obstruction when stopping in the lane",
    ),
    CommandSpec(
        "RETURN_HOME",
        "Drives back to the hub. Passengers on board are unhappy about it.",
        MISSION,
        (),
        terminal=True,
        cost_note="return time; unhappy passengers",
    ),
    CommandSpec(
        "REQUEST_REMOTE_ASSISTANCE",
        "Asks the remote control centre. The vehicle waits safely until it answers; its "
        "instruction appears in the next session under remote_instruction and must be followed. "
        "Every call costs money. No remote driving.",
        ESCALATION,
        (),
        cost_note="flat fee per call + standing time",
    ),
    CommandSpec(
        "RESUME",
        "Continues a paused maneuver or carries out the control centre's clearance "
        "(instruction PROCEED). Not allowed after a detected accident.",
        MOTION,
        cost_note="none",
    ),
    CommandSpec(
        "SECURE_SCENE",
        "Secures the scene: vehicle stops, hazard lights on, accident scene secured. The "
        "vehicle stays stopped afterwards.",
        MISSION,
        cost_note="standing time",
    ),
    CommandSpec(
        "REPORT_INCIDENT",
        "Reports an accident to the control centre and police, with an emergency call if "
        "injuries are suspected. The vehicle then waits at the scene; the episode ends.",
        ESCALATION,
        (ParamSpec("injured_persons_suspected", "bool"),),
        terminal=True,
        cost_note="accident report; a false alarm without an accident",
    ),
)

COMMAND_NAMES = tuple(COMMANDS)


def list_commands() -> list[dict[str, Any]]:
    return [spec.catalog_entry() for spec in COMMANDS.values()]


def parse_command(payload: object) -> CommandRequest:
    """Prueft Form und Parameterbounds eines Agentenbefehls (ohne Situationsbezug)."""
    if isinstance(payload, CommandRequest):
        payload = {
            "command": payload.command,
            "parameters": dict(payload.parameters),
            "reason": payload.reason,
        }
    if not isinstance(payload, Mapping):
        raise CommandError("INVALID_ARGUMENT", "agent output must be a JSON object")
    required = {"command", "parameters", "reason"}
    missing = required - set(payload)
    extra = set(payload) - required
    if missing:
        raise CommandError("INVALID_ARGUMENT", f"missing required fields: {', '.join(sorted(missing))}")
    if extra:
        raise CommandError("INVALID_ARGUMENT", f"unknown fields: {', '.join(sorted(extra))}")
    name = payload["command"]
    if not isinstance(name, str) or name not in COMMANDS:
        raise CommandError(
            "INVALID_ARGUMENT",
            f"unknown command {name!r}; allowed: {', '.join(COMMAND_NAMES)}",
        )
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise CommandError("INVALID_ARGUMENT", "reason must be a non-empty text")
    raw = payload["parameters"]
    if not isinstance(raw, Mapping):
        raise CommandError("INVALID_ARGUMENT", "parameters must be an object")
    spec = COMMANDS[name]
    known = {param.name for param in spec.params}
    unknown = set(raw) - known
    if unknown:
        raise CommandError(
            "INVALID_ARGUMENT", f"unknown parameters for {name}: {', '.join(sorted(unknown))}"
        )
    normalized: dict[str, Any] = {}
    for param in spec.params:
        if param.name in raw:
            normalized[param.name] = param.normalize(raw[param.name])
        elif param.required:
            raise CommandError("INVALID_ARGUMENT", f"parameter {param.name} missing for {name}")
        elif param.default is not None:
            normalized[param.name] = param.default
    return CommandRequest(command=name, parameters=normalized, reason=reason.strip())


def request_to_payload(request: CommandRequest) -> dict[str, Any]:
    return {
        "command": request.command,
        "parameters": dict(request.parameters),
        "reason": request.reason,
    }


def decision_schema() -> dict[str, Any]:
    """JSON-Schema der Agentenausgabe, aus der Registry erzeugt (siehe schemas/)."""
    branches = []
    for spec in COMMANDS.values():
        properties = {param.name: param.schema() for param in spec.params}
        branches.append(
            {
                "if": {"properties": {"command": {"const": spec.name}}, "required": ["command"]},
                "then": {
                    "properties": {
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [param.name for param in spec.params if param.required],
                            "properties": properties,
                        }
                    }
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.local/autonomy-recovery-sim/agent_decision.schema.json",
        "title": "AutonomyRecoverySim Agent Command 4.0",
        "type": "object",
        "additionalProperties": False,
        "required": ["command", "parameters", "reason"],
        "properties": {
            "command": {"enum": list(COMMAND_NAMES)},
            "parameters": {"type": "object"},
            "reason": {"type": "string", "minLength": 1},
        },
        "allOf": branches,
    }
