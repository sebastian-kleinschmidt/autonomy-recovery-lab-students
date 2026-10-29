> **Ausgabe: sprint2.** Enthalten sind nur die bisher freigegebenen Szenarien.
> Beispiele für spätere Sprints sind eine Vorschau und erst nach deren Freigabe ausführbar.

# Glossar

**Abbruchbedingung.** Eine Zeit- oder Distanzgrenze des Manöververtrags. Der Ausführer
überwacht diese Grenzen und fehlenden Fortschritt. Was beim Ausscheren neu auftaucht, meldet
er dem Agenten (`SITUATION_CHANGED`); der kann mit `ABORT_TO_LANE` zurück in die Spur.

**Agent.** Programm, das ein Sprachmodell in eine Schleife einbaut, ihm Werkzeuge gibt und
seine Vorschläge begrenzt ausführt. Siehe [Erster Agent](ERSTER_AGENT.md).

**Agentenloop.** Die Schleife aus Modellaufruf, Werkzeugausführung und erneutem
Modellaufruf, bis das Modell entscheidet oder ein Limit greift.

**Agent-Trace.** Protokoll einer Runde in `result.json`: Werkzeugaufrufe,
Entscheidungsversuche und Modellrunden in Reihenfolge.

**Auslöser (Trigger).** Regel im Szenario, die einen Akteur reagieren lässt, sobald das Ego
eine Bedingung erfüllt, etwa ausschert. So wird der Grund einer Blockade erst beim
Vorbeifahren sichtbar. Jeder Auslöser muss fair sein: vorher mit einem Werkzeug
erkennbar. Ihr begegnet Auslösern in den Szenariofamilien ab Sprint 2.

**Ausführer.** Der zertifizierte Teil des Simulators, der einen freigegebenen Befehl in
Fahrbewegung umsetzt. Er ist nicht der Agent.

**Baseline.** Vergleichswert, an dem ein Agent gemessen wird, z. B. der wartende Starter aus
[Erster Agent](ERSTER_AGENT.md) oder euer klassischer Agent gegenüber dem LLM-Agenten.

**Batchlauf.** Auswertung vieler Szenarien mit einem Agenten: `python3 -m autonomy_recovery_sim batch …`.

**Befehl (High-Level-Befehl).** Eine der 15 Anweisungen, die ein Agent geben darf, z. B.
`WAIT`. Kein Lenken, kein Bremsen. Siehe [COMMANDS.md](../COMMANDS.md).

**Neue Lage (`SITUATION_CHANGED`).** `PASS_OBSTACLE` hält ausgeschert an, wenn beim Schauen
etwas Neues auftaucht, Gegenverkehr naht oder die Gegenspur verdeckt bleibt. Der Agent
entscheidet mit `RESUME` oder `ABORT_TO_LANE`, während die Uhr weiterläuft.

**Chain of Causation (CoC).** Begründung eines VLA-Fahrstacks in Text: erst die Ursache,
dann die Entscheidung („Ein Lieferwagen blockiert die Spur, also halte ich“). Sie erklärt
das Verhalten, muss aber nicht stimmen. Hier über `get_vla_output`.

**Budget.** Obergrenze, hier in dreierlei Sinn: **Werkzeugbudget** (Aufrufe je Runde),
**Befehlsbudget** (Befehle je Episode) und **Kostenbudget** (Euro je Szenario).

**Deadlock.** Das Fahrzeug steht und kommt nicht weiter. Der Stillstandsmonitor erkennt es
nach einigen Sekunden und ruft den Agenten.

**Episode.** Ein Lauf eines Szenarios vom Start bis zum Ende, mit ggf. mehreren Runden.

**Evidenz / Beleg.** Werkzeugergebnisse, auf die sich eine Entscheidung stützt. Für
`PASS_OBSTACLE` werden die vier Lageprüfungen erwartet. Fehlende Belege erzeugen
Sicherheitswarnungen.

**Einstufung.** Zusammenfassung eines Batchlaufs als A bis D aus Bestehens- und Budgetquote
sowie Sicherheit. Rückmeldung, keine Note: Das Labor ist
unbenotet. Unsichere Fälle begrenzen sie auf höchstens C, mehr als 15 % ergeben D.

**Fail-safe.** Festes, sicheres Ersatzverhalten bei Fehlern. Hier `WAIT` (5 s).

**Fortschritt.** Vergleich eines Batchlaufs mit dem vorigen im selben Ausgabeordner: mehr
oder weniger bestanden, welche Fälle neu bestanden oder neu gescheitert sind. Der Verlauf
steht in `verlauf.jsonl`. Siehe [Prüfstand](../PRUEFSTAND.md#fortschritt-verfolgen).

**Function Calling.** Schnittstelle, über die ein Modell strukturierte Werkzeugaufrufe
(Name und JSON-Argumente) statt Prosa liefert.

**Guardrail.** Prüfung im Programm, die ein Agent nicht umgehen kann (Schema, Regeln,
Sicherheit, Budgets).

**Sicherheitsvertrag.** Technische Regeln für die Zusammenarbeit zwischen Agent und
Simulator: Ausgabeformat, erlaubte Befehle, Belege, Budgets und Ersatzverhalten bei
Fehlern. Sie werden im Programm geprüft. Details:
[Was der Simulator prüft](../SESSION.md#was-der-simulator-prüft).

**Halluzination.** Vom Modell erfundene Aussage, die nicht durch Daten gedeckt ist.

**Hybridagent.** Agent, der Regeln für einfache und harte Fälle und ein Modell für
schwierige Abwägungen nutzt.

**Injektion (Prompt-Injection).** Text in den Daten, der sich als Anweisung ausgibt und ein
Modell zu ungewollten Handlungen verleiten soll. Gegenmittel: Daten sind nie Anweisungen.

**Kontext.** Alle Nachrichten, die einem Modell in einer Runde mitgegeben werden. Begrenzt
und kostenpflichtig.

**Kontextgestaltung.** Die bewusste Auswahl dessen, was in der Nachrichtenliste steht: welche
Teile der Lage, welche Werkzeuge, welche Rückmeldungen.

**Konsole (Recovery Console).** Bedienfeld der Live-Oberfläche, in dem ein Mensch die
Agentenrolle übernimmt.

**Autonomy Recovery Agent.** Der Agent dieses Projekts: Er wählt bei einem
Deadlock einen High-Level-Befehl. Er fährt nicht selbst und kann bei Bedarf
separat Remote Assistance durch einen menschlichen Operator anfordern.


**Lösungsweg.** Geordnete Folge von Schritten, die eine mehrstufige Lage löst
(`reference.sequence`). Ein Schritt zählt nur mit einem erfolgreichen Befehl; bestanden ist der
Fall nur mit dem ganzen Weg. Siehe [Prüfstand](../PRUEFSTAND.md).

**Lageprüfung.** Werkzeug, das das Fahrzeugsystem aus der Objektliste rechnet:
`get_blocker`, `check_oncoming_traffic`, `check_rear_traffic`,
`check_vulnerable_road_users`. Es irrt mit der Objektliste und sieht nichts Verdecktes.

**Leitstelle (Remote Assistance).** Menschliche Operatoren, die bei Bedarf eine Anweisung
geben (`PROCEED`, `PASS_OBSTACLE`, `WAIT`, `REPLAN_ROUTE`, `END_TRIP`). Jeder Anruf kostet
Geld; die Anweisung ist zu befolgen. Vor dem Abbruch einer Fahrt muss sie gefragt werden.

**Manöver.** Befehl, der das Fahrzeug über die Fahrbahn bewegt (`PASS_OBSTACLE`,
`CROSS_LOW_RISK_OBJECT`).

**Nichtdeterminismus.** Dasselbe Modell liefert bei gleicher Eingabe verschiedene Antworten.
Der Simulator ist deterministisch, das Modell nicht.

**Observation Age.** `observation_age_s`: Alter der Daten in einem Werkzeugergebnis.

**Phantom.** Objekt, das die Wahrnehmung meldet, das physisch aber nicht existiert.

**Ausführungsverhalten.** Für alle Szenarien gleich: Der Agent beurteilt die Sicherheit;
fehlende Belege und Lagebedenken erzeugen Warnungen. Der Ausführer prüft harte Regeln
und Ausführbarkeit, hält Manövergrenzen ein und bremst für wahrgenommene Objekte.
Rückfragen bei neuer Lage und Unfallabwicklung gehören immer dazu. Siehe
[Was der Simulator prüft](../SESSION.md#was-der-simulator-prüft).

**ReAct.** Muster, bei dem das Modell vor jedem Werkzeugaufruf kurz begründet, was es erwartet
(„Reasoning and Acting“), und danach Erwartung und Ergebnis vergleicht.


**Runde.** Ein Aufruf des Agenten mit seinem Kontext, Werkzeug- und Versuchsbudget; im Code
das Objekt `session`. Eine Episode kann mehrere Runden haben.


**Streuung.** Schwankung der Ergebnisse über Wiederholungen; bei Modellen zu berichten.

**Szenario.** Eine Ausgangslage mit Straße, Akteuren, Mission, Regeln und erwartetem
Ergebnis (JSON in `autonomy_recovery_sim/scenarios/`).

**Szenariofamilie.** Eine Lage (F1 bis F6) in drei Varianten: sechs Einstiege ab Sprint 2, alle Varianten ab Sprint 3.
`sprint2.txt` enthält 29 Fälle, darunter sechs Familieneinstiege;
`sprint3.txt` enthält 57 Fälle, darunter alle 18 Familienvarianten.
Weitere Seeds prüfen Robustheit bei veränderten konkreten Bedingungen.

**Unfallabwicklung.** Was nach einem eigenen Aufprall zu tun ist, auch ohne eigene Schuld:
anhalten, `SECURE_SCENE`, dann `REPORT_INCIDENT`. Weiterfahren ohne Meldung zählt wie ein
Personenschaden. Fremde Unfälle meldet das Fahrzeug nicht.

**VLA (Vision-Language-Action-Modell).** Fahrmodell, das Kamerabilder liest und Text sowie
eine Trajektorie ausgibt, im Labor NVIDIA Alpamayo 1.5. Im Simulator als regelbasierter Mock
(`vla_mock.py`) in den Szenarien aus `scenario_sets/sprint3.txt`.

**Vertrauensgrenze.** Grenze zwischen dem, was ein Agent *glauben* darf (Regeln, Katalog) und
dem, was er nur als *Daten* behandelt (Werkzeugergebnisse, Freitext).

**Objektliste.** Wie der Agent die Lage sieht: Objekte mit Unsicherheit, Sichtabdeckung und
Karte im Stil eines Fahrstacks wie Autoware (`get_tracked_objects`, `get_sensor_coverage`,
`get_map_context`).

**Werkzeug (Tool).** Funktion mit Name, Beschreibung und Parameterschema, die ein Agent
aufrufen darf. Hier 13 lesende Werkzeuge (Objektliste, Lageprüfungen, Route, Mission,
Fahrzeug) und die Entscheidung `submit_decision`; in Szenarien mit VLA-Fahrstack kommen
`get_vla_output` und `get_camera_caption` dazu.
