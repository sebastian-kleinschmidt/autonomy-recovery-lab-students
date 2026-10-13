"""Deterministischer 2D-Simulator fuer Agentic Autonomy Recovery."""

from .env_file import load_env_file

load_env_file()  # Modellzugang aus .env, falls vorhanden

from .simulation import SimulationResult, run_scenario  # noqa: E402

__all__ = ["SimulationResult", "run_scenario"]
