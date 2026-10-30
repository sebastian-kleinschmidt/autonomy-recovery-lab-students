from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent_loader import load_agent
from .batch import BatchAborted, discover_scenarios, run_batch
from .render import write_svg
from .scenario import load_scenario
from .simulation import run_scenario


def _single_main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description="Deterministischer 2D-Simulator fuer Agentic Autonomy Recovery")
    parser.add_argument("scenario", type=Path, help="Szenario als JSON")
    parser.add_argument("--output", type=Path, default=Path("artifacts/autonomy-recovery-sim"))
    parser.add_argument(
        "--agent",
        default="none",
        help="none, llm oder python_modul:funktion (Standard: none)",
    )
    args = parser.parse_args(argv)
    try:
        loaded_agent = load_agent(args.agent)
    except ValueError as exc:
        parser.error(str(exc))

    scenario = load_scenario(args.scenario)
    result = run_scenario(
        scenario,
        agent=loaded_agent.policy,
        agent_name=loaded_agent.name,
    )
    target = args.output / result.scenario_id
    result.write_json(target / "result.json")
    write_svg(result, target / "replay.svg")

    print(f"Szenario:   {result.scenario_id}")
    print(f"Agent:      {result.agent_name} ({result.agent_status})")
    if result.agent_error:
        print(f"Agentfehler:{result.agent_error}")
    print(f"Befehl:     {result.command} ({'korrekt' if result.command_correct else 'falsch'})")
    print(f"Ausgang:    {result.outcome}")
    print(f"Kosten:     {float(result.costs['total_eur']):.2f} EUR")
    print(f"Kollision:  {'ja' if result.collision else 'nein'}")
    print(f"Ergebnis:   {target / 'result.json'}")
    print(f"Darstellung:{target / 'replay.svg'}")
    return 0 if result.command_correct and not result.collision else 1


# Je Ausgabe alle bis dahin freigegebenen Faelle (kumulativ).
RELEASE_SETS = {release: f"{release}.txt" for release in ("bootcamp", "sprint1", "sprint2", "sprint3", "sprint4", "final")}


def _earlier_cases(package: Path, release: str) -> frozenset[Path]:
    """Faelle der vorigen Ausgabe; --nur-neu laesst sie weg."""
    order = list(RELEASE_SETS)
    if order.index(release) == 0:
        return frozenset()
    return frozenset(discover_scenarios(package / "scenario_sets" / RELEASE_SETS[order[order.index(release) - 1]]))


def _batch_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Alle AutonomyRecoverySim-Szenarien reproduzierbar auswerten")
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Szenario, Ordner oder Set-Datei (Standard: alle freigegebenen Übungsfälle einschließlich Familien)",
    )
    parser.add_argument(
        "--release",
        choices=[*RELEASE_SETS, "all"],
        help="statt source: die bis zu dieser Phase freigegebenen Szenarien (all = alle vorhandenen Übungsfälle einschließlich Familien)",
    )
    parser.add_argument(
        "--nur-neu",
        action="store_true",
        help="mit --release: nur die in dieser Ausgabe neuen Faelle, ohne die frueheren",
    )
    parser.add_argument(
        "--hide-reference",
        action="store_true",
        help="Referenzbefehle und -begruendungen nicht in die Berichte schreiben (Versuch ohne Referenzanzeige)",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/autonomy-recovery-sim/batch"))
    parser.add_argument(
        "--agent",
        default="none",
        help="none, llm oder python_modul:funktion (Standard: none)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Laeufe je Szenario, fuer nichtdeterministische LLM-Agenten (Standard: 1)",
    )
    args = parser.parse_args(argv)
    if args.source is not None and args.release is not None:
        parser.error("source und --release nicht gleichzeitig angeben")
    if args.nur_neu and args.release not in RELEASE_SETS:
        parser.error("--nur-neu braucht --release bootcamp oder sprint1 bis sprint4")
    package = Path(__file__).resolve().parent
    if args.release in RELEASE_SETS:
        source = package / "scenario_sets" / RELEASE_SETS[args.release]
        if not source.is_file():
            parser.error(f"{args.release} ist in diesem Checkout noch nicht freigegeben. Zuerst die neue Sprintausgabe holen.")
    elif args.source is not None:
        source = args.source
    else:
        source = next(
            (package / "scenario_sets" / filename for filename in reversed(tuple(RELEASE_SETS.values()))
             if (package / "scenario_sets" / filename).is_file()),
            package / "scenarios",
        )
    try:
        loaded_agent = load_agent(args.agent)
    except ValueError as exc:
        parser.error(str(exc))
    if args.repeats < 1:
        parser.error("--repeats muss mindestens 1 sein")
    try:
        result = run_batch(
            source,
            agent=loaded_agent.policy,
            agent_name=loaded_agent.name,
            repeats=args.repeats,
            hide_reference=args.hide_reference,
            skip=_earlier_cases(package, args.release) if args.nur_neu else frozenset(),
        )
    except BatchAborted as exc:
        print(f"Batchlauf abgebrochen: {exc}")
        return 2
    progress = result.write(args.output)
    summary = result.to_dict()["summary"]
    assert isinstance(summary, dict)
    print(f"Szenarien:  {summary['passed_count']}/{summary['scenario_count']} bestanden")
    ratings = summary["rating_counts"]
    assert isinstance(ratings, dict)
    green, yellow, red, plain = ("\033[32m", "\033[33m", "\033[31m", "\033[0m") if sys.stdout.isatty() else ("", "", "", "")
    print(f"Bewertung:  {green}positiv {ratings['positive']}{plain} · {yellow}neutral {ratings['neutral']}{plain} · "
          f"{red}negativ {ratings['negative']}{plain}")
    tools = summary["tool_rating_counts"]
    assert isinstance(tools, dict)
    print(f"Werkzeuge:  {green}effizient {tools['efficient']}{plain} · {yellow}angemessen {tools['adequate']}{plain} · "
          f"{red}verschwenderisch {tools['wasteful']}{plain} ({summary['tool_call_count']} Abfragen, "
          f"Minimum {summary['min_tool_call_count']})")
    print(f"Agent:      {loaded_agent.name}")
    print(f"Genauigkeit:{float(summary['decision_accuracy']):.1%}")
    print(f"Kollisionen:{summary['collision_count']}")
    grading = summary["grading"]
    assert isinstance(grading, dict)
    print(f"Einstufung: {grading['grade']} (Leistung {grading['performance_grade']}, "
          f"{grading['unsafe_cases']} unsichere Fälle, Kosten {float(summary['total_cost_eur']):.2f} EUR, "
          f"im Budget {summary['within_budget_count']}/{summary['scenario_count']})")
    for row in result.to_dict()["releases"]:  # type: ignore[union-attr]
        print(f"  {row['release']:<12} {row['passed_count']}/{row['scenario_count']} bestanden")
    from .progress import summary_lines

    for line in summary_lines(progress):
        print(line)
    print(f"Bericht:    {args.output / 'batch-report.md'}")
    print(f"Website:    {args.output / 'batch-report.html'}")
    print(f"Details:    {args.output / 'scenarios'}/<szenario>/report.md")
    return 0 if result.passed else 1


def _validate_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="AutonomyRecoverySim-Szenarien validieren")
    parser.add_argument("source", type=Path, nargs="?", default=Path("autonomy_recovery_sim/scenarios"))
    args = parser.parse_args(argv)
    paths = [args.source] if args.source.is_file() else sorted(args.source.glob("*.json"))
    if not paths:
        parser.error(f"Keine Szenarien unter {args.source} gefunden")
    for path in paths:
        scenario = load_scenario(path)
        print(f"OK {scenario.scenario_id}: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else None
    if args is None:
        import sys

        args = sys.argv[1:]
    if args and args[0] == "batch":
        return _batch_main(args[1:])
    if args and args[0] == "validate":
        return _validate_main(args[1:])
    return _single_main(args)
