"""Hilfsbibliothek fuer Agenten im Wahrnehmungsmodell ``tracked``.

Die Funktionen rechnen nur mit den Ausgaben von ``get_tracked_objects``,
``get_sensor_coverage`` und ``get_map_context`` - nie mit der Simulation selbst. Sie
liefern dieselben Urteile wie die Lagewerkzeuge des Modells ``exact`` (Hindernis voraus,
Gegenverkehr, Sicht, Seitenabstand), aber auf **verrauschten Messungen**: Ihre Antworten
sind deshalb ebenfalls unsicher. Wer sie einsetzt, sollte wissen, was sie annehmen;
wer es besser kann, ersetzt sie.

Typischer Ablauf::

    objects = session.call_tool("get_tracked_objects")
    coverage = session.call_tool("get_sensor_coverage")
    lanes = session.call_tool("get_map_context")
    blocker = blocker_ahead(objects, lanes)
    oncoming = oncoming_conflict(objects, lanes)
    sight = lane_visibility(coverage, "ONCOMING_LANE")
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

Object = Mapping[str, Any]
VULNERABLE_LABELS = frozenset({"PEDESTRIAN", "BICYCLE", "ANIMAL"})


def best_label(obj: Object) -> tuple[str, float]:
    """Wahrscheinlichste Klasse und ihre Wahrscheinlichkeit (ohne Klassen: ``UNKNOWN``)."""
    if not obj.get("classification"):
        return "UNKNOWN", 0.0
    top = max(obj["classification"], key=lambda item: item["probability"])
    return str(top["label"]), float(top["probability"])


def label_probability(obj: Object, labels: frozenset[str] | set[str]) -> float:
    """Summe der Wahrscheinlichkeiten aller genannten Klassen."""
    return sum(float(item["probability"]) for item in obj.get("classification", ()) if item["label"] in labels)


def _lane(map_context: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    return next((lane for lane in map_context["lanes"] if lane["lane"] == name), None)


def to_lane_frame(
    position_m: Sequence[float], map_context: Mapping[str, Any], lane: str = "EGO_LANE"
) -> tuple[float, float]:
    """Punkt aus base_link in (Laengsabstand, Querablage) zur Mittellinie einer Spur.

    Der Laengsabstand zaehlt ab der Hoehe der Fahrzeugmitte, positiv nach vorn; die
    Querablage ist positiv links der Mittellinie. Die Mittellinie ist eine Polylinie mit
    10-m-Stuetzstellen; in engen Kurven ist das Ergebnis entsprechend grob.
    """
    entry = _lane(map_context, lane)
    if entry is None:
        raise ValueError(f"lane {lane} is not in the map")
    points = [tuple(point) for point in entry["centerline_m"]]
    origin_along, _ = _project(points, (0.0, 0.0))
    along, lateral = _project(points, (float(position_m[0]), float(position_m[1])))
    return along - origin_along, lateral


def _project(points: list[tuple[float, float]], point: tuple[float, float]) -> tuple[float, float]:
    best: tuple[float, float, float] | None = None  # (Abstand, along, lateral)
    travelled = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        vx, vy = bx - ax, by - ay
        length = math.hypot(vx, vy)
        if length < 1e-9:
            continue
        t = ((point[0] - ax) * vx + (point[1] - ay) * vy) / (length * length)
        clamped = min(1.0, max(0.0, t))
        px, py = ax + clamped * vx, ay + clamped * vy
        lateral = ((point[0] - ax) * -vy + (point[1] - ay) * vx) / length
        along = travelled + t * length
        distance = math.hypot(point[0] - px, point[1] - py)
        if best is None or distance < best[0]:
            best = (distance, along, lateral)
        travelled += length
    assert best is not None
    return best[1], best[2]


def objects_in_lane(
    tracked: Mapping[str, Any], lane: str, *, min_existence: float = 0.5, min_lane_probability: float = 0.5
) -> list[Object]:
    """Objekte, die der Kartenabgleich dieser Spur zuordnet."""
    return [
        obj
        for obj in tracked["objects"]
        if obj["existence_probability"] >= min_existence
        and obj["lane_association"]["lane"] == lane
        and obj["lane_association"]["probability"] >= min_lane_probability
    ]


def blocker_ahead(
    tracked: Mapping[str, Any],
    map_context: Mapping[str, Any],
    *,
    max_distance_m: float = 25.0,
    min_existence: float = 0.5,
) -> Object | None:
    """Naechstes stehendes Objekt voraus, das in die eigene Spur ragt (oder ``None``).

    Zaehlt die Spurzuordnung des Trackers oder die Breite: Eine Absperrung ueber die ganze
    Strasse liegt mit ihrer Mitte auf der Mittellinie, ragt aber in die eigene Spur.
    """
    ego_lane = _lane(map_context, "EGO_LANE")
    half_lane = float(ego_lane["width_m"]) / 2.0 if ego_lane is not None else 1.75
    candidates = []
    for obj in tracked["objects"]:
        if obj["existence_probability"] < min_existence or obj["kinematics"]["motion_state"] != "STATIONARY":
            continue
        along, offset = to_lane_frame(obj["kinematics"]["position_m"], map_context)
        half_width = float(obj["shape"]["dimensions_m"][1]) / 2.0
        assigned = obj["lane_association"]["lane"] == "EGO_LANE" and obj["lane_association"]["probability"] >= 0.5
        if not assigned and abs(offset) >= half_width + half_lane:
            continue
        if 0.0 < along <= max_distance_m:
            candidates.append((along, obj))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def oncoming_conflict(
    tracked: Mapping[str, Any],
    map_context: Mapping[str, Any],
    *,
    horizon_s: float = 8.0,
    min_existence: float = 0.3,
    sigma: float = 2.0,
    assumed_ego_speed_mps: float = 2.0,
) -> dict[str, Any]:
    """Gegenverkehr, der innerhalb des Horizonts den Ueberholweg erreicht.

    Konservativ: Der Abstand wird um ``sigma`` Standardabweichungen verkuerzt, und schon
    schwache Tracks (``min_existence``) zaehlen. Ein Fahrzeug, dessen Bewegung unbekannt
    ist (nur Radar), gilt als moeglicherweise entgegenkommend mit 8 m/s.
    """
    hits = []
    for obj in objects_in_lane(tracked, "ONCOMING_LANE", min_existence=min_existence, min_lane_probability=0.3):
        kinematics = obj["kinematics"]
        along, _ = to_lane_frame(kinematics["position_m"], map_context, "ONCOMING_LANE")
        if along <= 0:
            continue
        distance = max(0.0, along - sigma * float(kinematics["position_std_m"][0]))
        if kinematics["motion_state"] == "UNKNOWN":
            approach = 8.0
        else:
            approach = max(0.0, -float(kinematics["velocity_mps"][0]))
        closing = approach + assumed_ego_speed_mps
        ttc = distance / closing if closing > 0 else math.inf
        if ttc <= horizon_s:
            hits.append((ttc, obj))
    if not hits:
        return {"conflict": False, "horizon_s": horizon_s, "nearest_ttc_s": None}
    ttc, obj = min(hits, key=lambda item: item[0])
    return {
        "conflict": True,
        "horizon_s": horizon_s,
        "nearest_ttc_s": round(ttc, 2),
        "track_id": obj["track_id"],
        "existence_probability": obj["existence_probability"],
    }


def rear_conflict(
    tracked: Mapping[str, Any],
    map_context: Mapping[str, Any],
    *,
    lane: str = "ONCOMING_LANE",
    horizon_s: float = 6.0,
    min_existence: float = 0.3,
) -> dict[str, Any]:
    """Ein Fahrzeug, das von hinten auf der Zielspur aufschliesst (etwa ein Ueberholer)."""
    hits = []
    for obj in objects_in_lane(tracked, lane, min_existence=min_existence, min_lane_probability=0.3):
        kinematics = obj["kinematics"]
        along, _ = to_lane_frame(kinematics["position_m"], map_context, lane)
        closing = float(kinematics["velocity_mps"][0])
        if along >= 0 or closing <= 0.3:
            continue
        ttc = -along / closing
        if ttc <= horizon_s:
            hits.append((ttc, obj))
    if not hits:
        return {"conflict": False, "horizon_s": horizon_s, "nearest_ttc_s": None}
    ttc, obj = min(hits, key=lambda item: item[0])
    return {"conflict": True, "horizon_s": horizon_s, "nearest_ttc_s": round(ttc, 2), "track_id": obj["track_id"]}


def vulnerable_road_users(
    tracked: Mapping[str, Any],
    map_context: Mapping[str, Any],
    *,
    corridor_ahead_m: float = 35.0,
    min_probability: float = 0.3,
) -> dict[str, Any]:
    """Fuss- und Radverkehr oder Tiere auf der Fahrbahn im Korridor voraus."""
    found = []
    for obj in tracked["objects"]:
        if obj["existence_probability"] < 0.3 or label_probability(obj, VULNERABLE_LABELS) < min_probability:
            continue
        if obj["lane_association"]["lane"] == "OFF_ROAD":
            continue
        along, _ = to_lane_frame(obj["kinematics"]["position_m"], map_context)
        if -3.0 <= along <= corridor_ahead_m:
            found.append(obj["track_id"])
    return {"conflict": bool(found), "track_ids": found}


def lane_visibility(coverage: Mapping[str, Any], lane: str, *, required_m: float = 60.0) -> dict[str, Any]:
    """Wie weit eine Spur voraus ohne Verdeckung einsehbar ist."""
    reach = min(float(coverage["max_reliable_range_m"]["lidar"]), float(coverage["atmospheric_visibility_m"]))
    first_gap = min(
        (
            float(region["from_m"])
            for region in coverage["occluded_regions"]
            # Verdeckungen unter 1 m Laenge sind Messartefakte an Objektkanten.
            if region["lane"] == lane and float(region["to_m"]) > 0.0
            and float(region["to_m"]) - float(region["from_m"]) >= 1.0
        ),
        default=math.inf,
    )
    visible = max(0.0, min(reach, first_gap))
    return {"lane": lane, "visible_m": round(visible, 1), "required_m": required_m, "sufficient": visible >= required_m}


def lateral_clearance(
    blocker: Object,
    map_context: Mapping[str, Any],
    *,
    ego_width_m: float,
    sigma: float = 2.0,
) -> dict[str, Any]:
    """Freier Raum links am Hindernis vorbei bis zum Rand der Gegenspur."""
    ego_lane = _lane(map_context, "EGO_LANE")
    oncoming = _lane(map_context, "ONCOMING_LANE")
    assert ego_lane is not None and oncoming is not None
    _, offset = to_lane_frame(blocker["kinematics"]["position_m"], map_context)
    width = float(blocker["shape"]["dimensions_m"][1])
    left_edge = offset + width / 2.0 + sigma * float(blocker["kinematics"]["position_std_m"][1])
    road_left = float(ego_lane["width_m"]) / 2.0 + float(oncoming["width_m"])
    available = road_left - left_edge
    return {
        "available_m": round(available, 2),
        "needed_m": round(ego_width_m, 2),
        "margin_m": round(available - ego_width_m, 2),
    }
