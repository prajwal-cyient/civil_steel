"""Native overall success must not hide failed component or detailing checks."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agents.A5_idea_statica import A5B_trigger_api as idea


class IdeaResultStatusTests(unittest.TestCase):
    def calculate(self, folder, *, weld_status=True, native_passed=True, raw_error=False):
        template = Path(folder) / "connection.ideaCon"
        template.touch()
        client = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = client
        client.export.export_iom_connection_data.return_value = SimpleNamespace()
        client.calculation.get_raw_json_results.return_value = ['{"welds": [{"id": 7, "detailing": false}]}']
        if raw_error:
            client.calculation.get_raw_json_results.side_effect = RuntimeError("diagnostic service unavailable")
        checks = [SimpleNamespace(name=name, check_value=value, check_status=status,
                    skipped=False, unity_check_message="Detailing" if not status else "OK")
                  for name, value, status in [("Analysis", 100, True), ("Plates", 1, True),
                    ("Loc. deformation", 1, True), ("Bolts", 30, True), ("Welds", 67.78, weld_status)]]
        result = SimpleNamespace(passed=native_passed, result_summary=checks)
        with patch.object(idea, "_get_client", return_value=context), \
             patch.object(idea, "_resolve_connection", return_value=SimpleNamespace(id=1)), \
             patch.object(idea, "_get_parameters", return_value=[]), \
             patch.object(idea, "_validate_template_limits", return_value={"plastic_strain": .05, "local_deformation": .03}), \
             patch.object(idea, "calculate_connection", return_value=[result]):
            response = idea.design_joint({"template_project_path": str(template),
                "connection_selector": {"name": "CON1"}, "output_dir": folder})
        return response, client

    def test_native_success_with_failed_detailing_is_not_reported_as_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            result, client = self.calculate(folder, weld_status=False)
            self.assertFalse(result["passed"])
            self.assertTrue(result["native_calculation_passed"])
            self.assertIn("Detailing", result["validation_summary"])
            self.assertEqual(json.loads(Path(result["raw_results_file"]).read_text())["welds"][0]["id"], 7)
            client.calculation.get_raw_json_results.assert_called_once()
            self.assertEqual(client.calculation.get_raw_json_results.call_args.kwargs["_request_timeout"], (10.0, 60.0))

    def test_all_passing_checks_do_not_fetch_unrequested_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            result, client = self.calculate(folder)
            self.assertTrue(result["passed"])
            self.assertIsNone(result["raw_results_file"])
            client.calculation.get_raw_json_results.assert_not_called()

    def test_native_failure_cannot_be_overridden_by_passing_component_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            result, _ = self.calculate(folder, native_passed=False)
            self.assertFalse(result["passed"])
            self.assertFalse(result["native_calculation_passed"])
            self.assertIsNotNone(result["raw_results_file"])

    def test_optional_diagnostic_error_preserves_failed_result_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            result, _ = self.calculate(folder, weld_status=False, raw_error=True)
            self.assertFalse(result["passed"])
            self.assertIn("Detailing", result["validation_summary"])
            self.assertIsNone(result["raw_results_file"])
            self.assertIn("diagnostic service unavailable", result["raw_results_error"])


if __name__ == "__main__":
    unittest.main()
