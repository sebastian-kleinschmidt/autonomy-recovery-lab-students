"""Bewertung der Werkzeugnutzung: gemessen am Minimum, mit dem man die Lage erkennt und loest.

Das Minimum umfasst die Lagepruefungen, die die Befehle des guenstigsten Loesungswegs verlangen
(``required_evidence``), und die Werkzeuge, die die Hinweise des Szenarios nennen
(``reference.hints``): Ohne sie liesse sich die richtige Handlung nur raten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .commands import COMMANDS
from .rules import TURN_EVIDENCE_TOOLS

if TYPE_CHECKING:
    from .scenario import Scenario
    from .simulation import SimulationResult

TOOL_RATINGS = ("efficient", "adequate", "wasteful")
TOOL_RATING_LABELS = {"efficient": "effizient", "adequate": "angemessen", "wasteful": "verschwenderisch"}
# Spielraum ueber dem Minimum je Entscheidungsrunde: Ein allgemeiner Agent muss erst einordnen
# (Ampel, Route, Hindernis); drei Abfragen je Runde sind effizient, sechs angemessen.
EFFICIENT_PER_ROUND = 3
ADEQUATE_PER_ROUND = 6


TOOL_NAME = re.compile(r"\b(?:get|check)_[a-z_]+\b")


def hint_tools(scenario: Scenario) -> set[str]:
    """Werkzeuge, die die Hinweise des Szenarios ausdruecklich nennen."""
    try:
        reference = json.loads(Path(scenario.source_path).read_text("utf8")).get("reference", {})
    except (OSError, ValueError):
        return set()
    return {name for hint in reference.get("hints", []) for name in TOOL_NAME.findall(str(hint))}


def minimum_tools(scenario: Scenario) -> tuple[int, list[str]]:
    """Minimum und die Werkzeuge, auf denen es beruht (Pflichtpruefungen und Hinweise)."""
    evidence = _evidence_steps(scenario)
    named = set().union(*(tools for _, tools in evidence)) if evidence else set()
    extra = hint_tools(scenario) - named
    return sum(count for count, _ in evidence) + len(extra), sorted(named | extra)


def _evidence_steps(scenario: Scenario) -> list[tuple[int, set[str]]]:
    if scenario.solution_paths:
        paths = [path.sequence for path in scenario.solution_paths]
    elif scenario.expected_sequence:
        paths = [scenario.expected_sequence]
    else:
        paths = [(tuple(scenario.expected_commands),)] if scenario.expected_commands else [()]
    best: list[tuple[int, set[str]]] | None = None
    for path in paths:
        steps = []
        for step in path:
            options = [(len(_tools(command, scenario)), _tools(command, scenario)) for command in step]
            steps.append(min(options, key=lambda item: item[0]) if options else (0, set()))
        if best is None or sum(c for c, _ in steps) < sum(c for c, _ in best):
            best = steps
    return best or []


def _tools(command: str, scenario: Scenario) -> set[str]:
    if command not in COMMANDS:
        return set()
    tools = set(COMMANDS[command].evidence_tools)
    if command == "REPLAN_ROUTE" and scenario.route.turn_required:
        tools |= TURN_EVIDENCE_TOOLS
    return tools


def minimum_tool_calls(scenario: Scenario) -> int:
    """Wenigste Abfragen, mit denen man die Lage erkennt und den guenstigsten Weg besteht."""
    return minimum_tools(scenario)[0]


def tool_calls(result: SimulationResult) -> int:
    return sum(entry["type"] == "tool_call" for entry in result.agent_trace)


def decision_rounds(result: SimulationResult) -> int:
    """Entscheidungsrunden des Laufs (Sitzungen, in denen der Agent gefragt wurde)."""
    return len({entry.get("session") for entry in result.agent_trace if entry.get("session") is not None})


def rate_tool_use(used: int, minimum: int, rounds: int) -> str:
    if used <= minimum + EFFICIENT_PER_ROUND * rounds:
        return "efficient"
    if used <= minimum + ADEQUATE_PER_ROUND * rounds:
        return "adequate"
    return "wasteful"
