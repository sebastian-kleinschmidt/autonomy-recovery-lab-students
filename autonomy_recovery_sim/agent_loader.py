from __future__ import annotations

import importlib
from dataclasses import dataclass

from .agent_contract import AgentPolicy


@dataclass(frozen=True)
class LoadedAgent:
    name: str
    policy: AgentPolicy | None


def load_agent(specification: str) -> LoadedAgent:
    """Resolve a CLI agent specification without activating the agent."""
    value = specification.strip()
    if value in {"", "none"}:
        return LoadedAgent(name="none", policy=None)
    if value == "baseline":
        try:  # interne Referenzheuristik; im Studierenden-Repository nicht enthalten
            from .policy import heuristic_agent
        except ImportError as exc:
            raise ValueError("Die Baseline gibt es nur im Lehrenden-Repository") from exc
        return LoadedAgent(name="baseline", policy=heuristic_agent)
    if value == "llm":
        from .llm_agent import decide

        return LoadedAgent(name="llm", policy=decide)
    module_name, separator, function_name = value.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError(
            "Agent muss 'none', 'llm' oder als python_modul:funktion angegeben werden"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"Agentenmodul '{module_name}' konnte nicht geladen werden: {exc}") from exc
    policy = getattr(module, function_name, None)
    if not callable(policy):
        raise ValueError(f"'{value}' bezeichnet keine aufrufbare Agentenfunktion")
    return LoadedAgent(name=value, policy=policy)
