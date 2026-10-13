"""Vergleicht zwei Batch-Auswertungen (Tutorial 6).

    python3 -m autonomy_recovery_sim.tutorials.vergleich artifacts/experiments/prompt-a artifacts/experiments/prompt-b

Gelesen wird je Verzeichnis die unveraenderte ``batch-result.json``. Die Ausgabe zeigt die
Kennzahlen nebeneinander und die Szenarien, in denen sich das Ergebnis unterscheidet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

METRICS = (
    ("Einstufung", lambda s: s["grading"]["grade"]),
    ("Bestanden", lambda s: f"{s['passed_count']}/{s['scenario_count']}"),
    ("Im Budget", lambda s: f"{s['within_budget_count']}/{s['scenario_count']}"),
    ("Loesungswege", lambda s: f"{s['sequence_complete_count']}/{s['sequence_scenario_count']}" if s.get("sequence_scenario_count") else "-"),
    ("Kollisionen", lambda s: s["collision_count"]),
    ("Personenschaeden", lambda s: s["personal_injury_count"]),
    ("Fehlalarme", lambda s: f"{s['false_alarm_count']}/{s['no_intervention_scenarios']}"),
    ("Verpasste Eingriffe", lambda s: f"{s['missed_intervention_count']}/{s['intervention_scenarios']}"),
    ("Gesamtkosten (EUR)", lambda s: f"{s['total_cost_eur']:.2f}"),
    ("Remote-Assistance-Anrufe", lambda s: s["remote_assistance_calls"]),
    ("Kundenzufriedenheit", lambda s: "-" if s["average_customer_satisfaction"] is None else f"{s['average_customer_satisfaction']:.2f}"),
    ("Werkzeugaufrufe je Lauf", lambda s: f"{s['average_tool_calls']:.1f}"),
    ("Modelltokens je Lauf", lambda s: f"{s['average_model_tokens']:.0f}"),
    ("Modelllatenz je Lauf (s)", lambda s: f"{s['average_model_latency_s']:.1f}"),
)


def load(directory: Path) -> dict:
    path = directory / "batch-result.json"
    if not path.is_file():
        raise SystemExit(f"{path} fehlt. Zuerst 'python3 -m autonomy_recovery_sim batch ... --output {directory}' ausfuehren.")
    return json.loads(path.read_text(encoding="utf8"))


def pass_rates(payload: dict) -> dict[str, float]:
    return {stat["scenario_id"]: stat["pass_rate"] for stat in payload["scenario_stats"]}


def compare(first: dict, second: dict, names: tuple[str, str]) -> str:
    width = max(len(label) for label, _ in METRICS)
    lines = [f"{'':<{width}}  {names[0]:>14}  {names[1]:>14}", "-" * (width + 32)]
    for label, getter in METRICS:
        lines.append(f"{label:<{width}}  {str(getter(first['summary'])):>14}  {str(getter(second['summary'])):>14}")
    a, b = pass_rates(first), pass_rates(second)
    differing = sorted(name for name in a.keys() & b.keys() if a[name] != b[name])
    lines.append("")
    if not differing:
        lines.append("Kein Szenario unterscheidet sich in der Bestehensquote.")
    else:
        lines.append(f"Szenarien mit unterschiedlicher Bestehensquote ({len(differing)}):")
        for name in differing:
            lines.append(f"  {name:<40} {a[name]:>5.0%} -> {b[name]:>5.0%}")
    only = sorted(a.keys() ^ b.keys())
    if only:
        lines.append(f"Nur in einer Auswertung vorhanden: {', '.join(only)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    first, second = Path(args[0]), Path(args[1])
    print(compare(load(first), load(second), (first.name, second.name)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
