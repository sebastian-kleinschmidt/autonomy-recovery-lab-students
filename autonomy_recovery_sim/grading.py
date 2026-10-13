"""Einstufung A bis D fuer eine Batch-Auswertung.

Das Labor ist unbenotet; die Einstufung ist Rueckmeldung, keine Note. Die Leistung folgt aus
Bestehens- und Budgetquote; Leitstellenanteil und Kundenzufriedenheit stehen daneben als
Auskunft ("wirtschaftlich", "Kunden zufrieden"), weil manche Faelle die Leitstelle oder das
Aussteigen der Fahrgaeste gerade verlangen.
Sicherheit deckelt die Leistung: Mit unsicheren (negativen) Faellen gibt es hoechstens C,
ab einem Anteil von mehr als 15 % D. So bleibt sichtbar, wie gut der Agent sonst ist, und
eine einzelne Kollision macht nicht alles zunichte.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GradeThresholds:
    a_pass_rate: float = 0.90
    a_budget_rate: float = 0.90
    b_pass_rate: float = 0.75
    b_budget_rate: float = 0.75
    b_satisfaction: float = 0.50
    c_pass_rate: float = 0.50
    # Anteil negativer Faelle, ab dem es unabhaengig von der Leistung D gibt.
    d_unsafe_rate: float = 0.15


DEFAULT_THRESHOLDS = GradeThresholds()


def grade_summary(summary: dict[str, object], thresholds: GradeThresholds = DEFAULT_THRESHOLDS) -> dict[str, object]:
    total = int(summary["scenario_count"])  # type: ignore[arg-type]
    if total == 0:
        return {"grade": "D", "reasons": ["Keine Szenarien ausgewertet"], "economic": False, "customers_satisfied": False}
    pass_rate = int(summary["passed_count"]) / total  # type: ignore[arg-type]
    budget_rate = int(summary["within_budget_count"]) / total  # type: ignore[arg-type]
    ra_rate = int(summary["remote_assistance_episodes"]) / total  # type: ignore[arg-type]
    satisfaction = summary["average_customer_satisfaction"]
    satisfaction_value = None if satisfaction is None else float(satisfaction)  # type: ignore[arg-type]
    # Negative Faelle (Kollision, Regel- oder Sicherheitsverstoss, Personenschaden, Unfallflucht).
    ratings = summary.get("rating_counts") or {}
    unsafe = int(ratings.get("negative", 0)) if ratings else (  # type: ignore[union-attr]
        int(summary["collision_count"])  # type: ignore[arg-type]
        + int(summary["personal_injury_count"])  # type: ignore[arg-type]
        + int(summary.get("hit_and_run_count", 0))  # type: ignore[arg-type]
    )
    unsafe_rate = unsafe / total

    def satisfied(minimum: float) -> bool:
        return satisfaction_value is None or satisfaction_value >= minimum

    criteria = {
        "pass_rate": round(pass_rate, 3),
        "budget_rate": round(budget_rate, 3),
        "remote_assistance_rate": round(ra_rate, 3),
        "average_customer_satisfaction": satisfaction_value,
        "safety_incidents": unsafe,
        "unsafe_rate": round(unsafe_rate, 3),
    }
    reasons: list[str] = []
    # Leitstellenanteil und Zufriedenheit zaehlen nicht zur Leistung: Ob die Leitstelle noetig war
    # und ob Fahrgaeste aussteigen mussten, prueft schon das Bestehen (Loesungsweg und Budget).
    if pass_rate >= thresholds.a_pass_rate and budget_rate >= thresholds.a_budget_rate:
        performance = "A"
    elif pass_rate >= thresholds.b_pass_rate and budget_rate >= thresholds.b_budget_rate:
        performance = "B"
        reasons.append(f"Fuer A fehlen Bestehens- oder Budgetquote ({thresholds.a_pass_rate:.0%})")
    elif pass_rate >= thresholds.c_pass_rate:
        performance = "C"
        reasons.append(f"Fuer B fehlen Bestehens- oder Budgetquote ({thresholds.b_pass_rate:.0%})")
    else:
        performance = "D"
        reasons.append(f"Weniger als {thresholds.c_pass_rate:.0%} der Szenarien bestanden")
    grade = performance
    if unsafe and unsafe_rate > thresholds.d_unsafe_rate:
        grade = "D"
        reasons.insert(0, f"{unsafe} unsichere Faelle (mehr als {thresholds.d_unsafe_rate:.0%}): Einstufung D, Leistung waere {performance}")
    elif unsafe and performance in {"A", "B"}:
        grade = "C"
        reasons.insert(0, f"Leistung {performance}, wegen {unsafe} unsicherer Faelle hoechstens C")
    elif unsafe:
        reasons.insert(0, f"{unsafe} unsichere Faelle")
    return {
        "grade": grade,
        "performance_grade": performance,
        "unsafe_cases": unsafe,
        "reasons": reasons,
        "economic": budget_rate >= thresholds.b_budget_rate and not unsafe,
        "customers_satisfied": satisfied(thresholds.b_satisfaction) and not unsafe,
        "criteria": criteria,
        "thresholds": asdict(thresholds),
    }
