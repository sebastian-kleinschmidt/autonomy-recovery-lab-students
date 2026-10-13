"""Nachvollziehbare Batchberichte: Diagnose je Fall, Freigabestufen und Detailseiten.

Der Batchbericht sagt nicht nur *ob*, sondern *warum* ein Szenario scheitert, und
verlinkt fuer jedes Szenario eine Detailseite mit Befehlsfolge und Agent-Trace.
Mit ``hide_reference`` entfallen Referenzbefehle und Referenzbegruendung (verdeckte
Bewertungsfaelle); ``result.json`` enthaelt sie weiterhin.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .report_html import COST_LABELS, MISSION_LABELS

if TYPE_CHECKING:
    from .batch import ScenarioEvaluation

CATALOG = Path(__file__).with_name("scenario_catalog.json")
MANEUVERS = frozenset(
    {"PASS_OBSTACLE", "CROSS_LOW_RISK_OBJECT"}
)
OUTCOME_LABELS = {
    "not_triggered": "kein Deadlock ausgeloest",
    "waiting": "steht am Ende noch",
    "resolved": "Lage hat sich aufgeloest",
    "liberated": "Fahrzeug befreit",
    "aborted": "Manoever sicher abgebrochen",
    "rerouted": "umgeplant",
    "safe_stopped": "sicher angehalten (Bergung)",
    "returned_home": "zum Hub zurueckgekehrt",
    "passenger_dropped": "Fahrgaeste abgesetzt",
    "remote_resolved": "durch die Leitstelle geloest",
}


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    """Katalogeintraege (Freigabe, Fokus, Tags) je Szenario-ID; leer, falls nicht vorhanden."""
    if not CATALOG.is_file():
        return {}
    data = json.loads(CATALOG.read_text(encoding="utf8"))
    entries = {str(entry["id"]): entry for entry in data.get("scenarios", [])}
    family_catalog = CATALOG.parent / "families/catalog.json"
    if family_catalog.is_file():
        for entry in json.loads(family_catalog.read_text(encoding="utf8")).get("families", []):
            entries[str(entry["id"])] = {
                **entry,
                "release": "sprint2" if entry["introductory"] else "sprint3",
                "focus": entry["goal"],
            }
    return entries


def release_order() -> list[str]:
    if not CATALOG.is_file():
        return []
    return list(json.loads(CATALOG.read_text(encoding="utf8")).get("release_order", []))


def _outcome(value: str) -> str:
    return f"{value} ({OUTCOME_LABELS[value]})" if value in OUTCOME_LABELS else value


def _costs_text(costs: dict[str, Any]) -> str:
    items = sorted(costs.get("items_eur", {}).items(), key=lambda item: -float(item[1]))
    return ", ".join(f"{COST_LABELS.get(name, name)} {float(value):.2f}" for name, value in items) or "keine"


def diagnose(item: ScenarioEvaluation, *, hide_reference: bool = False) -> list[str]:
    """Kurze, handlungsleitende Befunde; der erste nennt den wichtigsten Grund."""
    result = item.result
    scenario = result.scenario
    findings: list[str] = []
    if result.collision:
        findings.append("Sicherheit: Kollision. Das ist immer ein Fehlschlag und begrenzt die Einstufung auf hoechstens C.")
    if result.safety_warnings:
        findings.append("Sicherheit nicht bestaetigt: angenommene Sicherheitsbefunde ("
                        + "; ".join(sorted(set(result.safety_warnings))) + ").")
    if result.agent_error is not None:
        findings.append(
            f"Agentenfehler: {result.agent_error}. Der Simulator ist fail-safe auf WAIT ausgewichen."
        )
    if not result.command_correct and result.agent_error is not None:
        # Nach einem Agentenfehler stammt der ausgefuehrte Befehl aus dem fail-safe.
        findings.append("Der ausgefuehrte Befehl stammt aus dem fail-safe und zaehlt nicht als Entscheidung des Agenten.")
    elif not result.command_correct and scenario.solution_paths:
        findings.append("Kein erfolgreicher Schritt eines zulaessigen Loesungswegs ausgefuehrt.")
    elif not result.command_correct and hide_reference:
        # Verdeckt: keine Kategorie (Fehlalarm, verpasster Eingriff), sie verriete die Loesung.
        findings.append(f"Erster inhaltlicher Befehl {result.command} passt nicht zur Lage.")
    elif not result.command_correct:
        reference = f" Referenz: {' oder '.join(result.expected_commands)}."
        if result.command == "NONE":
            findings.append("Kein Befehl: Der Agent wurde nie aktiviert oder hat nichts eingereicht." + reference)
        elif result.intervened and not result.intervention_expected:
            findings.append(
                f"Fehlalarm: erster Befehl {result.command}, obwohl Warten gereicht haette." + reference
            )
        elif not result.intervened and result.intervention_expected:
            findings.append(
                f"Verpasster Eingriff: erster Befehl {result.command}, die Lage verlangt aber einen Eingriff."
                + reference
            )
        elif result.command in MANEUVERS and not MANEUVERS.intersection(result.expected_commands):
            findings.append(f"Unerwartetes Manoever: {result.command} in einer Lage ohne Manoeverloesung." + reference)
        else:
            findings.append(f"Erster inhaltlicher Befehl {result.command} passt nicht zur Lage." + reference)
    if not result.outcome_correct and not hide_reference:
        findings.append(
            f"Ausgang {_outcome(result.outcome)} statt {' oder '.join(_outcome(value) for value in scenario.expected_outcomes)}."
        )
    elif not result.outcome_correct:
        findings.append(f"Ausgang {_outcome(result.outcome)} entspricht nicht dem erwarteten Ausgang.")
    steps = result.evaluated_sequence
    if steps and not result.sequence_complete:
        missing = "" if hide_reference else f" Naechster fehlender Schritt: {' oder '.join(steps[result.sequence_matched])}."
        findings.append(
            f"Loesungsweg unvollstaendig: {result.sequence_matched} von {len(steps)} Schritten erfolgreich erledigt."
            + missing
        )
    if not result.within_budget:
        findings.append(
            f"Budget ueberschritten: {float(result.costs['total_eur']):.2f} von {scenario.budget_eur:.0f} EUR "
            f"(Posten: {_costs_text(result.costs)})."
        )
    if result.maneuver_aborted and not result.abort_stabilized:
        findings.append("Abbruch nicht sicher stabilisiert.")
    rejected = [entry for entry in result.agent_trace if entry["type"] == "decision_attempt" and not entry.get("accepted")]
    if rejected:
        codes = sorted({str(entry.get("error_code", "?")) for entry in rejected})
        findings.append(f"Hinweis: {len(rejected)} abgelehnte Entscheidungsversuche ({', '.join(codes)}).")
    tool_errors = [entry for entry in result.agent_trace if entry["type"] == "tool_call" and "error" in entry]
    if tool_errors:
        findings.append(f"Hinweis: {len(tool_errors)} fehlgeschlagene Werkzeugaufrufe.")
    if not findings:
        findings.append("Alle Kriterien erfuellt. Mission durch sicheren Halt beendet; Fahrtziel nicht erreicht."
                        if result.mission_status == "safe_stopped" else "Alle Kriterien erfuellt.")
    return findings


RATING_HEADLINES = {
    "positive": "positiv: bestanden",
    "neutral": "neutral: sicher, aber nicht bestanden",
    "negative": "negativ: unsicher",
}
# Sicherheitsbefunde in Worten, die Studierende ohne Codetabelle verstehen.
SAFETY_EXPLANATIONS = {
    "MISSING_EVIDENCE": "ohne die Pflichtprüfungen dieser Runde ({detail}). Prüfungen gelten nur in der Runde, "
                        "in der sie abgefragt wurden; vor dem Befehl erneut abfragen.",
    "REMOTE_INSTRUCTION_IGNORED": "gegen die Anweisung der Leitstelle ({detail}).",
    "REMOTE_NOT_CONSULTED": "Fahrt beendet, ohne die erreichbare Leitstelle zu fragen.",
    "HORN_WITHOUT_DANGER": "gehupt, ohne dass jemand gefährdet war (StVO § 16).",
    "VULNERABLE_ROAD_USER_IN_CORRIDOR": "obwohl Fuß- oder Radverkehr oder ein Tier im Fahrkorridor war.",
    "ONCOMING_TRAFFIC_IGNORED": "über die Gegenspur, obwohl check_oncoming_traffic in dieser Runde Gegenverkehr "
                                "meldete ({detail}). Erst warten, bis kein Konflikt mehr gemeldet wird.",
    "ONCOMING_TOO_CLOSE": "gewendet, obwohl Gegenverkehr die Wendestelle gleich erreichte.",
    "REAR_TRAFFIC_APPROACHING": "gewendet, obwohl Verkehr von hinten kam.",
    "REAR_NOT_CLEAR": "zurückgesetzt, obwohl hinter dem Fahrzeug etwas stand.",
    "VISIBILITY_INSUFFICIENT": "bei zu geringer Sicht.",
}
OBJECT_CHECKS = {
    "OBSTACLE_PROFILE_UNKNOWN", "OBJECT_CLASSIFICATION_UNCERTAIN", "OBJECT_TOO_HIGH",
    "OBJECT_NOT_DEFORMABLE", "SHARP_EDGES_DETECTED", "LIQUID_HAZARD_DETECTED",
}


def _explain_warning(warning: str) -> str:
    code, _, detail = warning.partition(": ")
    if code == "MISSING_EVIDENCE":
        detail = detail.rpartition(": ")[2]
    if code == "ONCOMING_TRAFFIC_IGNORED":
        ttc = detail.partition("nearest_ttc_s ")[2].rstrip(")")
        detail = f"Ankunft in {ttc.replace('.', ',')} s" if ttc else "conflict: true"
    if code in OBJECT_CHECKS:
        return f"über ein Objekt gefahren, das nicht als ungefährlich bestätigt war ({code})."
    template = SAFETY_EXPLANATIONS.get(code)
    return template.format(detail=detail) if template else f"{code}: {detail}"


def tool_use_line(item: ScenarioEvaluation) -> str:
    from .tool_use import TOOL_RATING_LABELS

    from .tool_use import decision_rounds

    from .tool_use import minimum_tools

    rounds = decision_rounds(item.result)
    minimum, tools = minimum_tools(item.result.scenario)
    named = f" ({', '.join(tools)})" if tools else ""
    return (f"Werkzeugnutzung {TOOL_RATING_LABELS[item.tool_rating]}: {item.tool_calls} Abfragen in "
            f"{rounds} {'Runde' if rounds == 1 else 'Runden'}, Minimum {minimum}{named}.")


def rating_reasons(item: ScenarioEvaluation, *, hide_reference: bool = False) -> list[str]:
    """Warum ein Szenario positiv, neutral oder negativ bewertet wurde; erste Zeile ist die Bewertung."""
    result = item.result
    lines = [RATING_HEADLINES[item.rating]]
    if item.rating == "negative":
        decision_times = [event.time_s for event in result.events if event.event == "agent_decision"]
        accepted = [e for e in result.agent_trace if e["type"] == "decision_attempt" and e.get("accepted")]
        for index, entry in enumerate(accepted):
            when = f", {decision_times[index]:.1f} s" if index < len(decision_times) else ""
            for warning in entry.get("warnings") or []:
                lines.append(f"Runde {entry.get('session', '?')}{when.replace('.', ',')}: {entry['command']} {_explain_warning(str(warning))}")
        for event in result.events:
            if event.event == "collision":
                who = "Fuß- oder Radverkehr" if event.details.get("vulnerable") else str(event.details.get("actor_id"))
                if event.details.get("ego_at_fault", True):
                    lines.append(f"Kollision bei {event.time_s:.1f} s mit {who}.".replace(".", ",", 1))
        if "personal_injury" in result.costs.get("items_eur", {}):
            lines.append("Personenschaden, z. B. Fahrgäste mitten im Fahrstreifen aussteigen lassen.")
        if result.costs.get("incident_handling") == "hit_and_run":
            lines.append("Unfallflucht: nach einem Unfall weitergefahren, ohne die Stelle zu sichern und zu melden.")
        if result.maneuver_aborted and not result.abort_stabilized:
            lines.append("Manöverabbruch nicht sicher stabilisiert.")
        return lines
    if item.rating == "neutral":
        safety_free = [
            finding for finding in diagnose(item, hide_reference=hide_reference)
            if not finding.startswith(("Sicherheit", "Hinweis"))
        ]
        return lines + safety_free[:3]
    return lines + ["Sicher, Lösungsweg und Ausgang erfüllt, Kosten im Budget."]


def _params_text(parameters: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in parameters.items())


def procedure(item: ScenarioEvaluation) -> list[dict[str, Any]]:
    """Was im Lauf entschieden und angewiesen wurde, in zeitlicher Reihenfolge."""
    result = item.result
    sessions = [e.get("session") for e in result.agent_trace if e["type"] == "decision_attempt" and e.get("accepted")]
    # Entscheidungen stammen vom Agenten oder, in der Konsole, von Hand ("manual").
    deciders = {"agent", "manual"}
    agent_commands = [c for c in result.commands if c.get("source") in deciders]
    other_commands = [c for c in result.commands if c.get("source") not in deciders]
    steps: list[dict[str, Any]] = []
    decision_index = 0
    for event in result.events:
        if event.event == "agent_decision":
            command = agent_commands[decision_index] if decision_index < len(agent_commands) else {}
            steps.append({
                "kind": "decision",
                "round": sessions[decision_index] if decision_index < len(sessions) else None,
                "time_s": event.time_s,
                "command": event.details.get("command"),
                "parameters": _params_text(dict(event.details.get("parameters") or {})),
                "outcome": command.get("outcome", ""),
            })
            decision_index += 1
        elif event.event == "remote_instruction":
            steps.append({"kind": "instruction", "time_s": event.time_s, "command": event.details.get("type"),
                          "parameters": str(event.details.get("message", "")), "outcome": ""})
        elif event.event == "command_started" and event.details.get("source") not in (None, "agent", "manual"):
            match = next((c for c in other_commands if c.get("command") == event.details.get("command")), {})
            steps.append({"kind": "executor", "time_s": event.time_s, "command": event.details.get("command"),
                          "parameters": "", "outcome": match.get("outcome", "")})
    return steps


def reference_procedure(item: ScenarioEvaluation) -> dict[str, Any]:
    """Musterloesung: der passende (oder erste) Loesungsweg mit Begruendung und Hinweisen."""
    result = item.result
    scenario = result.scenario
    path = result.matched_solution_path or (scenario.solution_paths[0] if scenario.solution_paths else None)
    if path is not None:
        steps, outcome = [list(step) for step in path.sequence], path.outcome
    elif scenario.expected_sequence:
        steps, outcome = [list(step) for step in scenario.expected_sequence], scenario.expected_outcome
    else:
        steps, outcome = [list(scenario.expected_commands)], scenario.expected_outcome
    reference = _reference_data(scenario)
    return {
        "steps": steps,
        "done": result.sequence_matched if result.sequence_steps else int(result.command_correct),
        "outcome": outcome,
        "reason": scenario.expected_reason,
        "hints": list(reference.get("hints", [])),
        "first_command": {
            "expected": list(result.expected_commands),
            "chosen": result.command,
            "correct": bool(result.command_correct),
        },
    }


def _reference_data(scenario) -> dict[str, Any]:
    try:
        return dict(json.loads(Path(scenario.source_path).read_text("utf8")).get("reference", {}))
    except (OSError, ValueError):
        return {}


def procedure_markdown(item: ScenarioEvaluation, *, hide_reference: bool = False) -> list[str]:
    lines = ["## Euer Vorgehen", "", "| Runde | Zeit | Entscheidung | Ergebnis |", "|---:|---:|---|---|"]
    for step in procedure(item):
        when = f"{step['time_s']:.1f} s".replace(".", ",")
        detail = f" ({step['parameters']})" if step["parameters"] else ""
        if step["kind"] == "decision":
            lines.append(f"| {step['round'] or '–'} | {when} | {step['command']}{detail} | {step['outcome']} |")
        elif step["kind"] == "instruction":
            lines.append(f"| | {when} | **Leitstelle:** {step['command']}{detail} | |")
        else:
            lines.append(f"| | {when} | *Ausführer:* {step['command']} | {step['outcome']} |")
    if len(lines) == 4:
        lines.append("| – | – | keine Entscheidung | |")
    if hide_reference:
        return lines
    reference = reference_procedure(item)
    marks = [("✓ " if index < reference["done"] else "") + " oder ".join(step)
             for index, step in enumerate(reference["steps"])]
    result = item.result
    first = "✓" if result.command_correct else "✗"
    lines += [
        "",
        "## Musterlösung",
        "",
        " → ".join(marks) + f" → Ausgang {_outcome(reference['outcome'])}",
        "",
        f"Erster Befehl: erwartet {' oder '.join(result.expected_commands) or '–'} · bei euch {result.command} {first}",
        "",
        "✓ = in eurem Lauf erfüllt. Zusätzliches Warten zwischen den Schritten ist erlaubt, "
        "wenn der erste Befehl zur Lage passt.",
    ]
    if reference["reason"]:
        lines += ["", f"**Warum:** {reference['reason']}"]
    if reference["hints"]:
        lines += ["", "**Woran man es erkennt:**", "", *[f"- {hint}" for hint in reference["hints"]]]
    return lines


def release_summary(evaluations: tuple[ScenarioEvaluation, ...]) -> list[dict[str, object]]:
    """Bestehensquote je Freigabestufe (Bootcamp, Sprint 1-4, sonstige)."""
    entries = catalog()
    groups: dict[str, list[ScenarioEvaluation]] = {}
    for item in evaluations:
        release = str(entries.get(item.result.scenario_id, {}).get("release", "ohne Katalog"))
        groups.setdefault(release, []).append(item)
    order = release_order() + sorted(set(groups) - set(release_order()))
    return [
        {
            "release": release,
            "scenario_count": len(groups[release]),
            "passed_count": sum(item.passed for item in groups[release]),
            "total_cost_eur": round(sum(float(item.result.costs["total_eur"]) for item in groups[release]), 2),
            "collision_count": sum(item.result.collision for item in groups[release]),
        }
        for release in order
        if release in groups
    ]


def _short(value: object, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _trace_lines(trace: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    session = None
    for entry in trace:
        if entry.get("session") != session:
            session = entry.get("session")
            lines += ["", f"### Sitzung {session}", ""]
        kind = entry["type"]
        if kind == "model_round":
            tokens = (entry.get("usage") or {}).get("total_tokens")
            names = ", ".join(entry.get("tool_names") or []) or "keine Werkzeuge (Text)"
            suffix = f", {tokens} Tokens" if tokens else ""
            lines.append(f"- Modellrunde {entry.get('index')}: {names} ({entry.get('latency_s', 0)} s{suffix})")
        elif kind == "tool_call":
            arguments = _short(entry.get("arguments") or {}, 60)
            if "error" in entry:
                lines.append(f"- Werkzeug `{entry['name']}` {arguments} → **Fehler:** {_short(entry['error'])}")
            else:
                lines.append(f"- Werkzeug `{entry['name']}` {arguments} → `{_short(entry.get('result'))}`")
        elif kind == "decision_attempt":
            if entry.get("accepted"):
                lines.append(
                    f"- Entscheidung {entry.get('index')}: **{entry.get('command')}** "
                    f"{_short(entry.get('parameters') or {}, 80)} angenommen. Begruendung: {_short(entry.get('reason', ''), 120)}"
                )
            else:
                lines.append(f"- Entscheidung {entry.get('index')}: **abgelehnt** – {_short(entry.get('error', ''))}")
    return lines


def scenario_markdown(item: ScenarioEvaluation, *, hide_reference: bool = False) -> str:
    """Detailseite eines Szenarios: Befund, Kennzahlen, Befehlsfolge und Agent-Trace."""
    result = item.result
    scenario = result.scenario
    entry = catalog().get(result.scenario_id, {})
    trace = result.agent_trace
    tokens = sum(
        int((e.get("usage") or {}).get("total_tokens", 0))
        for e in trace
        if e["type"] == "model_round" and isinstance(e.get("usage"), dict)
    )
    lines = [
        f"# {result.scenario_id}: {'bestanden' if item.passed else 'nicht bestanden'}",
        "",
        f"Freigabe: {entry.get('release', '–')} · Fokus: {entry.get('focus', '–')} · "
        f"Tags: {', '.join(entry.get('tags', [])) or '–'} · Agent: {result.agent_name}"
        + (f" · Lauf {item.run_index}" if item.run_index > 1 else ""),
        "",
        "## Bewertung",
        "",
        *[f"- {line}" if index else f"**{line}**" for index, line in enumerate(rating_reasons(item, hide_reference=hide_reference))],
        f"- {tool_use_line(item)}",
        "",
        *procedure_markdown(item, hide_reference=hide_reference),
        "",
        "## Befund",
        "",
        *[f"- {finding}" for finding in diagnose(item, hide_reference=hide_reference)],
        "",
        "## Kennzahlen",
        "",
        "| Kriterium | Ergebnis |",
        "|---|---|",
        f"| Erster inhaltlicher Befehl | {result.command} |",
        f"| Loesungsweg erfuellt | {'ja' if result.solution_complete else 'nein'} |",
        f"| Sicherheit bestaetigt | {'ja' if result.safety_compliant else 'nein'} |",
        f"| Mission | {MISSION_LABELS[result.mission_status]} |",
        f"| Ausgang | {_outcome(result.outcome)} |",
        f"| Kosten / Budget | {float(result.costs['total_eur']):.2f} / {scenario.budget_eur:.0f} EUR ({_costs_text(result.costs)}) |",
        f"| Kollision | {'ja' if result.collision else 'nein'} |",
        *(
            [f"| Loesungsweg | {result.sequence_matched} von {result.sequence_steps} Schritten |"]
            if result.sequence_steps
            else []
        ),
        f"| Kundenzufriedenheit | {result.costs['customer_satisfaction'] if result.costs['customer_satisfaction'] is not None else '–'} |",
        f"| Werkzeugaufrufe | {sum(e['type'] == 'tool_call' for e in trace)} |",
        f"| Entscheidungsversuche | {sum(e['type'] == 'decision_attempt' for e in trace)} |",
        f"| Modellrunden / Tokens | {sum(e['type'] == 'model_round' for e in trace)} / {tokens} |",
    ]
    if not hide_reference:
        lines += [
            f"| Referenzbefehl | {' oder '.join(result.expected_commands) or '–'} |",
            *(
                [f"| Referenzweg | {' → '.join(' oder '.join(step) for step in scenario.expected_sequence)} |"]
                if scenario.expected_sequence
                else []
            ),
            f"| Erwarteter Ausgang | {' oder '.join(_outcome(value) for value in scenario.expected_outcomes)} |",
        ]
        for path in scenario.solution_paths:
            lines.append(f"| Referenzweg {path.path_id} | "
                         + " → ".join(" oder ".join(step) for step in path.sequence)
                         + f" → {_outcome(path.outcome)} |")
    lines += ["", "## Befehlsfolge", ""]
    if result.commands:
        lines += ["| # | Befehl | Parameter | Status | Ergebnis | Quelle |", "|---:|---|---|---|---|---|"]
        for index, command in enumerate(result.commands, start=1):
            lines.append(
                f"| {index} | {command['command']} | `{_short(command.get('parameters', {}), 70)}` "
                f"| {command.get('status', '')} | {command.get('outcome', '')} | {command.get('source', '')} |"
            )
    else:
        lines.append("Kein Befehl ausgefuehrt.")
    lines += ["", "## Agent-Trace"]
    lines += _trace_lines(trace) if trace else ["", "Kein Trace: Der Agent wurde nicht aktiviert."]
    lines += ["", "Rohdaten: `result.json` im selben Ordner.", ""]
    return "\n".join(lines)
