"""Controlled engineering-loop tests. Never invoke the desktop applications."""

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents import A4_staad as a4
from agents.A4_staad import A4B_trigger_api as a4_b, A4D_llm_reasoning as a4_d
from agents import A5_idea_statica as a5
from agents.A5_idea_statica import A5A_build_json as a5_a, A5B_trigger_api as a5_b
from orchestration.state import WorkflowState, GraderResult


def valid_staad():
    return {
        "analysis_status": {"ReturnValue": 2, "NoOfErrors": 0},
        "units": {"displacement": "mm"},
        "member_type_max_ratios": {"column": 0.8, "beam_longitudinal": 0.7},
        "member_ids_by_type": {"column": [1], "beam_longitudinal": [2]},
        "member_data_by_id": {1: {"name": "column-0-0", "profile": "CFRHS120X120X5"},
                              2: {"name": "beam-x-0-0", "profile": "HEA140"}},
        "member_ratios": {1: {"name": "column-0-0", "ratio": 0.8}, 2: {"name": "beam-x-0-0", "ratio": 0.7}},
        "envelope_summary_results": {1: {
            "node_displacements_limits": dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), 10.0),
            "node_displacements_limits_exceed_col_height_200": dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), False),
        }},
        "joint_groups": {"first": {}, "second": {}},
    }


def joint_result(value=50.0, status=True, parameters=None):
    return {"passed": status and value <= 100, "check_summary": {
        "summary": [{"name": "Welds", "check_value": value, "check_status": status, "skipped": False}]
                   + [{"name": name, "check_value": val, "check_status": True, "skipped": False}
                      for name,val in (("Analysis",100), ("Plates",0), ("Loc. deformation",0), ("Bolts",0))],
        "operation_parameters": parameters or [{"key": "EP_weld", "expression": "0.005"}],
    }}


def payload(state, group):
    return {"group": group, "operation_parameters": deepcopy(state.active_operation_params.get(group, []))}


class EngineeringGuardTests(unittest.TestCase):
    def setUp(self):
        from services.workflow_store import SqliteWorkflowStore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store_patch = patch("services.workflow_store.WORKFLOW_STORE", SqliteWorkflowStore(str(Path(directory.name) / "runs.db")))
        store_patch.start()
        self.addCleanup(store_patch.stop)

    def state(self):
        return WorkflowState(run_id="guards", status="RUNNING", design_generation=1,
            staad_results=valid_staad(), frame_grader_result=GraderResult(passed=True, summary="OK"))

    def test_staad_execution_errors_cannot_pass(self):
        result = valid_staad()
        result.update(analysis_failed=True, analysis_status={"ReturnValue": 4, "NoOfErrors": 132})
        grade = a4.grade_staad_results(result, 5000)
        self.assertFalse(grade.passed)
        self.assertFalse(grade.corrective_action_needed)

    def test_missing_nonfinite_or_negative_ratios_cannot_pass(self):
        for ratios in ({}, {"column": float("inf")}, {"column": float("nan")}, {"column": -1}):
            with self.subTest(ratios=ratios):
                result = valid_staad()
                result["member_type_max_ratios"] = ratios
                self.assertFalse(a4.grade_staad_results(result, 5000).passed)

    def test_missing_displacement_checks_cannot_pass(self):
        result = valid_staad()
        result["envelope_summary_results"] = {}
        self.assertFalse(a4.grade_staad_results(result, 5000).passed)

    def test_displacement_flag_cannot_hide_exceeded_limit(self):
        result = valid_staad()
        result["envelope_summary_results"][1]["node_displacements_limits"]["max_X"] = 30.0
        grade = a4.grade_staad_results(result, 5000)
        self.assertFalse(grade.passed)
        self.assertTrue(grade.details["invalid_results"])

    def test_live_exception_stops_a4_without_simulation(self):
        state = self.state()
        with patch.object(a4_b, "send_to_staad", new=AsyncMock(side_effect=RuntimeError("COM unavailable"))):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(state.status, "FAILED")
        self.assertIn("COM unavailable", state.error)

    def test_failed_analysis_stops_before_a5(self):
        from orchestration.workflow import route_after_a4
        state = self.state()
        result = valid_staad()
        result["analysis_failed"] = True
        with patch.object(a4_b, "send_to_staad", new=AsyncMock(return_value=result)):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(route_after_a4(state), "__end__")
        self.assertEqual(state.loop_a_count, 0)

    def test_frame_grade_correction_and_retry_limit(self):
        from orchestration.workflow import route_after_a4
        result = valid_staad()
        result["member_type_max_ratios"]["column"] = 1.2
        result["member_ratios"][1]["ratio"] = 1.2
        grader = a4.grade_staad_results(result, 5000)
        state = self.state()
        state.active_column_profile = "CFRHS120X120X5"
        state.max_loop_a = 1
        option = {"id": "CFRHS120X120X6", "profile": "CFRHS120X120X6"}

        with patch.object(a4_d, "frame_options", return_value=[option]):
            a4.apply_staad_grade(state, result, grader)
        self.assertEqual(state.active_column_profile, option["profile"])
        self.assertEqual(state.loop_a_count, 1)
        self.assertEqual(route_after_a4(state), "a3_frame_generator")
        self.assertEqual(state.loop_a_history[0]["affected_members"][0]["id"], 1)

        a4.apply_staad_grade(state, result, grader)
        self.assertEqual(state.status, "FAILED")
        self.assertIn("retry limit reached", state.error)
        self.assertEqual(route_after_a4(state), "__end__")

    def test_dominant_bracing_failure_targets_brace(self):
        result = valid_staad()
        result["member_type_max_ratios"]["bracing_longitudinal"] = 1.3
        self.assertEqual(a4.grade_staad_results(result, 5000).details["fix_target"], "longitudinal_brace")

    def test_false_status_with_small_value_is_not_claimed_over_limit(self):
        grade = a5.grade_joint_check_summary(joint_result(1.8e-11, False)["check_summary"])
        self.assertFalse(grade.passed)
        self.assertIn("check_status=False", grade.summary)
        self.assertNotIn("exceeds", grade.summary)
        self.assertFalse(grade.corrective_action_needed)

    def test_local_deformation_is_not_graded_as_plate_strain(self):
        grade = a5.grade_joint_check_summary({"summary": [
            {"name": "Plates - Local deformation", "check_value": 126.67, "check_status": True},
        ]})
        self.assertFalse(grade.passed)
        self.assertEqual(grade.details["max_deformation_utilization"], 126.67)
        self.assertEqual(grade.details["max_plate_utilization"], 0)

    def test_empty_or_incomplete_connection_results_cannot_pass(self):
        for summary in ({}, {"summary": [{"name": "Welds", "check_value": None, "check_status": True}]}):
            self.assertFalse(a5.grade_joint_check_summary(summary).passed)

    def test_generic_plate_summary_is_utilization_not_raw_strain(self):
        grade = a5.grade_joint_check_summary({"summary": [
            {"name": "Plates", "check_value": 85.0, "check_status": True},
            {"name": "Loc. deformation", "check_value": 90.0, "check_status": True},
        ]})
        self.assertTrue(grade.passed)
        self.assertEqual(grade.details["max_plate_utilization"], 85.0)

    def test_template_cannot_relax_or_disable_physical_checks(self):
        prefix = "calculationCommon/Checks/Shared/"
        setup = {prefix + "LimitPlasticStrain": 0.05, prefix + "LocalDeformationLimit": 0.03,
                 "calculationCommon/Analysis/AnalysisGeneral/Shared/LocalDeformationCheck": True}
        self.assertEqual(a5._validate_template_limits(setup)["plastic_strain"], 0.05)
        with self.assertRaises(a5.IdeaStaticaJointError):
            a5._validate_template_limits({**setup, prefix + "LimitPlasticStrain": 0.1})
        with self.assertRaises(a5.IdeaStaticaJointError):
            a5._validate_template_limits({})

    def test_weld_correction_changes_only_exposed_weld_parameter(self):
        parameters = [{"key": "CPL3_Thickness", "expression": "10"}, {"key": "EP_weld", "expression": "0.005"}]
        updated, changes = a5._correct_connection_parameters(parameters, "weld_size")
        self.assertEqual(updated[0]["expression"], "10")
        self.assertEqual(updated[1]["expression"], "0.006")
        self.assertEqual([c["key"] for c in changes], ["EP_weld"])
        self.assertEqual(a5._correct_connection_parameters(parameters[:1], "weld_size")[1], [])

    def test_correction_never_downsizes_or_rewrites_formula(self):
        parameters = [{"key": "EP_weld", "expression": "0.020"}, {"key": "CPL_weld", "expression": "GetWeldSize('CPL')"}]
        updated, changes = a5._correct_connection_parameters(parameters, "weld_size")
        self.assertEqual(changes, [])
        self.assertEqual(updated, parameters)

    def test_failed_run_cannot_be_accepted(self):
        from agents.A7_reporting import accept_gate3
        state = self.state()
        state.status = "FAILED"
        with self.assertRaises(ValueError):
            accept_gate3(state)

    def test_a5_retries_only_changed_group(self):
        state = self.state()
        first, fail, passed = joint_result(), joint_result(120, False), joint_result(parameters=[{"key": "EP_weld", "expression": "0.006"}])
        with patch.object(a5_a, "_connection_payload", side_effect=payload), patch.object(a5_b, "design_joint", side_effect=[first, fail, passed]) as solve:
            a5.run_a5_ideastatica_analyzer(state)
            self.assertEqual(state.agent_statuses["A5"]["status"], "REVISING")
            a5.run_a5_ideastatica_analyzer(state)
            self.assertEqual(state.agent_statuses["A5"]["status"], "PASSED")
            self.assertEqual([call.args[0]["group"] for call in solve.call_args_list], ["first", "second", "second"])

    def test_rejected_update_stops_without_fake_correction(self):
        state = self.state()
        with patch.object(a5_a, "_connection_payload", side_effect=payload), patch.object(a5_b, "design_joint", side_effect=RuntimeError("422 rejected")):
            a5.run_a5_ideastatica_analyzer(state)
        self.assertEqual(state.status, "FAILED")
        self.assertEqual(state.loop_b_history, [])
        self.assertEqual(state.ideastatica_results, {})

    def test_live_adapter_rejects_unknown_template_parameter_before_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "test.ideaCon"
            template.touch()
            client = MagicMock()
            context = MagicMock()
            context.__enter__.return_value = client
            data = {"template_project_path": str(template), "connection_selector": {"name": "CON1"},
                    "output_dir": directory, "operation_parameters": [{"key": "CPL3_Thickness", "expression": "12"}]}
            with patch.object(a5_b, "_get_client", return_value=context), \
                 patch.object(a5_b, "_resolve_connection", return_value=SimpleNamespace(id=1)), \
                 patch.object(a5_b, "_get_parameters", return_value=[SimpleNamespace(key="EP_weld")]):
                with self.assertRaisesRegex(a5.IdeaStaticaJointError, "not exposed"):
                    a5.design_joint(data)
            client.calculation.calculate.assert_not_called()

    def test_live_adapter_does_not_ignore_parameter_update_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "test.ideaCon"
            template.touch()
            client = MagicMock()
            context = MagicMock()
            context.__enter__.return_value = client
            data = {"template_project_path": str(template), "connection_selector": {"name": "CON1"},
                    "output_dir": directory, "operation_parameters": [{"key": "EP_weld", "expression": "0.006"}]}
            with patch.object(a5_b, "_get_client", return_value=context), \
                 patch.object(a5_b, "_resolve_connection", return_value=SimpleNamespace(id=1)), \
                 patch.object(a5_b, "_get_parameters", return_value=[SimpleNamespace(key="EP_weld")]), \
                 patch.object(a5_b, "_set_parameters", side_effect=RuntimeError("422")):
                with self.assertRaisesRegex(a5.IdeaStaticaJointError, "rejected"):
                    a5.design_joint(data)
            client.calculation.calculate.assert_not_called()

    def test_exhausted_retry_stops_before_other_groups(self):
        state = self.state()
        state.loop_b_count["first"] = state.max_loop_b
        with patch.object(a5_a, "_connection_payload", side_effect=payload), patch.object(a5_b, "design_joint", return_value=joint_result(120, False)) as solve:
            a5.run_a5_ideastatica_analyzer(state)
        self.assertEqual(state.status, "FAILED")
        self.assertEqual(solve.call_count, 1)

    def test_strength_envelopes_combined_without_sample_loads(self):
        state = self.state()
        state.staad_results["load_envelopes"] = [{"envelopeId": 1, "envelopeType": "SERVICEABILITY"}, {"envelopeId": 2, "envelopeType": "STRENGTH"}, {"envelopeId": 3, "envelopeType": "STRENGTH"}]
        data = {"member_updates": [{"member_name": "column"}], "load_effects": [{"name": "ULS", "member_loadings": [{"member_name": "column", "n": 10}]}]}
        state.staad_results["envelope_load_cases_by_joint_group"] = {2: {"first": data}, "3": {"first": data}}
        with patch.object(a5_a.os.path, "isfile", return_value=True):
            result = a5._connection_payload(state, "first")
            self.assertEqual(len(result["load_effects"]), 2)
            self.assertEqual(result["operation_parameters"], [])
            self.assertEqual(result["output_dir"], str(Path(a5.__file__).parents[3] / "output_first"))
            state.staad_results["results_local_or_global"] = 1
            with self.assertRaisesRegex(a5.IdeaStaticaJointError, "local member forces"):
                a5._connection_payload(state, "first")
            state.staad_results["results_local_or_global"] = 0
            del state.staad_results["envelope_load_cases_by_joint_group"]["3"]
            with self.assertRaises(a5.IdeaStaticaJointError):
                a5._connection_payload(state, "first")

    def test_staad_reanalysis_invalidates_connection_evidence(self):
        state = self.state()
        state.ideastatica_results["stale"] = {"passed": True}
        state.active_operation_params["stale"] = []
        state.tekla_results = {"created": 3}
        with patch.object(a4_b, "send_to_staad", new=AsyncMock(return_value=valid_staad())):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(state.ideastatica_results, {})
        self.assertEqual(state.active_operation_params, {})
        self.assertEqual(state.tekla_results, {})

    def test_property_mapping_and_zero_id_rejection(self):
        # Import COM helpers in a separate process/thread context is unnecessary:
        # extract this pure adapter and inject a fake property interface.
        import ast
        source = Path("src/agents/A4_staad/staad_com_helpers.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {"_assign_property", "tekla2staad"}
        functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        import re
        from typing import Dict, Optional
        ns = dict(OSProperty=object, Dict=Dict, Optional=Optional, re=re,
                  StaadMemberError=RuntimeError, PROFILE_NAME_MAP={}, MATERIAL_MAPPING={}, country2code=lambda _: 7)
        exec(compile(functions, "adapter", "exec"), ns)
        prop = MagicMock()
        prop.CreateBeamPropertyFromTable.return_value = 1
        prop.AssignBeamProperty.return_value = False  # STAAD 2025 / SDK 26 wrapper mismatch
        prop.GetBeamSectionName.return_value = "SHS120x120x6"
        prop.GetBeamMaterialName.return_value = "STEEL"
        member = {"profile": "CFRHS120X120X6", "material": "S355JR"}
        ns["_assign_property"](prop, 1, member, {})
        prop.CreateBeamPropertyFromTable.assert_called_with(34, "SHS120x120x6", 0, 0.0, 0.0)
        self.assertEqual(ns["tekla2staad"]("HEA140", "S355JR")[0], "HE140A")
        prop.GetBeamSectionName.return_value = "WRONG_SECTION"
        with self.assertRaisesRegex(RuntimeError, "read-back mismatch"):
            ns["_assign_property"](prop, 2, member, {})
        prop.CreateBeamPropertyFromTable.return_value = 0
        with self.assertRaises(RuntimeError):
            ns["_assign_property"](prop, 2, member, {})

    def test_tekla_partial_publication_never_reaches_reporting(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        with patch.object(a7, "send_to_tekla", return_value={"created": 1, "failed": 1, "errors": ["connection failed"]}):
            a7_d.run_a7_tekla_modeler(state)
        self.assertEqual(state.status, "FAILED")

    def test_idea_subgraph_reuses_verified_groups_and_runs_on_worker_thread(self):
        state = self.state()
        owner_thread = threading.get_ident()
        calls = []

        def solve(request):
            self.assertEqual(threading.get_ident(), owner_thread)
            calls.append(request["group"])
            return joint_result()

        with patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", side_effect=solve):
            asyncio.run(a5.run_a5_staged(state))
            self.assertEqual(state.agent_statuses["A5"]["status"], "PASSED")
            state.ideastatica_results["second"]["check_summary"]["summary"] = []
            asyncio.run(a5.run_a5_staged(state))
        self.assertEqual(calls, ["first", "second", "second"])
        self.assertEqual(state.agent_statuses["A5"]["status"], "PASSED")

    def test_idea_graph_audits_after_required_groups_and_routes_recovery(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A5_idea_statica.graph import run_idea_graph
        from orchestration.workflow import route_after_a5
        state = self.state()
        state.layout_version = 2
        calls = []

        def solve(request):
            calls.append(request["group"])
            return joint_result()

        def audit(candidate, *, agent_id):
            self.assertEqual(agent_id, "A5c")
            self.assertEqual(calls, ["first", "second"])
            candidate.validation_report = {
                "passed": False, "recovery_target": "a5_ideastatica_analyzer"
            }
            candidate.loop_c_count = 1
            candidate.agent_statuses[agent_id] = {"status": "REVISING", "message": "Connection evidence invalid."}
            return candidate

        with patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", side_effect=solve), \
             patch.object(package, "run_a6_package_validator", side_effect=audit):
            result = asyncio.run(run_idea_graph(state, audit_package=True))
        self.assertEqual(result.agent_statuses["A5"]["status"], "REVISING")
        self.assertEqual(result.substage_statuses["A5c"]["status"], "REVISING")
        self.assertEqual(result.substage_statuses["A5d"]["status"], "REVISING")
        self.assertEqual(route_after_a5(result), "a5_ideastatica_analyzer")

    def test_idea_input_change_during_solve_cannot_be_graded(self):
        state = self.state()

        def solve(request):
            state.active_operation_params[request["group"]] = [{"key": "EP_d", "expression": "0.02"}]
            return joint_result()

        with patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", side_effect=solve):
            asyncio.run(a5.run_a5_staged(state))
        self.assertEqual(state.status, "FAILED")
        self.assertIn("inputs or template changed", state.error)
        self.assertFalse(state.ideastatica_results)
        self.assertFalse(state.joint_grader_results)

    def test_idea_evidence_change_after_grade_cannot_drive_correction(self):
        state = self.state()
        with patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", return_value=joint_result()):
            context = a5.begin_a5(state)
            a5.prepare_a5(state, context)
            a5.execute_a5(state, context)
            a5.evaluate_a5(state, context)
            state.ideastatica_results["first"]["check_summary"]["operation_parameters"][0]["expression"] = "0.010"
            a5.decide_a5(state, context)
        self.assertEqual(state.status, "FAILED")
        self.assertIn("grade or evidence changed", state.error)
        self.assertFalse(state.loop_b_history)

    def test_tekla_subgraph_checks_request_and_keeps_native_call_on_worker_thread(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        owner_thread = threading.get_ident()

        def publish(payload):
            self.assertEqual(threading.get_ident(), owner_thread)
            return {"created": 1, "verified_members": 1, "failed": 0}

        with patch.object(a7, "send_to_tekla", side_effect=publish):
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.agent_statuses["A7"]["status"], "PASSED")
        self.assertFalse(result.tekla_request)

        stale = self.state()
        stale.frame_candidate = {"members": [{"name": "column"}]}
        stale.validation_report = {"passed": True}
        a7_a.prepare_a7_tekla(stale)
        stale.frame_candidate["members"].append({"name": "beam"})
        with patch.object(a7, "send_to_tekla") as send:
            a7.execute_a7_tekla(stale)
        self.assertEqual(stale.status, "FAILED")
        send.assert_not_called()

    def test_tekla_simulation_output_never_retries(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        with patch.object(a7, "send_to_tekla",
                          return_value={"created": 1, "verified_members": 1, "failed": 0, "simulation": True}) as send, \
             patch("services.correction_advisor.choose_correction") as advisor:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("simulation output", result.error)
        self.assertEqual(send.call_count, 1)
        advisor.assert_not_called()
        self.assertEqual(result.tekla_retry_count, 0)
        self.assertIsNotNone(result.tekla_grader_result)
        self.assertFalse(result.tekla_grader_result.passed)
        self.assertFalse(result.tekla_grader_result.corrective_action_needed)

    def test_tekla_failed_publication_retries_the_same_payload(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        for native_result in (
            {"created": 1, "verified_members": 0, "failed": 1},
            {"created": 0, "verified_members": 0, "failed": 0},
            {"created": 1, "verified_members": 1, "errors": ["read-back failed"]},
        ):
            with self.subTest(native_result=native_result):
                state = self.state()
                state.layout_version = 2
                state.max_tekla_retries = 1
                state.frame_candidate = {"members": [{"name": "column"}]}
                state.validation_report = {"passed": True}

                def revalidate(candidate, *, agent_id):
                    candidate.agent_statuses[agent_id] = {"status": "PASSED"}
                    return candidate

                with patch.object(package, "run_a6_package_validator", side_effect=revalidate), \
                     patch("services.correction_advisor.choose_correction", return_value={"id": "retry_publish"}) as advisor, \
                     patch.object(a7, "send_to_tekla", return_value=native_result) as send:
                    result = asyncio.run(a7_d.run_a7_staged(state))
                self.assertEqual(result.status, "FAILED")
                self.assertIn("retry limit reached", result.error)
                self.assertTrue(result.tekla_grader_result.corrective_action_needed)
                self.assertEqual(result.tekla_grader_result.details["phase"], "fabrication_readback")
                self.assertEqual(result.substage_statuses["A6c"]["status"], "FAILED")
                self.assertEqual(result.tekla_retry_count, 1)
                self.assertEqual(result.tekla_retry_history[0]["phase"], "fabrication_readback")
                self.assertEqual(result.tekla_retry_history[0]["action"], "retry_publish")
                self.assertEqual(send.call_count, 2)
                self.assertEqual(send.call_args_list[0].args[0]["members"], send.call_args_list[1].args[0]["members"])
                advisor.assert_called_once()

    def test_tekla_stale_grade_does_not_choose_the_retry_phase(self):
        from agents.A6_tekla.publication_corrections import decide_tekla_outcome
        state = self.state()
        state.tekla_request = {"members": [{"name": "column"}]}
        state.tekla_results = {"created": 1, "verified_members": 0, "failed": 1}
        state.tekla_publication_passed = False
        state.tekla_grader_result = GraderResult(
            passed=False, summary="stale grade", corrective_action_needed=True,
            details={"phase": "before_model_write"},
        )
        advisor = MagicMock(return_value={"id": "retry_publish"})
        result, ready = decide_tekla_outcome(
            state, request_matches=lambda _: True, choose_correction=advisor,
        )
        self.assertFalse(ready)
        self.assertNotEqual(result.status, "FAILED")
        self.assertEqual(result.agent_statuses["A7"]["status"], "REVISING")
        self.assertEqual(result.tekla_retry_history[0]["phase"], "fabrication_readback")
        advisor.assert_called_once()

    def test_tekla_unclassified_exception_never_retries(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        with patch("services.correction_advisor.choose_correction") as advisor, \
             patch.object(a7, "send_to_tekla", side_effect=RuntimeError("write interrupted")) as send:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("publication may be partial", result.error)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result.tekla_retry_count, 0)
        advisor.assert_not_called()

    def test_tekla_retries_only_prepublication_connection_failure(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.layout_version = 2
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}

        def revalidate(candidate, *, agent_id):
            candidate.agent_statuses[agent_id] = {"status": "PASSED"}
            return candidate

        with patch.object(package, "run_a6_package_validator", side_effect=revalidate) as audit, \
             patch("services.correction_advisor.choose_correction", return_value={"id": "retry_publish"}), \
             patch.object(a7, "send_to_tekla", side_effect=[
                 a7.TeklaPrePublicationConnectionError("connection unavailable"),
                 {"created": 1, "verified_members": 1, "failed": 0},
             ]) as send:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.agent_statuses["A6"]["status"], "PASSED")
        self.assertEqual(result.substage_statuses["A6a"]["status"], "PASSED")
        self.assertEqual(result.substage_statuses["A6d"]["status"], "PASSED")
        self.assertEqual(result.tekla_retry_count, 1)
        self.assertEqual(result.tekla_retry_history[0]["phase"], "before_model_write")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(audit.call_count, 1)

    def test_tekla_connection_error_is_classified_before_cleanup(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        class ConnectionErrorFromTekla(RuntimeError):
            pass
        common = SimpleNamespace(
            TeklaConnectionError=ConnectionErrorFromTekla,
            _get_connected_model=MagicMock(side_effect=ConnectionErrorFromTekla("not connected")),
        )
        with patch.object(a7, "tekla_common", common), patch.object(a7, "clean_tekla") as clean:
            with self.assertRaises(a7.TeklaPrePublicationConnectionError):
                a7.send_to_tekla({"expected_tekla_model_path": "working-model"})
        clean.assert_not_called()

    def test_tekla_prepublication_retry_is_bounded(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}

        def revalidate(candidate, *, agent_id):
            candidate.agent_statuses[agent_id] = {"status": "PASSED"}
            return candidate

        with patch.object(package, "run_a6_package_validator", side_effect=revalidate), \
             patch("services.correction_advisor.choose_correction", return_value={"id": "retry_publish"}), \
             patch.object(a7, "send_to_tekla",
                          side_effect=a7.TeklaPrePublicationConnectionError("connection unavailable")) as send:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.tekla_retry_count, 5)
        self.assertEqual(send.call_count, 6)
        self.assertIn("retry limit reached", result.error)

    def test_tekla_advisor_can_stop_a_safe_retry(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        with patch("services.correction_advisor.choose_correction", return_value={"id": "stop"}), \
             patch.object(a7, "send_to_tekla",
                          side_effect=a7.TeklaPrePublicationConnectionError("connection unavailable")) as send:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.tekla_retry_count, 0)
        self.assertEqual(send.call_count, 1)
        self.assertIn("decision stopped", result.error)

    def test_tekla_safe_retry_stops_if_revalidation_fails(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        state = self.state()
        state.layout_version = 2
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}

        def revalidate(candidate, *, agent_id):
            candidate.validation_report = {"passed": False}
            candidate.agent_statuses[agent_id] = {"status": "REVISING"}
            return candidate

        with patch.object(package, "run_a6_package_validator", side_effect=revalidate), \
             patch("services.correction_advisor.choose_correction", return_value={"id": "retry_publish"}), \
             patch.object(a7, "send_to_tekla",
                          side_effect=a7.TeklaPrePublicationConnectionError("connection unavailable")) as send:
            result = asyncio.run(a7_d.run_a7_staged(state))
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(send.call_count, 1)
        self.assertIn("failed revalidation", result.error)

    def test_tekla_payload_requires_audit_and_publication_requires_readback(self):
        from agents.A6_tekla.A6A_build_json import build_tekla_payload
        from agents.A6_tekla.A6C_check_publication import publication_passed

        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        with self.assertRaisesRegex(ValueError, "passing package validation"):
            build_tekla_payload(state)
        state.validation_report = {
            "passed": True, "validated_joint_groups_payload": {"joint": {"joints": []}},
            "plate_material_policy": {"grade": "S355"},
        }
        state.readiness["tekla_model_path"] = "model.db1"
        request = build_tekla_payload(state)
        self.assertEqual(request["joint_groups"], {"joint": {"joints": []}})
        self.assertEqual(request["expected_tekla_model_path"], "model.db1")
        result = {"created": 1, "verified_members": 1, "plate_materials_verified": True}
        self.assertTrue(publication_passed(result, request))
        for change in ({"verified_members": 0}, {"plate_materials_verified": False},
                       {"simulation": True}, {"errors": ["failed"]}):
            with self.subTest(change=change):
                self.assertFalse(publication_passed({**result, **change}, request))
        state.change_scope = {"groups": ["joint"]}
        with self.assertRaisesRegex(ValueError, "scoped Tekla adapter"):
            build_tekla_payload(state)

    def test_component_readback_rejects_different_attribute(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        component = MagicMock()
        model = MagicMock()
        model.SelectModelObject.return_value.GetAttribute.return_value = (True, 10.0)
        with self.assertRaisesRegex(RuntimeError, "differs"):
            a7._verify_component(model, component, {"tpl1": 12.0})

    def test_tekla_attribute_rejection_propagates(self):
        from agents.A6_tekla import A6A_build_json as a7_a, A6B_trigger_api as a7, A6D_llm_reasoning as a7_d
        component = MagicMock()
        component.SetAttribute.return_value = False
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            a7.set_component_attribute(component, "tpl1", 12.0)

    def test_full_graph_stops_solved_revision_without_physical_mapping(self):
        from orchestration import workflow as orchestrator
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, A3B_generate_structure as a3_b, connection_scope as a3_scope
        from agents.A6_tekla import A6B_trigger_api as a7
        from services.workflow_store import SqliteWorkflowStore
        bad_frame, good_frame = valid_staad(), valid_staad()
        bad_frame["member_type_max_ratios"]["column"] = 1.2
        bad_frame["member_ratios"][1]["ratio"] = 1.2
        members = [{"name": "column", "p1": [0, 0, 0], "p2": [0, 0, 5000]}]
        candidate = {"members": members, "members_split_beams": members}
        results = [joint_result(), joint_result(120, False), joint_result(parameters=[{"key": "EP_weld", "expression": "0.006"}])]
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteWorkflowStore(str(Path(directory) / "runs.db"))
            with patch("services.workflow_store.WORKFLOW_STORE", store), \
                 patch.object(orchestrator, "run_a2_readiness", side_effect=lambda state: state), \
                 patch.object(a3_b, "generate_members_from_parameters", return_value=candidate), \
                 patch.object(a3_a, "validate_profile_catalogue"), \
                 patch.object(a3_scope, "template_coverage", return_value={"missing": []}), \
                 patch.object(a4_d, "frame_options", return_value=[{"id": "CFRHS120X120X6", "profile": "CFRHS120X120X6", "target": "column"}]), \
                 patch.object(a4_b, "send_to_staad", new=AsyncMock(side_effect=[bad_frame, good_frame])), \
                 patch.object(a5_a, "_connection_payload", side_effect=payload), \
                 patch.object(a5_b, "design_joint", side_effect=results) as solve, \
                 patch.object(a7, "send_to_tekla") as publish:
                final = asyncio.run(orchestrator.run_agentic_workflow({"grid": {"n_long_bays": 1, "n_trans_bays": 1}, "load_configuration": {"load_combinations": [{"name": "SLS", "envelope": "SERVICEABILITY", "factors": {"DL": 1}}, {"name": "ULS", "envelope": "STRENGTH", "factors": {"DL": 1.35}}]}}, run_id="controlled_e2e"))
        self.assertEqual(final.status, "FAILED")
        self.assertIn("validated Tekla attribute mapping", final.error)
        self.assertEqual(final.loop_a_count, 1)
        self.assertEqual(final.loop_b_count, {"second": 1})
        self.assertEqual(solve.call_count, 3)
        publish.assert_not_called()
        self.assertEqual(final.tekla_results, {})

    def test_new_full_graph_audits_real_evidence_before_tekla_and_gate(self):
        from orchestration import workflow as orchestrator
        from agents.A3_generate_structure import A3A_evaluate_plan as a3_a, A3B_generate_structure as a3_b, connection_scope as a3_scope
        from agents.A6_tekla import A6B_trigger_api as a7
        members = [{"name": "column", "p1": [0, 0, 0], "p2": [0, 0, 5000]}]
        candidate = {"members": members, "members_split_beams": members}
        calls = []

        def solve(request):
            calls.append(request["group"])
            return joint_result()

        def publish(request):
            self.assertEqual(calls, ["first", "second"])
            self.assertEqual(set(request["joint_groups"]), {"first", "second"})
            return {"created": 1, "verified_members": 1, "failed": 0}

        with patch.dict("os.environ", {"PLANT_DEEP_AGENTS_ENABLED": "false", "PLANT_LLM_ENABLED": "false"}), \
             patch.object(orchestrator, "run_a2_readiness", side_effect=lambda state: state), \
             patch.object(a3_b, "generate_members_from_parameters", return_value=candidate), \
             patch.object(a3_a, "validate_profile_catalogue"), \
             patch.object(a3_scope, "template_coverage", return_value={"missing": []}), \
             patch.object(a4_b, "send_to_staad", new=AsyncMock(return_value=valid_staad())), \
             patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", side_effect=solve), \
             patch.object(a7, "send_to_tekla", side_effect=publish):
            final = asyncio.run(orchestrator.run_agentic_workflow(
                {"grid": {"n_long_bays": 1, "n_trans_bays": 1},
                 "load_configuration": {"load_combinations": [
                     {"name": "SLS", "envelope": "SERVICEABILITY", "factors": {"DL": 1}},
                     {"name": "ULS", "envelope": "STRENGTH", "factors": {"DL": 1.35}}]}},
                run_id="controlled_success"))
        self.assertEqual(final.status, "PAUSED_AT_GATE3", final.error)
        self.assertEqual(final.layout_version, 2)
        self.assertTrue(final.validation_report["passed"])
        self.assertEqual(final.agent_statuses["A5c"]["status"], "PASSED")
        self.assertEqual(final.agent_statuses["A6"]["status"], "PASSED")
        self.assertEqual(final.agent_statuses["A7"]["status"], "AWAITING_APPROVAL")
        self.assertFalse(final.gate3_approved)

    def test_a5d_llm_governs_a5c_package_audit_recovery(self):
        from agents.A5_idea_statica import package_validation as package
        from agents.A5_idea_statica.graph import run_idea_graph
        from orchestration.workflow import route_after_a5

        state = self.state()
        state.layout_version = 2
        state.active_operation_params = {"first": [{"key": "EP_d", "expression": "0.016"}]}
        state.joint_grader_results = {"first": GraderResult(passed=True, summary="ok")}
        state.ideastatica_results = {"first": {"status": "PASSED", "check_summary": {"passed": True}}}

        def audit_revising(candidate, *, agent_id="A5c"):
            candidate.validation_report = {"passed": False, "recovery_target": "a5_ideastatica_analyzer"}
            candidate.loop_c_count = 1
            candidate.agent_statuses[agent_id] = {"status": "REVISING", "message": "Refresh joint evidence."}
            return candidate

        with patch.object(a5_a, "_connection_payload", side_effect=payload), \
             patch.object(a5_b, "design_joint", return_value=joint_result()), \
             patch.object(package, "run_a6_package_validator", side_effect=audit_revising), \
             patch("services.correction_advisor.choose_correction", return_value={"id": "stop"}) as advisor:
            stopped = asyncio.run(run_idea_graph(state, audit_package=True))
        self.assertEqual(stopped.status, "FAILED")
        self.assertEqual(stopped.substage_statuses["A5c"]["status"], "REVISING")
        self.assertEqual(stopped.substage_statuses["A5d"]["status"], "FAILED")
        self.assertEqual(route_after_a5(stopped), "__end__")
        advisor.assert_called_once()
        self.assertEqual(advisor.call_args.args[1], "package")

    def test_a4d_and_a5d_invoke_llm_advisor_on_first_failure_when_multiple_options(self):
        from agents.A4_staad.frame_corrections import apply_staad_grade
        from agents.A5_idea_statica.joint_corrections import _correct_connection_parameters, apply_failed_joint_grade

        state = self.state()
        state.active_column_profile = "HEA120"
        state.frame_candidate = {"members": [{"name": "column", "profile": "HEA120"}]}
        bad_staad = valid_staad()
        bad_staad["member_type_max_ratios"]["column"] = 1.15
        bad_staad["member_ratios"][1]["ratio"] = 1.15
        frame_grader = GraderResult(
            passed=False,
            summary="ratio 1.15 > 1.0",
            details={"overall_max_ratio": 1.15, "member_type_max_ratios": {"column": 1.15}},
            corrective_action_needed=True,
            corrective_action={"target_type": "column", "action": "upsize"},
        )
        choices = [
            {"id": "HEA140", "target": "column", "profile": "HEA140"},
            {"id": "HEA160", "target": "column", "profile": "HEA160"},
        ]
        chosen = []
        apply_staad_grade(
            state,
            bad_staad,
            frame_grader,
            lambda _p, _t, _c: choices,
            lambda s, stage, ev, opts, default_id: chosen.append((stage, opts[1]["id"])) or opts[1],
        )
        self.assertEqual(chosen, [("frame", "HEA160")])
        self.assertEqual(state.active_column_profile, "HEA160")

        params = [{"key": "BP1_d", "expression": "0.016"}, {"key": "BP2_d", "expression": "0.016"}]
        joint_res = {
            "check_summary": {
                "summary": [{"name": "Bolts", "value": 115.0}],
                "failed_parameter_keys": ["BP1_d", "BP2_d"],
            }
        }
        joint_grader = GraderResult(
            passed=False,
            summary="bolt > 100%",
            details={"metrics": {"bolt_util_pct": 115.0}},
            corrective_action_needed=True,
            corrective_action={"fix_type": "bolt_dia"},
        )
        joint_chosen = []
        apply_failed_joint_grade(
            state,
            "first",
            joint_res,
            joint_grader,
            params,
            False,
            _correct_connection_parameters,
            lambda s, stage, ev, opts, default_id: joint_chosen.append((stage, len(opts))) or opts[1],
        )
        self.assertEqual(joint_chosen, [("connection", 3)])
        self.assertEqual(len(state.active_operation_params["first"]), 1)
        self.assertEqual(state.active_operation_params["first"][0]["key"], "BP1_d")


if __name__ == "__main__":
    unittest.main()
