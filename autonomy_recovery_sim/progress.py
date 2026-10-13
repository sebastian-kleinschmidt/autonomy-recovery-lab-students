"""Fortschritt zwischen Batchlaeufen: Hat sich der Agent verbessert?

Jeder Batchlauf vergleicht sich mit dem vorigen Lauf im selben Ausgabeordner und fuehrt
einen kurzen Verlauf (``verlauf.jsonl``). So sehen die Teams nach jeder Aenderung ohne
eigenes Zutun, wie viele Faelle sie gewonnen oder verloren haben und welche.

Verglichen wird nur, was vergleichbar ist: dieselbe Szenariomenge mit denselben Einstellungen
(Fahrstack, Wahrnehmungsmodell, Ausfuehrungsverhalten). Sonst nennt der Bericht den Unterschied, statt
Zahlen gegeneinander zu stellen, die nichts miteinander zu tun haben.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_FILE = "verlauf.jsonl"
HISTORY_SHOWN = 10


def scenario_pass_rates(payload: dict[str, Any]) -> dict[str, float]:
    """Bestehensquote je Szenario (bei Wiederholungen der Anteil bestandener Laeufe)."""
    runs: dict[str, list[bool]] = {}
    for item in payload.get("scenarios", []):
        runs.setdefault(str(item["scenario_id"]), []).append(bool(item["passed"]))
    return {scenario_id: sum(values) / len(values) for scenario_id, values in runs.items()}


def settings(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary", {})
    return {
        "agent": summary.get("agent_name"),
        "execution_policy": summary.get("execution_policy"),
        "evaluation_version": summary.get("evaluation_version", 1),
    }


def history_entry(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload["summary"]
    rates = scenario_pass_rates(payload)
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **settings(payload),
        "passed": summary["passed_count"],
        "scenarios": summary["scenario_count"],
        "collisions": summary["collision_count"],
        "hit_and_run": summary.get("hit_and_run_count", 0),
        "cost_eur": summary["total_cost_eur"],
        "rating": summary["grading"]["grade"],
        "failed": sorted(scenario_id for scenario_id, rate in rates.items() if rate < 1.0),
    }


def compare(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Unterschied zum vorigen Lauf; ``comparable`` sagt, ob die Zahlen vergleichbar sind."""
    if previous is None:
        return {"available": False, "reason": "erster Lauf in diesem Ausgabeordner"}
    before, after = scenario_pass_rates(previous), scenario_pass_rates(current)
    if set(before) != set(after):
        return {"available": True, "comparable": False, "reason": "andere Szenariomenge als im vorigen Lauf"}
    if settings(previous)["evaluation_version"] != settings(current)["evaluation_version"]:
        return {"available": True, "comparable": False, "reason": "andere Bewertungsversion als im vorigen Lauf"}
    changed = {key for key in ("execution_policy",) if settings(previous)[key] != settings(current)[key]}
    if changed:
        return {
            "available": True,
            "comparable": False,
            "reason": "andere Einstellungen als im vorigen Lauf: " + ", ".join(sorted(changed)),
        }
    old, new = previous["summary"], current["summary"]
    return {
        "available": True,
        "comparable": True,
        "agent_changed": settings(previous)["agent"] != settings(current)["agent"],
        "passed_before": old["passed_count"],
        "passed_now": new["passed_count"],
        "collisions_before": old["collision_count"],
        "collisions_now": new["collision_count"],
        "cost_before_eur": old["total_cost_eur"],
        "cost_now_eur": new["total_cost_eur"],
        "positive_before": (old.get("rating_counts") or {}).get("positive", old["passed_count"]),
        "positive_now": (new.get("rating_counts") or {}).get("positive", new["passed_count"]),
        "negative_before": (old.get("rating_counts") or {}).get("negative", 0),
        "negative_now": (new.get("rating_counts") or {}).get("negative", 0),
        "rating_before": old["grading"]["grade"],
        "rating_now": new["grading"]["grade"],
        "newly_passed": sorted(key for key in after if after[key] > before[key]),
        "newly_failed": sorted(key for key in after if after[key] < before[key]),
    }


def read_previous(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "batch-result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return None


def append_history(output_dir: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Haengt den Lauf an den Verlauf an und liefert die letzten Eintraege."""
    path = output_dir / HISTORY_FILE
    entries: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.append(entry)
    with path.open("a", encoding="utf8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entries[-HISTORY_SHOWN:]


def short_progress(progress: dict[str, Any]) -> str:
    """Eine Zeile fuer die Kopfzeile: was sich bei positiv und negativ getan hat."""
    if not progress.get("available") or not progress.get("comparable"):
        return ""
    positive = progress["positive_now"] - progress["positive_before"]
    negative = progress["negative_now"] - progress["negative_before"]
    return f"{positive:+d} positiv, {negative:+d} negativ"


def summary_lines(progress: dict[str, Any]) -> list[str]:
    """Kurzfassung fuer Terminal und Markdown-Bericht."""
    if not progress.get("available"):
        return [f"Fortschritt: {progress.get('reason', 'kein Vergleich')}"]
    if not progress.get("comparable"):
        return [f"Fortschritt: kein Vergleich ({progress['reason']})"]
    delta = progress["passed_now"] - progress["passed_before"]
    lines = [
        "Seit dem letzten Lauf: " + short_progress(progress),
        f"Fortschritt: {progress['passed_before']} -> {progress['passed_now']} bestanden ({delta:+d}), "
        f"Kollisionen {progress['collisions_before']} -> {progress['collisions_now']}, "
        f"Kosten {progress['cost_before_eur']:.0f} -> {progress['cost_now_eur']:.0f} EUR, "
        f"Einstufung {progress['rating_before']} -> {progress['rating_now']}"
    ]
    if progress["newly_passed"]:
        lines.append("  neu bestanden: " + ", ".join(progress["newly_passed"]))
    if progress["newly_failed"]:
        lines.append("  neu gescheitert: " + ", ".join(progress["newly_failed"]))
    if not progress["newly_passed"] and not progress["newly_failed"]:
        lines.append("  dieselben Faelle bestanden wie zuvor")
    return lines
