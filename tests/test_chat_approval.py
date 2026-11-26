"""Full-request routing and manual approval lifecycle. No native engineering/LLM calls."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from langchain_core.messages import AIMessage
from agentic.chat import ChatRuntime, requests_complete_workflow
from agentic.chat_store import ChatStore
from agentic.state import GraderResult, fail_workflow
from agentic.store import SqliteWorkflowStore
from test_chat import ControlledModel, call


class FullRequestScopeTests(unittest.TestCase):
    def test_general_runs_include_the_complete_workflow(self):
        for text in ['21X5 current input run', 'Run agentic workflow', 'Run the current inputs',
                     'Please run this 21 × 5 bay frame', 'Just run the workflow including Tekla',
                     'Execute the pipeline', 'Run A1-A7', 'Run A1–A7', 'Run A1-A8', 'Run A1–A8', 'run this']:
            with self.subTest(text=text):
                self.assertTrue(requests_complete_workflow(text))

    def test_narrow_and_negative_requests_are_preserved(self):
        for text in ['Run STAAD only', 'Run only STAAD', 'Analyze the frame', 'Run the IDEA connection checks',
                     'Run the workflow but stop before Tekla', 'Run only A1 to A6',
                     'Do not run the workflow', 'Show a preview only', 'What is the current status?',
                     'Continue the validated run through A7 and A8']:
            with self.subTest(text=text):
                self.assertFalse(requests_complete_workflow(text))


class ChatApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from agentic import chat_router, router, orchestrator
        self.chat_router, self.router, self.flow = chat_router, router, orchestrator
        self.temp = tempfile.TemporaryDirectory()
        self.chats = ChatStore(Path(self.temp.name) / 'chat.db')
        self.workflows = SqliteWorkflowStore(str(Path(self.temp.name) / 'runs.db'))
        self.session = self.chats.session()
        self.session['project_input'] = {'grid': {'n_long_bays': 21, 'n_trans_bays': 5}}
        self.chats.save_session(self.session)
        self.calls = []
        self.fail_stage = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.temp.cleanup)
        for module, field, value in [(chat_router, 'CHAT_STORE', self.chats),
                                     (chat_router, 'WORKFLOW_STORE', self.workflows),
                                     (router, 'WORKFLOW_STORE', self.workflows)]:
            self.stack.enter_context(patch.object(module, field, value))
        self.stack.enter_context(patch('agentic.chat.WORKFLOW_STORE', self.workflows))
        self.stack.enter_context(patch.dict(os.environ, {'PLANT_DEEP_AGENTS_ENABLED': 'false',
            'PLANT_LLM_ENABLED': 'false', 'PLANT_WORKFLOW_MODE': 'poc2_automation', 'LANGSMITH_TRACING': 'false',
            'PLANT_CHAT_ENABLED':'true', 'OPENAI_API_KEY':'controlled-test-key', 'PLANT_AGENT_MODEL':'controlled-model'}))
        self.stack.enter_context(patch.object(orchestrator, '_sync_state', self.workflows.save_state))
        real_a1, real_a8 = orchestrator.run_a1_requirements, orchestrator.run_a8_reporting
        def a1(state):
            self.calls.append('A1')
            return real_a1(state)
        def a8(state):
            self.calls.append('A8')
            return real_a8(state)
        self.stack.enter_context(patch.object(orchestrator, 'run_a1_requirements', a1))
        self.stack.enter_context(patch.object(orchestrator, 'run_a8_reporting', a8))
        names = {'A2':'run_a2_readiness', 'A3':'run_a3_staged', 'A4':'run_a4_staged',
                 'A5':'run_a5_staged', 'A6':'run_a6_package_validator', 'A7':'run_a7_staged'}
        for stage, name in names.items():
            def operation(state, stage=stage):
                self.calls.append(stage)
                if self.fail_stage == stage:
                    return fail_workflow(state, stage, 'Controlled failed engineering evidence')
                status_stage = 'A6' if stage == 'A7' and state.layout_version == 2 else stage
                state.agent_statuses[status_stage] = {'status':'PASSED'}
                if stage == 'A4':
                    state.frame_grader_result = GraderResult(passed=True, summary='Controlled frame evidence')
                if stage == 'A6':
                    state.validation_report = {'passed':True, 'package_hash':'controlled-fixture'}
                if stage == 'A7':
                    state.tekla_results = {'created':194}
                return state
            self.stack.enter_context(patch.object(orchestrator, name, operation))
        async def idea_graph(state, *, audit_package):
            self.assertTrue(audit_package)
            state = orchestrator.run_a5_staged(state)
            if state.status == "FAILED":
                return state
            state = orchestrator.run_a6_package_validator(state)
            if state.status != "FAILED":
                state.agent_statuses["A5c"] = state.agent_statuses.pop("A6")
            return state
        self.stack.enter_context(patch.object(orchestrator, 'run_idea_graph', idea_graph))
        async def execute(payload, run_id, **kwargs):
            self.assertNotIn('chat_action', kwargs, 'A general run must invoke the existing full graph')
            return await orchestrator.run_agentic_workflow(payload, run_id)
        self.controller = type('Controller', (), {'run_id':None, 'execute':AsyncMock(side_effect=execute)})()
        self.stack.enter_context(patch('agentic.execution.CONTROLLER', self.controller))

    async def full_request(self, model=None):
        turn = self.chats.new_turn(self.session['id'], '21X5 current input run')
        model = model or ControlledModel(call('run_complete_workflow'), AIMessage(content='A8 report prepared.'))
        await ChatRuntime(self.chats, lambda:model).respond(turn, allow_native=True)
        return self.chats.turn(turn['id']), model

    async def test_short_run_executes_all_eight_then_waits_for_manual_approval(self):
        turn, model = await self.full_request()
        self.assertEqual(self.calls, ['A'+str(n) for n in range(1,9)])
        self.assertEqual(turn['status'], 'AWAITING_APPROVAL')
        self.assertIn('manual engineer approval', turn['assistant'])
        self.assertIn('run_complete_workflow', model.tools)
        self.assertNotIn('validate_package', model.tools)
        state = self.workflows.get_state(turn['run_id'])
        self.assertEqual(state.status, 'PAUSED_AT_GATE3')
        self.assertFalse(state.gate3_approved)
        with self.assertRaises(sqlite3.IntegrityError):
            self.chats.new_turn(self.session['id'], 'I approve in chat')
        self.assertTrue(self.chats.list_sessions()[0]['awaiting_approval'])

    async def test_run_policy_reaches_a8_and_next_full_graph_uses_defaults(self):
        from agentic.run_policy import RunPolicy
        from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
        policy = {'max_member_ratio': 0.8, 'drift_denominator': 300,
                  'max_bolt_utilization_percent': 90, 'max_weld_utilization_percent': 85}
        model = ControlledModel(call('set_next_run_policy', {'run_policy': policy}),
                                call('run_complete_workflow'), AIMessage(content='Report ready.'))
        turn, _ = await self.full_request(model)
        self.assertEqual(self.calls, ['A'+str(n) for n in range(1,9)])
        state = self.workflows.get_state(turn['run_id'])
        self.assertEqual(state.run_policy, RunPolicy.model_validate(policy).model_dump())
        self.assertEqual(state.final_report['run_policy'], RunPolicy.model_validate(policy).model_dump())
        self.assertFalse(state.gate3_approved)
        self.workflows.save_state(accept_gate3(state, 'Test Engineer'))
        self.chat_router.sync_approval(turn)
        second, _ = await self.full_request()
        self.assertEqual(self.workflows.get_state(second['run_id']).run_policy, RunPolicy().model_dump())
        self.assertEqual(second['status'], 'AWAITING_APPROVAL')

    async def test_actual_manual_acceptance_completes_chat_once_and_allows_same_session(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        turn, _ = await self.full_request()
        app = FastAPI(); app.include_router(self.router.router); app.include_router(self.chat_router.router)
        with TestClient(app) as client:
            before = client.get('/api/v2/chat/turns/'+turn['id']).json()
            self.assertEqual(before['status'], 'AWAITING_APPROVAL')
            blocked = client.post('/api/v2/chat/messages', json={'session_id':self.session['id'], 'message':'I approve in chat'})
            self.assertEqual(blocked.status_code, 409)
            self.assertFalse(self.workflows.get_state(turn['run_id']).gate3_approved)
            response = client.post('/api/v2/agentic/accept/'+turn['run_id'], json={'engineer_name':'Test Engineer'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['final_report']['gate3_status'], 'ACCEPTED')
            accepted = client.get('/api/v2/chat/turns/'+turn['id']).json()
            self.assertEqual(accepted['status'], 'COMPLETED')
            self.assertEqual(accepted['approval']['accepted_by'], 'Test Engineer')
            self.assertTrue(accepted['progress']['gate3_approved'])
            again = client.get('/api/v2/chat/turns/'+turn['id']).json()
            self.assertEqual(len([e for e in again['events'] if e['type']=='approval_recorded']), 1)
            self.assertEqual(accepted['assistant'], again['assistant'])
        new_turn = self.chats.new_turn(self.session['id'], 'What is the run status?')
        model = ControlledModel(call('get_run_status'), AIMessage(content='The run was approved.'))
        await ChatRuntime(self.chats, lambda:model).respond(new_turn)
        self.assertEqual(self.chats.turn(new_turn['id'])['status'], 'COMPLETED')
        self.assertEqual(len(self.chats.history(self.session['id'])), 2)
        self.assertEqual(self.controller.execute.await_count, 1)

    async def test_pending_approval_survives_restart_and_cannot_be_faked_by_status_alone(self):
        turn, _ = await self.full_request()
        restored = ChatStore(self.chats.path)
        self.assertEqual(restored.turn(turn['id'])['status'], 'AWAITING_APPROVAL')
        state = self.workflows.get_state(turn['run_id'])
        state.status = 'COMPLETED'; state.gate3_approved = False
        self.workflows.save_state(state)
        result = self.chat_router.sync_approval(turn)
        self.assertEqual(result['status'], 'AWAITING_APPROVAL')

    async def test_real_engineering_failure_stops_before_tekla(self):
        self.fail_stage = 'A5'
        turn, _ = await self.full_request()
        self.assertEqual(self.calls, ['A1','A2','A3','A4','A5'])
        self.assertEqual(self.workflows.get_state(turn['run_id']).status, 'FAILED')
        self.assertEqual(turn['status'], 'COMPLETED')  # Response finished, engineering did not pass.
        self.assertNotIn('approval', turn)

    async def test_summary_failure_preserves_actual_pending_approval(self):
        model = ControlledModel(call('run_complete_workflow'))  # No final model reply available.
        turn, _ = await self.full_request(model)
        self.assertEqual(turn['status'], 'AWAITING_APPROVAL')
        self.assertEqual(self.workflows.get_state(turn['run_id']).status, 'PAUSED_AT_GATE3')


if __name__ == '__main__':
    unittest.main()
