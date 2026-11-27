"""Regression coverage for native integer load-case IDs in workflow state."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langgraph.graph import END, StateGraph

from agents.A4_staad import build_staad_payload, run_a4_staad_analyzer
from orchestration.state import WorkflowState
from services.workflow_store import SqliteWorkflowStore


class StaadWorkflowStateTests(unittest.TestCase):
    def setUp(self):
        self.results = {
            **{case: {"load_case_title": f"Case {case}"} for case in range(100, 108)},
            "member_type_max_ratios": {"column": 0.85},
            "analysis_status": {"ReturnValue": 2, "NoOfErrors": 0},
            "units": {"displacement": "mm"},
            "envelope_summary_results": {1: {
                "node_displacements_limits": dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), 10.0),
            "node_displacements_limits_exceed_col_height_200": dict.fromkeys(("min_X", "max_X", "min_Z", "max_Z"), False),
            }},
            "member_data_by_id": {900001: {"name": "column"}},
            "member_ids_by_type": {"column": [900001]},
            "member_ratios": {900001: {"name": "column", "ratio": 0.85}},
        }

    def test_native_load_case_ids_validate_and_serialize(self):
        state = WorkflowState(run_id="native_keys", staad_results=self.results)
        self.assertEqual(state.staad_results[100]["load_case_title"], "Case 100")
        self.assertEqual(state.staad_results["member_data_by_id"][900001]["name"], "column")
        serialized = json.loads(state.model_dump_json(warnings="error"))
        self.assertEqual(serialized["staad_results"]["107"]["load_case_title"], "Case 107")
        restored = WorkflowState.model_validate(serialized)
        self.assertEqual(restored.staad_results["100"]["load_case_title"], "Case 100")

    def test_payload_builder_preserves_request_and_evidence_invalidation_order(self):
        state = WorkflowState(
            run_id="payload",
            grid_config={"col_height_mm": 5000},
            load_config={"country": "EUROPE"},
            frame_candidate={"members_split_beams": [{"name": "column", "p1": [0, 0, 0]}]},
            readiness={"staad_model_path": "example.std"},
            ideastatica_results={"old": {}},
            tekla_results={"old": {}},
        )
        expected = {
            "grid": {"col_height_mm": 5000},
            "members_split_beams": [{"name": "column", "p1": [0, 0, 0]}],
            "load_configuration": {"country": "EUROPE"},
            "expected_staad_model_path": "example.std",
        }
        self.assertEqual(build_staad_payload(state), expected)
        self.assertIn("old", state.ideastatica_results)
        captured = []

        async def send(payload):
            captured.append(deepcopy(payload))
            self.assertFalse(state.ideastatica_results)
            self.assertFalse(state.tekla_results)
            payload["members_split_beams"][0]["p1"][0] = 123
            return deepcopy(self.results)

        with patch("agents.A4_staad.A4B_trigger_api.send_to_staad", side_effect=send):
            asyncio.run(run_a4_staad_analyzer(state))
        self.assertEqual(captured, [expected])
        self.assertEqual(state.frame_candidate["members_split_beams"][0]["p1"], [0, 0, 0])
        self.assertEqual(state.agent_statuses["A4"]["status"], "PASSED")

    def test_staged_a4_matches_existing_entry_and_rejects_stale_request(self):
        from agents import A4_staad as a4
        from agents.A4_staad import A4B_trigger_api as a4_b

        base = WorkflowState(
            run_id="staged_a4", status="RUNNING",
            grid_config={"col_height_mm": 5000},
            load_config={"country": "EUROPE"},
            frame_candidate={"members_split_beams": [{"name": "column", "p1": [0, 0, 0]}]},
            ideastatica_results={"old": {}},
        )
        legacy = base.model_copy(deep=True)
        staged = base.model_copy(deep=True)
        with patch.object(a4_b, "send_to_staad", side_effect=lambda _: deepcopy(self.results)) as solver:
            asyncio.run(a4.run_a4_staad_analyzer(legacy))
            a4.prepare_a4_staad(staged)
            staged = WorkflowState.model_validate_json(staged.model_dump_json())
            asyncio.run(a4.execute_a4_staad(staged))
            a4.evaluate_a4_staad(staged)
            a4.decide_a4_staad(staged)
            self.assertEqual(staged.staad_results, legacy.staad_results)
            self.assertEqual(staged.frame_grader_result, legacy.frame_grader_result)
            self.assertEqual(staged.agent_statuses["A4"], legacy.agent_statuses["A4"])
            self.assertEqual(staged.ideastatica_results, legacy.ideastatica_results)
            self.assertFalse(staged.staad_request)
            self.assertEqual(solver.call_count, 2)

            stale = base.model_copy(deep=True)
            a4.prepare_a4_staad(stale)
            stale.load_config["country"] = "UK"
            asyncio.run(a4.execute_a4_staad(stale))
            self.assertEqual(stale.status, "FAILED")
            self.assertIn("no longer matches", stale.error)
            self.assertFalse(stale.staad_request)
            self.assertEqual(solver.call_count, 2)

            changed_results = base.model_copy(deep=True)
            a4.prepare_a4_staad(changed_results)
            asyncio.run(a4.execute_a4_staad(changed_results))
            a4.evaluate_a4_staad(changed_results)
            changed_results.staad_results["member_type_max_ratios"]["column"] = 1.2
            a4.decide_a4_staad(changed_results)
            self.assertEqual(changed_results.status, "FAILED")
            self.assertIn("grade no longer matches", changed_results.error)
            self.assertEqual(changed_results.loop_a_count, 0)

    def test_a4_results_survive_next_graph_node_validation(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("analyze", run_a4_staad_analyzer)

        def next_agent(state):
            self.assertEqual(state.staad_results[107]["load_case_title"], "Case 107")
            self.assertTrue(state.frame_grader_result.passed)
            return {"current_agent": "A5"}

        graph.add_node("next_agent", next_agent)
        graph.set_entry_point("analyze")
        graph.add_edge("analyze", "next_agent")
        graph.add_edge("next_agent", END)
        with patch(
            "agents.A4_staad.A4B_trigger_api.send_to_staad",
            new=AsyncMock(return_value=self.results),
        ):
            output = asyncio.run(graph.compile().ainvoke(WorkflowState(run_id="graph_keys")))
        final = WorkflowState.model_validate(output)
        self.assertEqual(final.current_agent, "A5")

    def test_inflight_frame_change_cannot_claim_staad_evidence(self):
        from agents import A4_staad as a4
        from agents.A4_staad import A4B_trigger_api as a4_b
        state = WorkflowState(
            run_id="changed_during_staad", status="RUNNING",
            grid_config={"col_height_mm": 5000},
            load_config={"country": "EUROPE"},
            frame_candidate={"members_split_beams": [{"name": "column", "p1": [0, 0, 0]}]},
        )

        async def send(payload):
            state.load_config["country"] = "UK"
            return deepcopy(self.results)

        with patch.object(a4_b, "send_to_staad", side_effect=send):
            asyncio.run(a4.run_a4_staged(state))
        self.assertEqual(state.status, "FAILED")
        self.assertIn("changed during STAAD analysis", state.error)
        self.assertFalse(state.staad_results)
        self.assertIsNone(state.frame_grader_result)

    def test_subgraphs_run_on_worker_thread_and_stop_on_failure(self):
        from agents.A3_generate_structure import geometry_checks as a3_g
        from agents.A4_staad import A4A_build_json as a4_a, A4B_trigger_api as a4_b, A4C_evaluate_results as a4_c, A4D_llm_reasoning as a4_d
        from agents.A3_generate_structure.graph import build_frame_graph
        from agents.A4_staad.graph import build_staad_graph
        from orchestration.state import fail_workflow

        calls = []
        owner_thread = threading.get_ident()
        state = WorkflowState(run_id="subgraph_thread", status="RUNNING")

        def step(name, fail=False):
            def execute(candidate):
                self.assertIs(candidate, state)
                self.assertEqual(threading.get_ident(), owner_thread)
                calls.append(name)
                return fail_workflow(candidate, "A4", "controlled API failure") if fail else candidate
            return execute

        async def api(candidate):
            return step("a4b", fail=True)(candidate)

        with patch.object(a3_g, "evaluate_a3_frame", side_effect=step("a3a")), \
             patch.object(a3_g, "generate_a3_frame", side_effect=step("a3b")), \
             patch.object(a4_a, "prepare_a4_staad", side_effect=step("a4a")), \
             patch.object(a4_b, "execute_a4_staad", side_effect=api), \
             patch.object(a4_c, "evaluate_a4_staad") as evaluate, \
             patch.object(a4_d, "decide_a4_staad") as decide:
            frame = asyncio.run(build_frame_graph().ainvoke({"workflow": state}))["workflow"]
            result = asyncio.run(build_staad_graph().ainvoke({"workflow": frame}))["workflow"]
        self.assertIs(result, state)
        self.assertEqual(calls, ["a3a", "a3b", "a4a", "a4b"])
        self.assertEqual(result.status, "FAILED")
        evaluate.assert_not_called()
        decide.assert_not_called()

    def test_results_reload_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "runs.db")
            store = SqliteWorkflowStore(db_path)
            store.save_state(WorkflowState(run_id="stored_keys", staad_results=self.results))
            restored = SqliteWorkflowStore(db_path).get_state("stored_keys")
            self.assertEqual(restored.staad_results["100"]["load_case_title"], "Case 100")
            self.assertEqual(restored.staad_results["member_type_max_ratios"]["column"], 0.85)


if __name__ == "__main__":
    unittest.main()
