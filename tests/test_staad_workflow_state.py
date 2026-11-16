"""Regression coverage for native integer load-case IDs in workflow state."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langgraph.graph import END, StateGraph

from agentic.agents.a4_staad_analyzer_agent import run_a4_staad_analyzer
from agentic.state import WorkflowState
from agentic.store import SqliteWorkflowStore


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
            "agentic.agents.a4_staad_analyzer_agent.send_to_staad",
            new=AsyncMock(return_value=self.results),
        ):
            output = asyncio.run(graph.compile().ainvoke(WorkflowState(run_id="graph_keys")))
        final = WorkflowState.model_validate(output)
        self.assertEqual(final.current_agent, "A5")

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
