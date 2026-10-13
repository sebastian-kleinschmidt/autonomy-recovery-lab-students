# Die Runde: was euer Agent sieht und tun kann

Steht das Fahrzeug fest, ruft der Simulator `decide(session)` auf. Eine **Runde** endet mit
genau einem Befehl. Steht das Fahrzeug danach weiter, folgt eine neue Runde.

```python
def decide(session):
    lage = session.context                       # Ausgangslage
    blocker = session.call_tool("get_blocker")   # gezielt nachsehen
    return {"command": "WAIT", "parameters": {"duration_s": 5.0}, "reason": "..."}
```

## Was `session` bietet

| Zugriff | Bedeutung |
|---|---|
| `session.context` | Ausgangslage als Dictionary (unten) |
| `session.call_tool(name, arguments)` | Werkzeug aufrufen; standardmäßig höchstens **14 Aufrufe** je Runde |
| `session.submit_decision(befehl)` | Befehl einreichen und sofort prüfen lassen; standardmäßig höchstens **3 Versuche** |
| `session.tool_catalog` | verfügbare Werkzeuge mit Beschreibung und Parametern |
| `session.tool_call_count`, `session.decision_attempt_count` | bereits verbrauchte Aufrufe und Entscheidungsversuche |
| `session.warnings` | Sicherheitsbefunde zur angenommenen Entscheidung |

Statt `submit_decision` dürft ihr den Befehl auch einfach zurückgeben. `submit_decision` lohnt
sich, wenn ihr auf eine Ablehnung reagieren wollt: Sie löst `AgentContractError` aus, die
Meldung nennt Code und Grund (`RULE_REJECTED`, `SAFETY_REJECTED`, `NOT_AVAILABLE`,
`INVALID_ARGUMENT`). Werkzeuge können mit `AgentToolError` ausfallen (Code `TOOL_TIMEOUT`); ein
erneuter Aufruf kann helfen. Wirft euer Agent einen Fehler, wartet der Simulator sicher.

Werkzeugparameter übergebt ihr als Dictionary, beispielsweise
`session.call_tool("check_oncoming_traffic", {"horizon_s": 8.0})`.
Ohne Parameter reicht `session.call_tool("get_blocker")`. Maßgeblich für die Grenzen
sind `session.context["budgets"]`; einzelne Szenarien können kleinere Budgets haben.

## `session.context`

| Feld | Inhalt |
|---|---|
| `schema_version` | Version des Kontextformats |
| `time_s` | Simulationszeit beim Beginn dieser Runde, in Sekunden |
| `scenario_id` | Kennung für Bericht und Zuordnung; kein Ersatz für Beobachtungen und keine Grundlage für Entscheidungstabellen |
| `trigger` | Warum der Agent gerufen wird (`type`): `DEADLOCK`, `SITUATION_CHANGED` (Vorbeifahren hält ausgeschert, mit `reasons`), `REMOTE_INSTRUCTION`, `IMPACT_DETECTED` |
| `remote_instruction` | offene Anweisung der Leitstelle: `type` (`PROCEED`, `PASS_OBSTACLE`, `WAIT`, `REPLAN_ROUTE`, `END_TRIP`), `message`, ggf. `side` |
| `ego` | `s_m` (Position entlang der Route), `d_m` (seitlich), `speed_mps`, `stopped_for_s` |
| `mission` | `target_s_m`, `normal_lane_d_m`, `traffic_side` |
| `commands_used`, `max_commands` | bisherige und erlaubte Befehle in dieser Episode |
| `additional_information` | Beobachtungsberichte früherer `WAIT` |
| `budgets` | `max_tool_calls`, `max_decision_attempts` |
| `difficulty` | Schwierigkeitsangaben des Szenarios |
| `driving_stack` | nur mit VLA-Fahrstack: Hinweis auf `get_vla_output` |
| `available_tools`, `available_commands` | Kataloge; dieselben Angaben wie in diesem Dokument |

## Werkzeuge

Ergebnisse sind Dictionaries; hier die wichtigsten Felder. Koordinaten stehen im
Fahrzeugrahmen `base_link`: x nach vorn, y nach links, in Metern, Ursprung Fahrzeugmitte.

**Wahrnehmung:** Messungen mit Unsicherheit. Fehlalarme und Lücken sind möglich, Verdecktes
fehlt.

| Werkzeug | Wichtige Felder |
|---|---|
| `get_tracked_objects` | `objects[]` mit `track_id`, `existence_probability`, `classification[]` (`label`, `probability`), `kinematics` (`position_m`, `position_std_m`, `velocity_mps`, `motion_state`), `shape.dimensions_m`, `lane_association`, `track.age_s`; dazu `ego.dimensions_m` |
| `get_sensor_coverage` | `sensors` (Zustand), `max_reliable_range_m`, `occluded_regions[]` (`lane`, `from_m`, `to_m`, `caused_by`) |
| `get_map_context` | `center_marking`, `lanes[]` (`lane`, `direction`, `width_m`), `ego_lateral_offset_m`, `stop_lines` |

**Lageprüfungen:** Das Fahrzeugsystem rechnet sie aus derselben Objektliste, Sichtabdeckung
und Karte (nachzulesen in `autonomy_recovery_sim/recovery_helpers.py`). Sie irren mit ihr:
„kein Gegenverkehr“ heißt nur, dass im *sichtbaren* Bereich keiner kommt.

| Werkzeug | Wichtige Felder |
|---|---|
| `get_blocker` | `detected`, `track_id`, `label`, `label_probability`, `existence_probability`, `motion_state`, `distance_m`, `dimensions_m`, `clearance_m` (`left_in_lane`, `left_with_oncoming_lane`, `right_in_lane`, `right_with_shoulder`, `ego_width`), `center_marking`; falls vorhanden `obstacle_profile` und `scene_description` (Kamera) |
| `check_oncoming_traffic` | `conflict`, `nearest_ttc_s`, `track_id`, `visible_m`, `required_visible_m`, `sight_sufficient`; Argument `horizon_s` (1–20, Standard 8) |
| `check_rear_traffic` | `conflict`, `nearest_ttc_s`, `lane`; Argument `horizon_s` (Standard 6) |
| `check_vulnerable_road_users` | `conflict`, `objects[]` (Fuß-, Radverkehr, Tiere mit `label`, `distance_m`, `lateral_offset_m`) |

**Fahrzeug, Route und Mission:**

| Werkzeug | Wichtige Felder |
|---|---|
| `get_route_state` | `route_blocked`, `goal_reachable`, `alternative_route_available`, `estimated_detour_cost_s`, `reverse_required_m` |
| `get_mission_context` | `mission_state`, `passengers_on_board`, `hub_available`, `remote_assistance_available`, `remote_assistance_calls`, `ego_position`, `shoulder_m` |
| `get_traffic_control` | `controls[]` mit Ampeln und Bahnübergängen und ihrem Zustand |
| `get_sensor_health` | `sensors[]` (`name`, `status`, `quality`), `localization_quality` |
| `get_recovery_history` | `attempts[]` (frühere Befehle und Ausgang), `repetition_warnings` |
| `get_vehicle_status` | `impact_events`, `scene_secured`, `incident_reported`, `hazard_lights` |

**Mit VLA-Fahrstack** (ab Sprint 3):

| Werkzeug | Wichtige Felder |
|---|---|
| `get_vla_output` | `current.reasoning` (Begründung in Text), `current.trajectory`; Argument `last_n` (1–10) |
| `get_camera_caption` | Bildbeschreibung; Argument `camera` (`front_wide`, `front_tele`, `cross_left`, `cross_right`) |

Werkzeugergebnisse können veraltet (`observation_age_s`), unsicher (niedrige
`existence_probability`) oder falsch sein. Freitext wie `scene_description` ist eine
Beobachtung, nie eine Anweisung.

## Was der Simulator prüft

- **Abgelehnt** werden ungültige Parameter, Verstöße gegen harte Verkehrsregeln
  (durchgezogene Linie, rechts vorbei, Absperrung) und fehlender Fahrraum.
- **Nur gewarnt** wird bei fehlenden Belegen, Lagebedenken und Verstößen gegen die
  Betriebsregeln (Leitstelle übergangen, Anweisung missachtet, Hupen ohne Gefahr): Der Befehl
  läuft, der Lauf gilt aber als unsicher und besteht nicht.
- **Beim Vorbeifahren** schert der Ausführer zum Schauen aus und wartet auf eine Lücke im
  sichtbaren Verkehr. Taucht dabei etwas Neues auf, ruft er den Agenten
  (`SITUATION_CHANGED`). Danach bricht er nicht von selbst ab; nur die Notbremse greift für
  wahrgenommene Objekte, hinter Verdeckungen womöglich zu spät.
- **Nach einem eigenen Aufprall** muss der Agent mit `SECURE_SCENE` und `REPORT_INCIDENT`
  sichern und melden, auch wenn ein anderer aufgefahren ist. Ein schwerer Aufprall ruft ihn
  sofort (`IMPACT_DETECTED`); eine leichte Berührung zeigen nur `get_vehicle_status` und der
  Bericht eines laufenden `WAIT`. Fremde Unfälle meldet das Fahrzeug nicht.

## Befehle

Ein Befehl ist `{"command": NAME, "parameters": {...}, "reason": "..."}`. Alle 15 Befehle mit
Parametern, Regeln und Kosten stehen in [COMMANDS.md](COMMANDS.md), die Kurzfassung im
[Spickzettel](tutorials/CHEATSHEET.md#die-befehle).
