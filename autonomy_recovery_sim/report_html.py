"""Eigenstaendige HTML-Auswertung einer Batch-Auswertung (``batch-report.html``).

Beantwortet die Frage der Abteilung: Arbeitet die Remote Assistance mit ihrem
Agenten wirtschaftlich, und sind die Kunden zufrieden? Keine externen Ressourcen,
die Datei laesst sich offline oeffnen und archivieren.
"""

from __future__ import annotations

from html import escape

COST_LABELS = {
    "personal_injury": "Personenschaden",
    "material_damage": "Sachschaden",
    "remote_assistance": "Remote Assistance",
    "standstill": "Standzeit",
    "detour": "Umweg / Rueckfahrt",
    "recovery": "Bergung",
    "blocking_traffic": "Verkehrsbehinderung",
    "passenger_refund": "Fahrgast-Erstattung",
    "rejected_commands": "Abgelehnte Befehle",
    "language_model": "Sprachmodell",
}

RATING_LABELS = {"positive": "positiv", "neutral": "neutral", "negative": "negativ"}


TOOL_LABELS = {"efficient": "effizient", "adequate": "angemessen", "wasteful": "verschwenderisch"}
TOOL_CLASSES = {"efficient": "positive", "adequate": "neutral", "wasteful": "negative"}


def _ratings_strip(summary: dict) -> str:
    """Bewertung und Werkzeugnutzung der Szenarien als farbige Leisten ganz oben."""
    counts = summary.get("rating_counts") or {}
    if not counts:
        return ""
    strip = '<div class="ratings" aria-label="Bewertung der Szenarien">' + "".join(
        f'<div class="rating {key}"><b>{int(counts.get(key, 0))}</b>{label}</div>'
        for key, label in RATING_LABELS.items()
    ) + "</div>"
    tools = summary.get("tool_rating_counts") or {}
    if tools:
        strip += (
            '<p class="note">Werkzeugnutzung, gemessen am Minimum an Pflichtabfragen: '
            + " · ".join(f'<span class="tag {TOOL_CLASSES[key]}">{int(tools.get(key, 0))} {label}</span>'
                         for key, label in TOOL_LABELS.items())
            + f' · {int(summary.get("tool_call_count", 0))} Abfragen, Minimum {int(summary.get("min_tool_call_count", 0))}</p>'
        )
    return strip


def _inline_code(text: str) -> str:
    """Escapen und `code` aus den Hinweisen als <code> darstellen."""
    import re

    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text))


def _procedure_html(item: dict) -> str:
    """Aufklappbar: eigene Entscheidungen neben der Musterloesung, mit Begruendung."""
    rows = []
    for step in item.get("procedure") or []:
        when = f"{float(step['time_s']):.1f} s".replace(".", ",")
        detail = f" ({escape(str(step['parameters']))})" if step.get("parameters") else ""
        if step["kind"] == "decision":
            rows.append(f"<li><b>Runde {escape(str(step.get('round') or '–'))}</b>, {when}: "
                        f"<code>{escape(str(step['command']))}</code>{detail} → {escape(str(step.get('outcome', '')))}</li>")
        elif step["kind"] == "instruction":
            rows.append(f"<li class=instr>{when}: <b>Leitstelle</b> <code>{escape(str(step['command']))}</code>{detail}</li>")
        else:
            rows.append(f"<li class=muted>{when}: Ausführer <code>{escape(str(step['command']))}</code> → "
                        f"{escape(str(step.get('outcome', '')))}</li>")
    own = "<ul class=proc>" + ("".join(rows) or "<li>keine Entscheidung</li>") + "</ul>"
    reference = item.get("reference_procedure")
    if not reference:
        return f"<details class=proc-box><summary>Euer Vorgehen</summary>{own}</details>"
    done = int(reference.get("done", 0))
    steps = " → ".join(
        ("✓ " if index < done else "") + " oder ".join(f"<code>{escape(c)}</code>" for c in step)
        for index, step in enumerate(reference.get("steps") or [])
    )
    hints = "".join(f"<li>{_inline_code(str(h))}</li>" for h in reference.get("hints") or [])
    first = reference.get("first_command") or {}
    first_line = (
        f"<p>Erster Befehl: erwartet <code>{escape(' oder '.join(first.get('expected') or []) or '–')}</code> · "
        f"bei euch <code>{escape(str(first.get('chosen', '–')))}</code> {'✓' if first.get('correct') else '✗'}</p>"
        if first else ""
    )
    return (
        "<details class=proc-box><summary>Vorgehen mit Musterlösung vergleichen</summary>"
        f"<div class=proc-grid><div><b>Euer Vorgehen</b>{own}</div>"
        f"<div><b>Musterlösung</b><p>{steps} → {escape(str(reference.get('outcome', '')))}</p>{first_line}"
        f"<p><b>Warum:</b> {_inline_code(str(reference.get('reason', '')))}</p>"
        + (f"<b>Woran man es erkennt</b><ul>{hints}</ul>" if hints else "")
        + "</div></div></details>"
    )


def _tool_cell(item: dict) -> str:
    rating = str(item.get("tool_rating", ""))
    if rating not in TOOL_LABELS:
        return "<td>–</td>"
    return (f'<td class="num"><span class="tag {TOOL_CLASSES[rating]}">{TOOL_LABELS[rating]}</span><br>'
            f'{int(item.get("tool_call_count", 0))} / min. {int(item.get("min_tool_call_count", 0))}</td>')


def _rating_reason_html(item: dict) -> str:
    """Gruende der Bewertung unter dem farbigen Etikett, ohne die Kopfzeile zu wiederholen."""
    reasons = [str(line) for line in (item.get("rating_reasons") or [])[1:]]
    return "<ul>" + "".join(f"<li>{escape(line)}</li>" for line in reasons) + "</ul>" if reasons else ""


STYLE = """
:root{--bg:#f6f5f0;--card:#fff;--ink:#17212b;--muted:#5b6672;--line:#dcd9cf;--ok:#0a7a5c;--warn:#b7791f;--bad:#b42318;--bar:#087e8b}
@media (prefers-color-scheme:dark){:root{--bg:#12171c;--card:#1a2129;--ink:#e8ecef;--muted:#9aa5b1;--line:#2c3742;--ok:#3cc19b;--warn:#e0a63d;--bad:#f0655a;--bar:#3aa9b8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1280px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:1.4rem;margin:0 0 4px}h2{font-size:1.05rem;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}
.hero{display:grid;grid-template-columns:auto 1fr;gap:24px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
.grade{width:112px;height:112px;border-radius:24px;display:grid;place-items:center;font-size:4rem;font-weight:800;color:#fff}
.grade.A{background:var(--ok)}.grade.B{background:#5a9f3a}.grade.C{background:var(--warn)}.grade.D{background:var(--bad)}
.verdicts{display:flex;flex-wrap:wrap;gap:8px 24px;margin:8px 0 0}
.pill{font-weight:600}.pill.yes{color:var(--ok)}.pill.no{color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:1.35rem}.kpi span{color:var(--muted);font-size:.85rem}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle;font-size:.9rem}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}
td.finding{min-width:240px;font-size:.82rem}td.finding details{margin-top:4px;color:var(--muted)}td.finding ul{margin:4px 0 0;padding-left:18px}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.78rem;font-weight:700;color:#fff}
.tag.pass{background:var(--ok)}.tag.fail{background:var(--bad)}
.tag.positive{background:var(--ok)}.tag.neutral{background:var(--warn)}.tag.negative{background:var(--bad)}
td.rating-cell{min-width:220px;font-size:.82rem}
tr.proc-row td{padding-top:0;border-top:0}.proc-box{margin-top:0}
.proc-grid{position:sticky;left:12px;max-width:min(1100px,calc(100vw - 80px))}.proc-box summary{cursor:pointer;color:var(--bar);font-weight:600}
.proc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:6px;color:var(--ink)}
ul.proc{margin:4px 0;padding-left:0;list-style:none}ul.proc li.instr{color:#a4481a}ul.proc li.muted{color:var(--muted)}
.proc-grid p{margin:4px 0}.proc-grid ul{margin:4px 0;padding-left:18px}td.rating-cell ul{margin:4px 0 0;padding-left:18px}
.ratings{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 20px}
.rating{display:flex;align-items:baseline;gap:8px;padding:10px 16px;border-radius:12px;color:#fff;font-weight:700}
.rating b{font-size:1.6rem;font-variant-numeric:tabular-nums}.rating.positive{background:var(--ok)}
.rating.neutral{background:var(--warn)}.rating.negative{background:var(--bad)}
.meter{position:relative;height:8px;min-width:90px;background:var(--line);border-radius:4px;overflow:hidden}
.meter i{position:absolute;inset:0 auto 0 0;background:var(--bar)}.meter.over i{background:var(--bad)}
.bars{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.bar{display:grid;grid-template-columns:190px 1fr 96px;gap:12px;align-items:center;padding:4px 0}
.bar .meter{height:12px}.bar em{font-style:normal;text-align:right;font-variant-numeric:tabular-nums}
.note{color:var(--muted);font-size:.85rem;margin-top:8px}
a{color:var(--bar);text-underline-offset:2px}td a{font-weight:600;white-space:nowrap}td.num{white-space:nowrap}
.scroll,.table-scroll{overflow-x:auto;border-radius:12px}.scroll table{min-width:1100px}
td.mission{min-width:180px}
@media (max-width:640px){.hero{grid-template-columns:1fr}.bar{grid-template-columns:1fr 80px}.bar .meter{grid-column:1/-1;order:3}}
"""


def _eur(value: object) -> str:
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",") + " €"  # type: ignore[arg-type]


MISSION_LABELS = {
    "completed": "Fahrtziel erreicht",
    "safe_stopped": "Mission durch sicheren Halt beendet; Fahrtziel nicht erreicht",
    "terminated": "Mission beendet; Fahrtziel nicht erreicht",
    "open": "Mission offen; Fahrtziel nicht erreicht",
}


def _pct(value: object) -> str:
    return f"{float(value) * 100:.0f} %"  # type: ignore[arg-type]


def _grade_line(grading: dict, payload: dict) -> str:
    """Leistung, Sicherheit und Fortschritt getrennt: Was ist gut, was fehlt noch?"""
    from .progress import short_progress

    unsafe = int(grading.get("unsafe_cases", 0))
    parts = [f"Leistung {grading.get('performance_grade', grading['grade'])}"]
    parts.append(f"Sicherheit: {unsafe} unsichere Fälle" if unsafe else "Sicherheit: keine unsicheren Fälle")
    change = short_progress(payload.get("progress") or {})
    if change:
        parts.append(f"seit dem letzten Lauf {change}")
    return " · ".join(parts)


def _progress_section(payload: dict[str, object]) -> str:
    """Fortschritt gegenueber dem vorigen Lauf und kurzer Verlauf (progress.py)."""
    progress: dict = payload.get("progress") or {}  # type: ignore[assignment]
    if not progress:
        return ""
    if not progress.get("available") or not progress.get("comparable"):
        head = f'<p class="note">{escape(str(progress.get("reason", "kein Vergleich")))}.</p>'
    else:
        delta = int(progress["passed_now"]) - int(progress["passed_before"])
        lists = "".join(
            f"<p><strong>{label}:</strong> {escape(', '.join(progress[key]))}</p>"
            for label, key in (("Neu bestanden", "newly_passed"), ("Neu gescheitert", "newly_failed"))
            if progress[key]
        ) or "<p>Dieselben Fälle bestanden wie zuvor.</p>"
        head = (
            f"<p><strong>{progress['passed_before']} → {progress['passed_now']} bestanden ({delta:+d})</strong> · "
            f"Kollisionen {progress['collisions_before']} → {progress['collisions_now']} · "
            f"Kosten {_eur(progress['cost_before_eur'])} → {_eur(progress['cost_now_eur'])} · "
            f"Einstufung {escape(str(progress['rating_before']))} → {escape(str(progress['rating_now']))}</p>{lists}"
        )
    history = progress.get("history") or []
    rows = "".join(
        f"<tr><td>{escape(str(entry.get('time', '')).replace('T', ' ')[:16])}</td><td>{escape(str(entry.get('agent')))}</td>"
        f"<td class=num>{entry.get('passed')} / {entry.get('scenarios')}</td><td class=num>{entry.get('collisions')}</td>"
        f"<td class=num>{_eur(entry.get('cost_eur', 0.0))}</td><td>{escape(str(entry.get('rating')))}</td></tr>"
        for entry in reversed(history)
    )
    table = (
        '<div class="table-scroll" tabindex="0" aria-label="Laufverlauf"><table><thead><tr><th>Lauf</th><th>Agent</th><th>Bestanden</th><th>Kollisionen</th><th>Kosten</th>'
        f"<th>Einstufung</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if len(history) > 1
        else ""
    )
    return f"<h2>Fortschritt seit dem letzten Lauf</h2>{head}{table}"


def render_html_report(payload: dict[str, object]) -> str:
    summary: dict = payload["summary"]  # type: ignore[assignment]
    grading: dict = summary["grading"]
    criteria: dict = grading["criteria"]
    scenarios: list[dict] = payload["scenarios"]  # type: ignore[assignment]
    stats: list[dict] = payload["scenario_stats"]  # type: ignore[assignment]

    def yes_no(flag: bool) -> str:
        return f'<span class="pill {"yes" if flag else "no"}">{"ja" if flag else "nein"}</span>'

    satisfaction = summary["average_customer_satisfaction"]
    kpis = [
        ("Gesamtkosten", _eur(summary["total_cost_eur"])),
        ("Im Budget", f"{summary['within_budget_count']} / {summary['scenario_count']}"),
        ("Remote-Assistance-Anrufe", str(summary["remote_assistance_calls"])),
        ("Kundenzufriedenheit", "–" if satisfaction is None else f"{float(satisfaction):.2f}"),
        ("Kollisionen", str(summary["collision_count"])),
        ("Personenschäden", str(summary["personal_injury_count"])),
        ("Fehlalarme", f"{summary['false_alarm_count']} / {summary['no_intervention_scenarios']}"),
        ("Verpasste Eingriffe", f"{summary['missed_intervention_count']} / {summary['intervention_scenarios']}"),
        ("Bestanden", f"{summary['passed_count']} / {summary['scenario_count']}"),
        ("Sicherheit bestätigt", f"{summary['safety_compliant_count']} / {summary['scenario_count']}"),
        ("Fahrtziel erreicht", f"{summary['mission_completed_count']} / {summary['scenario_count']}"),
        ("Mission durch sicheren Halt beendet", str(summary['safe_stop_count'])),
    ]
    if summary.get("sequence_scenario_count"):
        kpis.append(
            (
                "Lösungswege vollständig",
                f"{summary['sequence_complete_count']} / {summary['sequence_scenario_count']}"
                f" · Ø {_pct(summary['average_sequence_score'])}",
            )
        )

    totals: dict[str, float] = dict(payload["cost_items"])  # type: ignore[call-overload]
    biggest = max(totals.values(), default=0.0) or 1.0
    bars = "".join(
        f'<div class="bar"><span>{escape(COST_LABELS.get(name, name))}</span>'
        f'<div class="meter"><i style="width:{min(100.0, value / biggest * 100):.1f}%"></i></div>'
        f"<em>{_eur(value)}</em></div>"
        for name, value in sorted(totals.items(), key=lambda item: -item[1])
    ) or '<p class="note">Keine Kosten angefallen.</p>'

    rows = []
    for item in scenarios:
        budget = float(item["budget_eur"])
        cost = float(item["total_cost_eur"])
        share = min(100.0, cost / budget * 100) if budget else 100.0
        run = f" · Lauf {item['run']}" if int(summary["repeats"]) > 1 else ""
        findings = [str(finding) for finding in item.get("diagnosis") or []]
        finding_html = ""
        if findings:
            rest = "".join(f"<li>{escape(finding)}</li>" for finding in findings[1:])
            finding_html = escape(findings[0]) + (
                f"<details><summary>{len(findings) - 1} weitere</summary><ul>{rest}</ul></details>" if rest else ""
            )
        details = item.get("details")
        name = escape(str(item["scenario_id"])) + run
        link = f'<a href="{escape(str(details))}">{name}</a>' if details else name
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('release') or '–'))}</td>"
            f"<td>{link}</td>"
            f"<td>{escape(str(item['command']))}</td>"
            + (
                f"<td>{escape(str(item['outcome']))}</td>"
                if payload.get("hide_reference")
                else f"<td>{escape(' oder '.join(item['expected_outcomes']))} → {escape(str(item['outcome']))}</td>"
            )
            + f"<td>{yes_no(item['safety_compliant'])}</td>"
            + f"<td>{yes_no(item['solution_complete'])}</td>"
            + f"<td class=mission>{escape(MISSION_LABELS[item['mission_status']])}</td>"
            f'<td><div class="meter {"over" if cost > budget else ""}"><i style="width:{share:.1f}%"></i></div></td>'
            f'<td class="num">{_eur(cost)} / {_eur(budget)}</td>'
            f'<td class="rating-cell"><span class="tag {item.get("rating", "pass" if item["passed"] else "fail")}">'
            f'{RATING_LABELS.get(str(item.get("rating")), "bestanden" if item["passed"] else "fehlgeschlagen")}</span>'
            f'{_rating_reason_html(item)}</td>'
            + _tool_cell(item)
            + f'<td class="finding">{finding_html}</td>'
            + "</tr>"
            + f'<tr class="proc-row"><td colspan="12">{_procedure_html(item)}</td></tr>'
        )

    stat_table = ""
    if int(summary["repeats"]) > 1:
        stat_rows = "".join(
            f"<tr><td>{escape(str(stat['scenario_id']))}</td><td class=num>{_pct(stat['pass_rate'])}</td>"
            f"<td class=num>{_eur(stat['cost_mean_eur'])}</td><td class=num>± {_eur(stat['cost_std_eur'])}</td>"
            f"<td>{escape(', '.join(stat['commands']))}</td></tr>"
            for stat in stats
        )
        stat_table = (
            f'<h2>Streuung über {summary["repeats"]} Läufe</h2><div class="table-scroll" tabindex="0" aria-label="Streuung"><table><thead><tr><th>Szenario</th>'
            "<th>Bestehensquote</th><th>Kosten Mittel</th><th>Streuung</th><th>Befehle</th></tr></thead>"
            f"<tbody>{stat_rows}</tbody></table></div>"
        )

    releases: list[dict] = payload.get("releases") or []  # type: ignore[assignment]
    release_table = ""
    if releases:
        release_rows = "".join(
            f"<tr><td>{escape(str(row['release']))}</td>"
            f"<td class=num>{row['passed_count']} / {row['scenario_count']}</td>"
            f"<td class=num>{_eur(row['total_cost_eur'])}</td><td class=num>{row['collision_count']}</td></tr>"
            for row in releases
        )
        release_table = (
            '<h2>Nach Freigabestufe</h2><div class="table-scroll" tabindex="0" aria-label="Freigabestufen"><table><thead><tr><th>Freigabe</th><th>Bestanden</th>'
            f"<th>Kosten</th><th>Kollisionen</th></tr></thead><tbody>{release_rows}</tbody></table></div>"
        )

    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in grading["reasons"])
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutonomyRecoverySim Auswertung</title><style>{STYLE}</style></head>
<body><main>
<h1>Remote-Assistance-Auswertung</h1>
<p class="sub">Arbeitet die Abteilung mit ihrem Agenten wirtschaftlich, und sind die Kunden zufrieden?</p>
{_ratings_strip(summary)}
<section class="hero">
<div class="grade {escape(str(grading['grade']))}" aria-label="Einstufung {escape(str(grading['grade']))}">{escape(str(grading['grade']))}</div>
<div><strong>Einstufung {escape(str(grading['grade']))}</strong>
<p class="note">{escape(_grade_line(grading, payload))}</p>
<div class="verdicts"><span>Wirtschaftlich: {yes_no(bool(grading['economic']))}</span>
<span>Kunden zufrieden: {yes_no(bool(grading['customers_satisfied']))}</span></div>
<p class="note">Bestehensquote {_pct(criteria['pass_rate'])} · Budgetquote {_pct(criteria['budget_rate'])} ·
Episoden mit Remote Assistance {_pct(criteria['remote_assistance_rate'])}</p>
<ul class="note">{reasons}</ul></div>
</section>
<div class="kpis">{''.join(f'<div class="kpi"><b>{escape(value)}</b><span>{escape(label)}</span></div>' for label, value in kpis)}</div>
{_progress_section(payload)}
<h2>Kosten nach Position</h2><div class="bars">{bars}</div>
{release_table}
<h2>Szenarien</h2>
<p class="note">Der Befund nennt den wichtigsten Grund zuerst. Ein Klick auf das Szenario öffnet die Detailseite
mit Befehlsfolge und Agent-Trace (<code>report.md</code>). Breite Tabellen lassen sich seitlich scrollen.</p>
<div class="scroll" tabindex="0" aria-label="Szenarien"><table><thead><tr><th>Freigabe</th><th>Szenario</th><th>Erster Befehl</th><th>Ausgang</th><th>Sicherheit bestätigt</th><th>Lösungsweg erfüllt</th><th>Mission</th><th>Budget</th><th>Kosten / Budget</th><th>Bewertung</th><th>Werkzeuge</th><th>Befund</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{stat_table}
<p class="note">Einstufung als Rückmeldung, keine Note: D bei jeder Kollision oder jedem Personenschaden. A ab 90 % Bestehens- und Budgetquote,
höchstens 25 % Episoden mit Remote Assistance und Zufriedenheit ≥ 0,70. B ab 75 % / 75 % und Zufriedenheit ≥ 0,50.
C ab 50 % Bestehensquote. Beträge sind Modellannahmen (costs.py).</p>
</main></body></html>
"""
