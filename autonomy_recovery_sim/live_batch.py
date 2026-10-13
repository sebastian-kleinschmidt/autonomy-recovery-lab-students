"""Szenario-Sets und Laufverwaltung fuer Batchfahrten in der Live-Oberflaeche."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from .batch import BatchResult, ScenarioEvaluation, discover_scenarios, rating_counts
from .scenario import Scenario, load_scenario
from .simulation import result_from_engine
from .engine import SimulationEngine

def load_batch_scenarios(source: Path) -> tuple[Scenario, ...]:
    paths = discover_scenarios(source)
    if not paths:
        raise ValueError(f"Keine Szenarien unter {source} gefunden")
    # Vollstaendig laden, bevor ein bestehender Lauf ersetzt wird.
    return tuple(load_scenario(path) for path in paths)


class LiveBatch:
    def __init__(self, scenarios: tuple[Scenario, ...], name: str, output_root: Path, *, hide_reference: bool = False):
        if not scenarios:
            raise ValueError("Ein Batch muss mindestens ein Szenario enthalten")
        self.scenarios = scenarios
        self.name = name
        self.output_root = output_root.resolve()
        self.hide_reference = hide_reference
        self.index = 0
        self.evaluations: list[ScenarioEvaluation] = []
        self.completed = False
        self.report_ready = False
        self.report_error: str | None = None
        self.summary: dict[str, object] | None = None
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        self.output_dir = self.output_root / self.run_id
        self.repeats = max(Counter(s.scenario_id for s in scenarios).values())

    def finish(self, engine: SimulationEngine) -> bool:
        """Speichert genau ein Ergebnis; True bedeutet, dass ein Nachfolger bereitsteht."""
        if self.completed or len(self.evaluations) > self.index or not engine.done:
            return False
        result = result_from_engine(engine)
        run_index = 1 + sum(e.result.scenario_id == result.scenario_id for e in self.evaluations)
        self.evaluations.append(ScenarioEvaluation.from_result(result, run_index=run_index))
        if self.index + 1 < len(self.scenarios):
            self.index += 1
            return True
        self.completed = True
        self.write_report()
        return False

    def write_report(self) -> None:
        if not self.completed:
            raise ValueError("Der Batch ist noch nicht abgeschlossen")
        result = BatchResult(tuple(self.evaluations), repeats=self.repeats, hide_reference=self.hide_reference)
        self.summary = result.to_dict()["summary"]  # type: ignore[assignment]
        try:
            result.write(self.output_dir)
        except OSError as error:
            self.report_error = f"Report konnte nicht gespeichert werden: {error}"
            self.report_ready = False
        else:
            self.report_error = None
            self.report_ready = True

    def snapshot(self) -> dict[str, object]:
        last = self.evaluations[-1] if self.evaluations else None
        base = f"/reports/{self.run_id}"
        return {
            "name": self.name,
            "index": self.index + 1,
            "total": len(self.scenarios),
            "completed_count": len(self.evaluations),
            "completed": self.completed,
            "report_ready": self.report_ready,
            "report_error": self.report_error,
            "report_url": f"{base}/batch-report.html" if self.report_ready else None,
            "data_url": f"{base}/batch-result.json" if self.report_ready else None,
            "summary": self.summary,
            "ratings": rating_counts(self.evaluations),
            "last_result": {"scenario_id": last.result.scenario_id, "outcome": last.result.outcome, "passed": last.passed, "rating": last.rating, "cost_eur": last.result.costs["total_eur"]} if last else None,
        }


def report_path(request_path: str, reports: dict[str, Path]) -> Path | None:
    parts = unquote(request_path).removeprefix("/reports/").split("/", 1)
    if len(parts) != 2 or parts[0] not in reports:
        return None
    root = reports[parts[0]].resolve()
    candidate = (root / parts[1]).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate
