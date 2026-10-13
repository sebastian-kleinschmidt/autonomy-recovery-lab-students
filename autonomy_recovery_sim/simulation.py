from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .agent_contract import AgentPolicy
from .commands import COMMANDS
from .costs import incident_handling
from .engine import SimulationEngine
from .models import Event
from .scenario import Scenario, SolutionPath, load_scenario


# Felder, die die Referenzloesung anzeigen (Versuch ohne Referenzanzeige, ``--hide-reference``).
REFERENCE_FIELDS = (
    "expected_commands",
    "expected_reason",
    "expected_sequence",
    "expected_outcome",
    "expected_outcomes",
    "expected_paths",
    "matched_solution_path",
    "intervention_expected",
)
PREPARATORY_COMMANDS: frozenset[str] = frozenset()
# Befehle, mit denen der Agent in die Lage eingreift (statt zu warten oder zu beobachten).
INTERVENING_COMMANDS = frozenset(
    {
        "PASS_OBSTACLE", "CROSS_LOW_RISK_OBJECT", "REPLAN_ROUTE",
        "REVERSE_SHORT", "PULL_OVER", "REQUEST_REMOTE_ASSISTANCE", "SAFE_STOP",
        "RETURN_HOME", "DROP_PASSENGER", "ABORT_TO_LANE", "HONK",
    }
)


def _sequence_matched(commands: list[dict[str, object]], steps: tuple[tuple[str, ...], ...]) -> int:
    matched = 0
    for item in commands:
        if matched == len(steps):
            break
        if item.get("status") == "SUCCEEDED" and item["command"] in steps[matched]:
            matched += 1
    return matched


@dataclass
class SimulationResult:
    scenario_id: str
    expected_commands: tuple[str, ...]
    command: str
    command_correct: bool
    commands: list[dict[str, object]]
    liberated: bool
    maneuver_aborted: bool
    abort_reason: str | None
    abort_stabilized: bool
    collision: bool
    reached_goal: bool
    terminal_outcome: str | None
    remote_resolved: bool
    final_time_s: float
    final_s_m: float
    minimum_clearance_m: float
    costs: dict[str, object]
    agent_name: str
    agent_status: str
    agent_trace: list[dict[str, object]]
    agent_error: str | None
    events: list[Event]
    trajectory: list[dict[str, float]]
    scenario: Scenario
    # Angenommene Sicherheitsbefunde; sie verhindern eine positive Sicherheitsbewertung.
    safety_warnings: list[str] = field(default_factory=list)
    # Unfaelle, ihre Abwicklung und was vor dem Aufprall abrufbar gewesen waere.
    incident: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "map": {
                "name": self.scenario.road.name,
                "source": self.scenario.road.source,
                "source_url": self.scenario.road.source_url,
                "osm_way_id": self.scenario.road.osm_way_id,
            },
            "difficulty": self.scenario.difficulty.to_dict(),
            "expected_commands": list(self.expected_commands),
            "expected_reason": self.scenario.expected_reason,
            "command": self.command,
            "command_correct": self.command_correct,
            "commands": self.commands,
            "expected_sequence": [list(step) for step in self.scenario.expected_sequence],
            "expected_outcomes": list(self.scenario.expected_outcomes),
            "expected_paths": [path.to_dict() for path in self.scenario.solution_paths],
            "matched_solution_path": self.matched_solution_path.path_id if self.matched_solution_path else None,
            "sequence_steps": self.sequence_steps,
            "sequence_matched": self.sequence_matched,
            "sequence_complete": self.sequence_complete,
            "solution_complete": self.solution_complete,
            "safety_compliant": self.safety_compliant,
            "mission_status": self.mission_status,
            "liberated": self.liberated,
            "maneuver_aborted": self.maneuver_aborted,
            "abort_reason": self.abort_reason,
            "abort_stabilized": self.abort_stabilized,
            "outcome": self.outcome,
            "collision": self.collision,
            "reached_goal": self.reached_goal,
            "final_time_s": round(self.final_time_s, 3),
            "final_s_m": round(self.final_s_m, 3),
            "minimum_clearance_m": round(self.minimum_clearance_m, 3),
            "costs": self.costs,
            "intervention_expected": self.intervention_expected,
            "intervened": self.intervened,
            "budget_eur": self.scenario.budget_eur,
            "within_budget": self.within_budget,
            "agent": {"name": self.agent_name, "status": self.agent_status},
            "agent_trace": self.agent_trace,
            "agent_error": self.agent_error,
            "safety_warnings": self.safety_warnings,
            "incident": self.incident,
            "events": [e.to_dict() for e in self.events],
            "trajectory": self.trajectory,
        }

    @property
    def intervention_expected(self) -> bool:
        """Verlangt das Szenario einen Eingriff (echter Deadlock) oder nur Warten bzw. Beobachten?"""
        return bool(INTERVENING_COMMANDS.intersection(self.expected_commands))

    @property
    def intervened(self) -> bool:
        """Hat der Agent (auch ueber die Regel-Engine) eingegriffen?"""
        return any(item["command"] in INTERVENING_COMMANDS for item in self.commands)

    @property
    def sequence_matched(self) -> int:
        """Erledigte Schritte des Loesungswegs: erfolgreiche Befehle in dieser Reihenfolge."""
        return _sequence_matched(self.commands, self.evaluated_sequence)

    @property
    def matched_solution_path(self) -> SolutionPath | None:
        return next((path for path in self.scenario.solution_paths
                     if path.outcome == self.outcome
                     and _sequence_matched(self.commands, path.sequence) == len(path.sequence)), None)

    @property
    def evaluated_sequence(self) -> tuple[tuple[str, ...], ...]:
        if not self.scenario.solution_paths:
            return self.scenario.expected_sequence
        # Teilfortschritt nie aus verschiedenen Alternativen zusammensetzen.
        path = self.matched_solution_path or max(
            self.scenario.solution_paths,
            key=lambda path: (path.outcome == self.outcome,
                              _sequence_matched(self.commands, path.sequence) / len(path.sequence)),
        )
        return path.sequence

    @property
    def sequence_steps(self) -> int:
        return len(self.evaluated_sequence)

    @property
    def sequence_complete(self) -> bool:
        return self.sequence_matched == self.sequence_steps

    @property
    def outcome_correct(self) -> bool:
        return self.outcome in self.scenario.expected_outcomes

    @property
    def solution_complete(self) -> bool:
        return (self.command_correct and self.sequence_complete and self.outcome_correct
                and (not self.scenario.solution_paths or self.matched_solution_path is not None))

    @property
    def safety_compliant(self) -> bool:
        return (not self.collision and not self.safety_warnings
                and (not self.maneuver_aborted or self.abort_stabilized))

    @property
    def mission_status(self) -> str:
        if self.reached_goal:
            return "completed"
        if self.terminal_outcome == "safe_stopped":
            return "safe_stopped"
        if self.terminal_outcome is not None:
            return "terminated"
        return "open"

    @property
    def within_budget(self) -> bool:
        return float(self.costs["total_eur"]) <= self.scenario.budget_eur

    @property
    def outcome(self) -> str:
        if self.agent_status == "waiting_for_agent":
            return "waiting_for_agent"
        if self.terminal_outcome is not None:
            return self.terminal_outcome
        if self.maneuver_aborted:
            return "aborted"
        if self.remote_resolved and self.reached_goal:
            return "remote_resolved"
        if self.liberated:
            return "liberated"
        if self.commands:
            return "resolved" if self.reached_goal else "waiting"
        return "not_triggered"

    def write_json(self, path: Path, *, hide_reference: bool = False) -> None:
        """Schreibt die Rohdaten; ``hide_reference`` laesst alles weg, was die Loesung verraet."""
        data = self.to_dict()
        if hide_reference:
            for key in REFERENCE_FIELDS:
                data.pop(key, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf8")


def run_scenario(
    source: str | Path | Scenario,
    *,
    agent: AgentPolicy | None = None,
    agent_name: str | None = None,
) -> SimulationResult:
    scenario = source if isinstance(source, Scenario) else load_scenario(source)
    engine = SimulationEngine(scenario, agent=agent, agent_name=agent_name)
    while not engine.done:
        engine.step()

    return result_from_engine(engine)


def result_from_engine(engine: SimulationEngine) -> SimulationResult:
    """Wertet den tatsaechlich gefahrenen Lauf aus, auch fuer die Live-Oberflaeche."""
    scenario = engine.scenario

    final_s_m, _, _, _ = engine.ego_route_state
    commands = list(engine.command_history)
    if engine.active is not None:
        commands.append(
            {
                "action_id": engine.active.action_id,
                "command": engine.active.request.command,
                "parameters": dict(engine.active.request.parameters),
                "status": "RUNNING",
                "outcome": "IN_PROGRESS",
                "elapsed_s": round(engine.time_s - engine.active.start_time_s, 2),
                "source": engine.active.source,
            }
        )
    substantive = next(
        (item for item in engine.commands if item.command not in PREPARATORY_COMMANDS),
        engine.first_command,
    )
    command = substantive.command if substantive else "NONE"
    first_name = engine.first_command.command if engine.first_command else "NONE"

    result = SimulationResult(
        scenario_id=scenario.scenario_id,
        expected_commands=scenario.expected_commands,
        command=command,
        # Richtig ist, wenn der erste Befehl ODER der erste inhaltliche Befehl erwartet war
        # (Informations- und Regel-Engine-Befehle bereiten nur vor).
        command_correct=(
            (command in scenario.expected_commands or first_name in scenario.expected_commands)
            and engine.agent_error is None
        ),
        commands=commands,
        liberated=engine.liberated,
        maneuver_aborted=engine.maneuver_aborted,
        abort_reason=engine.abort_reason,
        abort_stabilized=engine.abort_stabilized,
        collision=engine.collision,
        reached_goal=engine.reached_goal,
        terminal_outcome=engine.terminal_outcome,
        remote_resolved=engine.remote_resolved,
        final_time_s=engine.time_s,
        final_s_m=final_s_m,
        minimum_clearance_m=engine.minimum_clearance_m,
        costs=engine.costs(),
        agent_name=engine.agent_name,
        agent_status=engine.agent_status,
        agent_trace=engine.agent_trace,
        agent_error=engine.agent_error,
        events=engine.events,
        trajectory=engine.trajectory,
        scenario=scenario,
        safety_warnings=list(engine.safety_warnings),
        incident=incident_report(engine),
    )
    if scenario.solution_paths:
        # Vorbereitendes Warten ist erlaubt; die vollstaendige Alternative entscheidet.
        result.command_correct = engine.agent_error is None and any(
            _sequence_matched(commands, path.sequence) > 0 for path in scenario.solution_paths
        )
    return result


def incident_report(engine: SimulationEngine) -> dict[str, object]:
    """Unfaelle mit Rueckblick: Welche Entscheidung ging voraus, und was blieb ungeprueft?"""
    log = engine.incidents
    if not log.impacts:
        return {}
    decisions = [
        event
        for event in engine.events
        if event.event == "agent_decision_attempt" and event.details.get("accepted")
    ]
    impacts = []
    for impact in log.impacts:
        entry: dict[str, object] = {
            "t_s": round(impact.time_s, 1),
            "kind": impact.kind,
            "severity": impact.severity,
            "direction": impact.direction,
            "vulnerable": impact.vulnerable,
            "detected_by_vehicle": impact.detected,
        }
        # Die Sicherheitspruefung laeuft im Schritt vor der Agentensitzung: Entscheidungen im
        # selben Schritt fielen erst nach dem Aufprall.
        before = [event for event in decisions if event.time_s < impact.time_s - 1e-9]
        if before:
            decision = before[-1]
            session = decision.details.get("session")
            called = sorted(
                {
                    str(event.details.get("name"))
                    for event in engine.events
                    if event.event == "agent_tool_call"
                    and event.details.get("session") == session
                    and "result" in event.details
                }
            )
            command = str(decision.details.get("command"))
            required = COMMANDS[command].evidence_tools if command in COMMANDS else frozenset()
            entry["preceding_decision"] = {
                "t_s": round(decision.time_s, 1),
                "command": command,
                "tools_called": called,
                "required_evidence_missing": sorted(required - set(called)),
                "warnings_accepted": list(decision.details.get("warnings", [])),  # type: ignore[arg-type]
            }
        impacts.append(entry)
    return {
        "impacts": impacts,
        "handling": incident_handling(engine),
        "vehicle_status": log.vehicle_status(),
    }
