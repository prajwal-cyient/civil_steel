"""Runtime isolation, bounded GPT proposals, and input-policy regression tests."""

import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic import advisor, execution
from agentic.config import load_environment
from agentic.load_combinations import validate_combinations
from agentic.state import WorkflowState
from agentic.store import SqliteWorkflowStore


class AdvisorAndPolicyTests(unittest.TestCase):
    def test_environment_does_not_override_process_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"PLANT_PORT": "9000"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('# comment\nPLANT_PORT=8080\nPLANT_HOST="127.0.0.1"\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["PLANT_PORT"], "9000")
            self.assertEqual(os.environ["PLANT_HOST"], "127.0.0.1")

    def test_opposing_wind_and_temperature_are_rejected(self):
        for factors in ({"W+X": 1, "W-X": 1}, {"W+Z": 1, "W-Z": 1}, {"T+": 1, "T-": 1}):
            with self.subTest(factors=factors), self.assertRaisesRegex(ValueError, "mutually exclusive"):
                validate_combinations([{"name": "bad", "envelope": "STRENGTH", "factors": factors}])

    def test_empty_unknown_or_nonfinite_combinations_rejected(self):
        for combinations in ([], [{"name": "bad", "envelope": "STRENGTH", "factors": {"UNKNOWN": 1}}],
                             [{"name": "bad", "envelope": "STRENGTH", "factors": {"DL": float("nan")}}]):
            with self.subTest(combinations=combinations), self.assertRaises(ValueError):
                validate_combinations(combinations)

    def _choose(self, content, state=None):
        state = state or WorkflowState(run_id="advisor_test")
        body = {"choices": [{"message": {"content": json.dumps(content)}}]}
        response = BytesIO(json.dumps(body).encode())
        config = SimpleNamespace(llm_enabled=True, model="deployment", model_call_limit=2, agent_timeout=10)
        options = [{"id": "next", "profile": "HEA160"}, {"id": "larger", "profile": "HEA180"}]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-placeholder", "OPENAI_BASE_URL": "https://example.invalid/v1/"}), \
             patch.object(advisor, "settings", return_value=config), \
             patch.object(advisor, "urlopen", return_value=response) as call:
            result = advisor.choose_correction(state, "frame", {"ratio": 1.2}, options, "next")
        return state, result, call.call_count

    def test_gpt_can_select_only_a_supplied_candidate(self):
        state, result, calls = self._choose({"option_id": "larger", "rationale": "Try the larger allowed section."})
        self.assertEqual(result["profile"], "HEA180")
        self.assertEqual(state.model_events[-1]["status"], "VALIDATED")
        self.assertEqual(calls, 1)

    def test_unapproved_gpt_action_falls_back_without_applying_it(self):
        state, result, _ = self._choose({"option_id": "change_loads", "rationale": "Ignore loads"})
        self.assertEqual(result["id"], "next")
        self.assertEqual(state.model_events[-1]["status"], "FALLBACK")
        self.assertNotIn("unit-test-placeholder", json.dumps(state.model_events))

    def test_model_budget_prevents_network_call(self):
        state = WorkflowState(run_id="budget", model_call_count=2)
        _, result, calls = self._choose({"option_id": "larger", "rationale": "unused"}, state)
        self.assertEqual(calls, 0)
        self.assertEqual(result["id"], "next")

    def test_new_store_instance_sees_another_workers_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "runs.db")
            first, second = SqliteWorkflowStore(path), SqliteWorkflowStore(path)
            state = WorkflowState(run_id="shared", current_agent="A1")
            first.save_state(state)
            self.assertEqual(first.get_state("shared").current_agent, "A1")
            state.current_agent = "A5"
            second.save_state(state)
            self.assertEqual(first.get_state("shared").current_agent, "A5")


class WorkerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SqliteWorkflowStore(str(Path(directory.name) / "runs.db"))
        patched = patch.object(execution, "WORKFLOW_STORE", self.store)
        patched.start()
        self.addCleanup(patched.stop)

    async def test_worker_crash_cannot_leave_run_running(self):
        controller = execution.RunController()
        process = SimpleNamespace(returncode=1)
        with patch.object(execution.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            state = await controller.execute({}, "worker_crash")
        self.assertEqual(state.status, "FAILED")
        self.assertIsNone(controller.run_id)

    async def test_cancel_stops_worker_and_persists_cancelled_state(self):
        controller = execution.RunController()
        event = asyncio.Event()
        process = SimpleNamespace(returncode=None, wait=event.wait)

        async def terminate():
            process.returncode = -1
            event.set()

        with patch.object(execution.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)), \
             patch.object(controller, "_terminate", side_effect=terminate):
            task = asyncio.create_task(controller.execute({}, "worker_cancel"))
            while controller.process is None:
                await asyncio.sleep(0)
            await controller.cancel("worker_cancel")
            state = await task
        self.assertEqual(state.status, "CANCELLED")
        self.assertEqual(self.store.get_state("worker_cancel").status, "CANCELLED")

    async def test_stalled_worker_times_out(self):
        controller = execution.RunController()
        event = asyncio.Event()
        process = SimpleNamespace(returncode=None, wait=event.wait)
        config = SimpleNamespace(run_timeout=0.01, agent_timeout=0.01)

        async def terminate():
            process.returncode = -1
            event.set()

        with patch.object(execution, "settings", return_value=config), \
             patch.object(execution.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)), \
             patch.object(controller, "_terminate", side_effect=terminate):
            state = await controller.execute({}, "worker_timeout")
        self.assertEqual(state.status, "FAILED")
        self.assertIn("timed out", state.error)

    async def test_cancel_during_process_startup_still_terminates_child(self):
        controller = execution.RunController()
        spawned = asyncio.Event()
        finished = asyncio.Event()
        process = SimpleNamespace(returncode=None, wait=finished.wait)

        async def spawn(*args, **kwargs):
            await spawned.wait()
            return process

        async def terminate():
            if controller.process is not None:
                process.returncode = -1
                finished.set()

        with patch.object(execution.asyncio, "create_subprocess_exec", side_effect=spawn), \
             patch.object(controller, "_terminate", side_effect=terminate):
            task = asyncio.create_task(controller.execute({}, "startup_cancel"))
            while controller.run_id is None:
                await asyncio.sleep(0)
            await controller.cancel("startup_cancel")
            spawned.set()
            state = await task
        self.assertEqual(state.status, "CANCELLED")
