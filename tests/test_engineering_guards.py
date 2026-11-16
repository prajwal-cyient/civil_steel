"""Controlled engineering-loop tests. Never invoke the desktop applications."""

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentic.agents import a4_staad_analyzer_agent as a4
from agentic.agents import a5_ideastatica_analyzer_agent as a5
from agentic.state import WorkflowState, GraderResult


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
        from agentic.store import SqliteWorkflowStore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store_patch = patch("agentic.store.WORKFLOW_STORE", SqliteWorkflowStore(str(Path(directory.name) / "runs.db")))
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
        with patch.object(a4, "send_to_staad", new=AsyncMock(side_effect=RuntimeError("COM unavailable"))):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(state.status, "FAILED")
        self.assertIn("COM unavailable", state.error)

    def test_failed_analysis_stops_before_a5(self):
        from agentic.orchestrator import route_after_a4
        state = self.state()
        result = valid_staad()
        result["analysis_failed"] = True
        with patch.object(a4, "send_to_staad", new=AsyncMock(return_value=result)):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(route_after_a4(state), "__end__")
        self.assertEqual(state.loop_a_count, 0)

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
        from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
        state = self.state()
        state.status = "FAILED"
        with self.assertRaises(ValueError):
            accept_gate3(state)

    def test_a5_retries_only_changed_group(self):
        state = self.state()
        first, fail, passed = joint_result(), joint_result(120, False), joint_result(parameters=[{"key": "EP_weld", "expression": "0.006"}])
        with patch.object(a5, "_connection_payload", side_effect=payload), patch.object(a5, "design_joint", side_effect=[first, fail, passed]) as solve:
            a5.run_a5_ideastatica_analyzer(state)
            self.assertEqual(state.agent_statuses["A5"]["status"], "REVISING")
            a5.run_a5_ideastatica_analyzer(state)
            self.assertEqual(state.agent_statuses["A5"]["status"], "PASSED")
            self.assertEqual([call.args[0]["group"] for call in solve.call_args_list], ["first", "second", "second"])

    def test_rejected_update_stops_without_fake_correction(self):
        state = self.state()
        with patch.object(a5, "_connection_payload", side_effect=payload), patch.object(a5, "design_joint", side_effect=RuntimeError("422 rejected")):
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
            with patch.object(a5, "_get_client", return_value=context), \
                 patch.object(a5, "_resolve_connection", return_value=SimpleNamespace(id=1)), \
                 patch.object(a5, "_get_parameters", return_value=[SimpleNamespace(key="EP_weld")]):
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
            with patch.object(a5, "_get_client", return_value=context), \
                 patch.object(a5, "_resolve_connection", return_value=SimpleNamespace(id=1)), \
                 patch.object(a5, "_get_parameters", return_value=[SimpleNamespace(key="EP_weld")]), \
                 patch.object(a5, "_set_parameters", side_effect=RuntimeError("422")):
                with self.assertRaisesRegex(a5.IdeaStaticaJointError, "rejected"):
                    a5.design_joint(data)
            client.calculation.calculate.assert_not_called()

    def test_exhausted_retry_stops_before_other_groups(self):
        state = self.state()
        state.loop_b_count["first"] = state.max_loop_b
        with patch.object(a5, "_connection_payload", side_effect=payload), patch.object(a5, "design_joint", return_value=joint_result(120, False)) as solve:
            a5.run_a5_ideastatica_analyzer(state)
        self.assertEqual(state.status, "FAILED")
        self.assertEqual(solve.call_count, 1)

    def test_strength_envelopes_combined_without_sample_loads(self):
        state = self.state()
        state.staad_results["load_envelopes"] = [{"envelopeId": 1, "envelopeType": "SERVICEABILITY"}, {"envelopeId": 2, "envelopeType": "STRENGTH"}, {"envelopeId": 3, "envelopeType": "STRENGTH"}]
        data = {"member_updates": [{"member_name": "column"}], "load_effects": [{"name": "ULS", "member_loadings": [{"member_name": "column", "n": 10}]}]}
        state.staad_results["envelope_load_cases_by_joint_group"] = {2: {"first": data}, "3": {"first": data}}
        with patch.object(a5.os.path, "isfile", return_value=True):
            result = a5._connection_payload(state, "first")
            self.assertEqual(len(result["load_effects"]), 2)
            self.assertEqual(result["operation_parameters"], [])
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
        with patch.object(a4, "send_to_staad", new=AsyncMock(return_value=valid_staad())):
            asyncio.run(a4.run_a4_staad_analyzer(state))
        self.assertEqual(state.ideastatica_results, {})
        self.assertEqual(state.active_operation_params, {})
        self.assertEqual(state.tekla_results, {})

    def test_property_mapping_and_zero_id_rejection(self):
        # Import COM helpers in a separate process/thread context is unnecessary:
        # extract this pure adapter and inject a fake property interface.
        import ast
        source = Path("src/common/staad_common.py").read_text(encoding="utf-8")
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
        from agentic.agents import a7_tekla_modeler_agent as a7
        state = self.state()
        state.frame_candidate = {"members": [{"name": "column"}]}
        state.validation_report = {"passed": True}
        with patch.object(a7, "send_to_tekla", return_value={"created": 1, "failed": 1, "errors": ["connection failed"]}):
            a7.run_a7_tekla_modeler(state)
        self.assertEqual(state.status, "FAILED")

    def test_component_readback_rejects_different_attribute(self):
        from agentic.agents import a7_tekla_modeler_agent as a7
        component = MagicMock()
        model = MagicMock()
        model.SelectModelObject.return_value.GetAttribute.return_value = (True, 10.0)
        with self.assertRaisesRegex(RuntimeError, "differs"):
            a7._verify_component(model, component, {"tpl1": 12.0})

    def test_tekla_attribute_rejection_propagates(self):
        from agentic.agents import a7_tekla_modeler_agent as a7
        component = MagicMock()
        component.SetAttribute.return_value = False
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            a7.set_component_attribute(component, "tpl1", 12.0)

    def test_full_graph_stops_solved_revision_without_physical_mapping(self):
        from agentic import orchestrator
        from agentic.agents import a3_frame_generator_agent as a3, a7_tekla_modeler_agent as a7
        from agentic.store import SqliteWorkflowStore
        bad_frame, good_frame = valid_staad(), valid_staad()
        bad_frame["member_type_max_ratios"]["column"] = 1.2
        bad_frame["member_ratios"][1]["ratio"] = 1.2
        members = [{"name": "column", "p1": [0, 0, 0], "p2": [0, 0, 5000]}]
        candidate = {"members": members, "members_split_beams": members}
        results = [joint_result(), joint_result(120, False), joint_result(parameters=[{"key": "EP_weld", "expression": "0.006"}])]
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteWorkflowStore(str(Path(directory) / "runs.db"))
            with patch("agentic.store.WORKFLOW_STORE", store), \
                 patch.object(orchestrator, "run_a2_readiness", side_effect=lambda state: state), \
                 patch.object(a3, "generate_members", return_value=candidate), \
                 patch.object(a3, "validate_profile_catalogue"), \
                 patch.object(a3, "template_coverage", return_value={"missing": []}), \
                 patch.object(a4, "send_to_staad", new=AsyncMock(side_effect=[bad_frame, good_frame])), \
                 patch.object(a5, "_connection_payload", side_effect=payload), \
                 patch.object(a5, "design_joint", side_effect=results) as solve, \
                 patch.object(a7, "send_to_tekla") as publish:
                final = asyncio.run(orchestrator.run_agentic_workflow({"grid": {"n_long_bays": 1, "n_trans_bays": 1}, "load_configuration": {"load_combinations": [{"name": "SLS", "envelope": "SERVICEABILITY", "factors": {"DL": 1}}, {"name": "ULS", "envelope": "STRENGTH", "factors": {"DL": 1.35}}]}}, run_id="controlled_e2e"))
        self.assertEqual(final.status, "FAILED")
        self.assertIn("validated Tekla attribute mapping", final.error)
        self.assertEqual(final.loop_a_count, 1)
        self.assertEqual(final.loop_b_count, {"second": 1})
        self.assertEqual(solve.call_count, 3)
        publish.assert_not_called()
        self.assertEqual(final.tekla_results, {})


if __name__ == "__main__":
    unittest.main()
