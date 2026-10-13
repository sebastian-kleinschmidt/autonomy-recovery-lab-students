from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.request

from .agent_contract import AgentContractError
from .agent_session import AgentSession, AgentSessionError, TOOL_CATALOG
from .commands import list_commands
from .models import CommandRequest


DEFAULT_BASE_URL = "https://chat-ai.academiccloud.de/v1"
DEFAULT_MODEL = "qwen3.5-397b-a17b"
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_MAX_ROUNDS = 12


class LLMConfigurationError(ValueError):
    """Required connection settings for the language model are missing."""


class LLMServiceError(RuntimeError):
    """The configured model service failed or returned an invalid response."""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_rounds: int = DEFAULT_MAX_ROUNDS

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise LLMConfigurationError(
                "Kein API-Schluessel gesetzt. AUTONOMY_RECOVERY_API_KEY, "
                "CHATAI_API_KEY oder SAIA_API_KEY verwenden."
            )
        if not self.model.strip():
            raise LLMConfigurationError("AUTONOMY_RECOVERY_MODEL darf nicht leer sein")
        if not self.base_url.startswith(("https://", "http://")):
            raise LLMConfigurationError("AUTONOMY_RECOVERY_BASE_URL muss eine HTTP(S)-URL sein")
        if self.timeout_s <= 0:
            raise LLMConfigurationError("AUTONOMY_RECOVERY_TIMEOUT_S muss positiv sein")
        if not 1 <= self.max_rounds <= 30:
            raise LLMConfigurationError("AUTONOMY_RECOVERY_MAX_ROUNDS muss zwischen 1 und 30 liegen")

    @classmethod
    def from_environment(cls) -> "LLMConfig":
        api_key = next(
            (
                os.environ[name].strip()
                for name in ("AUTONOMY_RECOVERY_API_KEY", "CHATAI_API_KEY", "SAIA_API_KEY")
                if os.environ.get(name, "").strip()
            ),
            "",
        )
        try:
            timeout_s = float(os.environ.get("AUTONOMY_RECOVERY_TIMEOUT_S", DEFAULT_TIMEOUT_S))
            max_rounds = int(os.environ.get("AUTONOMY_RECOVERY_MAX_ROUNDS", DEFAULT_MAX_ROUNDS))
        except ValueError as exc:
            raise LLMConfigurationError(
                "AUTONOMY_RECOVERY_TIMEOUT_S und AUTONOMY_RECOVERY_MAX_ROUNDS muessen Zahlen sein"
            ) from exc
        return cls(
            api_key=api_key,
            model=os.environ.get("AUTONOMY_RECOVERY_MODEL", DEFAULT_MODEL).strip(),
            base_url=os.environ.get("AUTONOMY_RECOVERY_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            timeout_s=timeout_s,
            max_rounds=max_rounds,
        )


Transport = Callable[[dict[str, object]], dict[str, object]]


# Minimalprompt: nur die Bedienung der Schnittstelle. Das Fachwissen (Regeln, Lagebeurteilung)
# formuliert ihr selbst, z. B. in student_llm_agent.py oder mein_agent.py.
SYSTEM_PROMPT = """Du bist der Recovery-Agent eines autonomen Kleinbusses. Das Fahrzeug steht.
Untersuche die Lage mit den angebotenen Werkzeugen und reiche dann genau einen Befehl aus
available_commands mit submit_decision ein. Antworte nicht mit Prosa, sondern benutze die Werkzeuge."""

def _decision_schema() -> dict[str, object]:
    source = Path(__file__).with_name("schemas") / "agent_decision.schema.json"
    schema = json.loads(source.read_text(encoding="utf8"))
    return {
        key: value
        for key, value in schema.items()
        if key not in {"$schema", "$id", "title"}
    }


def _command_summary() -> str:
    lines = []
    for entry in list_commands():
        parameters = ", ".join(
            f"{name}={json.dumps(schema, ensure_ascii=False)}"
            for name, schema in entry["parameter_schema"].items()
        )
        lines.append(f"- {entry['command']}({parameters}): {entry['description']}")
    return "\n".join(lines)


def _openai_tools(catalog: Mapping[str, Mapping[str, object]] = TOOL_CATALOG) -> list[dict[str, object]]:
    tools = []
    for name, definition in catalog.items():
        properties = definition["arguments"]
        assert isinstance(properties, Mapping)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": definition["description"],
                    "parameters": {
                        "type": "object",
                        "properties": dict(properties),
                        "additionalProperties": False,
                    },
                },
            }
        )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "submit_decision",
                "description": (
                    "Fordert genau einen High-Level-Befehl an (command, parameters, reason). "
                    "Verfuegbare Befehle:\n" + _command_summary()
                ),
                "parameters": _decision_schema(),
            },
        }
    )
    return tools


def tool_definitions(catalog: Mapping[str, Mapping[str, object]] = TOOL_CATALOG) -> list[dict[str, object]]:
    """Werkzeugbeschreibungen im Function-Calling-Format, inklusive ``submit_decision``."""
    return _openai_tools(catalog)


class _Retryable(LLMServiceError):
    """Zeitueberschreitung, Serverfehler oder kurze Ratengrenze: ein weiterer Versuch kann helfen."""

    def __init__(self, message: str, wait_s: float = 2.0):
        super().__init__(message)
        self.wait_s = wait_s


# Steht in der Fehlermeldung, wenn das Kontingent des Schluessels fuer Stunde oder Tag erschoepft
# ist; der Batchlauf bricht dann ab, statt alle weiteren Faelle mit Agentenfehlern zu fuellen.
QUOTA_EXHAUSTED = "MODELL_KONTINGENT_ERSCHOEPFT"
MAX_RATE_WAIT_S = 65.0  # Minutengrenze: so lange abwarten lohnt sich


class LLMQuotaExhausted(LLMServiceError):
    """Das Kontingent des Schluessels ist fuer laengere Zeit erschoepft (HTTP 429)."""


def chat_completion(config: LLMConfig, payload: dict[str, object], *, attempts: int = 2) -> dict[str, object]:
    """HTTP-Aufruf an einen OpenAI-kompatiblen Endpunkt (``/chat/completions``).

    Zeitueberschreitungen und Serverfehler (5xx) des Dienstes werden einmal wiederholt.
    """
    for attempt in range(1, attempts + 1):
        try:
            return _chat_completion_once(config, payload)
        except _Retryable as exc:
            if attempt == attempts:
                raise LLMServiceError(str(exc)) from exc
            time.sleep(exc.wait_s)
    raise AssertionError("unerreichbar")


def _chat_completion_once(config: LLMConfig, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf8")
    request = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            decoded = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf8", "replace")[:300]
        if exc.code == 429:
            try:
                wait_s = float(exc.headers.get("Retry-After") or 60)
            except ValueError:
                wait_s = 60.0
            if wait_s <= MAX_RATE_WAIT_S:
                raise _Retryable("Ratengrenze je Minute erreicht", wait_s=wait_s) from exc
            limits = ", ".join(
                f"{exc.headers.get(f'X-RateLimit-Limit-{span}')} je {label}"
                for span, label in (("Minute", "Minute"), ("Hour", "Stunde"), ("Day", "Tag"))
                if exc.headers.get(f"X-RateLimit-Limit-{span}")
            )
            raise LLMQuotaExhausted(
                f"{QUOTA_EXHAUSTED}: Kontingent des Schluessels erschoepft ({limits or 'Grenze unbekannt'}); "
                f"wieder frei in etwa {round(wait_s / 60)} Minuten"
            ) from exc
        error = _Retryable if exc.code >= 500 else LLMServiceError
        raise error(f"Modellendpunkt antwortet mit HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMServiceError(f"Modellendpunkt nicht erreichbar: {exc.reason}") from exc
    except TimeoutError as exc:
        raise _Retryable(
            f"Modellanfrage nach {config.timeout_s:g} Sekunden abgebrochen"
        ) from exc
    except json.JSONDecodeError as exc:
        raise LLMServiceError("Modellendpunkt lieferte kein gueltiges JSON") from exc
    if not isinstance(decoded, dict):
        raise LLMServiceError("Modellantwort muss ein JSON-Objekt sein")
    return decoded


def _safe_usage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    allowed = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {
        key: int(value[key])
        for key in allowed
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
    }


class OpenAICompatibleAgent:
    """Bounded function-calling loop for OpenAI-compatible chat endpoints."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        transport: Transport | None = None,
    ):
        self.config = config
        self.system_prompt = system_prompt.strip()
        if not self.system_prompt:
            raise LLMConfigurationError("Der Systemprompt darf nicht leer sein")
        self._transport = transport or self._request

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        return chat_completion(self.config, payload)

    @staticmethod
    def _message(response: Mapping[str, object]) -> tuple[dict[str, object], str]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise LLMServiceError("Modellantwort enthaelt keine choices")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise LLMServiceError("Modellantwort enthaelt keine assistant-Nachricht")
        normalized: dict[str, object] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if "tool_calls" in message:
            normalized["tool_calls"] = message["tool_calls"]
        return normalized, str(choice.get("finish_reason", ""))

    @staticmethod
    def _tool_error(call_id: str, name: str, message: str) -> dict[str, object]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps({"ok": False, "error": message}, ensure_ascii=False),
        }

    def __call__(self, session: AgentSession) -> CommandRequest:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": (
                    "Der Deadlock wurde erkannt. Untersuche diese Ausgangslage und waehle einen Befehl: "
                    + json.dumps(
                        {
                            key: value
                            for key, value in session.context.items()
                            if key not in {"available_tools", "available_commands"}
                        },
                        ensure_ascii=False,
                    )
                ),
            },
        ]
        tools = _openai_tools(session.tool_catalog)
        for round_index in range(1, self.config.max_rounds + 1):
            started = time.monotonic()
            response = self._transport(
                {
                    "model": self.config.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0,
                    "max_tokens": 1400,
                }
            )
            if not isinstance(response, Mapping):
                raise LLMServiceError("Transport lieferte keine JSON-Antwort")
            assistant, finish_reason = self._message(response)
            raw_calls = assistant.get("tool_calls")
            tool_names = []
            if isinstance(raw_calls, list):
                tool_names = [
                    str(call.get("function", {}).get("name", ""))
                    for call in raw_calls
                    if isinstance(call, Mapping) and isinstance(call.get("function"), Mapping)
                ]
            session.trace.append(
                {
                    "type": "model_round",
                    "index": round_index,
                    "model": self.config.model,
                    "latency_s": round(time.monotonic() - started, 3),
                    "finish_reason": finish_reason,
                    "tool_names": tool_names,
                    "usage": _safe_usage(response.get("usage")),
                }
            )
            messages.append(assistant)
            if not isinstance(raw_calls, list) or not raw_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "Nutze die angebotenen Werkzeuge und schliesse mit submit_decision ab.",
                    }
                )
                continue

            for position, raw_call in enumerate(raw_calls, start=1):
                if not isinstance(raw_call, Mapping):
                    raise LLMServiceError("tool_calls enthaelt einen ungueltigen Eintrag")
                call_id = str(raw_call.get("id") or f"round-{round_index}-{position}")
                function = raw_call.get("function")
                if not isinstance(function, Mapping):
                    messages.append(self._tool_error(call_id, "unknown", "function fehlt"))
                    continue
                name = str(function.get("name", ""))
                arguments_text = function.get("arguments", "{}")
                try:
                    arguments = json.loads(arguments_text or "{}")
                except (json.JSONDecodeError, TypeError):
                    messages.append(self._tool_error(call_id, name, "arguments ist kein gueltiges JSON"))
                    continue
                if not isinstance(arguments, dict):
                    messages.append(self._tool_error(call_id, name, "arguments muss ein Objekt sein"))
                    continue
                try:
                    if name == "submit_decision":
                        return session.submit_decision(arguments)
                    result = session.call_tool(name, arguments)
                except (AgentSessionError, AgentContractError) as exc:
                    messages.append(self._tool_error(call_id, name, str(exc)))
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps({"ok": True, "result": result}, ensure_ascii=False),
                    }
                )
        raise LLMServiceError(
            f"Modell hat nach {self.config.max_rounds} Runden keine gueltige Entscheidung eingereicht"
        )


def decide(session: AgentSession) -> CommandRequest:
    """CLI entry point for ``--agent llm`` and module-based loading."""
    return OpenAICompatibleAgent(LLMConfig.from_environment())(session)
