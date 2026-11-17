"""Keep the existing form usable with the reference application's defaults."""

import ast
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic.load_combinations import reference_combinations, resolve_combinations
from agentic.agents.a1_requirements_agent import run_a1_requirements
from agentic.state import WorkflowState


class ReferenceAutomationTests(unittest.TestCase):
    def test_base_plate_offset_survives_validation_and_frame_handoff(self):
        from agentic.agents import a3_frame_generator_agent as a3

        for offset in (0, 30, 47.5):
            with self.subTest(offset=offset), patch.dict(os.environ, {
                "PLANT_WORKFLOW_MODE": "poc2_automation", "PLANT_LOAD_COMBINATIONS_FILE": ""
            }):
                state = run_a1_requirements(WorkflowState(
                    run_id="base_plate_offset", raw_input={"grid": {
                        "base_plate_offset_above_column_base_mm": offset
                    }}))
                self.assertEqual(state.agent_statuses["A1"]["status"], "PASSED")
                self.assertEqual(state.grid_config["base_plate_offset_above_column_base_mm"], offset)
                with patch.object(a3, "generate_members", side_effect=RuntimeError("handoff inspected")) as generate:
                    a3.run_a3_frame_generator(state)
                self.assertEqual(generate.call_args.args[0]["base_plate_offset_above_column_base_mm"], offset)

    def test_base_plate_offset_rejects_nonfinite_input(self):
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": "poc2_automation"}):
            state = run_a1_requirements(WorkflowState(
                run_id="invalid_offset", raw_input={"grid": {
                    "base_plate_offset_above_column_base_mm": float("inf")
                }}))
        self.assertEqual(state.agent_statuses["A1"]["status"], "FAILED")

    def test_existing_form_passes_a1_without_an_extra_combination_file(self):
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": "poc2_automation", "PLANT_LOAD_COMBINATIONS_FILE": ""}):
            state = run_a1_requirements(WorkflowState(run_id="reference_form", raw_input={"grid": {}, "load_configuration": {}}))
        self.assertEqual(state.agent_statuses["A1"]["status"], "PASSED")
        self.assertEqual(len(state.load_config["load_combinations"]), 8)
        self.assertEqual(state.readiness["combination_source"], "reference_procedure")

    def test_default_cases_preserve_reference_coefficients_and_envelopes(self):
        combinations = reference_combinations()
        self.assertEqual([c["envelope_id"] for c in combinations], [1, 1, 2, 2, 2, 3, 3, 3])
        self.assertEqual(list(combinations[0]["factors"].values()), [1, 1, .8] + [.75] * 9 + [1.2, 1.2])
        self.assertEqual(list(combinations[-1]["factors"].values()), [1, 1, .95] + [1.5] * 11)

    def test_explicit_project_combinations_are_not_replaced(self):
        supplied = [{"name": "SLS", "envelope": "SERVICEABILITY", "factors": {"DL": 1}},
                    {"name": "ULS", "envelope": "STRENGTH", "factors": {"DL": 1.35}}]
        self.assertEqual(resolve_combinations({"load_combinations": supplied}), supplied)

    def test_reviewed_mode_retains_explicit_criteria_requirement(self):
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": "reviewed_engineering", "PLANT_LOAD_COMBINATIONS_FILE": ""}):
            with self.assertRaises(ValueError):
                resolve_combinations({})


if __name__ == "__main__":
    unittest.main()
