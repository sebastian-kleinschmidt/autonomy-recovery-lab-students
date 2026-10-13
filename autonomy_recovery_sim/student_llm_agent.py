"""Editierbarer Einstieg fuer den studentischen Autonomy Recovery Agent."""

from __future__ import annotations

from .agent_session import AgentSession
from .llm_agent import LLMConfig, OpenAICompatibleAgent
from .models import CommandRequest


# Erste Aufgabe: Formuliert eine belastbare Untersuchungs- und Entscheidungsstrategie.
# Der Simulator erzwingt Regel- und Sicherheitspruefung; gute Werkzeugwahl, die Wahl
# unter den High-Level-Befehlen (siehe COMMANDS.md), Kostenbewusstsein und robuste
# Entscheidungen bleiben eure Agentenaufgabe.
STUDENT_SYSTEM_PROMPT = """Du bist der Recovery-Stratege eines festgefahrenen autonomen Fahrzeugs.
Untersuche die Situation mit den angebotenen Werkzeugen und waehle genau einen High-Level-Befehl.
Sicherheit geht vor Befreiungsrate; Personenschaeden sind am teuersten, Remote-Assistance-Anrufe
und Standzeit kosten Geld. Schliesse jede Untersuchung mit submit_decision ab. Erfinde keine
Beobachtungen und begruende deine Entscheidung knapp anhand der Werkzeugergebnisse."""


def decide(session: AgentSession) -> CommandRequest:
    config = LLMConfig.from_environment()
    agent = OpenAICompatibleAgent(config, system_prompt=STUDENT_SYSTEM_PROMPT)
    return agent(session)
