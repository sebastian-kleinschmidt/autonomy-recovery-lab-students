from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .agent_contract import AgentPolicy
from .agent_loader import load_agent
from .dynamics import Control
from .engine import SimulationEngine
from .batch import ScenarioEvaluation
from .report_details import procedure, rating_reasons, reference_procedure, tool_use_line
from .live_batch import LiveBatch, load_batch_scenarios, report_path
from .scenario import Scenario, load_scenario
from .simulation import result_from_engine


WEB_ROOT = Path(__file__).with_name("web")
VEHICLE_ROOT = Path(__file__).with_name("vehicles")


def resolve_static_path(request_path: str) -> Path | None:
    if request_path.startswith("/vehicles/"):
        root = VEHICLE_ROOT.resolve()
        relative = request_path.removeprefix("/vehicles/")
    else:
        root = WEB_ROOT.resolve()
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
    candidate = (root / relative).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate


# Sekunden, die das Endbild eines Batch-Szenarios mit seinem Bewertungsrahmen stehen bleibt.
RESULT_HOLD_S = 1.0


class LiveSimulation:
    def __init__(
        self,
        scenario: Scenario,
        agent: AgentPolicy | None = None,
        *,
        agent_name: str = "none",
        report_output: Path = Path("artifacts/autonomy-recovery-sim/live"),
        hide_reference: bool = False,
        confirm_results: bool = True,
    ):
        self._initial_scenario = scenario
        # Im Batch erst nach "OK" weiter: Wer zuschaut, will die Bewertung lesen, auch mit Agent.
        self.confirm_results = confirm_results
        self.scenario = self._configured(scenario)
        self.batch: LiveBatch | None = None
        self.report_output = report_output
        self.hide_reference = hide_reference
        self.reports: dict[str, Path] = {}
        self._engine_generation = 0
        self.agent = agent
        self.agent_name = agent_name
        self.engine = self._new_engine()
        self.control = Control()
        self.paused = True
        self.speed_factor = 1.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rating_cache: tuple[tuple[object, ...], dict[str, object]] | None = None
        # Im laufenden Batch bleibt das Endbild mit Bewertungsrahmen kurz stehen.
        self._advance_at: float | None = None
        # Von Hand gefahren (Konsole): das naechste Batch-Szenario startet erst nach "OK".
        self._awaiting_ack = False

    def _new_engine(self) -> SimulationEngine:
        self._engine_generation += 1
        # Ohne angeschlossenen Agenten uebernimmt die Recovery Console der Oberflaeche die Sitzung.
        return SimulationEngine(
            self.scenario,
            agent=self.agent,
            agent_name=self.agent_name,
            console=self.agent is None,
        )

    def _configured(self, scenario: Scenario) -> Scenario:
        return scenario

    def load_batch(self, scenarios: tuple[Scenario, ...], *, name: str) -> dict[str, object]:
        with self._lock:
            if not scenarios:
                raise ValueError("Ein Batch muss mindestens ein Szenario enthalten")
            self.batch = LiveBatch(scenarios, name, self.report_output, hide_reference=self.hide_reference)
            self.reports[self.batch.run_id] = self.batch.output_dir
            self._activate_batch_scenario()
            self.paused = True
            return self.snapshot()

    def _activate_batch_scenario(self) -> None:
        assert self.batch is not None
        self._initial_scenario = self.batch.scenarios[self.batch.index]
        self.scenario = self._configured(self._initial_scenario)
        self.engine = self._new_engine()
        self.control = Control()

    def _reset(self) -> None:
        self._advance_at = None
        self._awaiting_ack = False
        if self.batch is not None:
            self.load_batch(self.batch.scenarios, name=self.batch.name)
        else:
            self.engine = self._new_engine()
            self.control = Control()
        self.paused = True

    def _finish_scenario(self, *, hold: bool = False) -> None:
        """Ergebnis sofort festhalten; mit ``hold`` startet das naechste Szenario erst nach
        RESULT_HOLD_S, damit Endbild und Bewertungsrahmen sichtbar bleiben."""
        if self.batch is None or not self.engine.done or self._awaiting_ack:
            return
        if self._advance_at is not None:
            # Ergebnis ist bereits erfasst; nur noch die Haltezeit abwarten.
            if hold and time.monotonic() < self._advance_at:
                return
            self._advance_at = None
            self._activate_batch_scenario()
            return
        if self.batch.finish(self.engine):
            if self.confirm_results:
                # Wer von Hand faehrt, liest erst die Bewertung und bestaetigt mit "OK".
                self._awaiting_ack = True
                return
            if hold:
                self._advance_at = time.monotonic() + RESULT_HOLD_S
                return
            self._activate_batch_scenario()
        elif self.batch.completed:
            self.paused = True

    def start_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="autonomy-recovery-sim-loop")
        self._thread.start()

    def stop_worker(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            delay = next_tick - time.monotonic()
            if delay > 0:
                # Kurze Wartescheiben halten Stop und Bedienung reaktionsfaehig; geschritten
                # wird erst, wenn der Takt wirklich erreicht ist (sonst wirkt Zeitlupe nicht).
                self._stop.wait(min(delay, 0.1))
                continue
            with self._lock:
                paused = self.paused
                factor = self.speed_factor
                if not paused:
                    if not self.engine.done:
                        self.engine.step(self.control)
                    self._finish_scenario(hold=True)
            next_tick += self.scenario.dt_s / max(0.25, factor)
            if next_tick < time.monotonic():
                next_tick = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            result = self.engine.snapshot()
            result["paused"] = self.paused
            result["speed_factor"] = self.speed_factor
            result["run_id"] = self._engine_generation
            result["batch"] = self.batch.snapshot() if self.batch else None
            result.update(self._rating())
            result["awaiting_ack"] = self._awaiting_ack
            return result

    def _rating(self) -> dict[str, object]:
        """Bewertung mit Begruendung, sobald sie feststeht: negativ schon beim ersten Verstoss,
        positiv und neutral am Ende."""
        engine = self.engine
        if not (engine.done or engine.collision or engine.safety_warnings):
            return {"rating": None, "rating_reasons": [], "result_detail": {}}
        key = (self._engine_generation, engine.done, engine.collision, len(engine.safety_warnings))
        if self._rating_cache is None or self._rating_cache[0] != key:
            evaluation = ScenarioEvaluation.from_result(result_from_engine(engine))
            if not engine.done:
                # Noch nicht beendet: nur der Verstoss steht fest, nicht das Bestehen.
                evaluation = ScenarioEvaluation(evaluation.result, passed=False, run_index=1)
            hidden = self.hide_reference
            reasons = rating_reasons(evaluation, hide_reference=hidden)
            detail: dict[str, object] = {}
            if engine.done:
                reasons.append(tool_use_line(evaluation))
                # Fuer das Abschlussfenster: eigene Entscheidungen und (falls erlaubt) die Musterloesung.
                detail = {"procedure": procedure(evaluation)}
                if not hidden:
                    detail["reference_procedure"] = reference_procedure(evaluation)
            self._rating_cache = (key, {
                "rating": evaluation.rating,
                "rating_reasons": reasons,
                "result_detail": detail,
            })
        return self._rating_cache[1]

    def command(self, payload: dict[str, Any]) -> dict[str, object]:
        with self._lock:
            action = str(payload.get("action", ""))
            if action == "start":
                if self.engine.done:
                    self._reset()
                self.paused = False
            elif action == "pause":
                self.paused = True
            elif action == "toggle":
                self.paused = not self.paused
            elif action == "reset":
                self._reset()
            elif action == "ack_result":
                # "OK" im Abschlussfenster: im Batch geht es mit dem naechsten Szenario weiter.
                if self._awaiting_ack:
                    self._awaiting_ack = False
                    self._activate_batch_scenario()
                    self.paused = False
            elif action == "retry_report":
                if self.batch is None:
                    raise ValueError("Kein Batch geladen")
                self.batch.write_report()
            elif action == "step":
                if not self.engine.done:
                    self.engine.step(self.control)
            elif action == "console_tool":
                arguments = payload.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("arguments muss ein Objekt sein")
                self.engine.console_tool(str(payload.get("name", "")), arguments)
            elif action == "console_evidence":
                self.engine.console_gather_evidence()
            elif action == "console_submit":
                submitted = payload.get("payload", {})
                if not isinstance(submitted, dict):
                    raise ValueError("payload muss ein Objekt sein")
                self.engine.console_submit(submitted)
            elif action and action != "control":
                raise ValueError(f"Unbekannte Aktion: {action}")

            if "mode" in payload:
                self.engine.set_mode(str(payload["mode"]))
            if any(key in payload for key in ("throttle", "brake", "steering")):
                self.control = Control(
                    throttle=float(payload.get("throttle", self.control.throttle)),
                    brake=float(payload.get("brake", self.control.brake)),
                    steering=float(payload.get("steering", self.control.steering)),
                ).clamped()
            if "speed_factor" in payload:
                self.speed_factor = min(4.0, max(0.25, float(payload["speed_factor"])))
            self._finish_scenario()
            return self.snapshot()


def _handler_for(live: LiveSimulation) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutonomyRecoverySim/0.3"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/state":
                self._send_json(live.snapshot())
                return
            if path.startswith("/reports/"):
                with live._lock:
                    candidate = report_path(path, live.reports)
            else:
                candidate = resolve_static_path(path)
            if candidate is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = candidate.read_bytes()
            mime, _ = mimetypes.guess_type(candidate.name)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # The live UI is developed as a tightly coupled HTML/JS/CSS bundle.
            # Prevent mixed cached versions, which can leave controls attached
            # to stale markup after a simulator update.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/command":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON-Objekt erwartet")
                self._send_json(live.command(payload))
            except (ValueError, OSError) as error:
                self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    return Handler


def create_server(live: LiveSimulation, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler_for(live))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live-2D-Oberflaeche fuer AutonomyRecoverySim")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--scenario",
        type=Path,
    )
    source_group.add_argument("--batch", type=Path, help="Szenarioliste (.txt/.list), Szenario oder Ordner als Live-Batch laden")
    parser.add_argument("--output", type=Path, default=Path("artifacts/autonomy-recovery-sim/live"), help="Ordner fuer die Reports abgeschlossener Live-Batches")
    parser.add_argument("--hide-reference", action="store_true", help="Referenzbefehle nicht in den Batchreport schreiben")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--agent",
        default="none",
        help="none, llm oder python_modul:funktion (Standard: none)",
    )
    parser.add_argument(
        "--ohne-ok", action="store_true",
        help="im Batch nicht auf OK warten, sondern nach kurzer Pause weiter (fuer Laeufe ohne Zuschauer)",
    )
    args = parser.parse_args(argv)

    try:
        loaded_agent = load_agent(args.agent)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        scenarios = load_batch_scenarios(args.batch) if args.batch else (load_scenario(args.scenario or Path(__file__).with_name("scenarios") / "hannover_frei.json"),)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    live = LiveSimulation(
        scenarios[0],
        agent=loaded_agent.policy,
        agent_name=loaded_agent.name,
        report_output=args.output,
        hide_reference=args.hide_reference,
        confirm_results=not args.ohne_ok,
    )
    if args.batch:
        live.load_batch(scenarios, name=args.batch.name)
    try:
        server = create_server(live, args.host, args.port)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print(f"Port {args.port} ist bereits belegt.")
            print(f"Pruefe http://{args.host}:{args.port} oder starte mit --port {args.port + 1}.")
            return 2
        raise
    live.start_worker()
    print(f"AutonomyRecoverySim laeuft auf http://{args.host}:{server.server_port}")
    print(f"Agent: {loaded_agent.name}")
    print("Beenden mit Ctrl+C")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nAutonomyRecoverySim beendet")
    finally:
        server.server_close()
        live.stop_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
