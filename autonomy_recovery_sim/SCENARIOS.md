# Szenarienkatalog und Sprintfreigaben

**Aktuelle Ausgabe: sprint2.** Neue Übungsfälle kommen gesammelt zum Sprintbeginn;
bisherige Fälle bleiben für Regressionstests erhalten. Zusätzliche Demo-Prüfungen
folgen erst nach dem Agent-Freeze. Ablauf: [README](../README.md#ablauf-eines-sprints).

## Freigaben zum Sprintbeginn

| Phase | Neue Fälle | Kumulativ (inkl. Familien) |
|---|---:|---:|
| Bootcamp | 3 | 3 |
| Sprint 1 | 9 | 12 |
| Sprint 2 | 16 | 28 |
| Sprint 3 | 28 | 56 |
| Sprint 4 | 25 | 81 |

`sprint*.txt` lädt alle bis dahin freigegebenen Fälle; nur die neuen mit
`batch --release sprintN --nur-neu`. Die Familien gehören direkt zu den Sprint-Sets.

## Verfügbare Sprintfälle

| Datei | Freigabe |
|---|---|
| [hannover_frei](scenarios/hannover_frei.json) | bootcamp |
| [hannover_gegenverkehr](scenarios/hannover_gegenverkehr.json) | bootcamp |
| [hannover_durchgezogen](scenarios/hannover_durchgezogen.json) | bootcamp |
| [hannover_fussgaengergruppe](scenarios/hannover_fussgaengergruppe.json) | sprint1 |
| [hannover_radverkehr](scenarios/hannover_radverkehr.json) | sprint1 |
| [hannover_schmal](scenarios/hannover_schmal.json) | sprint1 |
| [hannover_sichtweite](scenarios/hannover_sichtweite.json) | sprint1 |
| [hannover_linienbus](scenarios/hannover_linienbus.json) | sprint1 |
| [hannover_einparker](scenarios/hannover_einparker.json) | sprint1 |
| [hannover_umzug](scenarios/hannover_umzug.json) | sprint1 |
| [hannover_radfahrer_gegenrichtung](scenarios/hannover_radfahrer_gegenrichtung.json) | sprint1 |
| [hannover_polizei_handzeichen](scenarios/hannover_polizei_handzeichen.json) | sprint1 |
| [hannover_baustelle_umleitung](scenarios/hannover_baustelle_umleitung.json) | sprint2 |
| [hannover_sackgasse](scenarios/hannover_sackgasse.json) | sprint2 |
| [hannover_muelltonne](scenarios/hannover_muelltonne.json) | sprint2 |
| [hannover_objekt_falsch_abgestellt](scenarios/hannover_objekt_falsch_abgestellt.json) | sprint2 |
| [hannover_lieferwagen_faehrt_los](scenarios/hannover_lieferwagen_faehrt_los.json) | sprint2 |
| [hannover_passagier_absetzen](scenarios/hannover_passagier_absetzen.json) | sprint2 |
| [hannover_ziel_gesperrt](scenarios/hannover_ziel_gesperrt.json) | sprint2 |
| [hannover_leitstelle_offline](scenarios/hannover_leitstelle_offline.json) | sprint2 |

## Szenariofamilien F1 bis F6: Lagen, die sich ändern

Sechs Familieneinstiege werden mit Sprint 2 freigegeben, die weiteren
Varianten mit Sprint 3. Akteure können auf das Fahrzeug reagieren;
bei neuer Lage (SITUATION_CHANGED) muss der Agent anhand seiner Beobachtungen neu entscheiden.

## Verfügbare Familienvarianten

| Datei | Familie |
|---|---|
| [f1_muellabfuhr](families/f1_muellabfuhr.json) | F1 |
| [f2_fahrer_weg](families/f2_fahrer_weg.json) | F2 |
| [f3_pkw_kommt](families/f3_pkw_kommt.json) | F3 |
| [f4_frueh_zu](families/f4_frueh_zu.json) | F4 |
| [f5_frei](families/f5_frei.json) | F5 |
| [f6_stau_loest_sich](families/f6_stau_loest_sich.json) | F6 |

## VLA-Fahrstack: Selbstauskunft pruefen

Diese Fälle folgen mit Sprint 3. Die Selbstauskunft des Fahrstacks ist
eine zu prüfende Quelle; unabhängige Kamera- und Messdaten können ihr widersprechen.

## Robustheit mit Seed-Variationen

[make_variants.py](scripts/make_variants.py) variiert Positionen, Geschwindigkeiten
und Zeitpunkte der bereits freigegebenen Fälle. Dokumentiert Entwicklungs-Seeds und
weitere Seeds nach dem Freeze. Ein Seed ändert keinen Falltyp und ersetzt keine neue
Demo-Prüfung. Entscheidungen nach Szenario-ID oder Referenzlabels erfüllen die Aufgabe nicht.
