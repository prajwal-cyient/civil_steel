"""
test_agentic_workflow.py

Comprehensive unit and integration test for the Enhanced Autonomous Design Workflow:
1. Ladders and parameter stepping algorithms (profiles, bolts, plates, welds in mm and m).
2. Domain geometry checks (finite coordinates, non-zero length, BFS graph connectivity).
3. Frame Grader (boundary checks on stress and drift).
4. Joint Grader (boundary checks on plastic strain, bolts, and welds).
5. Loop C (Package validation recovery).
6. Durable SQLite persistence.
7. End-to-end execution of LangGraph state machine with Loop A, Loop B, and Gate 3 acceptance.
"""

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentic.ladders import (
    get_next_profile,
    get_next_plate_thickness,
    get_next_bolt_diameter,
    get_next_weld_size,
    step_parameter_expression,
    COLUMN_PROFILE_LADDER,
    BEAM_PROFILE_LADDER
)
from agentic.agents.a3_frame_generator_agent import check_geometry
from agentic.agents.a4_staad_analyzer_agent import grade_staad_results
from agentic.agents.a5_ideastatica_analyzer_agent import grade_joint_check_summary
from agentic.agents.a6_package_validator_agent import run_a6_package_validator
from agentic.orchestrator import run_agentic_workflow
from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
from agentic.store import SqliteWorkflowStore
from agentic.state import WorkflowState, GraderResult


class TestEnhancedAgenticWorkflow(unittest.TestCase):

    def test_ladders_and_expression_stepping(self):
        """Test profile and parameter ladders in both millimeter and meter units."""
        # 1. Profile ladders
        next_col = get_next_profile("CFRHS100X100X4", "column")
        self.assertEqual(next_col, "CFRHS100X100X5")

        next_beam = get_next_profile("HEA140", "beam")
        self.assertEqual(next_beam, "HEA160")

        # 2. Discrete stepping
        self.assertEqual(get_next_plate_thickness(10.0), 12.0)
        self.assertEqual(get_next_bolt_diameter(16.0), 20.0)
        self.assertEqual(get_next_weld_size(5.0), 6.0)

        # 3. Parameter expression stepping in meters
        bolt_m = step_parameter_expression("EP_d", "0.016", "bolt_dia")
        self.assertEqual(bolt_m, "0.020")

        weld_m = step_parameter_expression("EP_weld", "0.005", "weld_size")
        self.assertEqual(weld_m, "0.006")

        plate_m = step_parameter_expression("EP_pl_th", "0.010", "plate_thickness")
        self.assertEqual(plate_m, "0.012")

        # 4. Parameter expression stepping in millimeters
        bolt_mm = step_parameter_expression("bolt_diameter", "16", "bolt_dia")
        self.assertEqual(bolt_mm, "20")

        plate_mm = step_parameter_expression("CPL3_Thickness", "10", "plate_thickness")
        self.assertEqual(plate_mm, "12")

    def test_domain_geometry_checks(self):
        """Test geometric verification and BFS graph connectivity checks."""
        # Valid candidate
        valid_candidate = {
            "members": [{"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 5000]}],
            "members_split_beams": [
                {"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 5000]},
                {"name": "beam1", "p1": [0, 0, 5000], "p2": [2500, 0, 5000]}
            ]
        }
        # Should pass without exception
        check_geometry(valid_candidate)

        # Zero length member
        zero_len_candidate = {
            "members": [{"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 0]}],
            "members_split_beams": [{"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 0]}]
        }
        with self.assertRaises(ValueError):
            check_geometry(zero_len_candidate)

        # Disconnected frame components (two disjoint islands)
        disconnected_candidate = {
            "members": [
                {"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 5000]},
                {"name": "col2", "p1": [10000, 10000, 0], "p2": [10000, 10000, 5000]}
            ],
            "members_split_beams": [
                {"name": "col1", "p1": [0, 0, 0], "p2": [0, 0, 5000]},
                {"name": "col2", "p1": [10000, 10000, 0], "p2": [10000, 10000, 5000]}
            ]
        }
        with self.assertRaises(ValueError):
            check_geometry(disconnected_candidate)

    def test_frame_grader(self):
        """Test Frame Grader with passing and failing conditions."""
        # Pass
        staad_pass = {
            "member_type_max_ratios": {"column": 0.85, "beam": 0.70},
            "envelope_summary_results": {
                "1": {"node_displacements_limits_exceed_col_height_200": {"min_X": False, "max_X": False}}
            }
        }
        g_pass = grade_staad_results(staad_pass, col_height_mm=5000)
        self.assertTrue(g_pass.passed)

        # Fail stress
        staad_fail = {
            "member_type_max_ratios": {"column": 1.25, "beam": 0.70},
            "envelope_summary_results": {
                "1": {"node_displacements_limits_exceed_col_height_200": {"min_X": False, "max_X": False}}
            }
        }
        g_fail = grade_staad_results(staad_fail, col_height_mm=5000)
        self.assertFalse(g_fail.passed)
        self.assertEqual(g_fail.details.get("fix_target"), "column")

    def test_joint_grader(self):
        """Test Joint Grader with bolt (<=75%), weld (<=100%), and plate strain (<=5%) checks."""
        # Fail bolt with 78% (>75% rubric limit)
        failing_bolt = {
            "summary": [
                {"name": "Plates - eps_pl", "check_value": 2.5, "check_status": True, "skipped": False},
                {"name": "Bolts - Tension/Shear", "check_value": 78.0, "check_status": True, "skipped": False}
            ]
        }
        g_bolt = grade_joint_check_summary(failing_bolt)
        self.assertFalse(g_bolt.passed)
        self.assertEqual(g_bolt.details.get("fix_type"), "bolt_dia")

        # Fail local deformation (>3.0% limit)
        failing_def = {
            "summary": [
                {"name": "Plates - Local deformation", "check_value": 3.8, "check_status": False, "skipped": False},
                {"name": "Bolts", "check_value": 65.0, "check_status": True, "skipped": False}
            ]
        }
        g_def = grade_joint_check_summary(failing_def)
        self.assertFalse(g_def.passed)
        self.assertEqual(g_def.details.get("fix_type"), "plate_thickness")

        # Pass with underutilization advisory
        passing_check = {
            "summary": [
                {"name": "Plates - eps_pl", "check_value": 0.8, "check_status": True, "skipped": False},
                {"name": "Bolts", "check_value": 45.0, "check_status": True, "skipped": False},
                {"name": "Welds", "check_value": 35.0, "check_status": True, "skipped": False}
            ]
        }
        g_ok = grade_joint_check_summary(passing_check)
        self.assertTrue(g_ok.passed)
        self.assertFalse(g_ok.details.get("downsizing_permitted"))
        self.assertTrue(any("underutilized" in a.lower() for a in g_ok.details.get("advisories", [])))

    def test_loop_c_package_recovery(self):
        """Test Loop C recovering from missing connection evidence."""
        state = WorkflowState(
            run_id="test_loop_c",
            frame_candidate={"members": [{"name": "m1"}]},
            frame_grader_result=GraderResult(passed=True, summary="OK"),
            staad_results={"joint_groups": {"con1": {}}},
            joint_grader_results={"con1": GraderResult(passed=False, summary="Missing solve")}
        )
        # First attempt: triggers Loop C recovery
        res = run_a6_package_validator(state)
        self.assertEqual(res.agent_statuses.get("A6", {}).get("status"), "REVISING")
        self.assertEqual(res.loop_c_count, 1)
        self.assertEqual(res.validation_report.get("recovery_target"), "a5_ideastatica_analyzer")

    def test_sqlite_persistence(self):
        """Test saving and retrieving WorkflowState from SQLite."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "test_runs.db")
            store = SqliteWorkflowStore(db_path=db_path)

            initial = WorkflowState(
                run_id="run_persisted_123",
                status="RUNNING",
                active_column_profile="CFRHS120X120X5"
            )
            store.save_state(initial)

            # Clear memory cache to force SQLite disk read
            store._cache.clear()
            loaded = store.get_state("run_persisted_123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.run_id, "run_persisted_123")
            self.assertEqual(loaded.active_column_profile, "CFRHS120X120X5")

            # Check listing
            recent = store.list_runs()
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["run_id"], "run_persisted_123")

    def test_orchestrator_end_to_end(self):
        """Runs the complete 8-agent LangGraph workflow end-to-end."""
        payload = {
            "grid": {
                "n_long_bays": 5,
                "n_trans_bays": 3,
                "bay_long_mm": 2600,
                "bay_trans_mm": 3300,
                "col_height_mm": 5000,
                "total_structure_height": 5300,
                "column_profile": "CFRHS100X100X4",  # Loops A to upsize
                "beam_profile": "HEA140",
                "longitudinal_brace_profile": "CFRHS100X100X4",
                "transverse_brace_profile": "CFRHS100X100X4"
            },
            "load_configuration": {
                "radiator_support_type": "support_at_middle",
                "wind_speed_mph": 90
            }
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = loop.run_until_complete(run_agentic_workflow(payload))
        finally:
            loop.close()

        # Validate Gate 3 reached
        self.assertEqual(state.status, "PAUSED_AT_GATE3")
        self.assertTrue(len(state.logs) >= 8)

        # Validate Loop A occurred
        self.assertGreater(state.loop_a_count, 0)
        self.assertNotEqual(state.active_column_profile, "CFRHS100X100X4")

        # Validate Loop B occurred
        total_loop_b = sum(state.loop_b_count.values())
        self.assertGreater(total_loop_b, 0)

        # Validate Gate 3 acceptance
        accepted_state = accept_gate3(state, "Lead Structural Engineer")
        self.assertEqual(accepted_state.status, "COMPLETED")
        self.assertTrue(accepted_state.gate3_approved)
        self.assertEqual(accepted_state.accepted_by, "Lead Structural Engineer")


if __name__ == "__main__":
    unittest.main()
