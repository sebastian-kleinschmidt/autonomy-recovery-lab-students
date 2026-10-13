from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, TypeAlias

from .commands import CommandError, parse_command, request_to_payload
from .models import CommandRequest

if TYPE_CHECKING:
    from .agent_session import AgentSession


AgentPayload: TypeAlias = Mapping[str, object]
AgentPolicy: TypeAlias = Callable[["AgentSession"], AgentPayload | CommandRequest | None]

# Ablehnungen tragen den Standardfehlercode (INVALID_ARGUMENT, RULE_REJECTED, ...).
AgentContractError = CommandError


def contract_to_payload(request: CommandRequest) -> dict[str, object]:
    return request_to_payload(request)


def validate_agent_decision(payload: AgentPayload | CommandRequest) -> CommandRequest:
    """Strictly validate untrusted agent output before the executor sees it.

    Prueft nur Form, Befehlsname und Parameterbounds. Situationsabhaengige
    Regel- und Sicherheitspruefungen folgen in ``AgentSession.submit_decision``.
    """
    return parse_command(payload)
