import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common.staad_input import analysis_errors, prepare_analysis_input


class StaadInputTests(unittest.TestCase):
    def test_transient_empty_com_filename_is_retried_not_converted_to_dot(self):
        from common.staad_common import saved_model_path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.std"
            path.write_text("STAAD SPACE\nFINISH\n")
            staad = Mock()
            staad.GetSTAADFile.side_effect = ["", " ", str(path)]
            with patch("common.staad_common.time.sleep"):
                self.assertEqual(saved_model_path(staad, path), path.resolve())
            self.assertEqual(staad.GetSTAADFile.call_count, 3)

    def test_permanent_blank_has_bounded_diagnostic_and_no_save_or_solve(self):
        from common.staad_common import prepare_saved_analysis
        staad = Mock()
        staad.GetSTAADFile.return_value = ""
        with patch("common.staad_common.time.monotonic", side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, "last filename: ''"):
                prepare_saved_analysis(staad)
        staad.SaveModel.assert_not_called()
        staad.Analyze.assert_not_called()

    def test_active_model_change_is_not_silently_accepted(self):
        from common.staad_common import saved_model_path
        with tempfile.TemporaryDirectory() as directory:
            expected, other = Path(directory) / "expected.std", Path(directory) / "other.std"
            expected.touch(); other.touch()
            staad = Mock()
            staad.GetSTAADFile.return_value = str(other)
            with self.assertRaisesRegex(RuntimeError, "active model changed"):
                saved_model_path(staad, expected)

    def test_saved_input_uses_supported_option_and_verifies_actual_file(self):
        from common.staad_common import prepare_saved_analysis
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.std"
            source = "STAAD SPACE\nLOAD COMB 100\n1 1.0\nDEFINE ENVELOPE\nEND DEFINE ENVELOPE\nFINISH\n"
            path.write_text(source)
            staad = Mock()
            staad.GetSTAADFile.return_value = str(path)
            staad.Command.PerformAnalysis.side_effect = lambda option: path.write_text(prepare_analysis_input(source))
            self.assertEqual(prepare_saved_analysis(staad), path)
            staad.Command.PerformAnalysis.assert_called_once_with(0)
            self.assertEqual(next(Path(directory).glob("*.std.bak")).read_text(), source)
            prepare_saved_analysis(staad)
            staad.Command.PerformAnalysis.assert_called_once()

    def test_silent_sdk_failure_never_starts_solver(self):
        from common.staad_common import prepare_saved_analysis
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.std"
            path.write_text("STAAD SPACE\nDEFINE ENVELOPE\nEND DEFINE ENVELOPE\nFINISH\n")
            staad = Mock()
            staad.GetSTAADFile.return_value = str(path)
            with self.assertRaisesRegex(RuntimeError, "did not retain PERFORM ANALYSIS"):
                prepare_saved_analysis(staad)
            staad.Analyze.assert_not_called()
            staad.AnalyzeEx.assert_not_called()

    def test_missing_sdk_command_inserted_after_loads_before_envelopes(self):
        original = "STAAD SPACE\r\nLOAD 1 LOADTYPE Dead\r\nSELFWEIGHT Y -1\r\nLOAD COMB 100\r\n1 1.0\r\nDEFINE ENVELOPE\r\n100 ENVELOPE 1 TYPE STRENGTH\r\nEND DEFINE ENVELOPE\r\nFINISH\r\n"
        result = prepare_analysis_input(original)
        self.assertIn("1 1.0\r\nPERFORM ANALYSIS\r\nDEFINE ENVELOPE", result)
        self.assertEqual(result.replace("PERFORM ANALYSIS\r\n", ""), original)
        self.assertEqual(prepare_analysis_input(result), result)

    def test_existing_analysis_retained(self):
        original = "PERFORM ANALYSIS PRINT ALL\nDEFINE ENVELOPE\nEND DEFINE ENVELOPE\n"
        self.assertEqual(prepare_analysis_input(original), original)

    def test_ambiguous_order_is_rejected(self):
        with self.assertRaises(ValueError):
            prepare_analysis_input("DEFINE ENVELOPE\nEND DEFINE ENVELOPE\nPERFORM ANALYSIS\n")

    def test_report_includes_wrapped_error(self):
        self.assertEqual(analysis_errors('  *** ERROR : "DEFINE" COMMANDS FOR LOAD GENERATION SHOULD\n     BE PROVIDED BEFORE SPECIFYING PRIMARY LOAD CASES.\n\n'),
                         ['*** ERROR : "DEFINE" COMMANDS FOR LOAD GENERATION SHOULD BE PROVIDED BEFORE SPECIFYING PRIMARY LOAD CASES.'])


if __name__ == "__main__":
    unittest.main()
