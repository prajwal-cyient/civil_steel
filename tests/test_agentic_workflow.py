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
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

# Ensure src/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config.ladders import (
    get_next_profile,
    get_next_plate_thickness,
    get_next_bolt_diameter,
    get_next_weld_size,
    step_parameter_expression,
    COLUMN_PROFILE_LADDER,
    BEAM_PROFILE_LADDER
)
from agents.A3_generate_structure import (
    check_geometry,
    evaluate_frame_parameters,
    generate_members,
    generate_members_from_parameters,
)
from agents.A4_staad import grade_staad_results
from agents.A5_idea_statica import grade_joint_check_summary
from agents.A5_idea_statica.package_validation import run_a6_package_validator
from orchestration.workflow import run_agentic_workflow
from agents.A7_reporting import accept_gate3
from services.workflow_store import SqliteWorkflowStore
from orchestration.state import WorkflowState, GraderResult


class TestEnhancedAgenticWorkflow(unittest.TestCase):

    def test_frame_parameter_extraction_preserves_generated_geometry(self):
        base = {
            "n_long_bays": 2, "n_trans_bays": 1,
            "bay_long_mm": 4000, "bay_trans_mm": 3000,
            "col_height_mm": 5000, "total_structure_height": 5000,
            "beam_profile": "HEA140", "column_profile": "CFRHS120X120X5",
            "longitudinal_brace_bays": [{"bay": 1, "mirror": True}],
            "transverse_brace_bays": [{"bay": 1}],
        }
        cases = [
            (base, "c4c376232fbed34e35c56fd3400b06945be6f91ec0264f7a0c4ba92e022a9f50"),
            ({**base, "col_height_mm": 3000, "total_structure_height": 6000,
              "starting_point": [100, 200, 300], "vertical_base_eccentricity_mm": 50},
             "9fc07871a43ab16f5705c2eaba2d77c62ca34e9010f02f31797ddbacd9cd5d03"),
        ]
        dimensions = {"HEA140": 133, "CFRHS120X120X5": 120}
        widths = {"HEA140": 140, "CFRHS120X120X5": 120}
        for cfg, expected_hash in cases:
            with self.subTest(cfg=cfg), \
                 patch("agents.A3_generate_structure.A3A_evaluate_plan._lookup_profile_height",
                       side_effect=dimensions.__getitem__) as heights, \
                 patch("agents.A3_generate_structure.A3A_evaluate_plan._lookup_profile_width",
                       side_effect=widths.__getitem__) as profile_widths:
                original_entry_result = generate_members(cfg)
                parameters = evaluate_frame_parameters(cfg)
                split_result = generate_members_from_parameters(cfg, parameters)
                self.assertEqual(split_result, original_entry_result)
                encoded = json.dumps(split_result, sort_keys=True, separators=(",", ":")).encode()
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), expected_hash)
                self.assertEqual(heights.call_count, 4)
                self.assertEqual(profile_widths.call_count, 4)

    def test_frame_parameters_reject_invalid_or_stale_heights(self):
        cfg = {
            "n_long_bays": 1, "n_trans_bays": 1,
            "bay_long_mm": 4000, "bay_trans_mm": 3000,
            "col_height_mm": 3000, "total_structure_height": 4000,
        }
        with patch("agents.A3_generate_structure.A3A_evaluate_plan._lookup_profile_height", return_value=133), \
             patch("agents.A3_generate_structure.A3A_evaluate_plan._lookup_profile_width", return_value=120):
            with self.assertRaisesRegex(ValueError, "too small"):
                evaluate_frame_parameters({**cfg, "total_structure_height": 266})
            parameters = evaluate_frame_parameters(cfg)
            with self.assertRaisesRegex(ValueError, "do not match"):
                generate_members_from_parameters({**cfg, "beam_profile": "HEA160"}, parameters)
            with self.assertRaisesRegex(ValueError, "do not match"):
                generate_members_from_parameters({**cfg, "total_structure_height": 5000}, parameters)

    def test_a3_stages_match_existing_entry_and_reject_stale_plan(self):
        from agents import A3_generate_structure as a3
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, connection_scope as a3_scope
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, connection_scope as a3_scope
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, connection_scope as a3_scope
        cfg = {
            "n_long_bays": 2, "n_trans_bays": 1,
            "bay_long_mm": 4000, "bay_trans_mm": 3000,
            "col_height_mm": 5000, "total_structure_height": 5000,
            "beam_profile": "HEA140", "column_profile": "CFRHS120X120X5",
            "longitudinal_brace_bays": [{"bay": 1}],
        }
        original = WorkflowState(run_id="original", grid_config=cfg)
        staged = WorkflowState(run_id="staged", grid_config=cfg)
        with patch.object(a3_a, "validate_profile_catalogue"), \
             patch.object(a3_scope, "template_coverage", return_value={"missing": []}), \
             patch.object(a3_a, "_lookup_profile_height", return_value=133), \
             patch.object(a3_a, "_lookup_profile_width", return_value=120):
            a3.run_a3_frame_generator(original)
            a3.evaluate_a3_frame(staged)
            staged = WorkflowState.model_validate_json(staged.model_dump_json())
            a3.generate_a3_frame(staged)
            self.assertEqual(staged.agent_statuses["A3"]["status"], "PASSED")
            self.assertEqual(staged.frame_candidate, original.frame_candidate)
            self.assertEqual(staged.grid_config, original.grid_config)
            self.assertEqual(staged.design_generation, original.design_generation)
            self.assertFalse(staged.frame_plan)

            stale = WorkflowState(run_id="stale", grid_config=cfg)
            a3.evaluate_a3_frame(stale)
            stale.grid_config["longitudinal_brace_bays"][0]["bay"] = 2
            a3.generate_a3_frame(stale)
            self.assertEqual(stale.status, "FAILED")
            self.assertIn("no longer matches", stale.error)
            self.assertFalse(stale.frame_plan)
            self.assertFalse(stale.frame_candidate)

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
        staad_pass["analysis_status"] = {"ReturnValue": 2, "NoOfErrors": 0}
        staad_pass["units"] = {"displacement": "mm"}
        staad_pass["envelope_summary_results"]["1"]["node_displacements_limits"] = dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), 10.0)
        staad_pass["envelope_summary_results"]["1"]["node_displacements_limits_exceed_col_height_200"].update(min_Z=False, max_Z=False)
        g_pass = grade_staad_results(staad_pass, col_height_mm=5000)
        self.assertTrue(g_pass.passed)

        # Fail stress
        staad_fail = {
            "member_type_max_ratios": {"column": 1.25, "beam": 0.70},
            "envelope_summary_results": {
                "1": {"node_displacements_limits_exceed_col_height_200": {"min_X": False, "max_X": False}}
            }
        }
        staad_fail["analysis_status"] = {"ReturnValue": 2, "NoOfErrors": 0}
        staad_fail["units"] = {"displacement": "mm"}
        staad_fail["envelope_summary_results"]["1"]["node_displacements_limits"] = dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), 10.0)
        staad_fail["envelope_summary_results"]["1"]["node_displacements_limits_exceed_col_height_200"].update(min_Z=False, max_Z=False)
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
                {"name": "Plates - Local deformation", "check_value": 126.67, "check_status": False, "skipped": False},
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
        with patch("agents.A5_idea_statica.package_validation.frame_signature", return_value="current"), \
             patch("agents.A5_idea_statica.package_validation.grade_staad_results",
                   return_value=GraderResult(passed=True, summary="OK")):
            state.staad_results["candidate_signature"] = "current"
            res = run_a6_package_validator(state)
        self.assertEqual(res.agent_statuses.get("A6", {}).get("status"), "REVISING")
        self.assertEqual(res.loop_c_count, 1)
        self.assertEqual(res.validation_report.get("recovery_target"), "a5_ideastatica_analyzer")

    def test_strict_package_audit_stops_stale_frame_but_retries_joint_evidence(self):
        from agents.A5_idea_statica import package_validation as audit
        state = WorkflowState(
            run_id="strict_audit",
            frame_candidate={"members": [{"name": "m1"}]},
            frame_grader_result=GraderResult(passed=True, summary="OK"),
            staad_results={"joint_groups": {"con1": {}}, "candidate_signature": "old"},
            joint_grader_results={"con1": GraderResult(passed=False, summary="Missing solve")},
        )
        with patch.object(audit, "frame_signature", return_value="current"), \
             patch.object(audit, "grade_staad_results", return_value=GraderResult(passed=True, summary="OK")):
            audit.run_a6_package_validator(state)
        self.assertEqual(state.status, "FAILED")
        self.assertIn("does not belong to the current frame", state.error)
        self.assertFalse(state.validation_report["passed"])
        self.assertEqual(state.loop_c_count, 0)

        joint_only = WorkflowState(
            run_id="joint_audit",
            frame_candidate={"members": [{"name": "m1"}]},
            frame_grader_result=GraderResult(passed=True, summary="OK"),
            staad_results={"joint_groups": {"con1": {}}, "candidate_signature": "current"},
            joint_grader_results={"con1": GraderResult(passed=False, summary="Missing solve")},
        )
        with patch.object(audit, "frame_signature", return_value="current"), \
             patch.object(audit, "grade_staad_results", return_value=GraderResult(passed=True, summary="OK")):
            audit.run_a6_package_validator(joint_only)
        self.assertEqual(joint_only.agent_statuses["A6"]["status"], "REVISING")
        self.assertEqual(joint_only.validation_report["recovery_target"], "a5_ideastatica_analyzer")

        no_groups = WorkflowState(
            run_id="missing_staad_scope",
            frame_candidate={"members": [{"name": "m1"}]},
            frame_grader_result=GraderResult(passed=True, summary="OK"),
            staad_results={"candidate_signature": "current"},
        )
        with patch.object(audit, "frame_signature", return_value="current"), \
             patch.object(audit, "grade_staad_results", return_value=GraderResult(passed=True, summary="OK")):
            audit.run_a6_package_validator(no_groups)
        self.assertEqual(no_groups.status, "FAILED")
        self.assertIn("No connection groups returned by STAAD", no_groups.error)
        self.assertEqual(no_groups.loop_c_count, 0)

    def test_package_graph_has_no_frame_recovery_edge_and_rejects_invalid_target(self):
        from orchestration import workflow as orchestrator
        edges = orchestrator.build_workflow_graph().compile().get_graph().edges
        self.assertNotIn(
            ("a5c_package_audit", "a3_frame_generator"),
            {(edge.source, edge.target) for edge in edges},
        )
        self.assertIn(
            ("a5_ideastatica_analyzer", "a6_tekla_modeler"),
            {(edge.source, edge.target) for edge in edges},
        )
        self.assertNotIn("a5c_package_audit", {edge.source for edge in edges})
        state = WorkflowState(
            run_id="invalid_recovery", status="RUNNING", loop_c_count=1,
            validation_report={"recovery_target": "a3_frame_generator"},
        )
        def invalid_audit(candidate, *, agent_id):
            candidate.agent_statuses["A6"] = {"status": "REVISING"}
            return candidate

        with patch.object(orchestrator, "run_a6_package_validator", side_effect=invalid_audit), \
             patch.object(orchestrator, "_sync_state"), patch.object(orchestrator, "guarded"), \
             patch.object(orchestrator, "deep_agents_enabled", return_value=False):
            result = asyncio.run(orchestrator.node_a6(state))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("recovery is invalid", result.error)
        self.assertEqual(orchestrator.route_after_a6(result), "__end__")

    def test_idea_routes_to_tekla_only_after_package_audit_passes(self):
        from orchestration.workflow import route_after_a5

        state = WorkflowState(
            run_id="idea_routing", layout_version=2, status="RUNNING",
            agent_statuses={"A5": {"status": "PASSED"}, "A5c": {"status": "PASSED"}},
            validation_report={"passed": True},
        )
        self.assertEqual(route_after_a5(state), "a6_tekla_modeler")
        state.validation_report["passed"] = False
        self.assertEqual(route_after_a5(state), "__end__")
        state.validation_report["passed"] = True
        state.agent_statuses["A5c"]["status"] = "REVISING"
        self.assertEqual(route_after_a5(state), "__end__")
        state.agent_statuses["A5"]["status"] = "REVISING"
        self.assertEqual(route_after_a5(state), "a5_ideastatica_analyzer")

    def test_graph_gate_requires_verified_publication_and_pending_human_review(self):
        from orchestration import workflow as orchestrator
        state = WorkflowState(
            run_id="gate", status="PAUSED_AT_GATE3",
            validation_report={"passed": True},
            agent_statuses={"A7": {"status": "PASSED"}, "A8": {"status": "AWAITING_APPROVAL"}},
            final_report={"gate3_status": "PENDING_ENGINEER_ACCEPTANCE"},
        )
        with patch.object(orchestrator, "_sync_state"):
            result = asyncio.run(orchestrator.node_gate3(state))
            self.assertEqual(result.status, "PAUSED_AT_GATE3")
            self.assertFalse(result.gate3_approved)
            result.validation_report["passed"] = False
            result = asyncio.run(orchestrator.node_gate3(result))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("verified publication", result.error)

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

    def test_orchestrator_stops_on_failed_analysis(self):
        """Exercise graph failure routing with adapters replaced; never touch live models."""
        from orchestration import workflow as orchestrator
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, A3B_generate_structure as a3_b, connection_scope as a3_scope
        from agents.A4_staad import A4B_trigger_api as a4_b
        candidate = {"members": [{"name": "column", "p1": [0, 0, 0], "p2": [0, 0, 5000]}],
                     "members_split_beams": [{"name": "column", "p1": [0, 0, 0], "p2": [0, 0, 5000]}]}
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteWorkflowStore(os.path.join(directory, "runs.db"))
            with patch("services.workflow_store.WORKFLOW_STORE", store), \
                 patch.object(orchestrator, "run_a2_readiness", side_effect=lambda state: state), \
                 patch.object(a3_b, "generate_members_from_parameters", return_value=candidate), \
                 patch.object(a3_a, "validate_profile_catalogue"), \
                 patch.object(a3_scope, "template_coverage", return_value={"missing": []}), \
                 patch.object(a4_b, "send_to_staad", new=AsyncMock(return_value={"analysis_failed": True})), \
                 patch.object(orchestrator, "run_a5_staged") as idea:
                state = asyncio.run(run_agentic_workflow({"grid": {}, "load_configuration": {"load_combinations": [{"name": "SLS", "envelope": "SERVICEABILITY", "factors": {"DL": 1}}, {"name": "ULS", "envelope": "STRENGTH", "factors": {"DL": 1.35}}]}}, run_id="controlled_failure"))
        self.assertEqual(state.status, "FAILED")
        self.assertEqual(state.current_agent, "A4")
        idea.assert_not_called()


if __name__ == "__main__":
    unittest.main()
