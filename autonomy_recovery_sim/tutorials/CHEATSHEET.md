> **Ausgabe: sprint1.** Enthalten sind nur die bisher freigegebenen Szenarien.
> Beispiele für spätere Sprints sind eine Vorschau und erst nach deren Freigabe ausführbar.

# Spickzettel

Alles zum Nachschlagen auf einer Seite. Details: [SESSION.md](../SESSION.md) (Werkzeuge, Kontext), [COMMANDS.md](../COMMANDS.md) (Befehle).

## Befehle starten

```bash
# Prüfen (Selbsttest; alle Tests mit: python3 -m unittest discover -s tests)
python3 -m unittest tests.test_student_release
python3 -m autonomy_recovery_sim validate autonomy_recovery_sim/scenarios

# Live-Oberfläche (ohne Agent: Recovery Console = ihr seid der Agent)
python3 -m autonomy_recovery_sim.live --scenario autonomy_recovery_sim/scenarios/hannover_frei.json

# Ein Szenario mit Agent (Ergebnis: artifacts/autonomy-recovery-sim/<szenario>/result.json)
python3 -m autonomy_recovery_sim autonomy_recovery_sim/scenarios/hannover_frei.json --agent mein_agent:decide

# Prüfstand: euer Agent gegen alle Szenarien oder eine Freigabestufe (siehe PRUEFSTAND.md)
python3 -m autonomy_recovery_sim batch --agent mein_agent:decide
python3 -m autonomy_recovery_sim batch --release sprint2 --agent mein_agent:decide --output artifacts/loop/e1

# Euer LLM-Agent, dreimal je Fall
python3 -m autonomy_recovery_sim batch --release sprint2 --agent mein_llm_agent:decide --output artifacts/llm
python3 -m autonomy_recovery_sim batch --release sprint2 --nur-neu --repeats 3 --agent mein_llm_agent:decide --output artifacts/llm-stabil

# Viele Szenarien (Bericht: artifacts/autonomy-recovery-sim/batch/ oder --output)
python3 -m autonomy_recovery_sim batch autonomy_recovery_sim/scenario_sets/bootcamp.txt --agent autonomy_recovery_sim.student_llm_agent:decide --repeats 3

# Zwei Auswertungen vergleichen
python3 -m autonomy_recovery_sim.tutorials.vergleich <ordner-a> <ordner-b>
```

`mein_agent.py` erstellt ihr mit [ERSTER_AGENT.md](ERSTER_AGENT.md) und erweitert es Sprint für Sprint.

`--agent`: `none` · `llm` · `python_modul:funktion`.

## Szenariemengen

| Datei unter `scenario_sets/` | Inhalt |
|---|---|
| `bootcamp.txt` | drei erklärte Einstiegsfälle |
| `sprint1.txt` bis `sprint4.txt` | alle bis dahin freigegebenen Fälle: 12, 26, 54 und 80 einschließlich Familien |

Nur die neuen Fälle eines Sprints: `batch --release sprint2 --nur-neu`. In jedem Sprint messt
ihr beide Agenten auf derselben Menge; die neuen Fälle fahrt ihr mit dem LLM-Agenten zusätzlich
dreimal (`--nur-neu --repeats 3`).

Die Zuordnung und Schwerpunkte stehen im [Szenarienkatalog](../SCENARIOS.md).
Einzelne Fälle ladet ihr mit `live --scenario PFAD` oder `run PFAD`.

## Die Werkzeuge

Euer Agent sieht die Welt als **Objektliste** mit Messunsicherheit. Koordinaten im
Fahrzeugrahmen `base_link`: x nach vorn, y nach links.

| Werkzeug | Liefert |
|---|---|
| `get_tracked_objects` | Objekte mit Klasse und Wahrscheinlichkeit, Position, Geschwindigkeit, Unsicherheit, Spur, Alter |
| `get_sensor_coverage` | verlässliche Reichweite und verdeckte Abschnitte je Spur |
| `get_map_context` | Fahrstreifen, Mittellinie, Seitenstreifen, Haltelinien aus der Karte |
| `get_blocker` ² | stehendes Hindernis voraus: Klasse, Abstand, freier Platz links/rechts, Mittellinie, ggf. Kameraeinschätzung |
| `check_oncoming_traffic` ² | Gegenverkehr im Horizont (`horizon_s`, Standard 8) und wie weit die Gegenspur einsehbar ist |
| `check_rear_traffic` ² | Verkehr, der von hinten aufschließt (`horizon_s`, Standard 6) |
| `check_vulnerable_road_users` ² | Fuß- und Radverkehr sowie Tiere im Fahrkorridor |
| `get_route_state` | Routenblockade, Alternative, Umwegkosten |
| `get_mission_context` | Fahrgäste, Hub, Leitstelle, Halteposition, `mission_state` |
| `get_traffic_control` | Ampeln und Bahnübergänge voraus: Zustand (`RED`, `GREEN`, `DARK`, `OPEN`, `CLOSED`) |
| `get_sensor_health` | Kamera, LiDAR, Lokalisierung |
| `get_recovery_history` | frühere Befehle, Wiederholungswarnungen |
| `get_vehicle_status` | Aufprall, Warnblinker, Meldung |

² **Lageprüfungen:** Das Fahrzeugsystem rechnet sie aus der Objektliste; sie irren mit ihr und
sehen nichts Verdecktes. `PASS_OBSTACLE` verlangt alle vier als Beleg. Ergebnisse können
`observation_age_s` (veraltet) tragen. Budget: 14 Aufrufe je Runde, 3 Entscheidungsversuche.

Nur in Szenarien mit VLA-Fahrstack (`driving_stack` im Kontext) kommen zwei Werkzeuge dazu.
Beide liefern **Modellausgaben**, die falsch sein können:

| Werkzeug | Liefert |
|---|---|
| `get_vla_output` | Begründung des Fahrstacks, 6,4-s-Trajektorie, letzte Ausgaben (`last_n`) |
| `get_camera_caption` | Bildbeschreibung einer Kamera (`front_wide`, `front_tele`, `cross_left`, `cross_right`) |

## Die Befehle

| Befehl | Pflichtparameter | Endet Fahrt |
|---|---|:---:|
| `WAIT` | `duration_s` (1–30) | ab 3 s: Phantome weg, Daten frisch, Karte abgeglichen |
| `HONK` | – (nur zur Warnung von Personen oder Tieren auf der Fahrbahn) | |
| `PASS_OBSTACLE` | `side` | |
| `ABORT_TO_LANE` | – | |
| `CROSS_LOW_RISK_OBJECT` | – | |
| `REVERSE_SHORT` | `max_distance_m` (0,5–5) | |
| `REPLAN_ROUTE` | – (wendet, wenn nötig) | ja |
| `PULL_OVER` | – | |
| `DROP_PASSENGER` | – | ja |
| `SAFE_STOP` | – | ja |
| `RETURN_HOME` | – | ja |
| `REQUEST_REMOTE_ASSISTANCE` | – | |
| `RESUME` | – | |
| `SECURE_SCENE` | – (nur nach eigenem Aufprall) | |
| `REPORT_INCIDENT` | `injured_persons_suspected` (nur nach eigenem Aufprall) | ja |

`PASS_OBSTACLE` schert zum Schauen aus und wartet auf eine Lücke. Taucht etwas Neues auf, hält
es an (`trigger.type = "SITUATION_CHANGED"`): `RESUME` setzt fort, `ABORT_TO_LANE` bricht ab.

Die Leitstelle antwortet mit einer Anweisung (`remote_instruction`: `PROCEED`,
`PASS_OBSTACLE`, `WAIT`, `REPLAN_ROUTE`, `END_TRIP`), die euer Agent befolgt. Vor dem Abbruch
einer Fahrt muss die erreichbare Leitstelle gefragt werden.

Jede Runde nennt ihren Auslöser unter `trigger`: `DEADLOCK`, `SITUATION_CHANGED`,
`REMOTE_INSTRUCTION` oder `IMPACT_DETECTED`.

Ausgabeformat: `{"command": ..., "parameters": {...}, "reason": "..."}`.

## Fehlercodes

| Code | Bedeutung | Ursache liegt bei |
|---|---|---|
| `INVALID_ARGUMENT` | Schema oder Bereich verletzt | Agent |
| `NOT_AVAILABLE` | Befehl in dieser Lage nicht verfügbar (z. B. kein Seitenstreifen) | Lage |
| `RULE_REJECTED` | Verkehrsregel (durchgezogene Linie, Gegenfahrbahn, Absperrung) | Regelprüfung |
| `SAFETY_REJECTED` | Nicht ausführbare Geometrie (z. B. vollständig blockierte Straße) | Sicherheitsprüfung |
| `TOOL_TIMEOUT` | Werkzeug ausgefallen, erneut versuchbar | Werkzeug |

## Ausgänge (`outcome`)

`liberated` (Hindernis passiert) · `resolved` (Weg wurde frei) · `waiting` · `aborted`
(Manöver sicher abgebrochen) · `rerouted` · `safe_stopped` · `returned_home` ·
`passenger_dropped` · `remote_resolved` (mit Hilfe der Leitstelle ans Ziel) ·
`incident_reported` · `not_triggered`.

## Kosten (Modellannahmen)

Personenschaden 100 000 € · Sachschaden 5 000 € · Leitstellenanruf 25 € · Stillstand 0,50 €/s ·
Umweg/Rückfahrt 0,50 €/s · Bergung 150 € (+100 € im Fahrstreifen) · Erstattung 20 € je
abgesetztem Fahrgast · abgelehnter Befehl 2 €.

## Wo steht was in `result.json`

| Feld | Inhalt |
|---|---|
| `command`, `commands` | erster inhaltlicher Befehl; Verlauf aller Befehle mit Status |
| `outcome`, `collision`, `reached_goal` | Ergebnis |
| `costs` | Kostenpositionen, Gesamtsumme, Kundenzufriedenheit |
| `within_budget`, `budget_eur` | Budget des Szenarios |
| `agent_trace` | `tool_call`, `decision_attempt`, `model_round` in Reihenfolge |
| `agent_error` | Fehler, der zum Ersatzverhalten `WAIT` führte |
| `events` | alles, was in der Simulation geschah |

## Modell einrichten

`cp .env.example .env` und den Schlüssel eintragen (siehe [Erster Agent](ERSTER_AGENT.md#mit-einem-echten-modell)).
Weitere Variablen: `AUTONOMY_RECOVERY_TIMEOUT_S` (90), `AUTONOMY_RECOVERY_MAX_ROUNDS` (12).

## Dateien, die euch gehören

- `mein_agent.py` und `mein_llm_agent.py` im Wurzelverzeichnis: eure beiden Agenten
  (Einstieg: [ERSTER_AGENT.md](ERSTER_AGENT.md))
- [`student_llm_agent.py`](../student_llm_agent.py): Vorlage für den LLM-Agenten mit Minimalprompt
- [`tutorials/helpers.py`](helpers.py): Wiederholung, Datenalter, Belege
- [`recovery_helpers.py`](../recovery_helpers.py): so rechnen die Lageprüfungen aus der Objektliste
