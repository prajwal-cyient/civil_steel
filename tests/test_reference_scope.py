import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agents.A3_generate_structure.connection_scope import build_connection_scope, scope_for_state, template_for_group
from orchestration.state import WorkflowState, GraderResult
from agents import A5_idea_statica as a5
from agents.A5_idea_statica import A5A_build_json as a5_a, A5B_trigger_api as a5_b


class ReferenceScopeTests(unittest.TestCase):
    def setUp(self):
        self.groups = {name: {} for name in ("support_node_no_bracing", "corner_node",
            "inner_node_2_longitudinal_bracings", "inner_node_2_longitudinal_1_transverse_bracing")}

    def state(self):
        state = WorkflowState(run_id="scope_test", staad_results={"joint_groups": self.groups},
                              frame_grader_result=GraderResult(passed=True, summary="Verified"))
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": "poc2_automation"}):
            state.readiness["connection_scope"] = build_connection_scope(self.groups)
        return state

    def test_reference_plan_separates_calculation_and_standard_details(self):
        scope = scope_for_state(self.state())
        self.assertEqual(len(scope["idea_required_groups"]), 2)
        self.assertEqual(scope["standard_detail_groups"], ["corner_node", "support_node_no_bracing"])
        self.assertEqual(scope["standard_details"]["base"]["component"], 1004)
        self.assertEqual(template_for_group("inner_node_2_longitudinal_bracings", True).name, "two-longitudinal.ideaCon")

    def test_reviewed_mode_keeps_full_joint_requirement(self):
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": "reviewed_engineering"}):
            scope = build_connection_scope(self.groups)
        self.assertEqual(set(scope["idea_required_groups"]), set(self.groups))
        self.assertEqual(scope["standard_detail_groups"], [])

    def test_missing_plan_does_not_silently_reduce_existing_scope(self):
        state = self.state()
        state.readiness.clear()
        self.assertEqual(set(scope_for_state(state)["idea_required_groups"]), set(self.groups))

    def test_plan_cannot_drop_a_required_calculation(self):
        state = self.state()
        scope = state.readiness["connection_scope"]
        scope["standard_detail_groups"].append(scope["idea_required_groups"].pop())
        with self.assertRaisesRegex(ValueError, "differs from"):
            scope_for_state(state)

    def test_stale_scope_rejected_for_new_geometry_groups(self):
        state = self.state()
        state.staad_results["joint_groups"]["new_family"] = {}
        with self.assertRaisesRegex(ValueError, "exactly once"):
            scope_for_state(state)

    def test_reference_plan_cannot_substitute_a_weaker_material(self):
        state = self.state()
        state.readiness["connection_scope"]["material_catalogue_mapping"]["S355JR"] = "S 235"
        with self.assertRaisesRegex(ValueError, "differs from"):
            scope_for_state(state)

    def test_standard_detail_groups_are_not_recorded_as_idea_passes(self):
        state = self.state()
        result = {"passed": True, "check_summary": {"summary": [
            {"name": "Plates", "check_value": 10, "check_status": True, "skipped": False}]}}
        with patch.object(a5_a, "_connection_payload", return_value={}), \
             patch.object(a5_b, "design_joint", return_value=result) as solve, \
             patch("services.workflow_store.WORKFLOW_STORE", Mock()):
            a5.run_a5_ideastatica_analyzer(state)
        self.assertEqual(solve.call_count, 2)
        self.assertNotIn("support_node_no_bracing", state.joint_grader_results)
        self.assertNotIn("corner_node", state.ideastatica_results)
        self.assertEqual(state.agent_statuses["A5"]["status"], "PASSED")

    def test_double_brace_transverse_parameters_reach_tekla_in_mm(self):
        from agents.A6_tekla import connection_attributes
        group = "inner_node_2_longitudinal_1_transverse_bracing"
        groups = {group: {"operation_parameters": [
            {"key": "CPL_th", "expression": "0.010"},
            {"key": "D2_CPL2_th", "expression": "0.012"},
            {"key": "D2_CPL2_d", "expression": "0.016"}]}}
        transverse = connection_attributes(groups, [group], 20, "transverse")
        longitudinal = connection_attributes(groups, [group], 20, "longitudinal")
        self.assertEqual(transverse["tpl1"], 12)
        self.assertEqual(transverse["diameter"], 16)
        self.assertEqual(longitudinal["tpl1"], 10)

    def test_integer_attributes_use_the_integer_readback_overload(self):
        from agents import A6_tekla as a7
        class Int32(int):
            pass
        component, persisted, model = Mock(), Mock(), Mock()
        model.SelectModelObject.return_value = persisted
        persisted.GetAttribute.side_effect = lambda key, default: (type(default) is Int32, 2)
        with patch.dict(sys.modules, {"System": SimpleNamespace(Int32=Int32)}):
            a7.set_component_attribute(component, "nb", 2)
            a7._verify_component(model, component, {"nb": 2})
        self.assertIs(type(component.SetAttribute.call_args.args[1]), Int32)


if __name__ == "__main__":
    unittest.main()
