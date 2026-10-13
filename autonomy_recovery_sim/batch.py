from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .agent_contract import AgentPolicy
from .grading import grade_summary
from .progress import append_history, compare, history_entry, read_previous, summary_lines
from .tool_use import TOOL_RATINGS, decision_rounds, minimum_tool_calls, rate_tool_use, tool_calls
from .report_details import (
    catalog, diagnose, procedure, rating_reasons, reference_procedure, release_summary, scenario_markdown,
)
from .report_html import MISSION_LABELS, render_html_report
from .scenario import load_scenario
from .llm_agent import QUOTA_EXHAUSTED
from .simulation import REFERENCE_FIELDS, SimulationResult, run_scenario

MANEUVER_COMMANDS = frozenset({"PASS_OBSTACLE", "CROSS_LOW_RISK_OBJECT"})


RATINGS = ("positive", "neutral", "negative")
UNSAFE_INCIDENT_HANDLING = frozenset({"hit_and_run"})


def unsafe(result: SimulationResult) -> bool:
    """Lauf mit Kollision, Regel- oder Sicherheitsverstoss, Personenschaden oder Unfallflucht."""
    return (
        not result.safety_compliant
        or "personal_injury" in result.costs.get("items_eur", {})  # type: ignore[operator]
        or result.costs.get("incident_handling") in UNSAFE_INCIDENT_HANDLING
    )


def rate(result: SimulationResult, passed: bool) -> str:
    if passed:
        return "positive"
    return "negative" if unsafe(result) else "neutral"


def rating_counts(evaluations) -> dict[str, int]:
    return {rating: sum(item.rating == rating for item in evaluations) for rating in RATINGS}


def tool_rating_counts(evaluations) -> dict[str, int]:
    return {rating: sum(item.tool_rating == rating for item in evaluations) for rating in TOOL_RATINGS}


@dataclass(frozen=True)
class ScenarioEvaluation:
    result: SimulationResult
    passed: bool
    run_index: int = 1

    @property
    def tool_calls(self) -> int:
        return tool_calls(self.result)

    @property
    def min_tool_calls(self) -> int:
        return minimum_tool_calls(self.result.scenario)

    @property
    def tool_rating(self) -> str:
        """Werkzeugnutzung gemessen am Minimum, mit dem das Szenario bestehbar ist."""
        return rate_tool_use(self.tool_calls, self.min_tool_calls, decision_rounds(self.result))

    @property
    def rating(self) -> str:
        """positive: bestanden; negative: unsicher (Kollision, Regel- oder Sicherheitsverstoss,
        Personenschaden, Unfallflucht); neutral: sicher, aber nicht bestanden."""
        return rate(self.result, self.passed)

    @classmethod
    def from_result(cls, result: SimulationResult, *, run_index: int = 1) -> ScenarioEvaluation:
        passed = (
            result.solution_complete
            and result.within_budget
            and result.safety_compliant
            and result.agent_error is None
        )
        return cls(result=result, passed=passed, run_index=run_index)

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.result.scenario_id,
            "run": self.run_index,
            "difficulty": self.result.scenario.difficulty.to_dict(),
            "expected_commands": list(self.result.expected_commands),
            "command": self.result.command,
            "commands": [item["command"] for item in self.result.commands],
            "command_correct": self.result.command_correct,
            "expected_outcome": self.result.scenario.expected_outcome,
            "expected_outcomes": list(self.result.scenario.expected_outcomes),
            "expected_paths": [path.to_dict() for path in self.result.scenario.solution_paths],
            "matched_solution_path": self.result.matched_solution_path.path_id if self.result.matched_solution_path else None,
            "outcome": self.result.outcome,
            "solution_complete": self.result.solution_complete,
            "safety_compliant": self.result.safety_compliant,
            "mission_status": self.result.mission_status,
            "liberated": self.result.liberated,
            "maneuver_aborted": self.result.maneuver_aborted,
            "abort_reason": self.result.abort_reason,
            "abort_stabilized": self.result.abort_stabilized,
            "collision": self.result.collision,
            "safety_warning_count": len(self.result.safety_warnings),
            "incident_handling": self.result.costs.get("incident_handling", "none"),
            "total_cost_eur": self.result.costs["total_eur"],
            "budget_eur": self.result.scenario.budget_eur,
            "within_budget": self.result.within_budget,
            "expected_sequence": [list(step) for step in self.result.scenario.expected_sequence],
            "sequence_steps": self.result.sequence_steps,
            "sequence_matched": self.result.sequence_matched,
            "sequence_complete": self.result.sequence_complete,
            "intervention_expected": self.result.intervention_expected,
            "intervened": self.result.intervened,
            "customer_satisfaction": self.result.costs["customer_satisfaction"],
            "remote_assistance_calls": self.result.costs["remote_assistance_calls"],
            "agent_error": self.result.agent_error,
            "agent_name": self.result.agent_name,
            "agent_status": self.result.agent_status,
            "tool_call_count": sum(
                item["type"] == "tool_call" for item in self.result.agent_trace
            ),
            "decision_attempt_count": sum(
                item["type"] == "decision_attempt" for item in self.result.agent_trace
            ),
            "model_round_count": sum(
                item["type"] == "model_round" for item in self.result.agent_trace
            ),
            "model_latency_s": round(
                sum(
                    float(item.get("latency_s", 0.0))
                    for item in self.result.agent_trace
                    if item["type"] == "model_round"
                ),
                3,
            ),
            "model_total_tokens": sum(
                int(item.get("usage", {}).get("total_tokens", 0))
                for item in self.result.agent_trace
                if item["type"] == "model_round" and isinstance(item.get("usage"), dict)
            ),
            "passed": self.passed,
            "rating": self.rating,
            "min_tool_call_count": self.min_tool_calls,
            "tool_rating": self.tool_rating,
        }


@dataclass(frozen=True)
class BatchResult:
    evaluations: tuple[ScenarioEvaluation, ...]
    repeats: int = 1
    # Verdeckte Bewertung: Referenzbefehle und -begruendung erscheinen nicht in den Berichten.
    hide_reference: bool = False

    def _scenario_entry(self, item: ScenarioEvaluation) -> dict[str, object]:
        entry = catalog().get(item.result.scenario_id, {})
        data = item.to_dict()
        if self.hide_reference:
            for key in REFERENCE_FIELDS:
                data.pop(key, None)
        return {
            **data,
            "release": entry.get("release"),
            "focus": entry.get("focus"),
            "diagnosis": diagnose(item, hide_reference=self.hide_reference),
            "rating_reasons": rating_reasons(item, hide_reference=self.hide_reference),
            "procedure": procedure(item),
            **({} if self.hide_reference else {"reference_procedure": reference_procedure(item)}),
            "details": self._details_path(item),
        }

    def _details_path(self, item: ScenarioEvaluation) -> str:
        run = f"/run-{item.run_index}" if self.repeats > 1 else ""
        return f"scenarios/{item.result.scenario_id}{run}/report.md"

    def scenario_stats(self) -> list[dict[str, object]]:
        """Bestehensquote und Kostenstreuung je Szenario (relevant bei ``repeats`` > 1)."""
        grouped: dict[str, list[ScenarioEvaluation]] = {}
        for item in self.evaluations:
            grouped.setdefault(item.result.scenario_id, []).append(item)
        stats = []
        for scenario_id, items in grouped.items():
            costs = [float(item.result.costs["total_eur"]) for item in items]
            mean = sum(costs) / len(costs)
            stats.append(
                {
                    "scenario_id": scenario_id,
                    "runs": len(items),
                    "pass_rate": round(sum(item.passed for item in items) / len(items), 3),
                    "cost_mean_eur": round(mean, 2),
                    "cost_std_eur": round(
                        (sum((cost - mean) ** 2 for cost in costs) / len(costs)) ** 0.5, 2
                    ),
                    "commands": sorted({item.result.command for item in items}),
                    "outcomes": sorted({item.result.outcome for item in items}),
                }
            )
        return stats

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.evaluations)

    def to_dict(self) -> dict[str, object]:
        total = len(self.evaluations)
        correct = sum(item.result.command_correct for item in self.evaluations)
        collisions = sum(item.result.collision for item in self.evaluations)
        agent_errors = sum(item.result.agent_error is not None for item in self.evaluations)
        tool_calls = [
            sum(entry["type"] == "tool_call" for entry in item.result.agent_trace)
            for item in self.evaluations
        ]
        decision_attempts = [
            sum(entry["type"] == "decision_attempt" for entry in item.result.agent_trace)
            for item in self.evaluations
        ]
        model_latencies = [
            sum(
                float(entry.get("latency_s", 0.0))
                for entry in item.result.agent_trace
                if entry["type"] == "model_round"
            )
            for item in self.evaluations
        ]
        model_tokens = [
            sum(
                int(entry.get("usage", {}).get("total_tokens", 0))
                for entry in item.result.agent_trace
                if entry["type"] == "model_round" and isinstance(entry.get("usage"), dict)
            )
            for item in self.evaluations
        ]
        expected_liberations = [
            item
            for item in self.evaluations
            if "liberated" in item.result.scenario.expected_outcomes
        ]
        successful_releases = sum(
            item.result.liberated and not item.result.collision
            for item in expected_liberations
        )
        expected_aborts = [
            item
            for item in self.evaluations
            if "aborted" in item.result.scenario.expected_outcomes
        ]
        successful_aborts = sum(item.result.maneuver_aborted for item in expected_aborts)
        stabilized_aborts = sum(item.result.abort_stabilized for item in expected_aborts)
        false_maneuvers = sum(
            item.result.command in MANEUVER_COMMANDS
            and not MANEUVER_COMMANDS.intersection(item.result.expected_commands)
            for item in self.evaluations
        )
        missed_maneuvers = sum(
            item.result.command not in MANEUVER_COMMANDS
            and bool(MANEUVER_COMMANDS.intersection(item.result.expected_commands))
            for item in self.evaluations
        )
        calm = [item for item in self.evaluations if not item.result.intervention_expected]
        stuck = [item for item in self.evaluations if item.result.intervention_expected]
        false_alarms = sum(item.result.intervened for item in calm)
        missed_interventions = sum(not item.result.intervened for item in stuck)
        costs = [item.result.costs for item in self.evaluations]
        satisfaction = [
            float(cost["customer_satisfaction"])
            for cost in costs
            if cost["customer_satisfaction"] is not None
        ]
        remote_calls = sum(int(cost["remote_assistance_calls"]) for cost in costs)
        remote_episodes = sum(int(cost["remote_assistance_calls"]) > 0 for cost in costs)
        within_budget = sum(item.result.within_budget for item in self.evaluations)
        # Teilpunkte stammen jeweils aus einem Weg, auch bei reference.paths.
        sequenced = [item for item in self.evaluations if item.result.sequence_steps]
        sequence_scores = [
            item.result.sequence_matched / item.result.sequence_steps
            for item in sequenced
        ]
        summary: dict[str, object] = {
                "passed": self.passed,
                "evaluation_version": 5,
                "repeats": self.repeats,
                "distinct_scenario_count": len(self.scenario_stats()),
                "stable_passed_count": sum(stat["pass_rate"] == 1.0 for stat in self.scenario_stats()),
                "scenario_count": total,
                "passed_count": sum(item.passed for item in self.evaluations),
                "rating_counts": rating_counts(self.evaluations),
                "tool_rating_counts": tool_rating_counts(self.evaluations),
                "tool_call_count": sum(item.tool_calls for item in self.evaluations),
                "min_tool_call_count": sum(item.min_tool_calls for item in self.evaluations),
                "decision_accuracy": round(correct / total, 4) if total else 0.0,
                "collision_count": collisions,
                "safety_compliant_count": sum(item.result.safety_compliant for item in self.evaluations),
                "solution_complete_count": sum(item.result.solution_complete for item in self.evaluations),
                "mission_completed_count": sum(item.result.mission_status == "completed" for item in self.evaluations),
                "safe_stop_count": sum(item.result.mission_status == "safe_stopped" for item in self.evaluations),
                "agent_error_count": agent_errors,
                "average_tool_calls": round(sum(tool_calls) / total, 2) if total else 0.0,
                "average_decision_attempts": (
                    round(sum(decision_attempts) / total, 2) if total else 0.0
                ),
                "average_model_latency_s": (
                    round(sum(model_latencies) / total, 3) if total else 0.0
                ),
                "average_model_tokens": (
                    round(sum(model_tokens) / total, 2) if total else 0.0
                ),
                "no_intervention_scenarios": len(calm),
                "intervention_scenarios": len(stuck),
                "false_alarm_count": false_alarms,
                "missed_intervention_count": missed_interventions,
                "false_alarm_rate": round(false_alarms / len(calm), 4) if calm else None,
                "missed_intervention_rate": round(missed_interventions / len(stuck), 4) if stuck else None,
                "false_maneuver_count": false_maneuvers,
                "missed_maneuver_count": missed_maneuvers,
                "total_cost_eur": round(sum(float(cost["total_eur"]) for cost in costs), 2),
                "remote_assistance_calls": remote_calls,
                "remote_assistance_episodes": remote_episodes,
                "within_budget_count": within_budget,
                "sequence_scenario_count": len(sequenced),
                "sequence_complete_count": sum(item.result.solution_complete for item in sequenced),
                "average_sequence_score": (
                    round(sum(sequence_scores) / len(sequence_scores), 3) if sequence_scores else None
                ),
                "personal_injury_count": sum(
                    "personal_injury" in cost["items_eur"] for cost in costs  # type: ignore[operator]
                ),
                "agent_name": self.evaluations[0].result.agent_name if self.evaluations else None,
                "execution_policy": "agent_responsible",
                "hit_and_run_count": sum(
                    cost.get("incident_handling") == "hit_and_run" for cost in costs  # type: ignore[union-attr]
                ),
                "average_customer_satisfaction": (
                    round(sum(satisfaction) / len(satisfaction), 3) if satisfaction else None
                ),
                "successful_release_rate": (
                    round(successful_releases / len(expected_liberations), 4)
                    if expected_liberations
                    else None
                ),
                "expected_abort_count": len(expected_aborts),
                "successful_abort_count": successful_aborts,
                "stabilized_abort_count": stabilized_aborts,
            }
        summary["grading"] = grade_summary(summary)
        cost_items: dict[str, float] = {}
        for cost in costs:
            for name, amount in cost["items_eur"].items():  # type: ignore[union-attr]
                cost_items[name] = round(cost_items.get(name, 0.0) + float(amount), 2)
        return {
            "summary": summary,
            "cost_items": cost_items,
            "scenario_stats": self.scenario_stats(),
            "releases": release_summary(self.evaluations),
            "hide_reference": self.hide_reference,
            "scenarios": [self._scenario_entry(item) for item in self.evaluations],
        }

    def write(self, output_dir: Path) -> dict[str, object]:
        """Schreibt alle Berichte und liefert den Fortschritt gegenueber dem vorigen Lauf."""
        output_dir.mkdir(parents=True, exist_ok=True)
        previous = read_previous(output_dir)
        for item in self.evaluations:
            target = output_dir / "scenarios" / item.result.scenario_id
            if self.repeats > 1:
                target = target / f"run-{item.run_index}"
            item.result.write_json(target / "result.json", hide_reference=self.hide_reference)
            (target / "report.md").write_text(
                scenario_markdown(item, hide_reference=self.hide_reference), encoding="utf8"
            )
        payload = self.to_dict()
        progress = compare(previous, payload)
        progress["history"] = append_history(output_dir, history_entry(payload))
        payload["progress"] = progress
        (output_dir / "batch-result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf8",
        )
        summary = payload["summary"]
        assert isinstance(summary, dict)
        grading = summary["grading"]
        assert isinstance(grading, dict)
        lines = [
            "# AutonomyRecoverySim Batch-Auswertung",
            "",
            f"Gesamtergebnis: **{'bestanden' if self.passed else 'nicht bestanden'}**",
            f"Einstufung: **{grading['grade']}** (Leistung {grading['performance_grade']}, "
            f"{grading['unsafe_cases']} unsichere Fälle) · wirtschaftlich: "
            f"{'ja' if grading['economic'] else 'nein'} · Kunden zufrieden: "
            f"{'ja' if grading['customers_satisfied'] else 'nein'}",
            "",
            "Bewertung: positiv {positive} · neutral {neutral} · negativ {negative}".format(**summary["rating_counts"]),  # type: ignore[arg-type]
            "Werkzeugnutzung: effizient {efficient} · angemessen {adequate} · verschwenderisch {wasteful}".format(**summary["tool_rating_counts"])  # type: ignore[arg-type]
            + f" ({summary['tool_call_count']} Abfragen, Minimum {summary['min_tool_call_count']})",
            "",
            *summary_lines(progress),
            "",
            f"- Laeufe je Szenario: {summary['repeats']}",
            f"- Im Budget: {summary['within_budget_count']}/{summary['scenario_count']}",
            (
                f"- Loesungswege vollstaendig: {summary['sequence_complete_count']}/{summary['sequence_scenario_count']}"
                f" (Teilpunkte im Mittel {float(summary['average_sequence_score']):.0%})"
                if summary["sequence_scenario_count"]
                else "- Loesungswege: keine mehrstufigen Szenarien in dieser Menge"
            ),
            f"- Episoden mit Remote Assistance: {summary['remote_assistance_episodes']}",
            f"- Szenarien: {summary['passed_count']}/{summary['scenario_count']} bestanden",
            f"- Entscheidungsgenauigkeit: {float(summary['decision_accuracy']):.1%}",
            f"- Kollisionen: {summary['collision_count']}",
            f"- Ungueltige Agentenausgaben: {summary['agent_error_count']}",
            f"- Werkzeugaufrufe pro Szenario: {float(summary['average_tool_calls']):.2f}",
            f"- Entscheidungsversuche pro Szenario: {float(summary['average_decision_attempts']):.2f}",
            f"- Modelllatenz pro Szenario: {float(summary['average_model_latency_s']):.2f} s",
            f"- Modelltokens pro Szenario: {float(summary['average_model_tokens']):.0f}",
            *(
                [f"- In allen {self.repeats} Wiederholungen bestanden: {summary['stable_passed_count']}/{summary['distinct_scenario_count']} Szenarien"]
                if self.repeats > 1
                else []
            ),
            f"- Fehlalarme (Eingriff, obwohl Warten reicht): {summary['false_alarm_count']}/{summary['no_intervention_scenarios']}",
            f"- Verpasste Eingriffe (gewartet, obwohl Eingriff noetig): {summary['missed_intervention_count']}/{summary['intervention_scenarios']}",
            f"- Unerwartete Manoever: {summary['false_maneuver_count']}",
            f"- Verpasste Manoever: {summary['missed_maneuver_count']}",
            f"- Gesamtkosten: {float(summary['total_cost_eur']):.2f} EUR",
            f"- Remote-Assistance-Anrufe: {summary['remote_assistance_calls']}",
            f"- Personenschaeden: {summary['personal_injury_count']}",
            f"- Unfallflucht: {summary['hit_and_run_count']}",
            "- Kundenzufriedenheit: "
            + (
                f"{float(summary['average_customer_satisfaction']):.2f}"
                if summary["average_customer_satisfaction"] is not None
                else "n/a"
            ),
            f"- Erwartete Manoeverabbrueche: {summary['successful_abort_count']}/{summary['expected_abort_count']}",
            f"- Sicher stabilisierte Abbrueche: {summary['stabilized_abort_count']}/{summary['expected_abort_count']}",
            "",
            "## Nach Freigabestufe",
            "",
            "| Freigabe | Bestanden | Kosten (EUR) | Kollisionen |",
            "|---|---:|---:|---:|",
            *[
                f"| {row['release']} | {row['passed_count']}/{row['scenario_count']} "
                f"| {float(row['total_cost_eur']):.2f} | {row['collision_count']} |"  # type: ignore[arg-type]
                for row in payload["releases"]  # type: ignore[union-attr]
            ],
            "",
            "## Szenarien",
            "",
            "Der Befund nennt den wichtigsten Grund zuerst. Die Detailseite je Szenario zeigt",
            "Befehlsfolge und Agent-Trace.",
            "",
            "| Freigabe | Szenario | Erster Befehl | Ausgang | Sicherheit bestaetigt | Loesungsweg erfuellt | Mission | Kosten / Budget (EUR) | Ergebnis | Befund |",
            "|---|---|---|---|---|---|---|---:|---|---|",
        ]
        for item, entry in zip(self.evaluations, payload["scenarios"]):  # type: ignore[arg-type]
            result = item.result
            run = f" (Lauf {item.run_index})" if self.repeats > 1 else ""
            lines.append(
                f"| {entry['release'] or '–'} | [{result.scenario_id}{run}]({entry['details']}) | {result.command} "
                f"| {result.outcome} | {'ja' if result.safety_compliant else 'nein'} "
                f"| {'ja' if result.solution_complete else 'nein'} | {MISSION_LABELS[result.mission_status]} "
                f"| {float(result.costs['total_eur']):.2f} / {result.scenario.budget_eur:.0f} "
                f"| {'bestanden' if item.passed else 'fehlgeschlagen'} "
                f"| {entry['diagnosis'][0]} |"
            )
        if self.repeats > 1:
            lines += [
                "",
                f"## Streuung ueber {self.repeats} Laeufe",
                "",
                "| Szenario | Bestehensquote | Kosten Mittel (EUR) | Kosten Std. (EUR) | Befehle |",
                "|---|---:|---:|---:|---|",
            ]
            for stat in self.scenario_stats():
                lines.append(
                    f"| {stat['scenario_id']} | {float(stat['pass_rate']):.0%} "  # type: ignore[arg-type]
                    f"| {stat['cost_mean_eur']} | {stat['cost_std_eur']} "
                    f"| {', '.join(stat['commands'])} |"  # type: ignore[arg-type]
                )
        (output_dir / "batch-report.md").write_text("\n".join(lines) + "\n", encoding="utf8")
        (output_dir / "batch-report.html").write_text(render_html_report(payload), encoding="utf8")
        return progress


def discover_scenarios(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix in {".txt", ".list"}:
            paths = []
            for raw_line in source.read_text(encoding="utf8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                path = (source.parent / line).resolve()
                if not path.is_file():
                    raise ValueError(f"Szenario aus {source} fehlt: {line}")
                paths.append(path)
            return paths
        return [source]
    return sorted(path for path in source.glob("*.json") if path.is_file())


class BatchAborted(RuntimeError):
    """Der Batchlauf kann nicht sinnvoll weiterlaufen, z. B. weil das Modellkontingent erschoepft ist."""


def run_batch(
    source: Path,
    *,
    agent: AgentPolicy | None = None,
    agent_name: str | None = None,
    repeats: int = 1,
    hide_reference: bool = False,
    skip: frozenset[Path] = frozenset(),
) -> BatchResult:
    if repeats < 1:
        raise ValueError("repeats muss mindestens 1 sein")
    paths = [path for path in discover_scenarios(source) if path not in skip]
    if not paths:
        raise ValueError(f"Keine Szenarien unter {source} gefunden")
    evaluations = []
    for path, run_index in ((path, index) for path in paths for index in range(1, repeats + 1)):
        scenario = load_scenario(path)
        result = run_scenario(scenario, agent=agent, agent_name=agent_name)
        if result.agent_error and QUOTA_EXHAUSTED in result.agent_error:
            # Ohne Modell waeren alle weiteren Faelle wertlos: lieber abbrechen und spaeter neu starten.
            raise BatchAborted(result.agent_error.split(": ", 1)[-1] + f" (abgebrochen bei {path.stem})")
        evaluations.append(ScenarioEvaluation.from_result(result, run_index=run_index))
    return BatchResult(tuple(evaluations), repeats=repeats, hide_reference=hide_reference)
