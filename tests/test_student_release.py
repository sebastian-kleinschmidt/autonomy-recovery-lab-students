"""Kurzer Selbsttest der im jeweiligen Checkout verfügbaren Szenarien."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from autonomy_recovery_sim.batch import discover_scenarios, ScenarioEvaluation
from autonomy_recovery_sim.scenario import load_scenario
from autonomy_recovery_sim.simulation import run_scenario
from autonomy_recovery_sim import cli
from autonomy_recovery_sim.report_details import catalog

SIM = Path(__file__).resolve().parents[1] / "autonomy_recovery_sim"


class StudentReleaseTest(unittest.TestCase):
    def test_all_available_scenarios_load(self):
        for folder in ("scenarios", "families"):
            for path in (SIM / folder).glob("*.json"):
                if path.name != "catalog.json":
                    with self.subTest(path=path.name):
                        load_scenario(path)

    def test_all_available_batches_point_to_existing_scenarios(self):
        for path in (SIM / "scenario_sets").glob("*.txt"):
            with self.subTest(batch=path.name):
                self.assertTrue(discover_scenarios(path))

    def test_catalog_matches_available_files(self):
        entries = json.loads((SIM / "scenario_catalog.json").read_text("utf8"))["scenarios"]
        self.assertEqual({item["id"] for item in entries}, {path.stem for path in (SIM / "scenarios").glob("*.json")})

    def test_default_and_all_batches_include_every_available_case(self):
        available = {path.resolve() for folder in ("scenarios", "families")
                     for path in (SIM / folder).glob("*.json") if path.name != "catalog.json"}
        for arguments in ([], ["--release", "all"]):
            with self.subTest(arguments=arguments), mock.patch.object(cli, "run_batch", side_effect=RuntimeError("batch selected")) as runner:
                with self.assertRaisesRegex(RuntimeError, "batch selected"):
                    cli._batch_main(arguments)
                source = runner.call_args.args[0]
                self.assertEqual({path.resolve() for path in discover_scenarios(source)}, available)

    def test_family_reports_use_their_sprint_release(self):
        path = SIM / "families/catalog.json"
        entries = json.loads(path.read_text("utf8"))["families"] if path.is_file() else []
        for item in entries:
            self.assertEqual(catalog()[item["id"]]["release"], "sprint2" if item["introductory"] else "sprint3")

    def test_bootcamp_runs_safely_with_a_waiting_agent(self):
        from tests.wartender_agent import decide

        for path in discover_scenarios(SIM / "scenario_sets/bootcamp.txt"):
            with self.subTest(scenario=path.stem):
                evaluation = ScenarioEvaluation.from_result(run_scenario(path, agent=decide))
                self.assertIsNone(evaluation.result.agent_error)
                self.assertFalse(evaluation.result.collision)


if __name__ == "__main__":
    unittest.main()
