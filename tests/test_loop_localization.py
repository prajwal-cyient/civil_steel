"""Controlled regression tests, not substitutes for the real solver trials."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentic.agents.a4_staad_analyzer_agent import frame_correction_scope
from agentic.agents.a5_ideastatica_analyzer_agent import _correct_connection_parameters
from agentic.agents.a7_tekla_modeler_agent import unmapped_connection_corrections
from agentic.state import WorkflowState
from agentic.agents.a5_ideastatica_analyzer_agent import validate_calculation_check_coverage, IdeaStaticaJointError
from agentic.agents.a4_staad_analyzer_agent import grade_staad_results
from agentic.agents import a5_ideastatica_analyzer_agent as idea


class LoopLocalizationTests(unittest.TestCase):
    def test_weld_probe_rejects_unrelated_geometry_and_restores_expression(self):
        api=Mock()
        current={"size":"0.005"}
        api.export.export_iom_connection_data.side_effect=lambda *_:{
            "beams":[{"id":10,"section":"changed" if current["size"]!="0.005" else "original"}],
            "welds":[{"id":1,"thickness":float(current["size"])}]}
        def set_parameters(*args):current["size"]=args[-1][0]["expression"]
        with patch.object(idea,"_set_parameters",side_effect=set_parameters):
            with self.assertRaisesRegex(IdeaStaticaJointError,"unrelated connection geometry"):
                idea._localize_failed_weld_parameters(api,"project",1,
                    [{"key":"a_weld","expression":"0.005"}],
                    {"welds":{"row":{"id":1,"unityCheckWeld":1.2}}},lambda _:None)
        self.assertEqual(current["size"],"0.005")

    def test_underreported_type_maximum_cannot_hide_individual_failure(self):
        from test_engineering_guards import valid_staad
        result=valid_staad()
        result["member_ratios"][1]["ratio"]=1.5
        grade=grade_staad_results(result,5000)
        self.assertFalse(grade.passed)
        self.assertIn("disagrees",grade.summary)

    def test_weld_feedback_probe_restores_geometry_and_maps_exact_solver_id(self):
        api=Mock()
        current={"a_weld":"0.005", "b_weld":"0.005"}
        api.export.export_iom_connection_data.side_effect=lambda *_:{
            "beams":[{"id":10}],"welds":[{"id":1,"thickness":float(current["a_weld"])},
                                         {"id":2,"thickness":float(current["b_weld"])}]}
        def set_parameters(*args):
            for row in args[-1]:current[row["key"]]=row["expression"]
        raw={"welds":{"row":{"id":2,"unityCheckWeld":1.2}}}
        params=[{"key":key,"expression":value} for key,value in current.items()]
        with patch.object(idea,"_set_parameters",side_effect=set_parameters):
            mapped=idea._localize_failed_weld_parameters(api,"project",1,params,raw,lambda _:None)
        self.assertEqual(mapped["failed_parameter_keys"],["b_weld"])
        self.assertEqual(current,{"a_weld":"0.005", "b_weld":"0.005"})

    def test_shared_weld_parameter_cannot_change_a_passing_weld(self):
        api=Mock()
        current={"size":"0.005"}
        api.export.export_iom_connection_data.side_effect=lambda *_:{
            "beams":[],"welds":[{"id":i,"thickness":float(current["size"])} for i in (1,2)]}
        def set_parameters(*args):current["size"]=args[-1][0]["expression"]
        with patch.object(idea,"_set_parameters",side_effect=set_parameters):
            with self.assertRaisesRegex(IdeaStaticaJointError,"passing weld IDs"):
                idea._localize_failed_weld_parameters(api,"project",1,
                    [{"key":"shared_weld","expression":"0.005"}],
                    {"welds":{"row":{"id":1,"unityCheckWeld":1.2}}},lambda _:None)
        self.assertEqual(current["size"],"0.005")

    def test_loop_c_damaged_cached_pass_is_recalculated(self):
        from test_engineering_guards import joint_result
        from agentic.state import GraderResult
        from agentic.store import SqliteWorkflowStore
        import tempfile
        state=WorkflowState(run_id="repair",status="RUNNING",staad_results={"joint_groups":{"one":{}}},
                            frame_grader_result=GraderResult(passed=True,summary="Controlled passing frame"))
        state.joint_grader_results["one"]=GraderResult(passed=True,summary="Retained cached flag")
        damaged=joint_result()
        damaged["check_summary"]["summary"]=[]
        damaged["input_signature"]=idea._connection_signature({},state.design_generation)
        state.ideastatica_results["one"]=damaged
        with tempfile.TemporaryDirectory() as folder:
            with patch("agentic.store.WORKFLOW_STORE",SqliteWorkflowStore(str(Path(folder)/"runs.db"))), \
                 patch.object(idea,"_connection_payload",return_value={}), \
                 patch.object(idea,"design_joint",return_value=joint_result()) as solver:
                idea.run_a5_ideastatica_analyzer(state)
        solver.assert_called_once()
        self.assertEqual(state.agent_statuses["A5"]["status"],"PASSED")
    def test_missing_individual_evidence_cannot_pass_using_only_maxima(self):
        from test_engineering_guards import valid_staad
        result = valid_staad()
        del result["member_ratios"]
        self.assertFalse(grade_staad_results(result,5000).passed)

    def test_missing_or_skipped_weld_check_cannot_validate_live_template(self):
        checks = [{"name":name,"skipped":False} for name in ("Analysis","Plates","Loc. deformation","Bolts","Welds")]
        validate_calculation_check_coverage(checks)
        checks[-1]["skipped"] = True
        with self.assertRaisesRegex(IdeaStaticaJointError,"welds"):
            validate_calculation_check_coverage(checks)
    def test_unmapped_idea_revision_cannot_silently_disappear_at_publication(self):
        state = WorkflowState(run_id="mapping_guard", raw_input={})
        state.loop_b_history = [{"joint_group":"inner_node_2_longitudinal_bracings",
                                 "changes":[{"key":"Stiff_bot_weld(flange)"}]}]
        self.assertEqual(unmapped_connection_corrections(state),[
            {"joint_group":"inner_node_2_longitudinal_bracings", "parameter":"Stiff_bot_weld(flange)"}])
        state.loop_b_history[0]["changes"] = [{"key":"EP_d"}]
        self.assertEqual(unmapped_connection_corrections(state),[])
    def test_shared_frame_profile_must_not_change_a_passing_member(self):
        evidence = {"member_ids_by_type": {"column": [1, 2]},
                    "member_data_by_id": {1: {"name": "column-0-0"}, 2: {"name": "column-0-1"}},
                    "member_ratios": {1: {"ratio": 1.2}, 2: {"ratio": .7}}}
        with self.assertRaisesRegex(ValueError, "passing elements"):
            frame_correction_scope(evidence, "column")

    def test_all_failing_group_records_exact_members_and_preserves_other_types(self):
        evidence = {"member_ids_by_type": {"column": [1, 2], "beam_longitudinal": [3]},
                    "member_data_by_id": {i: {"name": f"element-{i}"} for i in (1, 2, 3)},
                    "member_ratios": {1: {"ratio": 1.2}, 2: {"ratio": 1.4}, 3: {"ratio": .7}}}
        self.assertEqual([row["id"] for row in frame_correction_scope(evidence, "column")], [1, 2])

    def test_frame_correction_without_individual_evidence_stops(self):
        with self.assertRaisesRegex(ValueError, "per-member"):
            frame_correction_scope({"member_type_max_ratios": {"column": 1.2}}, "column")

    def test_ambiguous_weld_summary_does_not_modify_all_welds(self):
        params = [{"key": "EP_weld", "expression": "0.005"}, {"key": "CPL_weld", "expression": "0.005"}]
        updated, changes = _correct_connection_parameters(params, "weld_size")
        self.assertEqual(updated, params)
        self.assertEqual(changes, [])

    def test_explicit_localization_changes_only_the_failed_parameter(self):
        params = [{"key": "EP_weld", "expression": "0.005"}, {"key": "CPL_weld", "expression": "0.005"},
                  {"key": "EP_d", "expression": "0.016"}]
        updated, changes = _correct_connection_parameters(params, "weld_size", ["CPL_weld"])
        self.assertEqual([row["key"] for row in changes], ["CPL_weld"])
        self.assertEqual(updated[0], params[0])
        self.assertEqual(updated[2], params[2])
        self.assertEqual(updated[1]["expression"], "0.006")


if __name__ == "__main__":
    unittest.main()
