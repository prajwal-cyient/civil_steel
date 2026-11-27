"""Temporary chat inputs reset without changing the master or saved evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

import test_chat as fixtures
from chatbot.chatbot import ChatRuntime
from chatbot.parameter_changes import build_project_proposal
from chatbot.input_lifetime import reset_finished_inputs, next_request_inputs
from orchestration.state import WorkflowState


MASTER = {'grid':{'n_long_bays':21,'n_trans_bays':5}}
CUSTOM = {'grid':{'n_long_bays':8,'n_trans_bays':5}}


class InputLifetimeTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ChatTests.setUp
    tearDown = fixtures.ChatTests.tearDown

    async def test_custom_run_then_new_run_uses_master_and_default_policy(self):
        payloads = []
        async def execute(payload, run_id):
            payloads.append(deepcopy(payload))
            state = WorkflowState(run_id=run_id, status='COMPLETED', raw_input=payload)
            self.workflows.save_state(state)
            return state
        controller = SimpleNamespace(run_id=None, execute=AsyncMock(side_effect=execute))
        session = self.session
        session['next_run_policy'] = {'max_bolt_utilization_percent':90.}
        runtime = ChatRuntime(self.store)
        with patch('orchestration.execution.CONTROLLER', controller), patch('chatbot.chatbot.WORKFLOW_STORE',self.workflows), \
             patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(MASTER)):
            await runtime.engineering(session, 'run_complete_workflow', True)
            self.assertEqual(session['project_input'], MASTER)
            first_id = session['run_id']
            await runtime.engineering(session, 'run_complete_workflow', True)
        self.assertEqual(payloads[0]['grid']['n_long_bays'],8)
        self.assertEqual(payloads[0]['run_policy']['max_bolt_utilization_percent'],90.)
        self.assertEqual(payloads[1],MASTER)
        self.assertEqual(self.workflows.get_state(first_id).raw_input,payloads[0])

    async def test_reset_only_after_terminal_status_and_recovery_after_restart(self):
        for status in ['RUNNING','READY','PAUSED_AT_GATE3','COMPLETED','FAILED','CANCELLED']:
            with self.subTest(status=status):
                state = WorkflowState(run_id='run_12345678',status=status,raw_input=deepcopy(CUSTOM))
                self.workflows.save_state(state)
                session = self.store.session()
                session.update(project_input=deepcopy(CUSTOM),run_id=state.run_id,temporary_input_run_id=state.run_id)
                self.store.save_session(session)
                # Load a fresh session to exercise persisted recovery markers.
                session = self.store.session(session['id'])
                with patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(MASTER)):
                    result = reset_finished_inputs(session,self.store,self.workflows)
                expected = status in {'COMPLETED','FAILED','CANCELLED'}
                self.assertEqual(result,expected)
                self.assertEqual(session['project_input'],MASTER if expected else CUSTOM)
                self.assertEqual(session['run_id'],state.run_id)

    async def test_old_form_cannot_reintroduce_custom_geometry_but_new_edits_can(self):
        session = {'project_input':deepcopy(MASTER), 'input_reset':{
            'previous_run_input':deepcopy(CUSTOM),'master_input':deepcopy(MASTER)}}
        with patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(MASTER)):
            self.assertEqual(next_request_inputs(session,CUSTOM,fresh=True),MASTER)
            self.assertIsNone(next_request_inputs(session,CUSTOM,fresh=False))
            explicit = {'grid':{'n_long_bays':12,'n_trans_bays':5}}
            self.assertEqual(next_request_inputs(session,explicit,fresh=True),explicit)
            explicit_policy = {**CUSTOM,'run_policy':{'max_bolt_utilization_percent':80.}}
            self.assertEqual(next_request_inputs(session,explicit_policy,fresh=True)['run_policy'],explicit_policy['run_policy'])

    async def test_master_is_reloaded_for_next_run_and_invalid_master_is_not_guessed(self):
        session = self.session
        session.update(project_input=deepcopy(MASTER),input_reset={
            'previous_run_input':deepcopy(CUSTOM),'master_input':deepcopy(MASTER)})
        newer = {'grid':{'n_long_bays':22,'n_trans_bays':5}}
        with patch('chatbot.input_lifetime.master_inputs',return_value=newer):
            self.assertEqual(next_request_inputs(session,MASTER,fresh=True),newer)
        state=WorkflowState(run_id='run_12345678',status='FAILED',raw_input=CUSTOM)
        session.update(project_input=deepcopy(CUSTOM),temporary_input_run_id=state.run_id)
        with patch('chatbot.input_lifetime.master_inputs',side_effect=ValueError('invalid master')):
            self.assertFalse(reset_finished_inputs(session,self.store,self.workflows,state))
        self.assertEqual(session['project_input'],CUSTOM)
        self.assertIn('input_reset_error',session)

    async def test_approved_run_exports_custom_inputs_while_session_restores_master(self):
        runtime=ChatRuntime(self.store)
        plan=build_project_proposal(self.session,{'n_long_bays':10},None,'run_complete_workflow')
        self.store.save_plan(plan)
        plan=self.store.transition_plan(plan['id'],self.session['id'],'PENDING','EXECUTING')
        turn=self.store.new_turn(self.session['id'],'Apply approved changes')
        async def execute(payload,run_id,*,chat_parameter_rerun=False):
            self.assertTrue(chat_parameter_rerun)
            state=WorkflowState(run_id=run_id,status='COMPLETED',raw_input=payload)
            self.workflows.save_state(state)
            return state
        controller=SimpleNamespace(run_id=None,execute=AsyncMock(side_effect=execute))
        with patch('orchestration.execution.CONTROLLER',controller),patch('chatbot.chatbot.WORKFLOW_STORE',self.workflows), \
             patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(MASTER)):
            await runtime.execute_approved(turn,plan,True)
        self.assertEqual(turn['status'],'COMPLETED',turn)
        self.assertEqual(self.store.session(self.session['id'])['project_input'],MASTER)
        saved=json.loads((Path(turn['artifacts']['directory'])/'project_input.json').read_text())
        self.assertEqual(saved['grid']['n_long_bays'],10)

    async def test_master_file_is_read_only(self):
        from config.settings import ROOT
        from chatbot.input_lifetime import master_inputs
        path=ROOT/'src/agents/A3_generate_structure/frame_specs.json'
        before=path.read_bytes()
        master_inputs()
        self.assertEqual(path.read_bytes(),before)

    async def test_manual_approval_observation_resets_inputs_and_keeps_saved_result(self):
        from chatbot import chat_router
        state=WorkflowState(run_id='run_12345678',status='COMPLETED',raw_input=deepcopy(CUSTOM),
            gate3_approved=True,agent_statuses={'A8':{'status':'PASSED'}},accepted_by='Test Engineer',
            acceptance_timestamp='2026-11-23T12:00:00')
        self.workflows.save_state(state)
        self.session.update(project_input=deepcopy(CUSTOM),run_id=state.run_id,temporary_input_run_id=state.run_id)
        self.store.save_session(self.session)
        turn=self.store.new_turn(self.session['id'],'Run customized inputs')
        turn.update(status='AWAITING_APPROVAL',run_id=state.run_id)
        self.store.save_turn(turn)
        with patch.object(chat_router,'CHAT_STORE',self.store),patch.object(chat_router,'WORKFLOW_STORE',self.workflows), \
             patch('chatbot.input_lifetime.master_inputs',return_value=deepcopy(MASTER)):
            completed=chat_router.sync_approval(turn)
        self.assertEqual(completed['status'],'COMPLETED')
        self.assertEqual(self.store.session(self.session['id'])['project_input'],MASTER)
        self.assertEqual(self.workflows.get_state(state.run_id).raw_input,CUSTOM)


if __name__=='__main__':unittest.main()
