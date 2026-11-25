"""Audit guarantees with isolated storage and no live models or network calls."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentic import audit, orchestrator
from agentic.state import WorkflowState, AgentLogEntry, GraderResult
from agentic.store import SqliteWorkflowStore
from agentic.publication_recovery import prepare_publication_retry


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkflowState(run_id='run_audit', current_agent='A4', status='RUNNING')
        self.flags = patch.dict(os.environ, {'LANGSMITH_TRACING_V2':'false'})
        self.flags.start()
        self.addCleanup(self.flags.stop)

    def test_operation_and_exception_have_timing_and_safe_frame_diagnostics(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'private-test-credential'}):
            with self.assertRaises(ValueError):
                with audit.observe(self.state, 'native solver', 'STAAD'):
                    raise ValueError('response body private-test-credential')
        operation, error = self.state.audit_events
        self.assertEqual(operation['status'], 'FAILED')
        self.assertGreaterEqual(operation['elapsed_seconds'], 0)
        self.assertEqual(error['error_type'], 'ValueError')
        self.assertTrue(error['traceback'])
        self.assertNotIn('response body', json.dumps(self.state.audit_events))
        self.assertNotIn('private-test-credential', json.dumps(self.state.audit_events))

    def test_trace_disabled_never_opens_a_remote_span(self):
        with patch('langsmith.trace') as trace:
            with audit.observe(self.state, 'solver', 'STAAD'):
                pass
        trace.assert_not_called()

    def test_trace_has_correlation_and_sanitized_completion(self):
        span = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = span
        with patch.object(audit, 'tracing_enabled', return_value=True), patch('langsmith.trace', return_value=context) as trace:
            with audit.observe(self.state, 'solve', 'IDEA_StatiCa', joint_group='exact_group') as record:
                record['status'] = 'PASSED'
        self.assertEqual(trace.call_args.kwargs['metadata']['run_id'], self.state.run_id)
        self.assertEqual(trace.call_args.kwargs['metadata']['joint_group'], 'exact_group')
        self.assertEqual(span.end.call_args.kwargs['outputs']['status'], 'PASSED')
        context.__exit__.assert_called_once_with(None, None, None)

    def test_trace_backend_failure_does_not_change_engineering_result(self):
        with patch.object(audit, 'tracing_enabled', return_value=True), patch('langsmith.trace', side_effect=ConnectionError('offline')):
            with audit.observe(self.state, 'solve') as record:
                record['status'] = 'PASSED'
        self.assertEqual(self.state.status, 'RUNNING')
        self.assertEqual(record['status'], 'PASSED')
        self.assertTrue(any(e['kind']=='tracing_warning' for e in self.state.audit_events))

    def test_span_end_failure_still_restores_parent_context(self):
        context = MagicMock()
        context.__enter__.return_value.end.side_effect = ConnectionError('offline')
        with patch.object(audit,'tracing_enabled',return_value=True), patch('langsmith.trace',return_value=context):
            with audit.observe(self.state, 'native operation') as record:
                record['status'] = 'PASSED'
        context.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(record['status'], 'PASSED')

    def test_real_sdk_nests_operation_and_evaluation_without_network(self):
        import langsmith
        original = langsmith.trace
        contexts = []
        def trace(*args, **kwargs):
            context = original(*args, **kwargs)
            contexts.append(context)
            return context
        with langsmith.tracing_context(enabled='local'), \
             patch.object(audit,'tracing_enabled',return_value=True), \
             patch('langsmith.trace',side_effect=trace), \
             patch('langsmith.client.Client.create_run') as create, \
             patch('langsmith.client.Client.update_run') as update:
            with audit.observe(self.state, 'Agent A4'):
                audit.evaluation(self.state, 'STAAD', {'passed':False,'summary':'ratio exceeded'})
        create.assert_not_called()
        update.assert_not_called()
        self.assertEqual(contexts[1].new_run.parent_run_id, contexts[0].new_run.id)
        self.assertEqual(contexts[1].new_run.outputs['status'], 'FAILED')
        self.assertIsNotNone(contexts[1].new_run.error)

    def test_correction_links_to_actual_recheck_for_each_tool(self):
        for tool in ['STAAD', 'IDEA_StatiCa', 'Tekla']:
            correction = audit.event(self.state, 'correction', tool, status='AWAITING_RECHECK', action={})
            audit.evaluation(self.state, tool, GraderResult(passed=False, summary='failed actual check'))
            self.assertEqual(correction['status'], 'RECHECK_FAILED')
            result = next(e for e in self.state.audit_events if e['id']==correction['evaluation_id'])
            self.assertEqual(result['correction_ids'], [correction['id']])

    def test_idea_recheck_does_not_resolve_another_groups_correction(self):
        correction = audit.event(self.state, 'correction', 'IDEA_StatiCa', status='AWAITING_RECHECK', action={'joint_group':'group_a'})
        audit.evaluation(self.state, 'IDEA_StatiCa', {'passed':True}, joint_group='group_b')
        self.assertEqual(correction['status'], 'AWAITING_RECHECK')
        audit.evaluation(self.state, 'IDEA_StatiCa', {'passed':True}, joint_group='group_a')
        self.assertEqual(correction['status'], 'VERIFIED')

    def test_tekla_retry_preserves_source_and_records_failure_and_action(self):
        source = WorkflowState(run_id='source', current_agent='A7', status='FAILED',
            error='component readback failed', validation_report={'passed':True}, tekla_results={'errors':['bad component']})
        retry = prepare_publication_retry(source, 'retry')
        action = retry.audit_events[-1]
        self.assertEqual(action['source_run_id'], source.run_id)
        self.assertEqual(action['action']['previous_errors'], ['bad component'])
        self.assertEqual(source.audit_events, [])
        audit.evaluation(retry, 'Tekla', {'passed':True})
        self.assertEqual(action['status'], 'VERIFIED')

    def test_store_and_exports_preserve_events_without_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteWorkflowStore(str(Path(d)/'runs.db'))
            self.state.logs.append(AgentLogEntry(agent_id='A4',agent_name='STAAD',status='FAILED',message='check failed',timestamp=audit.now()))
            audit.evaluation(self.state, 'STAAD', {'passed':False,'summary':'check failed'})
            store.save_state(self.state)
            store.save_state(self.state)
            with store._connection() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM workflow_events').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT count(*) FROM workflow_audit_events').fetchone()[0], 1)
            loaded = store.get_state(self.state.run_id)
            restarted = SqliteWorkflowStore(store.db_path)
            self.assertEqual(restarted.get_state(self.state.run_id).audit_events, loaded.audit_events)
            root = Path(loaded.output_archive['directory'])
            self.assertEqual(json.loads((root/'04_logs/audit_events.json').read_text()), loaded.audit_events)
            self.assertEqual(json.loads((root/'03_final_output/evaluation_results.json').read_text())[0]['result']['summary'], 'check failed')

    def test_worker_flush_is_bounded_and_failure_is_local(self):
        with patch.object(audit,'tracing_enabled',return_value=True), patch('langsmith.run_trees.get_cached_client') as get:
            get.return_value.flush.side_effect = TimeoutError('offline')
            audit.flush_traces(self.state)
            get.return_value.flush.assert_called_once_with(timeout=2.0)
        self.assertEqual(self.state.audit_events[-1]['kind'], 'tracing_warning')

    def test_redaction_keeps_parameter_keys_but_removes_credentials(self):
        result = audit.clean({'key':'EP_d','failed_parameter_keys':['EP_d'],'api_key':'hidden','value':float('inf')})
        self.assertEqual(result['key'], 'EP_d')
        self.assertEqual(result['failed_parameter_keys'], ['EP_d'])
        self.assertEqual(result['api_key'], '[REDACTED]')
        json.dumps(result, allow_nan=False)

    def test_stage_records_actual_change_and_subsequent_evaluation(self):
        async def operation(state):
            audit.evaluation(state, 'STAAD', {'passed':False,'summary':'ratio too high'})
            state.loop_a_history.append({'reason':'ratio too high','failing_profile':'old','upgraded_profile':'new'})
            state.agent_statuses['A4']={'status':'REVISING'}
            return state
        with patch.object(orchestrator,'_sync_state'), patch.object(orchestrator,'deep_agents_enabled',return_value=False):
            asyncio.run(orchestrator._run_stage(self.state, 'A4', operation))
        correction = next(e for e in self.state.audit_events if e['kind']=='correction')
        self.assertEqual(correction['action']['upgraded_profile'], 'new')
        audit.evaluation(self.state, 'STAAD', {'passed':True})
        self.assertEqual(correction['status'], 'VERIFIED')

    def test_worker_parent_audit_survives_graph_state_copy_and_manual_gate(self):
        from agentic import worker
        async def run(candidate, action):
            result = candidate.model_copy(deep=True)
            audit.evaluation(result, 'STAAD', {'passed':True})
            result.status = 'PAUSED_AT_GATE3'
            return result
        store = MagicMock()
        store.get_state.return_value = self.state
        with patch.object(worker,'load_environment'), \
             patch.object(sys,'argv',['worker','--run-id',self.state.run_id,'--chat-action','preview_structure']), \
             patch('agentic.store.WORKFLOW_STORE',store), \
             patch('agentic.chat_actions.run_chat_action',new=AsyncMock(side_effect=run)):
            worker.main()
        result = store.save_state.call_args.args[0]
        parents = [e for e in result.audit_events if e['kind']=='workflow']
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]['status'], 'PAUSED_AT_GATE3')
        self.assertIn('finished_at', parents[0])
        self.assertFalse(result.gate3_approved)


if __name__ == '__main__':
    unittest.main()
