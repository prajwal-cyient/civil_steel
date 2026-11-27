"""Run isolation and acceptance-policy checks; no live desktop calculations."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config.run_policy import RunPolicy
from orchestration.state import WorkflowState, GraderResult
from agents.A1_requirements import run_a1_requirements
from agents import A4_staad as a4
from agents import A5_idea_statica as a5
from agents.A5_idea_statica import A5A_build_json as a5_a
from agents.A7_reporting import run_a8_reporting, accept_gate3
from chatbot.chatbot import ChatRuntime, project_input
from chatbot.chat_store import ChatStore
from test_engineering_guards import valid_staad, joint_result


class RunPolicyTests(unittest.TestCase):
    def test_reject_unsupported_out_of_range_nonfinite_and_non_numeric_rules(self):
        for value in [{"max_member_ratio": 1.01}, {"drift_denominator": 199},
                      {"max_bolt_utilization_percent": 101}, {"max_member_ratio": 0},
                      {"max_bolt_utilization_percent": -1}, {"max_member_ratio": True},
                      {"drift_denominator": float("inf")}, {"max_member_ratio": float("nan")},
                      {"max_member_ratio": "0.8"}, {"formula": "arbitrary code"}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RunPolicy.model_validate(value)

    def test_a1_policy_is_per_run_and_defaults_unchanged(self):
        first = run_a1_requirements(WorkflowState(run_id="first", raw_input={
            "grid": {}, "run_policy": {"max_member_ratio": 0.8, "drift_denominator": 300,
                                       "max_bolt_utilization_percent": 60}}))
        second = run_a1_requirements(WorkflowState(run_id="second", raw_input={"grid": {}}))
        self.assertNotEqual(first.status, "FAILED")
        self.assertEqual(first.run_policy["drift_denominator"], 300)
        self.assertEqual(second.run_policy, RunPolicy().model_dump())
        self.assertEqual(WorkflowState.model_validate_json(first.model_dump_json()).run_policy, first.run_policy)

    def test_a1_rejects_bad_policy_before_native_stages(self):
        state = run_a1_requirements(WorkflowState(run_id="invalid", raw_input={
            "grid": {}, "run_policy": {"max_bolt_utilization_percent": 101}}))
        self.assertEqual(state.status, "FAILED")
        self.assertIn("100", state.error)

    def test_bolt_90_applies_to_one_run_and_preserves_native_failure(self):
        checks = {"summary": [{"name": "Bolts", "check_value": 85., "check_status": True}]}
        first = run_a1_requirements(WorkflowState(run_id="custom90", raw_input={
            "grid": {}, "run_policy": {"max_bolt_utilization_percent": 90}}))
        self.assertTrue(a5.grade_joint_check_summary(checks, first.run_policy).passed)
        checks['summary'][0]['check_value'] = 90.
        self.assertTrue(a5.grade_joint_check_summary(checks, first.run_policy).passed)
        checks['summary'][0]['check_value'] = 90.01
        self.assertFalse(a5.grade_joint_check_summary(checks, first.run_policy).passed)
        checks['summary'][0].update(check_value=85., check_status=False)
        self.assertFalse(a5.grade_joint_check_summary(checks, first.run_policy).passed)
        second = run_a1_requirements(WorkflowState(run_id="default75", raw_input={"grid": {}}))
        self.assertEqual(second.run_policy['max_bolt_utilization_percent'], 75.)
        checks['summary'][0]['check_status'] = True
        self.assertFalse(a5.grade_joint_check_summary(checks, second.run_policy).passed)
        self.assertEqual(first.run_policy['max_bolt_utilization_percent'], 90.)

    def test_component_thresholds_are_validated_and_used(self):
        for field, name in [('max_weld_utilization_percent', 'Welds'),
                            ('max_plate_utilization_percent', 'Plates'),
                            ('max_deformation_utilization_percent', 'Local deformation')]:
            with self.subTest(field=field):
                checks = {'summary': [{'name': name, 'check_value': 85., 'check_status': True}]}
                self.assertTrue(a5.grade_joint_check_summary(checks).passed)
                self.assertTrue(a5.grade_joint_check_summary(checks, {field: 85.}).passed)
                self.assertFalse(a5.grade_joint_check_summary(checks, {field: 80.}).passed)
                for value in [0, -1, 101, False, None, float('inf'), '90']:
                    with self.assertRaises(ValueError):
                        RunPolicy.model_validate({field: value})

    def test_member_ratio_boundary_and_default(self):
        result = valid_staad()
        self.assertTrue(a4.grade_staad_results(result, 5000).passed)
        self.assertTrue(a4.grade_staad_results(result, 5000, {"max_member_ratio": 0.8}).passed)
        grade = a4.grade_staad_results(result, 5000, {"max_member_ratio": 0.79})
        self.assertFalse(grade.passed)
        self.assertEqual(grade.details["failed_members"][0]["id"], "1")

    def test_stricter_drift_uses_actual_mm_not_default_flags(self):
        result = valid_staad()
        result["envelope_summary_results"][1]["node_displacements_limits"]["max_X"] = 20
        self.assertTrue(a4.grade_staad_results(result, 5000).passed)
        self.assertFalse(a4.grade_staad_results(result, 5000, {"drift_denominator": 300}).passed)
        self.assertTrue(a4.grade_staad_results(result, 5000, {"drift_denominator": 250}).passed)
        # Native H/200 evidence must still be internally consistent.
        result["envelope_summary_results"][1]["node_displacements_limits_exceed_col_height_200"]["max_X"] = True
        self.assertTrue(a4.grade_staad_results(result, 5000, {"drift_denominator": 300}).details["invalid_results"])

    def test_correction_scope_uses_same_ratio_limit(self):
        self.assertEqual(len(a4.frame_correction_scope(valid_staad(), "column", 0.79)), 1)
        with self.assertRaises(ValueError):
            a4.frame_correction_scope(valid_staad(), "column")

    def test_bolt_boundary_and_native_failure_preserved(self):
        checks = {"summary": [{"name": "Bolts", "check_value": 65., "check_status": True}]}
        self.assertTrue(a5.grade_joint_check_summary(checks).passed)
        self.assertTrue(a5.grade_joint_check_summary(checks, {"max_bolt_utilization_percent": 65}).passed)
        self.assertFalse(a5.grade_joint_check_summary(checks, {"max_bolt_utilization_percent": 60}).passed)
        checks["summary"][0]["check_status"] = False
        self.assertFalse(a5.grade_joint_check_summary(checks).passed)

    def test_a6_rechecks_policy_even_with_cached_pass(self):
        from agents.A5_idea_statica import package_validation as a6
        state = WorkflowState(run_id="audit", run_policy={"max_member_ratio": 0.7},
            grid_config={"col_height_mm": 5000}, frame_candidate={"members": [{}]},
            staad_results=valid_staad(), frame_grader_result=GraderResult(passed=True, summary="old PASS"))
        with patch("agents.A6_tekla.A6B_trigger_api.unmapped_connection_corrections", return_value=[]), \
             patch.object(a6, "frame_signature", return_value=None), \
             patch("agents.A3_generate_structure.connection_scope.scope_for_state", return_value={
                 "mode": "reviewed_engineering", "idea_required_groups": [], "standard_detail_groups": []}):
            a6.run_a6_package_validator(state)
        self.assertFalse(state.validation_report["passed"])
        self.assertIn("0.7", " ".join(state.validation_report["failures"]))

    def test_report_records_policy_and_does_not_auto_approve(self):
        state = run_a8_reporting(WorkflowState(run_id="report", run_policy=RunPolicy(max_member_ratio=0.8).model_dump()))
        self.assertEqual(state.final_report["run_policy"]["max_member_ratio"], 0.8)
        self.assertEqual(state.status, "PAUSED_AT_GATE3")
        self.assertFalse(state.gate3_approved)
        with self.assertRaises(ValueError):
            accept_gate3(state)

    def test_a6_rechecks_bolt_policy_and_hash_records_thresholds(self):
        from agents.A5_idea_statica import package_validation as a6
        result = joint_result()
        result['input_signature'] = 'fixture'
        next(c for c in result['check_summary']['summary'] if c['name'] == 'Bolts')['check_value'] = 65.
        original = WorkflowState(run_id='audit-bolts', grid_config={'col_height_mm': 5000},
            frame_candidate={'members': [{}]}, staad_results=valid_staad(),
            frame_grader_result=GraderResult(passed=True, summary='cached'),
            ideastatica_results={'first': result},
            joint_grader_results={'first': GraderResult(passed=True, summary='cached')})
        with patch('agents.A6_tekla.A6B_trigger_api.unmapped_connection_corrections', return_value=[]), \
             patch.object(a6, 'frame_signature', return_value=None), \
             patch.object(a6, 'verify_plate_material_evidence'), \
             patch.object(a5_a, '_connection_payload', return_value={}), \
             patch.object(a5_a, '_connection_signature', return_value='fixture'), \
             patch('agents.A3_generate_structure.connection_scope.scope_for_state', return_value={
                 'mode': 'reviewed_engineering', 'idea_required_groups': ['first'], 'standard_detail_groups': []}):
            default = a6.run_a6_package_validator(original.model_copy(deep=True))
            self.assertTrue(default.validation_report['passed'])
            strict = original.model_copy(deep=True)
            strict.run_policy = RunPolicy(max_bolt_utilization_percent=60).model_dump()
            self.assertFalse(a6.run_a6_package_validator(strict).validation_report['passed'])
            changed = original.model_copy(deep=True)
            changed.run_policy = RunPolicy(max_bolt_utilization_percent=70).model_dump()
            a6.run_a6_package_validator(changed)
            self.assertTrue(changed.validation_report['passed'])
            self.assertNotEqual(default.package_hash, changed.package_hash)
            relaxed = original.model_copy(deep=True)
            relaxed.run_policy = RunPolicy(max_bolt_utilization_percent=90).model_dump()
            bolts = next(c for c in relaxed.ideastatica_results['first']['check_summary']['summary'] if c['name'] == 'Bolts')
            bolts['check_value'] = 85.
            self.assertTrue(a6.run_a6_package_validator(relaxed).validation_report['passed'])
            self.assertEqual(relaxed.validation_report['run_policy']['max_bolt_utilization_percent'], 90.)
            default_again = relaxed.model_copy(deep=True)
            default_again.run_policy = RunPolicy().model_dump()
            self.assertFalse(a6.run_a6_package_validator(default_again).validation_report['passed'])
            bolts['check_status'] = False
            self.assertFalse(a6.run_a6_package_validator(relaxed).validation_report['passed'])

    def test_json_policy_validated(self):
        value = project_input({"grid": {}, "run_policy": {"max_member_ratio": 0.8}})
        self.assertEqual(value["run_policy"]["max_member_ratio"], 0.8)
        with self.assertRaises(ValueError):
            project_input({"grid": {}, "run_policy": {"unknown_rule": 1}})


class ChatRunPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ChatStore(Path(self.temp.name) / "chat.db")
        self.session = self.store.session()
        self.session["project_input"] = {"grid": {"n_long_bays": 8, "n_trans_bays": 5}}
        self.store.save_session(self.session)
        self.runtime = ChatRuntime(self.store)

    def policy_tool(self):
        return next(t for t in self.runtime.tools(self.session, True) if t.name == "set_next_run_policy")

    async def test_consumed_once_even_when_engineering_fails_and_other_session_isolated(self):
        await self.policy_tool().ainvoke({"run_policy": {"max_bolt_utilization_percent": 90,
                                                     "max_weld_utilization_percent": 85}})
        original = deepcopy(self.session["project_input"])
        payloads = []
        async def execute(payload, run_id):
            payloads.append(deepcopy(payload))
            return run_a1_requirements(WorkflowState(run_id=run_id, raw_input=payload, status="FAILED"))
        controller = SimpleNamespace(run_id=None, execute=AsyncMock(side_effect=execute))
        baseline = {'grid':{'n_long_bays':21,'n_trans_bays':5}}
        with patch("orchestration.execution.CONTROLLER", controller), \
             patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(baseline)):
            await self.runtime.engineering(self.session, "run_complete_workflow", True)
            self.assertNotIn("next_run_policy", self.store.session(self.session["id"]))
            await self.runtime.engineering(self.session, "run_complete_workflow", True)
        self.assertEqual(payloads[0]["run_policy"]["max_bolt_utilization_percent"], 90)
        self.assertEqual(payloads[0]["run_policy"]["max_weld_utilization_percent"], 85)
        self.assertNotIn("run_policy", payloads[1])
        self.assertEqual(payloads[0]['grid'], original['grid'])
        self.assertEqual(self.session["project_input"], baseline)
        self.assertNotIn("next_run_policy", self.store.session())

    async def test_invalid_rule_cannot_silently_run_defaults(self):
        with self.assertRaises(ValueError):
            await self.policy_tool().ainvoke({"run_policy": {"max_member_ratio": 2}})
        controller = SimpleNamespace(run_id=None, execute=AsyncMock())
        with patch("orchestration.execution.CONTROLLER", controller):
            with self.assertRaisesRegex(ValueError, "rejected run policy"):
                await self.runtime.engineering(self.session, "run_complete_workflow", True)
        controller.execute.assert_not_awaited()
        await self.policy_tool().ainvoke({"run_policy": {}})
        self.assertNotIn("run_policy_error", self.session)

    async def test_busy_or_disabled_tools_do_not_consume_pending_policy(self):
        await self.policy_tool().ainvoke({"run_policy": {"drift_denominator": 300}})
        with self.assertRaises(ValueError):
            await self.runtime.engineering(self.session, "run_complete_workflow", False)
        self.assertIn("next_run_policy", self.session)
        with patch("orchestration.execution.CONTROLLER", SimpleNamespace(run_id="busy")):
            with self.assertRaises(ValueError):
                await self.runtime.engineering(self.session, "run_complete_workflow", True)
        self.assertIn("next_run_policy", self.session)

    async def test_json_policy_consumed_without_resetting_geometry(self):
        self.session["project_input"]["run_policy"] = {"max_member_ratio": 0.8}
        controller = SimpleNamespace(run_id=None, execute=AsyncMock(return_value=WorkflowState(run_id="result")))
        with patch("orchestration.execution.CONTROLLER", controller):
            await self.runtime.engineering(self.session, "run_complete_workflow", True)
        self.assertEqual(controller.execute.call_args.args[0]["run_policy"]["max_member_ratio"], 0.8)
        self.assertNotIn("run_policy", self.session["project_input"])
        self.assertEqual(self.session["project_input"]["grid"]["n_long_bays"], 8)

    async def test_preview_does_not_consume_and_old_stage_cannot_adopt_new_policy(self):
        await self.policy_tool().ainvoke({"run_policy": {"drift_denominator": 300}})
        controller = SimpleNamespace(run_id=None, execute=AsyncMock(return_value=WorkflowState(run_id="preview")))
        with patch("orchestration.execution.CONTROLLER", controller):
            await self.runtime.engineering(self.session, "preview_structure", False)
            self.assertIn("next_run_policy", self.session)
            with self.assertRaisesRegex(ValueError, "new generation"):
                await self.runtime.engineering(self.session, "analyze_frame", True)
        self.assertEqual(controller.execute.await_count, 1)


if __name__ == "__main__":
    unittest.main()
