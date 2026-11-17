"""Real Deep Agents SDK/graph, controlled chat model and engineering adapters.

These tests exercise orchestration and guards, not live model inference or FEA.
"""
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from agentic import deep_runtime, orchestrator
from agentic.config import workflow_mode
from agentic.state import WorkflowState


class StageModel(BaseChatModel):
    model_name: str = "engineering-unit-test"
    behaviour: str = "normal"
    bound_tools: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "openai-chat"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = [t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.behaviour == "network_error":
            raise ConnectionError("secret value must not enter audit")
        if self.behaviour == "plan_forever":
            message = AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {"todos": []}, "id": "plan"}])
        elif self.behaviour == "skip" or isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="The stage tool returned its actual result; retain the engineering gate.")
        else:
            name = "execute" if self.behaviour == "unauthorized" else "execute_stage"
            calls = [{"name": name, "args": {}, "id": "call_1"}]
            if self.behaviour == "duplicate":
                calls.append({"name": name, "args": {}, "id": "call_2"})
            message = AIMessage(content="", tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


class DeepAgentTests(unittest.TestCase):
    def _run(self, behaviour="normal", operation=None, state=None, limit="64"):
        state = state or WorkflowState(run_id="deep-test", status="RUNNING")
        model = StageModel(behaviour=behaviour)
        seen = []
        def stage(candidate):
            seen.append(threading.get_ident())
            candidate.agent_statuses["A1"] = {"status": "PASSED"}
            return candidate
        owner = threading.get_ident()
        with patch.dict(os.environ, {"PLANT_DEEP_AGENT_MODEL_CALL_LIMIT": limit}), \
             patch.object(deep_runtime, "_build_model", return_value=model):
            result = asyncio.run(deep_runtime.run_deep_stage(state, "A1", operation or stage, lambda _: None))
        return result, model, seen, owner

    def test_real_sdk_calls_stage_once_on_owner_thread_and_audits_model_calls(self):
        result, model, seen, owner = self._run()
        self.assertEqual(result.agent_statuses["A1"]["status"], "PASSED")
        self.assertEqual(seen, [owner])
        self.assertEqual(result.deep_agent_model_call_count, 2)
        self.assertEqual(result.deep_agent_events[-1]["status"], "VERIFIED")
        self.assertEqual(set(model.bound_tools), {"execute_stage", "write_todos"})
        self.assertFalse(result.gate3_approved)

    def test_async_stage_is_awaited_on_owner_thread(self):
        owner = threading.get_ident()
        async def stage(state):
            await asyncio.sleep(0)
            self.assertEqual(threading.get_ident(), owner)
            state.agent_statuses["A1"] = {"status": "PASSED"}
            return state
        result, _, _, _ = self._run(operation=stage)
        self.assertEqual(result.deep_agent_events[-1]["status"], "VERIFIED")

    def test_model_cannot_skip_engineering_tool_and_invent_success(self):
        result, _, seen, _ = self._run("skip")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(seen, [])

    def test_duplicate_model_tool_requests_do_not_repeat_side_effects(self):
        result, _, seen, _ = self._run("duplicate")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(len(seen), 1)
        self.assertEqual(result.deep_agent_events[-1]["duplicate_requests"], 1)

    def test_unauthorized_tools_are_rejected_before_execution(self):
        result, _, seen, _ = self._run("unauthorized")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(seen, [])

    def test_model_failure_is_not_silently_replaced_with_legacy_execution(self):
        result, _, seen, _ = self._run("network_error")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(seen, [])
        self.assertNotIn("secret value", result.model_dump_json())

    def test_run_budget_stops_before_stage_mutation(self):
        state = WorkflowState(run_id="budget", status="RUNNING", deep_agent_model_call_count=1)
        result, _, seen, _ = self._run(state=state, limit="1")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(seen, [])

    def test_stage_model_loop_cannot_exceed_four_requests(self):
        result, _, seen, _ = self._run("plan_forever")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.deep_agent_model_call_count, 4)
        self.assertEqual(seen, [])

    def test_engineering_failure_cannot_be_overridden_by_model_review(self):
        def failed(state):
            state.status = "FAILED"
            state.agent_statuses["A1"] = {"status": "FAILED", "message": "Actual grader rejected input"}
            return state
        result, _, _, _ = self._run(operation=failed)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.deep_agent_events[-1]["status"], "STAGE_FAILED")

    def test_blank_workflow_mode_uses_documented_default(self):
        with patch.dict(os.environ, {"PLANT_WORKFLOW_MODE": ""}):
            self.assertEqual(workflow_mode(), "poc2_automation")

    def test_real_sdk_all_eight_stages_preserve_three_recovery_loops_and_gate3(self):
        seen = []
        counts = {}
        def operation(agent):
            def run(state):
                seen.append(agent);counts[agent] = counts.get(agent, 0) + 1
                state.agent_statuses[agent] = {"status": "PASSED"}
                if agent == "A4" and counts[agent] == 1:
                    state.loop_a_count += 1;state.agent_statuses[agent]["status"] = "REVISING"
                if agent == "A5" and counts[agent] == 1:
                    state.loop_b_count["controlled"] = 1;state.agent_statuses[agent]["status"] = "REVISING"
                if agent == "A6":
                    if counts[agent] == 1:
                        state.loop_c_count += 1;state.agent_statuses[agent]["status"] = "REVISING"
                        state.validation_report = {"recovery_target": "a5_ideastatica_analyzer"}
                    else:state.validation_report = {"passed": True}
                return state
            return run
        names = ["requirements", "readiness", "frame_generator", "staad_analyzer", "ideastatica_analyzer", "package_validator", "tekla_modeler"]
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"PLANT_DEEP_AGENTS_ENABLED": "true", "PLANT_DEEP_AGENT_MODEL_CALL_LIMIT": "64"}))
            stack.enter_context(patch.object(orchestrator, "_sync_state"))
            stack.enter_context(patch.object(deep_runtime, "_build_model", side_effect=StageModel))
            for i, name in enumerate(names, 1):
                stack.enter_context(patch.object(orchestrator, f"run_a{i}_{name}", side_effect=operation(f"A{i}")))
            result = asyncio.run(orchestrator.run_agentic_workflow({}, "deep-graph"))
        self.assertEqual(result.status, "PAUSED_AT_GATE3", result.error)
        self.assertEqual({e["stage"] for e in result.deep_agent_events}, {f"A{i}" for i in range(1, 9)})
        self.assertTrue(all(e["status"] == "VERIFIED" and e["tool_calls"] == 1 for e in result.deep_agent_events))
        self.assertEqual(counts["A3"], 2)
        self.assertEqual(counts["A5"], 3)
        self.assertEqual(counts["A6"], 2)
        self.assertEqual(result.agent_statuses["A8"]["status"], "AWAITING_APPROVAL")
        self.assertFalse(result.gate3_approved)


if __name__ == "__main__":
    unittest.main()
