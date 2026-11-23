"""Controlled model/adapters: chat routing and engineering guards, never live FEA."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from langchain_core.messages import AIMessage, ToolMessage
from agentic.chat import ChatRuntime, project_input, redact
from agentic.chat_store import ChatStore
from agentic.chat_actions import run_chat_action
from agentic.state import WorkflowState, GraderResult
from agentic.store import SqliteWorkflowStore


class ControlledModel:
    """Supplies explicit model tool calls; this is not a live language-understanding test."""
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.messages = []

    def bind_tools(self, tools, **kwargs):
        self.tools = {t.name: t for t in tools}
        return self

    async def ainvoke(self, messages):
        self.messages = messages[:]
        return next(self.replies)


def call(name, args=None, ident="test-call"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": ident}])


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name) / "chat.db")
        self.workflows = SqliteWorkflowStore(str(Path(self.temp.name) / "runs.db"))
        self.session = self.store.session()
        self.session["project_input"] = {"grid": {"n_long_bays": 8, "n_trans_bays": 5}}
        self.store.save_session(self.session)

    def tearDown(self):
        self.temp.cleanup()

    async def respond(self, model, text="Show current inputs", live=False):
        turn = self.store.new_turn(self.session["id"], text)
        runtime = ChatRuntime(self.store, lambda: model)
        with patch("agentic.chat.WORKFLOW_STORE", self.workflows):
            await runtime.respond(turn, live)
        return self.store.turn(turn["id"])

    async def test_sequential_calls_return_results_to_model_and_persist_history(self):
        model = ControlledModel(call("get_project_context"), call("update_project_inputs", {"grid_updates": {"n_long_bays": 10}}, "second"), AIMessage(content="Updated to 10 longitudinal bays; no analysis performed."))
        turn = await self.respond(model, "Change longitudinal bays to 10")
        self.assertEqual(turn["status"], "COMPLETED")
        self.assertEqual(self.store.session(self.session["id"])["project_input"]["grid"]["n_long_bays"], 8)
        self.assertEqual(turn["outcome"], "CHANGE_APPROVAL_REQUIRED")
        outputs = [json.loads(m.content) for m in model.messages if isinstance(m, ToolMessage)]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(turn["change_proposals"][0]["candidate"]["grid"]["n_long_bays"], 10)
        self.assertEqual([e["tool"] for e in turn["events"] if e["type"] == "tool_completed"], ["get_project_context", "update_project_inputs"])
        self.assertEqual(self.store.history(self.session["id"])[0]["assistant"], turn["assistant"])

    async def test_model_selected_stage_tools_are_not_a_hardcoded_sequence(self):
        for name in ("preview_structure", "generate_structure", "analyze_frame", "design_connections", "validate_package", "publish_to_tekla", "run_complete_workflow"):
            with self.subTest(tool=name):
                model = ControlledModel(call(name), AIMessage(content="The tool returned a controlled result."))
                with patch.object(ChatRuntime, "engineering", new=AsyncMock(return_value={"status": "MOCK_ONLY"})) as engineering:
                    turn = await self.respond(model, name, live=True)
                engineering.assert_awaited_once()
                self.assertEqual(engineering.await_args.args[1], name)
                self.assertEqual(turn["status"], "COMPLETED")

    async def test_live_tool_permission_is_checked_outside_model(self):
        model = ControlledModel(call("publish_to_tekla"), AIMessage(content="Live tools are disabled."))
        with patch("agentic.execution.CONTROLLER.execute", new_callable=AsyncMock) as execute:
            turn = await self.respond(model, "Publish to Tekla")
        execute.assert_not_awaited()
        self.assertTrue(any(e["type"] == "tool_error" and "not enabled" in e["error"] for e in turn["events"]))

    async def test_unknown_tool_and_duplicate_mutation_are_rejected(self):
        model = ControlledModel(call("execute_shell"), call("set_next_run_policy", {"run_policy": {}}), call("set_next_run_policy", {"run_policy": {}}, "duplicate"), AIMessage(content="Unsupported actions stopped."))
        turn = await self.respond(model, "Set the next workflow rules to defaults")
        errors = [e["error"] for e in turn["events"] if e["type"] == "tool_error"]
        self.assertTrue(any("Unregistered" in e for e in errors))
        self.assertTrue(any("Duplicate" in e for e in errors))
        self.assertEqual(self.store.session(self.session["id"])["project_input"]["grid"]["n_long_bays"], 8)

    async def test_model_error_and_credentials_are_not_exposed(self):
        class Broken(ControlledModel):
            async def ainvoke(self, messages):
                raise RuntimeError("PRIVATE-CREDENTIAL")
        turn = await self.respond(Broken())
        self.assertEqual(turn["status"], "ERROR")
        self.assertNotIn("PRIVATE-CREDENTIAL", json.dumps(turn))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "PRIVATE-CREDENTIAL"}):
            self.assertNotIn("PRIVATE-CREDENTIAL", json.dumps(redact({"error": "PRIVATE-CREDENTIAL", "api_key": "x"})))

    async def test_active_session_lock_and_restart_do_not_replay(self):
        turn = self.store.new_turn(self.session["id"], "first")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.new_turn(self.session["id"], "second")
        restored = ChatStore(self.store.path)
        self.assertEqual(restored.turn(turn["id"])["status"], "ERROR")

    async def test_input_changes_invalidate_selected_run_and_reject_forged_results(self):
        self.session["run_id"] = "run_12345678"
        self.store.save_session(self.session)
        proposed = await self.respond(ControlledModel(call("update_project_inputs", {"grid_updates": {"n_trans_bays": 6}}), AIMessage(content="New generation needed.")), "Change transverse bays to 6")
        self.assertEqual(self.store.session(self.session["id"])["run_id"], "run_12345678")
        plan = proposed["change_proposals"][0]
        plan = self.store.transition_plan(plan["id"], self.session["id"], "PENDING", "EXECUTING")
        approved = self.store.new_turn(self.session["id"], "Apply approved project change")
        await ChatRuntime(self.store).execute_approved(approved, plan, False)
        self.assertIsNone(self.store.session(self.session["id"])["run_id"])
        raw = {"grid": {"n_long_bays": 8}, "frame_grader_result": {"passed": True}}
        self.assertNotIn("frame_grader_result", project_input(raw))
        for grid in ({"n_long_bays": -1}, {"n_long_bays": 10000}, {"starting_point": [0, 0]}, {"template_path": "arbitrary"}):
            with self.assertRaises(ValueError):
                project_input({"grid": grid})

    async def test_preview_uses_existing_geometry_and_cannot_pass_engineering_gate(self):
        state = WorkflowState(run_id="preview", status="RUNNING", raw_input=self.session["project_input"])
        with patch("agentic.orchestrator._sync_state"), patch("agentic.deep_runtime.run_deep_stage"), patch.dict(os.environ, {"PLANT_DEEP_AGENTS_ENABLED": "false"}), \
             patch("agentic.agents.a3_frame_generator_agent._lookup_profile_height", return_value=133), \
             patch("agentic.agents.a3_frame_generator_agent._lookup_profile_width", return_value=120):
            state = await run_chat_action(state, "preview_structure")
            self.assertEqual(state.status, "READY")
            self.assertTrue(state.readiness["preview_only"])
            self.assertGreater(len(state.frame_candidate["members"]), 0)
            self.assertEqual(state.agent_statuses["A3"]["status"], "PREVIEW")
            state.status = "RUNNING"
            state = await run_chat_action(state, "analyze_frame")
        self.assertEqual(state.status, "FAILED")
        self.assertIn("preview", state.error)

    async def test_loop_a_reuses_existing_routing_and_stages(self):
        state = WorkflowState(run_id="loop", status="RUNNING", agent_statuses={"A3": {"status": "PASSED"}})
        calls = []
        async def a4(s):
            calls.append("A4")
            s.agent_statuses["A4"] = {"status": "REVISING" if len(calls) == 1 else "PASSED"}
            s.loop_a_count = 1
            return s
        async def a3(s): calls.append("A3"); return s
        with patch("agentic.orchestrator.node_a4", side_effect=a4), patch("agentic.orchestrator.node_a3", side_effect=a3):
            result = await run_chat_action(state, "analyze_frame")
        self.assertEqual(calls, ["A4", "A3", "A4"])
        self.assertEqual(result.status, "READY")

    async def test_missing_frame_evidence_blocks_idea_and_publication(self):
        for action in ("design_connections", "publish_to_tekla"):
            state = WorkflowState(run_id="invalid", status="RUNNING", agent_statuses={"A3": {"status": "PASSED"}})
            with patch("agentic.orchestrator.node_a5", new_callable=AsyncMock) as a5, patch("agentic.orchestrator.node_a7", new_callable=AsyncMock) as a7:
                result = await run_chat_action(state, action)
                a5.assert_not_awaited(); a7.assert_not_awaited()
                self.assertEqual(result.status, "FAILED")

    async def test_connection_and_package_loops_preserve_human_gate(self):
        from agentic.evidence import frame_signature
        state = WorkflowState(run_id="loops", status="RUNNING", agent_statuses={"A3": {"status": "PASSED"}},
                              frame_grader_result=GraderResult(passed=True, summary="Controlled passing fixture"))
        state.staad_results["candidate_signature"] = frame_signature(state)
        calls = []
        async def a5(s):
            calls.append("A5")
            s.agent_statuses["A5"] = {"status": "REVISING" if calls.count("A5") == 1 else "PASSED"}
            s.loop_b_count = 1
            return s
        async def a6(s):
            calls.append("A6")
            s.loop_c_count = 1
            s.agent_statuses["A6"] = {"status": "REVISING" if calls.count("A6") == 1 else "PASSED"}
            s.validation_report = {"passed": calls.count("A6") > 1, "recovery_target": "a5_ideastatica_analyzer"}
            return s
        async def a7(s): calls.append("A7"); return s
        async def a8(s): calls.append("A8"); s.status = "PAUSED_AT_GATE3"; return s
        with patch("agentic.orchestrator.node_a5", side_effect=a5), patch("agentic.orchestrator.node_a6", side_effect=a6), \
             patch("agentic.orchestrator.node_a7", side_effect=a7), patch("agentic.orchestrator.node_a8", side_effect=a8), \
             patch("agentic.orchestrator.route_after_a6", return_value="a5_ideastatica_analyzer"):
            result = await run_chat_action(state, "publish_to_tekla")
        self.assertEqual(calls, ["A6", "A5", "A5", "A6", "A7", "A8"])
        self.assertEqual(result.status, "PAUSED_AT_GATE3")
        self.assertFalse(result.gate3_approved)


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentic import chat_router
        self.module = chat_router
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name) / "chat.db")
        self.patchers = [patch.object(chat_router, "CHAT_STORE", self.store),
                         patch.object(chat_router, "RUNTIME", ChatRuntime(self.store, lambda: ControlledModel(call("get_project_context"), AIMessage(content="Inputs read.")))),
                         patch.dict(os.environ, {"PLANT_CHAT_ENABLED": "true", "OPENAI_API_KEY": "test-placeholder", "PLANT_AGENT_MODEL": "controlled-test"})]
        for p in self.patchers: p.start()
        app = FastAPI(); app.include_router(chat_router.router)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for p in reversed(self.patchers): p.stop()
        self.temp.cleanup()

    def test_chat_endpoint_poll_history_and_validation(self):
        response = self.client.post('/api/v2/chat/messages', json={"message": "Read inputs", "project_input": {"grid": {"n_long_bays": 8}}})
        self.assertEqual(response.status_code, 202)
        turn_id = response.json()["turn_id"]
        import time
        for _ in range(50):
            result = self.client.get('/api/v2/chat/turns/' + turn_id).json()
            if result["status"] != "RUNNING": break
            time.sleep(.01)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["assistant"], "Inputs read.")
        session = self.client.get('/api/v2/chat/sessions/' + response.json()["session_id"]).json()
        self.assertEqual(len(session["turns"]), 1)
        self.assertEqual(self.client.post('/api/v2/chat/messages', json={"message":"x", "project_input":{"grid":{"n_long_bays":-1}}}).status_code, 422)
        self.assertEqual(self.client.get('/api/v2/chat/turns/missing').status_code, 404)

    def test_missing_credentials_do_not_create_fake_chat_response(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.client.get('/api/v2/chat/config').json()["enabled"], False)
            self.assertEqual(self.client.post('/api/v2/chat/messages', json={"message":"Hi"}).status_code, 503)

    def test_running_turn_follows_each_current_candidate_before_tool_completion(self):
        workflows = SqliteWorkflowStore(str(Path(self.temp.name) / 'runs.db'))
        session = self.store.session()
        turn = self.store.new_turn(session['id'], 'Generate, analyze and check connections')
        turn['run_id'] = 'run_11111111'  # Previous completed tool; still stale during the next action.
        self.store.save_turn(turn)
        with patch.object(self.module, 'WORKFLOW_STORE', workflows):
            for number, agent in enumerate(('A3', 'A4', 'A5', 'A6'), 2):
                run_id = 'run_' + str(number) * 8
                workflows.save_state(WorkflowState(run_id=run_id, status='RUNNING', current_agent=agent))
                session['run_id'] = run_id
                self.store.save_session(session)
                response = self.client.get('/api/v2/chat/turns/' + turn['id']).json()
                self.assertEqual(response['run_id'], run_id)
                self.assertEqual(response['progress']['run_id'], run_id)
                self.assertEqual(response['progress']['current_agent'], agent)
        self.assertEqual(self.store.turn(turn['id'])['run_id'], 'run_11111111',
                         'Polling must not rewrite historical tool evidence.')

    def test_cleared_inputs_do_not_reuse_a_running_turns_previous_candidate(self):
        session = self.store.session()
        turn = self.store.new_turn(session['id'], 'Change inputs before generation')
        turn['run_id'] = 'run_11111111'
        self.store.save_turn(turn)
        session['run_id'] = None
        self.store.save_session(session)
        response = self.client.get('/api/v2/chat/turns/' + turn['id']).json()
        self.assertIsNone(response['run_id'])
        self.assertIsNone(response['progress'])

    def test_completed_turn_keeps_its_result_when_the_session_selects_another_run(self):
        workflows = SqliteWorkflowStore(str(Path(self.temp.name) / 'runs.db'))
        session = self.store.session()
        turn = self.store.new_turn(session['id'], 'Previous calculation')
        turn.update(status='COMPLETED', run_id='run_11111111')
        self.store.save_turn(turn)
        session['run_id'] = 'run_22222222'
        self.store.save_session(session)
        workflows.save_state(WorkflowState(run_id='run_11111111', status='READY', current_agent='A4'))
        workflows.save_state(WorkflowState(run_id='run_22222222', status='RUNNING', current_agent='A5'))
        with patch.object(self.module, 'WORKFLOW_STORE', workflows):
            response = self.client.get('/api/v2/chat/turns/' + turn['id']).json()
        self.assertEqual(response['run_id'], 'run_11111111')
        self.assertEqual(response['progress']['current_agent'], 'A4')

    def test_saved_sessions_rename_and_complete_history_survive_new_chat(self):
        original = self.client.post('/api/v2/chat/sessions').json()
        # More than 50 turns must remain available for reopening and copying a full chat.
        for i in range(55):
            turn = self.store.new_turn(original['id'], 'Message ' + str(i))
            turn.update(status='COMPLETED', assistant='Reply ' + str(i))
            self.store.save_turn(turn)
        active = self.store.new_turn(original['id'], 'Still running')
        stale = self.store.session(original['id'])
        response = self.client.patch('/api/v2/chat/sessions/' + original['id'], json={'title':'8x5 design review'})
        self.assertEqual(response.status_code, 200)
        self.store.save_session(stale)  # A running tool cannot undo a concurrent rename.
        created = self.client.post('/api/v2/chat/sessions').json()
        self.assertNotEqual(created['id'], original['id'])
        saved = self.client.get('/api/v2/chat/sessions/' + original['id']).json()
        self.assertEqual(saved['title'], '8x5 design review')
        self.assertEqual(len(saved['turns']), 56)
        self.assertEqual(saved['turns'][0]['message'], 'Message 0')
        self.assertEqual(saved['turns'][-1]['status'], 'RUNNING')
        listed = self.client.get('/api/v2/chat/sessions').json()['sessions']
        self.assertTrue(next(s for s in listed if s['id']==original['id'])['running'])
        self.assertEqual(self.client.get('/api/v2/chat/sessions/' + created['id']).json()['turns'], [])
        self.assertEqual(self.client.patch('/api/v2/chat/sessions/' + original['id'], json={'title':'  '}).status_code, 422)
        self.assertEqual(self.client.patch('/api/v2/chat/sessions/missing', json={'title':'x'}).status_code, 404)
        active.update(status='COMPLETED', assistant='Finished in the original chat')
        self.store.save_turn(active)
        self.assertEqual(self.client.get('/api/v2/chat/sessions/' + original['id']).json()['turns'][-1]['assistant'], 'Finished in the original chat')


if __name__ == '__main__':
    unittest.main()
