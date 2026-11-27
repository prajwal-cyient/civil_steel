"""Runtime isolation, bounded GPT proposals, and input-policy regression tests."""

import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services import correction_advisor as advisor
from orchestration import execution
from config.settings import load_environment
from agents.A1_requirements.prepare_load_basis import validate_combinations
from orchestration.state import WorkflowState
from services.workflow_store import SqliteWorkflowStore


class AdvisorAndPolicyTests(unittest.TestCase):
    def test_environment_does_not_override_process_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"PLANT_PORT": "9000"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('# comment\nPLANT_PORT=8080\nPLANT_HOST="127.0.0.1"\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["PLANT_PORT"], "9000")
            self.assertEqual(os.environ["PLANT_HOST"], "127.0.0.1")

    def test_file_enables_tracing_despite_inherited_disabled_aliases(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false", "LANGSMITH_TRACING_V2": "false",
            "LANGCHAIN_TRACING_V2": "false", "PLANT_PORT": "9000"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('LANGSMITH_TRACING=true\nPLANT_PORT=8060\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "true")
            self.assertEqual(os.environ["LANGSMITH_TRACING_V2"], "true")
            self.assertEqual(os.environ["PLANT_PORT"], "9000")
            # Same loader is used by the worker on every new run.
            load_environment(path)
            self.assertEqual(os.environ["LANGSMITH_TRACING_V2"], "true")

    def test_explicit_file_can_disable_tracing(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LANGSMITH_TRACING": "true", "LANGSMITH_TRACING_V2": "true"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('LANGSMITH_TRACING="FALSE"\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "false")
            self.assertEqual(os.environ["LANGSMITH_TRACING_V2"], "false")

    def test_absent_file_flag_preserves_inherited_tracing_choice(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('PLANT_PORT=8060\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "false")
            self.assertNotIn("LANGSMITH_TRACING_V2", os.environ)

    def test_explicit_v2_file_flag_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('LANGSMITH_TRACING=true\nLANGSMITH_TRACING_V2=false\n', encoding="utf-8")
            load_environment(path)
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "false")
            self.assertEqual(os.environ["LANGSMITH_TRACING_V2"], "false")

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


class DesktopLockTests(unittest.TestCase):
    def test_lock_excludes_other_processes_and_releases_after_failure(self):
        from orchestration import desktop_lock as locking
        script = (
            "import sys; from pathlib import Path; "
            "from orchestration import desktop_lock as lock; "
            "lock.DESKTOP_LOCK_PATH=Path(sys.argv[1]); "
            "guard=lock.desktop_lock(); "
            "\ntry:\n guard.__enter__()\n"
            "except lock.DesktopBusyError:\n sys.exit(23)\n"
            "else:\n guard.__exit__(None,None,None)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop.lock"
            with patch.object(locking, "DESKTOP_LOCK_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "test failure"):
                    with locking.desktop_lock():
                        child = subprocess.run([sys.executable, "-c", script, str(path)],
                                               capture_output=True, timeout=15)
                        self.assertEqual(child.returncode, 23, child.stderr.decode(errors="replace"))
                        raise RuntimeError("test failure")
                child = subprocess.run([sys.executable, "-c", script, str(path)],
                                       capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))

    def test_manual_endpoints_reject_busy_desktop(self):
        from fastapi.testclient import TestClient
        from orchestration import desktop_lock as locking
        from main import create_app

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(locking, "DESKTOP_LOCK_PATH", Path(directory) / "desktop.lock"):
            app = create_app()
            with TestClient(app) as client, locking.desktop_lock():
                for path in ("/api/v1/radiator_support/generate", "/api/v1/radiator_support/to_staad",
                             "/api/v1/radiator_support/to_tekla", "/api/v1/to_ideastatica"):
                    with self.subTest(path=path):
                        response = client.post(path, json={})
                        self.assertEqual(response.status_code, 409)
                        self.assertIn("desktop tools", response.json()["detail"])

    def test_busy_worker_fails_before_starting_any_stage(self):
        from orchestration import desktop_lock as locking
        from orchestration import worker
        from unittest.mock import MagicMock

        store = MagicMock()
        store.get_state.return_value = WorkflowState(run_id="locked_worker", status="RUNNING")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(locking, "DESKTOP_LOCK_PATH", Path(directory) / "desktop.lock"), \
             patch.object(worker, "load_environment"), \
             patch.object(sys, "argv", ["worker", "--run-id", "locked_worker"]), \
             patch("services.workflow_store.WORKFLOW_STORE", store), \
             patch("orchestration.workflow.run_agentic_workflow", new_callable=AsyncMock) as run:
            with locking.desktop_lock():
                worker.main()
            run.assert_not_awaited()
        result = store.save_state.call_args.args[0]
        self.assertEqual(result.status, "FAILED")
        self.assertIn("desktop tools", result.error)


class ChatParameterRerunTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_rerun_enters_graph_at_a2_with_confirmed_inputs(self):
        from orchestration import workflow as orchestrator
        edges = orchestrator.build_workflow_graph("a2_readiness").compile().get_graph().edges
        self.assertEqual([edge.target for edge in edges if edge.source == "__start__"], ["a2_readiness"])
        calls = []

        def readiness(state):
            calls.append((state.grid_config["n_long_bays"], state.run_policy["max_member_ratio"]))
            state.status = "FAILED"
            state.error = "Controlled readiness stop"
            return state

        payload = {"grid": {"n_long_bays": 3}, "run_policy": {"max_member_ratio": 0.8}}
        with patch.object(orchestrator, "_sync_state"), patch.object(orchestrator, "guarded"), \
             patch.object(orchestrator, "deep_agents_enabled", return_value=False), \
             patch.object(orchestrator, "run_a2_readiness", side_effect=readiness):
            result = await orchestrator.run_agentic_workflow(payload, "chat_rerun", chat_parameter_rerun=True)
        self.assertEqual(calls, [(3, 0.8)])
        self.assertEqual(result.agent_statuses["A1"]["status"], "PASSED")
        self.assertEqual(sum(log.agent_id == "A1" for log in result.logs), 1)

    async def test_invalid_rerun_never_reaches_readiness(self):
        from orchestration import workflow as orchestrator
        for payload in ({"grid": {"n_long_bays": 0}}, {"grid": {}, "run_policy": {"max_member_ratio": 1.5}}):
            with self.subTest(payload=payload), \
                 patch.object(orchestrator, "_sync_state"), patch.object(orchestrator, "guarded"), \
                 patch.object(orchestrator, "deep_agents_enabled", return_value=False), \
                 patch.object(orchestrator, "run_a2_readiness") as readiness:
                result = await orchestrator.run_agentic_workflow(payload, "invalid_chat_rerun", chat_parameter_rerun=True)
            self.assertEqual(result.status, "FAILED")
            self.assertEqual(result.agent_statuses["A1"]["status"], "FAILED")
            readiness.assert_not_called()

    def test_dashboard_graph_starts_at_a1(self):
        from orchestration.workflow import build_workflow_graph
        edges = build_workflow_graph().compile().get_graph().edges
        self.assertEqual([edge.target for edge in edges if edge.source == "__start__"], ["a1_requirements"])


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

    async def test_chat_parameter_rerun_dispatches_worker_with_explicit_entry(self):
        controller = execution.RunController()
        process = SimpleNamespace(returncode=1)
        with patch.object(execution.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            await controller.execute({"grid": {}}, "worker_chat_rerun", chat_parameter_rerun=True)
        self.assertIn("--chat-parameter-rerun", spawn.await_args.args)
        with self.assertRaisesRegex(ValueError, "fresh full workflow"):
            await controller.execute({}, "worker_chat_rerun", chat_action="preview_structure",
                                     chat_parameter_rerun=True)

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
