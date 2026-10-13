"""Der Agentenloop von innen, ganz ohne Modell und Schluessel (Tutorial 3).

Ein *gespieltes* Modell antwortet mit festen Nachrichten. Der echte Adapter
(``OpenAICompatibleAgent``) fuehrt die Werkzeugaufrufe aus und reicht die Ergebnisse zurueck.
Das Skript druckt jede Runde, damit sichtbar wird, was zwischen Modell und Simulator wandert.

    python3 -m autonomy_recovery_sim.tutorials.mock_llm_demo
    python3 -m autonomy_recovery_sim.tutorials.mock_llm_demo autonomy_recovery_sim/scenarios/hannover_durchgezogen.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from autonomy_recovery_sim.commands import PASS_EVIDENCE_TOOLS
from autonomy_recovery_sim.llm_agent import LLMConfig, OpenAICompatibleAgent
from autonomy_recovery_sim.simulation import run_scenario

DEFAULT_SCENARIO = Path("autonomy_recovery_sim/scenarios/hannover_frei.json")


def _tool_call(index: int, name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": f"call-{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


def _reply(tool_calls: list[dict[str, object]]) -> dict[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def scripted_model() -> list[dict[str, object]]:
    """Feste Antworten: erst die Belege, dann die Entscheidung."""
    evidence = [_tool_call(i, name) for i, name in enumerate(sorted(PASS_EVIDENCE_TOOLS), start=1)]
    decision = {
        "command": "PASS_OBSTACLE",
        "parameters": {"side": "LEFT"},
        "reason": "Blocker steht, die vier Lagepruefungen sprechen nicht dagegen",
    }
    return [_reply(evidence), _reply([_tool_call(99, "submit_decision", decision)])]


def _shorten(text: str, limit: int = 110) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    scenario = Path(args[0]) if args else DEFAULT_SCENARIO
    replies = scripted_model()
    round_number = 0

    def transport(payload: dict[str, object]) -> dict[str, object]:
        nonlocal round_number
        round_number += 1
        messages = payload["messages"]
        assert isinstance(messages, list)
        print(f"\n=== Runde {round_number}: Der Simulator schickt {len(messages)} Nachrichten ans Modell ===")
        print(f"    (dazu {len(payload['tools'])} Werkzeugbeschreibungen; das Modell sieht sie als Auswahlmenue)")  # type: ignore[arg-type]
        for message in messages[-3:]:
            content = message.get("content") or ""
            print(f"  [{message['role']}] {_shorten(str(content).replace(chr(10), ' '))}")
        if not replies:
            print("  <- Das Drehbuch ist zu Ende: das gespielte Modell antwortet nicht mehr")
            raise RuntimeError("Drehbuch des gespielten Modells zu Ende")
        reply = replies.pop(0)
        message = reply["choices"][0]["message"]  # type: ignore[index]
        names = [call["function"]["name"] for call in message["tool_calls"]]
        print(f"  <- Das Modell antwortet mit Werkzeugaufrufen: {', '.join(names)}")
        return reply

    agent = OpenAICompatibleAgent(LLMConfig(api_key="nur-fuer-die-demo", model="gespieltes-modell"), transport=transport)
    result = run_scenario(scenario, agent=agent, agent_name="mock-llm")

    print("\n=== Ergebnis ===")
    print(f"Befehl:  {result.command} ({'korrekt' if result.command_correct else 'falsch'})")
    print(f"Ausgang: {result.outcome}, Kosten {result.costs['total_eur']:.2f} EUR")
    print(f"Kollision: {result.collision}, Sicherheitswarnungen: {result.safety_warnings}")
    calls = [entry for entry in result.agent_trace if entry["type"] == "tool_call"]
    print(f"Der Agent-Trace enthaelt {len(calls)} Werkzeugaufrufe und {sum(e['type'] == 'model_round' for e in result.agent_trace)} Modellrunden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
