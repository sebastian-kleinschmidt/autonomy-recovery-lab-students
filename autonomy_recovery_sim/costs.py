"""Wirtschaftliches Bewertungsmodell einer Recovery-Episode.

Alle Betraege sind Modellannahmen fuer den Laborbetrieb (Euro-Aequivalente),
keine Marktdaten. Reihenfolge der Schwere: Personenschaden > Sachschaden >
Remote-Assistance-Anrufe und Standzeit. Kundenzufriedenheit misst, ob Fahrgaeste
schnell und sicher ans Ziel kommen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostModel:
    standstill_eur_per_s: float = 0.50
    detour_eur_per_s: float = 0.50
    remote_assistance_call_eur: float = 25.0
    material_damage_eur: float = 5_000.0
    personal_injury_eur: float = 100_000.0
    rejected_command_eur: float = 2.0
    safe_stop_recovery_eur: float = 150.0
    blocking_traffic_eur: float = 100.0
    passenger_refund_eur: float = 20.0
    return_home_passenger_refund_eur: float = 50.0
    # Unfallabwicklung (siehe incidents.py).
    light_damage_eur: float = 1_000.0
    vehicle_damage_eur: float = 3_000.0
    hit_and_run_eur: float = 100_000.0  # wie ein Personenschaden
    unreported_incident_eur: float = 20_000.0
    missing_emergency_call_eur: float = 20_000.0
    unsecured_scene_eur: float = 2_000.0
    incident_recovery_eur: float = 150.0
    # Sprachmodell: Rechenkosten und Wartezeit der Leitstelle je 1000 Tokens (didaktischer Wert,
    # vorlaeufig; mit echtem Modell kalibrieren). Ein Prompt, der auch klare Faelle ans Modell
    # gibt, zahlt dafuer.
    model_eur_per_1k_tokens: float = 0.10
    # Kundenzufriedenheit: Abzug je Sekunde Verspaetung bis zur Untergrenze.
    delay_satisfaction_per_s: float = 1.0 / 400.0


DEFAULT_COST_MODEL = CostModel()


def assess_episode(engine, model: CostModel = DEFAULT_COST_MODEL) -> dict[str, object]:
    """Berechnet Kostenpositionen und Kundenzufriedenheit aus dem Engine-Zustand."""
    items: dict[str, float] = {}

    def add(name: str, amount: float) -> None:
        if amount:
            items[name] = round(items.get(name, 0.0) + amount, 2)

    add("standstill", engine.stationary_s * model.standstill_eur_per_s)
    add("remote_assistance", engine.remote_assistance_calls * model.remote_assistance_call_eur)
    add("rejected_commands", engine.rejected_commands * model.rejected_command_eur)
    add("detour", engine.extra_time_s * model.detour_eur_per_s)
    tokens = sum(
        int(entry.get("usage", {}).get("total_tokens", 0))
        for entry in engine.agent_trace
        if entry.get("type") == "model_round" and isinstance(entry.get("usage"), dict)
    )
    add("language_model", tokens / 1000.0 * model.model_eur_per_1k_tokens)
    if engine.incidents.impacts:
        # Jeder Aufprall einzeln; eine gute Abwicklung macht ihn nicht billiger.
        for impact in engine.incidents.impacts:
            if not impact.ego_at_fault:
                continue  # den Schaden traegt der Unfallgegner
            if impact.kind == "UNDERBODY":
                add("vehicle_damage", model.light_damage_eur if impact.severity == "LIGHT" else model.vehicle_damage_eur)
            elif impact.vulnerable:
                add("personal_injury", model.personal_injury_eur)
            elif impact.severity == "LIGHT":
                add("material_damage", model.light_damage_eur)
            else:
                add("material_damage", model.material_damage_eur)
        handling = incident_handling(engine)
        findings = set(handling["findings"])  # type: ignore[arg-type]
        if "LEFT_ACCIDENT_SCENE" in findings:
            add("hit_and_run", model.hit_and_run_eur)
        if "INCIDENT_NOT_REPORTED" in findings:
            add("unreported_incident", model.unreported_incident_eur)
        if "NO_EMERGENCY_CALL" in findings:
            add("missing_emergency_call", model.missing_emergency_call_eur)
        if "SCENE_NOT_SECURED" in findings:
            add("unsecured_scene", model.unsecured_scene_eur)
    elif engine.collision:
        if engine.collision_with_vulnerable:
            add("personal_injury", model.personal_injury_eur)
        else:
            add("material_damage", model.material_damage_eur)
    if engine.terminal_outcome == "incident_reported":
        add("recovery", model.incident_recovery_eur)
    if engine.passengers_injured:
        add("personal_injury", engine.passengers_injured * model.personal_injury_eur)
    if engine.terminal_outcome == "safe_stopped":
        add("recovery", model.safe_stop_recovery_eur)
        if engine.stopped_in_travel_lane:
            add("blocking_traffic", model.blocking_traffic_eur)
    if engine.passengers_dropped_safely:
        add("passenger_refund", engine.passengers_dropped_safely * model.passenger_refund_eur)
    if engine.passengers_returned_home:
        add("passenger_refund", engine.passengers_returned_home * model.return_home_passenger_refund_eur)

    return {
        "model": asdict(model),
        "items_eur": items,
        "total_eur": round(sum(items.values()), 2),
        "customer_satisfaction": _satisfaction(engine, model),
        "stationary_s": round(engine.stationary_s, 2),
        "extra_time_s": round(engine.extra_time_s, 2),
        "remote_assistance_calls": engine.remote_assistance_calls,
        "rejected_commands": engine.rejected_commands,
        "passengers_injured": engine.passengers_injured,
        "incident_handling": incident_handling(engine)["result"],
    }


def incident_handling(engine) -> dict[str, object]:
    """Bewertung der Unfallabwicklung (``none`` ohne Unfall mit Dritten)."""
    episode_left_scene = engine.reached_goal or engine.terminal_outcome in {"rerouted", "returned_home"}
    return engine.incidents.handling(
        episode_left_scene=episode_left_scene, in_travel_lane=engine.incident_in_travel_lane
    )


def _satisfaction(engine, model: CostModel) -> float | None:
    """0..1; ``None`` ohne Fahrgaeste an Bord (leere Mission)."""
    if engine.initial_passengers <= 0:
        return None
    if engine.collision or engine.passengers_injured:
        return 0.0
    outcome = engine.terminal_outcome
    if outcome == "passenger_dropped":
        return 0.4 if engine.passengers_dropped_safely else 0.0
    if outcome == "returned_home":
        return 0.1
    if outcome == "safe_stopped":
        return 0.2
    delay_s = engine.stationary_s + engine.extra_time_s
    if outcome == "rerouted" or engine.reached_goal:
        return round(max(0.4, 1.0 - delay_s * model.delay_satisfaction_per_s), 3)
    return round(max(0.0, 0.5 - delay_s * model.delay_satisfaction_per_s), 3)
