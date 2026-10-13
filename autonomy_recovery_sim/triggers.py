"""Ausloeser: Akteure reagieren auf das, was das Ego-Fahrzeug tut.

Ein Akteur kann eine Liste ``triggers`` tragen. Jeder Ausloeser feuert hoechstens einmal,
sobald **alle** Bedingungen unter ``when`` erfuellt sind, und aendert dann das Verhalten
des Akteurs (``then``)::

    "triggers": [
      {
        "when": {"ego_phase": "PASS_OBSTACLE.PULLED_OUT"},
        "then": {"start_motion": {"target_speed_mps": 3.0}},
        "hint": {"tool": "get_vla_output", "why": "Der Fahrstack erwaehnt Warnblinker und einen Fahrer."}
      }
    ]

Bedingungen: ``time_s``, ``ego_s_m``, ``ego_lateral_offset_m`` (Versatz zur eigenen
Spurmitte, links positiv), ``since_first_session_s``, ``since_horn_s`` (Sekunden seit dem
ersten Schallzeichen; jeweils ``{"gte": Zahl}``) und
``ego_phase`` (``"BEFEHL"`` oder ``"BEFEHL.PHASE"`` des laufenden Manoevers).
Folgen: ``start_motion`` (``target_speed_mps``), ``stop`` und ``cross``
(``exit_d_m``, ``speed_mps``).

Fairnessregeln, die der Szenariolader prueft:

1. **Nichts entsteht aus dem Nichts.** Ausloeser aendern nur das Verhalten von Akteuren,
   die von Anfang an existieren; Phantome loesen nichts aus.
2. **Jede Ueberraschung hat einen Hinweis.** ``hint.tool`` nennt ein Werkzeug, das den
   Hinweis vorher liefert; ``hint.why`` sagt, worauf man achten muss. Ob der Hinweis
   reicht, prueft ein Referenzagent ueber alle Varianten (``tests/test_families.py``).
3. **Akteure verhalten sich plausibel.** Tempo bleibt in den Grenzen der Art, und kein
   Ausloeser lenkt einen Akteur auf das Ego zu. Einzige Ausnahme: ein Auffahrer, der im
   Schritttempo auf das stehende Ego rollt (``hannover_auffahrer``). Er lehrt die
   Unfallmeldung ohne eigene Schuld; vermeiden kann der Agent ihn nicht.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

CONDITIONS = frozenset(
    {"time_s", "ego_s_m", "ego_lateral_offset_m", "since_first_session_s", "since_horn_s", "ego_phase"}
)
EFFECTS = frozenset({"start_motion", "stop", "cross"})
# Hoechsttempo je Art in m/s (Fairnessregel 3).
MAX_SPEED_MPS = {
    "pedestrian": 3.0,
    "animal": 4.0,
    "bicycle": 8.0,
    "car": 14.0,
    "van": 14.0,
    "truck": 12.0,
    "bus": 12.0,
    "train": 25.0,
}
CROSSING_KINDS = frozenset({"pedestrian", "animal", "bicycle"})


def validate_triggers(
    actor_id: str,
    kind: str,
    phantom: bool,
    triggers: Iterable[Mapping[str, Any]],
    known_tools: Iterable[str],
) -> tuple[dict[str, Any], ...]:
    """Prueft Form und Fairnessregeln; liefert die Ausloeser als unveraenderliche Kopie."""
    tools = set(known_tools)
    result = []
    for index, trigger in enumerate(triggers):
        where = f"{actor_id}.triggers[{index}]"
        if not isinstance(trigger, Mapping) or set(trigger) - {"when", "then", "hint"}:
            raise ValueError(f"{where}: erlaubt sind nur when, then und hint")
        if phantom:
            raise ValueError(f"{where}: Phantome duerfen nichts ausloesen (Fairnessregel 1)")
        when, then, hint = trigger.get("when"), trigger.get("then"), trigger.get("hint")
        if not isinstance(when, Mapping) or not when or set(when) - CONDITIONS:
            raise ValueError(f"{where}.when: Bedingungen aus {', '.join(sorted(CONDITIONS))}")
        for name, value in when.items():
            if name == "ego_phase":
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{where}.when.ego_phase muss ein Text wie PASS_OBSTACLE.PULLED_OUT sein")
            elif not isinstance(value, Mapping) or set(value) != {"gte"}:
                raise ValueError(f"{where}.when.{name} braucht die Form {{\"gte\": Zahl}}")
        if not isinstance(then, Mapping) or len(then) != 1 or set(then) - EFFECTS:
            raise ValueError(f"{where}.then: genau eine Folge aus {', '.join(sorted(EFFECTS))}")
        effect, settings = next(iter(then.items()))
        settings = dict(settings or {})
        limit = MAX_SPEED_MPS.get(kind)
        if effect == "start_motion":
            speed = float(settings.get("target_speed_mps", -1.0))
            if speed <= 0 or limit is None or speed > limit:
                raise ValueError(f"{where}: target_speed_mps muss zwischen 0 und {limit} m/s liegen (Fairnessregel 3)")
        if effect == "cross":
            speed = float(settings.get("speed_mps", -1.0))
            if kind not in CROSSING_KINDS or "exit_d_m" not in settings or speed <= 0 or speed > (limit or 0.0):
                raise ValueError(f"{where}: cross nur fuer Fuss-, Radverkehr und Tiere mit exit_d_m und plausiblem Tempo")
        if not isinstance(hint, Mapping) or hint.get("tool") not in tools or not str(hint.get("why", "")).strip():
            raise ValueError(f"{where}.hint: Werkzeug und Begruendung fuer den Hinweis fehlen (Fairnessregel 2)")
        result.append({"when": dict(when), "then": {effect: settings}, "hint": dict(hint)})
    return tuple(result)


def conditions_met(when: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    """``state``: aktuelle Werte der Bedingungen; ``ego_phase`` als Menge passender Texte."""
    for name, value in when.items():
        if name == "ego_phase":
            if value not in state["ego_phase"]:
                return False
            continue
        current = state.get(name)
        if current is None or current < float(value["gte"]):
            return False
    return True
