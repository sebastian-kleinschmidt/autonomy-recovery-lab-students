# Prüfstand: den Agenten gegen alle Szenarien testen

Der Prüfstand fährt euren Agenten nacheinander durch eine Menge von Szenarien und schreibt einen
Bericht, der für jeden Fall sagt, **ob** er bestanden ist und **warum nicht**, und der zeigt,
**ob** sich euer Agent seit dem letzten Lauf verbessert hat. Er ist derselbe Batchlauf wie der
Abschlusslauf mit weiteren Seeds und zusätzlichen Demo-Prüfungen nach dem Freeze. Alle Befehle gelten aus dem
Repository-Wurzelverzeichnis.

## Der Grundbefehl

```bash
python3 -m autonomy_recovery_sim batch --agent mein_agent:decide
```

`mein_agent.py` ist euer eigener Agent (Einstieg: [ERSTER_AGENT.md](tutorials/ERSTER_AGENT.md)).
Ohne weitere Angabe laufen **alle im Checkout freigegebenen Übungsfälle**, einschließlich
der Familien. Die vollständige Sprint-4-Ausgabe umfasst 80 Fälle. Die Konsole zeigt eine Zusammenfassung und je
Freigabestufe, wie viele Fälle bestanden sind. Der Bericht enthält unter anderem Bestehensquote, Entscheidungsgenauigkeit,
Kollisionen, Kosten, Ergebnisse je Sprint und den Fortschritt zum vorigen Lauf.
Die konkreten Werte hängen von eurem Agenten und den gewählten Einstellungen ab.

Der Rückgabewert ist `0` nur, wenn **alle** Fälle bestanden sind. So lässt sich der Prüfstand
auch in einem eigenen Test oder einer CI-Pipeline verwenden.

## Welcher Agent, welche Szenarien

| Option | Bedeutung | Beispiel |
|---|---|---|
| `--agent` | `none`, `llm` oder `modul:funktion` | `--agent mein_agent:decide` |
| `--release` | alle bis zu dieser Phase freigegebenen Übungsfälle einschließlich Familien: `bootcamp`, `sprint1` … `sprint4`, `all` | `--release sprint2` |
| Quelle (statt `--release`) | ein Szenario, ein Ordner oder eine Set-Datei | `autonomy_recovery_sim/scenario_sets/sprint3.txt` |
| `--repeats` | Läufe je Szenario; bei Sprachmodellen für die Stabilität (z. B. die neuen Fälle) | `--repeats 3` |
| `--output` | Zielordner, je Experiment ein eigener | `--output artifacts/loop/e1` |
| `--hide-reference` | keine Referenzbefehle und -begründungen in den Berichten | optional für einen Versuch ohne Referenzanzeige |

Die Agenten im Repository:

| Agent | Datei | Wofür |
|---|---|---|
| euer klassischer Agent | `mein_agent.py` | Regeln ohne Sprachmodell |
| euer LLM-Agent | `mein_llm_agent.py` | Sprachmodell entscheidet; neue Fälle zusätzlich mit `--repeats 3` |
| Vorlage LLM-Agent | [`student_llm_agent.py`](student_llm_agent.py) | fertige Schleife mit Minimalprompt, Startpunkt für euren |

Beide Agenten auf derselben Menge, jeder in einen eigenen Ordner:

```bash
python3 -m autonomy_recovery_sim batch --release sprint2 \
  --agent mein_agent:decide --output artifacts/sprint2/klassisch
python3 -m autonomy_recovery_sim batch --release sprint2 \
  --agent mein_llm_agent:decide --output artifacts/sprint2/llm
python3 -m autonomy_recovery_sim batch --release sprint2 --nur-neu --repeats 3 \
  --agent mein_llm_agent:decide --output artifacts/sprint2/llm-stabil
python3 -m autonomy_recovery_sim.tutorials.vergleich artifacts/sprint2/klassisch artifacts/sprint2/llm
```

Die Kosten enthalten beim LLM-Agenten auch das Sprachmodell (nach Tokens). Mit `--repeats`
nennt der Bericht, wie viele Szenarien in allen Wiederholungen bestanden sind.

## Was im Zielordner liegt

| Datei | Inhalt |
|---|---|
| `batch-report.html` | Bericht zum Lesen im Browser: Einstufung, Kennzahlen, Fortschritt, Kosten, Tabelle nach Freigabestufe, alle Szenarien mit Befund |
| `batch-report.md` | derselbe Bericht als Markdown, gut für Journal und Abgabe |
| `batch-result.json` | alle Kennzahlen maschinenlesbar, Eingabe für `tutorials.vergleich` |
| `scenarios/<id>/report.md` | **Detailseite** eines Szenarios: Befund, Kennzahlen, Befehlsfolge, Agent-Trace je Runde |
| `scenarios/<id>/result.json` | Rohdaten des Laufs, inklusive vollständigem Trace, Ereignissen und bei Unfällen dem Rückblick (`incident`) |
| `verlauf.jsonl` | eine Zeile je Lauf in diesem Ordner: Agent, Einstellungen, Bestanden, Kollisionen, Kosten, gescheiterte Fälle |

Mit `--repeats` liegen die Detailseiten unter `scenarios/<id>/run-<n>/`.

## Wann ein Szenario besteht

Alle Bedingungen müssen gelten:

1. keine Kollision und keine angenommenen Sicherheitsbefunde; auch ein Manöverabbruch ist sicher stabilisiert,
2. kein Agentenfehler (keine Ausnahme, kein Überschreiten der Budgets),
3. bei der klassischen Einzelreferenz passt der erste inhaltliche Befehl zur Lage
   (Informations- und Regel-Engine-Befehle bereiten nur vor),
4. ein zulässiger Ausgang tritt ein,
5. die Kosten bleiben im Budget des Szenarios,
6. bei erwartetem Abbruch stabilisiert sich das Fahrzeug sicher,
7. bei **mehrstufigen oder alternativen Lösungswegen** ist ein vollständiger Weg erfüllt: Jeder Schritt wurde mit einem
   erfolgreichen Befehl erledigt, in der vorgegebenen Reihenfolge. Zwischenschritte wie ein
   zusätzliches `WAIT` sind erlaubt, kosten aber Zeit; ein gescheiterter Befehl zählt nicht.

`reference.paths` erlaubt mehrere vollständige Wege mit jeweils eigenem Endzustand.
Schritte eines Wegs dürfen nicht mit dem Endzustand eines anderen kombiniert werden.
Bei diesen Fällen wird die vollständige Folge bewertet; vorheriges begrenztes Warten
führt deshalb nicht allein zum Fehlschlag.

Der Bericht trennt **Sicherheit**, **Lösungsweg**, **Mission** und **Kosten/Budget**.

Jedes Szenario erhält eine **Bewertung**:

| Bewertung | Bedeutung |
|---|---|
| positiv (grün) | bestanden |
| neutral (gelb) | sicher, aber nicht bestanden, z. B. nur gewartet oder Budget überschritten |
| negativ (rot) | Kollision, Sicherheits- oder Regelverstoß, Personenschaden oder Unfallflucht |

Die Live-Ansicht rahmt die 3D-Ansicht in dieser Farbe, sobald die Bewertung feststeht: rot schon
beim ersten Verstoß, grün und gelb am Ende. Batch-Bericht, Konsole und Live-Batch zählen oben mit.

Zusätzlich wird die **Werkzeugnutzung** bewertet. Maßstab ist das Minimum an Abfragen, mit dem
man die Lage erkennt und löst: die Pflichtprüfungen der Befehle auf dem günstigsten Lösungsweg
(4 vor `PASS_OBSTACLE`, 2 vor `CROSS_LOW_RISK_OBJECT`, 2 vor einer Wende)
und die Werkzeuge, in denen die entscheidenden Hinweise stehen. Die Bewertung nennt sie.

| Werkzeugnutzung | Abfragen insgesamt |
|---|---|
| effizient | höchstens Minimum + 3 je Entscheidungsrunde (Einordnen: Ampel, Route, Hindernis) |
| angemessen | höchstens Minimum + 6 je Entscheidungsrunde |
| verschwenderisch | mehr |

Die Werkzeugnutzung beeinflusst weder Bestehen noch Kosten; sie zeigt, ob ein Agent gezielt fragt.

Aus allen Fällen eines Laufs ergibt sich die **Einstufung** ([`grading.py`](grading.py)), eine
Rückmeldung, keine Note:

| Einstufung | Voraussetzung |
|:---:|---|
| **D** | weniger als 50 % bestanden, oder mehr als 15 % unsichere (negative) Fälle |
| **C** | mindestens 50 % bestanden; mit unsicheren Fällen höchstens C, auch wenn die Leistung höher wäre |
| **B** | mindestens 75 % bestanden und im Budget |
| **A** | mindestens 90 % bestanden und im Budget, keine unsicheren Fälle |

Leitstellenanteil und Kundenzufriedenheit stehen als Auskunft daneben (*wirtschaftlich*,
*Kunden zufrieden*). Sie senken die Einstufung nicht: Ob die Leitstelle nötig war oder
Fahrgäste aussteigen mussten, prüft schon der Lösungsweg.



Für mehrstufige Szenarien zeigt der Bericht zusätzlich **Teilpunkte**: den Anteil erledigter
Schritte, im Mittel über alle diese Szenarien („Lösungswege vollständig … · Ø …“). Die Einstufung
berücksichtigt nur das Bestehen; die Teilpunkte zeigen, wie nah ein Agent an der Lösung war.

## Fortschritt verfolgen

Lauft nach jeder Änderung in **denselben Ausgabeordner**. Der Bericht vergleicht dann
automatisch mit dem vorigen Lauf und nennt oben:

- wie viele Fälle vorher und jetzt bestanden sind, dazu Kollisionen, Kosten und Einstufung,
- welche Fälle **neu bestanden** und welche **neu gescheitert** sind,
- einen Verlauf über die letzten zehn Läufe (`verlauf.jsonl`).

Verglichen wird nur Vergleichbares: Ändert sich die Szenariomenge, das
Ausführungsverhalten oder die Bewertungsversion, sagt der Bericht das, statt Zahlen gegeneinander zu
stellen. Wer zwei Varianten nebeneinander halten will, nutzt eigene Ordner und den Vergleich
unten.

## Den Bericht lesen

Arbeitet von oben nach unten:

1. **Einstufung und Sicherheit.** Neben der Einstufung stehen eure Leistung und die Zahl
   unsicherer Fälle. Unsichere Fälle zuerst beheben, alles andere danach.
2. **Nach Freigabestufe.** Wo verliert ihr Fälle: bei den Grundlagen oder erst bei den späten
   Stufen? Ein Agent, der Sprint 1 nicht vollständig besteht, braucht keine Sprint-4-Tricks.
3. **Befund.** Jede Zeile nennt den wichtigsten Grund zuerst:

   | Befund | Was er bedeutet | Wo ihr sucht |
   |---|---|---|
   | Sicherheit: Kollision | das Fahrzeug hat etwas berührt | Befehlsfolge, Abbruchereignisse |
   | Agentenfehler | Ausnahme, Budget überschritten oder dreimal abgelehnt | letzte Einträge im Trace |
   | fail-safe | der ausgeführte Befehl kam nicht vom Agenten | Agentenfehler davor |
   | Fehlalarm | eingegriffen, obwohl Warten gereicht hätte | Werkzeuge, die einen regulären Halt zeigen |
   | Verpasster Eingriff | gewartet, obwohl die Lage einen Eingriff verlangt | Route, Mission, Signalzustand |
   | Unerwartetes Manöver | Manöver in einer Lage ohne Manöverlösung | Belege gegen das Manöver |
   | Befehl passt nicht | anderer Befehl als die Lage verlangt | welcher Befehl die Lage besser trifft |
   | Ausgang … statt … | der Befehl passte, die Wirkung nicht | Befehlsfolge: was geschah danach |
   | Lösungsweg unvollständig | ein Schritt fehlt oder ist gescheitert | Befehlsfolge: Status je Befehl, nächste Runde im Trace |
   | Budget überschritten | zu teuer, Posten stehen dabei | Standzeit, Anrufe, Umweg |
   | Hinweis | abgelehnte Versuche oder Werkzeugausfälle, auch in bestandenen Fällen | Trace |

4. **Vorgehen vergleichen.** Unter jedem Szenario klappt „Vorgehen mit Musterlösung
   vergleichen“ auf: links eure Entscheidungen je Runde (mit Zeit, Ergebnis und Anweisungen
   der Leitstelle), rechts die Musterlösung mit Haken für erfüllte Schritte, dem erwarteten
   ersten Befehl, der Begründung und den Hinweisen, woran man die Lage erkennt. Klappt sie
   erst auf, wenn ihr eine eigene Erklärung im Journal habt.
5. **Detailseite.** Ein Klick auf das Szenario öffnet `report.md` mit demselben Vergleich,
   der Befehlsfolge und dem Trace: Welche Werkzeuge hat der Agent gefragt, was kam zurück,
   was hat er daraus gemacht?

## Zwei Läufe vergleichen

```bash
python3 -m autonomy_recovery_sim.tutorials.vergleich artifacts/loop/e0 artifacts/loop/e1
```

Die Ausgabe stellt Einstufung, Bestehensquote, Kosten, Werkzeugaufrufe, Tokens und Latenz nebeneinander
und listet die Szenarien, deren Ergebnis sich geändert hat. Bei Sprachmodellen gilt ein
Unterschied erst, wenn er größer ist als die Streuung über `--repeats`.

## Robustheit mit weiteren Seeds prüfen

Übungsfälle werden nach Sprint freigegeben. Die sechs Familieneinstiege stehen ab
Sprint 2 bereit, alle 18 Varianten ab Sprint 3. Sie gehören direkt zu den jeweiligen
Sprint-Sets. In Sprint 4 sind alle 80 Übungsfälle verfügbar. Zusätzliche Seed-Variationen erzeugt
[`scripts/make_variants.py`](scripts/make_variants.py); Befehle und Grenzen stehen im
[Szenarienkatalog](SCENARIOS.md#robustheit-mit-seed-variationen).

Legt eure Entwicklungs-Seeds vorab fest, friert den Agenten ein und messt anschließend
weitere Seeds. Jeder Seed bekommt eigene Varianten und Berichte; dokumentiert den Seed und
prüft auffällige Referenzlabels am Trace. `--repeats` wiederholt denselben Szenario-Seed und
misst bei einem Sprachmodell dessen Streuung.

