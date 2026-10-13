"""CPU-Mock eines Vision-Language-Action-Fahrstacks (angelehnt an NVIDIA Alpamayo 1.5).

Alpamayo 1.5 (``nvidia/Alpamayo-1.5-10B``, das Modell des Labors) liest vier Kameras
(front-wide, front-tele, cross-left, cross-right; 0,4 s Verlauf bei 10 Hz) und die
Eigenbewegung und liefert pro Inferenz eine Begruendung in natuerlicher Sprache
("Chain of Causation") und eine Trajektorie ueber 6,4 s (64 Wegpunkte bei 10 Hz). Es nimmt
Nutzerbefehle und Navigationshinweise als Text an und beantwortet Fragen zur Szene.

Der Mock bildet diese Schnittstelle nach: ``get_vla_output`` entspricht Begruendung und
Trajektorie, ``get_camera_caption`` einer Frage an das Modell, was eine Kamera zeigt.
``meta_action`` ist keine Ausgabe von Alpamayo 1.5, sondern eine vom Simulator abgeleitete
Kurzform der Trajektorie (grobe Meta-Aktionen liefert erst Alpamayo 2 Super).

Dieser Mock rechnet kein Modell. Er uebersetzt die Fahrabsicht des Simulators
(``PlannerIntent``) regelbasiert in genau diese Ausgabeform und kann typische
Schwaechen solcher Modelle gezielt einspielen: erfundene Ursachen, Text und
Trajektorie passen nicht zusammen, vage Objektbeschreibungen und
Ueberkonfidenz. Er sieht nur, was die Wahrnehmung sieht; verdeckte Ursachen
bleiben im Text vage. Alles ist deterministisch (Seed) und braucht keine GPU.

Die Ausgabe ist eine weitere, unzuverlaessige Quelle fuer den Recovery Agent,
keine Wahrheit: Der Agent soll sie mit den Lagewerkzeugen abgleichen.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .models import ActorState
from .perception import PerceptionSnapshot
from .scenario import Scenario, VlaSpec

HORIZON_STEPS = 64
STEP_S = 0.1
HISTORY_INTERVAL_S = 1.0
HISTORY_LENGTH = 30
CAMERAS: dict[str, dict[str, float]] = {
    # Blickrichtung und halber Oeffnungswinkel in Grad (Ego-Koordinaten, links positiv).
    "front_wide": {"heading_deg": 0.0, "half_fov_deg": 60.0, "detail_range_m": 25.0},
    "front_tele": {"heading_deg": 0.0, "half_fov_deg": 15.0, "detail_range_m": 80.0},
    "cross_left": {"heading_deg": 90.0, "half_fov_deg": 60.0, "detail_range_m": 20.0},
    "cross_right": {"heading_deg": -90.0, "half_fov_deg": 60.0, "detail_range_m": 20.0},
}
META_ACTIONS = frozenset(
    {
        "FOLLOW_LANE",
        "FOLLOW_VEHICLE",
        "YIELD",
        "STOP",
        "NUDGE_LEFT",
        "NUDGE_RIGHT",
        "CREEP",
        "REVERSE",
        "PULL_OVER",
        "MANUAL",
    }
)

KIND_PHRASES: dict[str, tuple[str, str]] = {
    # (genaue Beschreibung, grobe Beschreibung aus der Ferne)
    "car": ("a parked car", "a vehicle"),
    "van": ("a stopped delivery van", "a vehicle"),
    "truck": ("a stationary truck", "a large vehicle"),
    "bus": ("a stopped bus", "a large vehicle"),
    "debris": ("an object lying on the road", "something on the road"),
    "beacon": ("a construction beacon", "a small upright object"),
    "trash_bin": ("a trash bin", "a small upright object"),
    "barrier": ("a road barrier", "a barrier-like structure"),
    "pedestrian": ("pedestrians", "people"),
    "bicycle": ("a cyclist", "a two-wheeler"),
    "animal": ("an animal", "a moving shape"),
    "train": ("a train", "a large moving object"),
}
VAGUE_PHRASE = "an unidentified obstruction"


@dataclass(frozen=True)
class PlannerIntent:
    """Was der Fahrstack gerade tut und warum (aus Sicht des Simulators)."""

    meta_action: str
    cause: str
    target_speed_mps: float
    stop_distance_m: float | None = None
    actor: ActorState | None = None
    control: dict[str, Any] | None = None
    command: str | None = None
    target_d_m: float | None = None
    # Abstand Stossstange-Objekt fuer den Begruendungstext (nicht der Anhalteweg).
    distance_m: float | None = None
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class VlaOutput:
    t_s: float
    reasoning: str
    meta_action: str
    confidence: float
    trajectory_spread_m: float
    trajectory_xy: tuple[tuple[float, float], ...]
    trajectory_ego: tuple[tuple[float, float], ...]
    instruction: str | None
    stationary: bool

    def summary(self, now_s: float) -> dict[str, object]:
        end_x, end_y = self.trajectory_ego[-1]
        length = sum(
            math.dist(a, b)
            for a, b in zip(((0.0, 0.0),) + self.trajectory_ego, self.trajectory_ego)
        )
        return {
            "t_s": round(self.t_s, 1),
            "output_age_s": round(max(0.0, now_s - self.t_s), 1),
            "reasoning": self.reasoning,
            "meta_action": self.meta_action,
            "confidence": round(self.confidence, 2),
            "instruction": self.instruction,
            "trajectory": {
                "horizon_s": round(HORIZON_STEPS * STEP_S, 1),
                "waypoints": HORIZON_STEPS,
                "path_length_m": round(length, 1),
                "end_point_ego_m": [round(end_x, 1), round(end_y, 1)],
                "stationary": self.stationary,
                "spread_m": round(self.trajectory_spread_m, 2),
                # Jeder achte Wegpunkt (0,8 s Abstand) in Ego-Koordinaten: x vorne, y links.
                "sampled_points_ego_m": (
                    []
                    if self.stationary
                    else [[round(x, 1), round(y, 1)] for x, y in self.trajectory_ego[7::8]]
                ),
            },
        }


@dataclass
class _Timeline:
    history: deque[VlaOutput] = field(default_factory=lambda: deque(maxlen=HISTORY_LENGTH))
    last_history_t_s: float = float("-inf")
    stationary_since_s: float | None = None


def _active(fault: dict[str, Any], time_s: float, actor_id: str | None = None) -> bool:
    if time_s < float(fault.get("from_s", 0.0)):
        return False
    until = fault.get("until_s")
    if until is not None and time_s >= float(until):
        return False
    wanted = fault.get("actor_id")
    return wanted is None or wanted == actor_id


class VlaMock:
    """Erzeugt Begruendung, Meta-Aktion und Trajektorie wie ein VLA-Fahrstack."""

    def __init__(self, scenario: Scenario, spec: VlaSpec):
        self.scenario = scenario
        self.spec = spec
        self.period_s = max(STEP_S, math.ceil(spec.latency_ms / 100.0 - 1e-9) * STEP_S)
        self.latest: VlaOutput | None = None
        self._timeline = _Timeline()

    # ----------------------------------------------------------------- Ablauf

    def due(self, time_s: float) -> bool:
        """Neue Inferenz erst nach Ablauf der Latenz; dazwischen gilt die letzte Ausgabe."""
        return self.latest is None or time_s - self.latest.t_s >= self.period_s - 1e-9

    def update(
        self,
        time_s: float,
        intent: PlannerIntent,
        ego_pose: tuple[float, float, float, float, float, float],
        perception: PerceptionSnapshot | None,
    ) -> VlaOutput:
        """``ego_pose`` = (x, y, yaw, s, d, speed)."""
        if not self.due(time_s):
            assert self.latest is not None
            return self.latest
        output = self._infer(time_s, intent, ego_pose, perception)
        self.latest = output
        timeline = self._timeline
        # Gemessen an der Trajektorie, nicht am (moeglicherweise falschen) Text.
        if output.stationary:
            if timeline.stationary_since_s is None:
                timeline.stationary_since_s = time_s
        else:
            timeline.stationary_since_s = None
        if time_s - timeline.last_history_t_s >= HISTORY_INTERVAL_S - 1e-9:
            timeline.history.append(output)
            timeline.last_history_t_s = time_s
        return output

    def report(self, now_s: float, last_n: int = 5) -> dict[str, object]:
        """Ergebnis des Werkzeugs ``get_vla_output``."""
        if self.latest is None:
            return {"available": False}
        # Gleichlautende Ausgaben hintereinander werden zu einem Zeitraum zusammengefasst.
        recent: list[dict[str, object]] = []
        for item in self._timeline.history:
            previous = recent[-1] if recent else None
            if (
                previous is not None
                and previous["meta_action"] == item.meta_action
                and previous["reasoning"] == item.reasoning
            ):
                previous["to_s"] = round(item.t_s, 1)
                continue
            recent.append(
                {
                    "from_s": round(item.t_s, 1),
                    "to_s": round(item.t_s, 1),
                    "meta_action": item.meta_action,
                    "confidence": round(item.confidence, 2),
                    "reasoning": item.reasoning,
                }
            )
        since = self._timeline.stationary_since_s
        return {
            "available": True,
            "source": "vla_mock (modelled on Alpamayo 1.5, not a real model)",
            "current": self.latest.summary(now_s),
            "stationary_plan_for_s": 0.0 if since is None else round(now_s - since, 1),
            "recent": recent[-last_n:],
        }

    def snapshot(self, now_s: float) -> dict[str, object] | None:
        """Kompakte Sicht fuer die Live-Oberflaeche (Trajektorie in Weltkoordinaten)."""
        if self.latest is None:
            return None
        return {
            **self.latest.summary(now_s),
            "trajectory_xy": [[round(x, 2), round(y, 2)] for x, y in self.latest.trajectory_xy],
            "perception_interface": self.spec.perception_interface,
        }

    # -------------------------------------------------------------- Inferenz

    def _faults(self, fault_type: str, time_s: float, actor_id: str | None = None) -> list[dict[str, Any]]:
        return [
            fault
            for fault in self.spec.faults
            if fault["type"] == fault_type and _active(fault, time_s, actor_id)
        ]

    def _rng(self, key: str) -> random.Random:
        # Stabil pro Ursache: Der Text flackert nicht bei jeder Inferenz.
        return random.Random(f"{self.spec.seed}:{self.scenario.seed}:{key}")

    def _describe_actor(
        self,
        actor: ActorState | None,
        perception: PerceptionSnapshot | None,
        time_s: float,
        distance_m: float | None,
    ) -> tuple[str, float]:
        """Objektbeschreibung und Konfidenz, wie ein Kameramodell sie liefern wuerde."""
        if actor is None:
            return VAGUE_PHRASE, 0.4
        metadata = perception.actor_metadata.get(actor.spec.actor_id) if perception else None
        if metadata is None:
            return VAGUE_PHRASE, 0.35
        if self._faults("VAGUE_CAUSE", time_s, actor.spec.actor_id):
            return VAGUE_PHRASE, 0.5
        precise, coarse = KIND_PHRASES.get(actor.spec.kind, ("an obstacle", "an obstacle"))
        visible = float(metadata.get("visible_fraction", 0.0))
        confidence = actor.spec.confidence * (0.6 + 0.4 * visible)
        overconfident = bool(self._faults("OVERCONFIDENT", time_s, actor.spec.actor_id))
        if actor.spec.confidence < 0.5 and not overconfident:
            return "a faint, unclear shape", confidence
        if distance_m is not None and distance_m > 40.0:
            return coarse, confidence * 0.9
        return precise, confidence

    def _cause_sentence(
        self,
        intent: PlannerIntent,
        perception: PerceptionSnapshot | None,
        time_s: float,
    ) -> tuple[str, float]:
        cause = intent.cause
        distance = intent.distance_m
        if cause == "blocker" or cause == "vulnerable" or cause == "lead_vehicle":
            phrase, confidence = self._describe_actor(intent.actor, perception, time_s, distance)
            where = "in the ego lane" if cause != "vulnerable" else "near the planned path"
            if distance is not None:
                where += f" about {max(distance, 0.0):.0f} m ahead"
            if cause == "blocker":
                variants = (
                    f"{phrase.capitalize()} blocks the path {where}",
                    f"There is {phrase} {where} that leaves no room to proceed",
                    f"The lane is obstructed by {phrase} {where}",
                )
            elif cause == "vulnerable":
                variants = (
                    f"{phrase.capitalize()} {where} may enter the path",
                    f"I observe {phrase} {where}",
                )
            else:
                variants = (
                    f"{phrase.capitalize()} is driving ahead {where}",
                    f"I am following {phrase} {where}",
                )
            key = f"{cause}:{intent.actor.spec.actor_id if intent.actor else '-'}"
            return self._rng(key).choice(variants), confidence
        if cause == "traffic_control" and intent.control is not None:
            state = str(intent.control.get("state"))
            kind = "railway crossing" if intent.control.get("type") == "RAILWAY_CROSSING" else "traffic light"
            text = {
                "RED": f"The {kind} ahead is red",
                "YELLOW": f"The {kind} ahead turns yellow",
                "CLOSED": f"The {kind} ahead is closed",
                "DARK": f"The {kind} ahead appears to be switched off",
            }.get(state, f"The {kind} ahead is in state {state.lower()}")
            return text, 0.55 if state == "DARK" else 0.9
        if cause == "planned_stop":
            return "The planned passenger stop is reached", 0.95
        if cause == "pulled_over":
            return "The vehicle is parked at the road edge after pulling over", 0.9
        if cause == "maneuver_aborted":
            return "The previous maneuver was aborted for safety", 0.85
        if cause == "recovery_command":
            return f"An approved recovery instruction is being executed ({intent.command})", 0.85
        if cause == "manual":
            return "The vehicle is under manual control", 1.0
        return "The lane ahead is clear", 0.9

    def _decision_sentence(self, intent: PlannerIntent) -> str:
        return {
            "STOP": "so I stop and wait",
            "YIELD": "so I slow down and yield",
            "FOLLOW_VEHICLE": "so I keep a safe following distance",
            "FOLLOW_LANE": "so I continue along the lane",
            "NUDGE_LEFT": "so I pass it slowly on the left",
            "NUDGE_RIGHT": "so I pass it slowly on the right",
            "CREEP": "so I creep forward at walking speed",
            "REVERSE": "so I reverse slowly",
            "PULL_OVER": "so I pull over to the road edge",
            "MANUAL": "so I do not plan",
        }.get(intent.meta_action, "so I proceed carefully")

    def _infer(
        self,
        time_s: float,
        intent: PlannerIntent,
        ego_pose: tuple[float, float, float, float, float, float],
        perception: PerceptionSnapshot | None,
    ) -> VlaOutput:
        trajectory_xy, trajectory_ego = self._rollout(intent, ego_pose)
        stationary = math.dist(trajectory_ego[0], trajectory_ego[-1]) < 0.5
        cause_text, confidence = self._cause_sentence(intent, perception, time_s)
        meta_action = intent.meta_action

        hallucinated = self._faults("HALLUCINATED_CAUSE", time_s)
        if hallucinated and intent.cause not in {"clear", "manual"}:
            cause_text = str(hallucinated[0]["text"]).strip().rstrip(".")
            cause_text = cause_text[0].upper() + cause_text[1:]
            confidence = max(confidence, 0.8)
        reasoning = f"{cause_text}, {self._decision_sentence(intent)}."
        context = [sentence for sentence in intent.context if sentence]
        if context:
            reasoning += " " + " ".join(context)

        if self._faults("REASONING_ACTION_MISMATCH", time_s) and intent.cause != "manual":
            # Text und Meta-Aktion behaupten Fahrt, die Trajektorie haelt an (oder umgekehrt).
            if stationary:
                reasoning = "The path ahead is clear, so I continue along the lane at cruise speed."
                meta_action = "FOLLOW_LANE"
            else:
                reasoning = "The path is blocked, so I stop and wait."
                meta_action = "STOP"

        actor_id = intent.actor.spec.actor_id if intent.actor else None
        if self._faults("OVERCONFIDENT", time_s, actor_id):
            confidence = 0.97
        else:
            # Auch ein sicheres Modell meldet selten mehr als 0,95.
            confidence = min(confidence, 0.95)
        confidence = min(0.97, max(0.05, confidence))
        spread = 0.1 + (1.0 - confidence) * 4.0
        return VlaOutput(
            t_s=time_s,
            reasoning=reasoning,
            meta_action=meta_action,
            confidence=confidence,
            trajectory_spread_m=spread,
            trajectory_xy=trajectory_xy,
            trajectory_ego=trajectory_ego,
            instruction=intent.command,
            stationary=stationary,
        )

    def _rollout(
        self,
        intent: PlannerIntent,
        ego_pose: tuple[float, float, float, float, float, float],
    ) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]:
        """Einfache kinematische Vorausschau entlang der Route (Beschleunigung/Kruemmung)."""
        x0, y0, yaw, s_m, d_m, speed = ego_pose
        road = self.scenario.road
        target_d = d_m if intent.target_d_m is None else intent.target_d_m
        reverse = intent.meta_action == "REVERSE"
        stop_remaining = intent.stop_distance_m
        v = abs(speed)
        s, d = s_m, d_m
        world: list[tuple[float, float]] = []
        ego: list[tuple[float, float]] = []
        cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
        for _ in range(HORIZON_STEPS):
            desired = intent.target_speed_mps
            if stop_remaining is not None:
                desired = min(desired, math.sqrt(2.0 * 2.0 * max(0.0, stop_remaining - 0.2)))
            if desired > v:
                v = min(desired, v + 1.5 * STEP_S)
            else:
                v = max(desired, v - 3.0 * STEP_S)
            step = v * STEP_S
            if stop_remaining is not None:
                step = min(step, max(0.0, stop_remaining))
                stop_remaining -= step
            s += -step if reverse else step
            d += (target_d - d) * min(1.0, STEP_S / 1.5)
            s = min(max(s, 0.0), road.length_m)
            x, y, _ = road.to_xy(s, d)
            world.append((x, y))
            dx, dy = x - x0, y - y0
            ego.append((dx * cos_yaw + dy * sin_yaw, -dx * sin_yaw + dy * cos_yaw))
        return tuple(world), tuple(ego)

    # ---------------------------------------------------------- Kamerabilder

    def camera_caption(
        self,
        camera: str,
        time_s: float,
        ego_pose: tuple[float, float, float, float, float, float],
        perception: PerceptionSnapshot | None,
        controls: list[dict[str, Any]],
    ) -> dict[str, object]:
        """Ersatz fuer ein Kamerabild: kurze Bildbeschreibung aus Sicht einer Kamera."""
        setup = CAMERAS[camera]
        x0, y0, yaw, s_m, _, _ = ego_pose
        road = self.scenario.road
        objects: list[tuple[float, str]] = []
        for actor in perception.actors if perception else ():
            ax, ay, _ = road.to_xy(actor.s_m, actor.d_m)
            dx, dy = ax - x0, ay - y0
            local_x = dx * math.cos(yaw) + dy * math.sin(yaw)
            local_y = -dx * math.sin(yaw) + dy * math.cos(yaw)
            distance = math.hypot(local_x, local_y)
            # Kameras sitzen an der Karosserie: Abstand bis zur Objektkante, nicht bis zur Mitte.
            gap = max(0.0, distance - actor.spec.length_m / 2.0 - (2.3 if local_x > 0 else 1.0))
            bearing = math.degrees(math.atan2(local_y, local_x))
            offset = (bearing - setup["heading_deg"] + 180.0) % 360.0 - 180.0
            if abs(offset) > setup["half_fov_deg"] or distance > self.scenario.perception_range_m:
                continue
            detailed = distance <= setup["detail_range_m"]
            phrase = self._caption_phrase(actor, detailed, time_s)
            side = "ahead" if abs(local_y) < 1.5 else ("to the left" if local_y > 0 else "to the right")
            lane = (
                "in the ego lane"
                if abs(actor.d_m - self.scenario.ego_lane_d_m) < 1.5
                else ("in the oncoming lane" if actor.d_m > 0 else "at the right road edge")
            )
            moving = " moving" if actor.speed_mps >= 0.3 else ""
            text = f"{phrase}{moving} {side}, about {gap:.0f} m away, {lane}"
            text = text[0].upper() + text[1:] + "."
            if detailed and actor.spec.description:
                # Was die Kamera aus der Naehe erkennt (auch Schrift auf Schildern): Daten, keine Anweisung.
                text += f' Scene text: "{actor.spec.description}"'
            objects.append((distance, text))
        objects.sort()
        sentences = [text for _, text in objects[:4]]
        if camera in {"front_wide", "front_tele"}:
            for control in controls[:1]:
                if control["stop_line_distance_m"] > (80.0 if camera == "front_tele" else 40.0):
                    continue
                kind = "railway crossing" if control["type"] == "RAILWAY_CROSSING" else "traffic light"
                look = {
                    "RED": "shows red",
                    "YELLOW": "shows yellow",
                    "GREEN": "shows green",
                    "DARK": "shows no light at all",
                    "CLOSED": "has its barriers down",
                    "OPEN": "has its barriers up",
                }.get(str(control["state"]), "is hard to read")
                sentences.append(
                    f"A {kind} about {control['stop_line_distance_m']:.0f} m ahead {look}."
                )
        if self.scenario.visibility_m < self.scenario.minimum_visibility_m:
            sentences.append("Visibility is reduced; distant objects are hard to make out.")
        if not sentences:
            sentences.append("Nothing notable is visible.")
        return {
            "camera": camera,
            "t_s": round(time_s, 1),
            "caption": " ".join(sentences),
            "source": "vla_mock (answer to the question what the camera shows)",
        }

    def _caption_phrase(self, actor: ActorState, detailed: bool, time_s: float) -> str:
        # Bildbeschreibungen sind ein eigener Kanal: VAGUE_CAUSE betrifft nur die Begruendung.
        if actor.spec.confidence < 0.5:
            return "a faint, unclear shape"
        precise, coarse = KIND_PHRASES.get(actor.spec.kind, ("an obstacle", "an obstacle"))
        if not detailed:
            return coarse
        profile = actor.spec.obstacle_profile
        if profile is None or profile.assessment_confidence < 0.6:
            return precise
        details = [
            "flat" if profile.height_m <= 0.12 else ("low" if profile.height_m <= 0.3 else "tall"),
            {
                "CARDBOARD": "made of cardboard",
                "FOAM": "made of foam",
                "WOOD": "made of wood",
                "RUBBER": "made of rubber",
                "GLASS": "made of glass",
                "METAL": "made of metal",
                "PLASTIC": "made of plastic",
                "MIXED": "of mixed material",
            }.get(profile.material, "of unclear material"),
        ]
        if profile.deformability == "HIGH":
            details.append("looks soft and easily deformed")
        if profile.sharp_edges:
            details.append("with sharp edges")
        if profile.liquid:
            details.append("with liquid spreading around it")
        return f"{precise} ({', '.join(details)})"


@dataclass(frozen=True)
class VlaAccess:
    """Lesezugriff des Recovery Agents auf den Fahrstack zu einem Zeitpunkt."""

    mock: VlaMock
    time_s: float
    ego_pose: tuple[float, float, float, float, float, float]
    perception: PerceptionSnapshot | None
    controls: tuple[dict[str, Any], ...] = ()

    @property
    def perception_interface(self) -> str:
        return self.mock.spec.perception_interface

    def output(self, last_n: int = 5) -> dict[str, object]:
        return self.mock.report(self.time_s, last_n)

    def caption(self, camera: str) -> dict[str, object]:
        return self.mock.camera_caption(
            camera, self.time_s, self.ego_pose, self.perception, list(self.controls)
        )
