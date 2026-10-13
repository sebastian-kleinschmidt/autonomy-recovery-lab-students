"""Ein wartender Agent für Tests: fragt das Hindernis ab und wartet immer fünf Sekunden."""

from __future__ import annotations

from autonomy_recovery_sim.agent_session import AgentSession


def decide(session: AgentSession):
    blocker = session.call_tool("get_blocker")
    return session.submit_decision(
        {
            "command": "WAIT",
            "parameters": {"duration_s": 5.0},
            "reason": f"Wartender Testagent; Blockade erkannt: {blocker['detected']}",
        }
    )
