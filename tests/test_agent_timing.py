"""Stage clocks include engine overhead, accumulate retries and stop at Gate 3."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.state import WorkflowState
from orchestration import workflow as flow
from utils.agent_timing import stop_running_timings


class AgentTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_clock_is_persisted_and_retries_accumulate_only_execution(self):
        state = WorkflowState(run_id='timed')
        snapshots = []
        def persist(s): snapshots.append(s.model_copy(deep=True))
        def operation(s):
            s.agent_statuses['A5'] = {'status':'PASSED'}
            return s
        with patch.object(flow,'deep_agents_enabled',return_value=False), patch.object(flow,'_sync_state',side_effect=persist), \
             patch('utils.agent_timing.time.monotonic',side_effect=[100.,135.,500.,505.]):
            await flow._run_stage(state,'A5',operation)
            await flow._run_stage(state,'A5',operation)
        timing = state.agent_timings['A5']
        self.assertEqual(timing['elapsed_seconds'],40.)
        self.assertEqual(timing['attempts'],2)
        self.assertFalse(timing['running'])
        self.assertTrue(snapshots[0].agent_timings['A5']['running'])
        self.assertEqual(snapshots[0].agent_statuses['A5']['status'],'RUNNING')
        restored=WorkflowState.model_validate_json(state.model_dump_json())
        self.assertEqual(restored.agent_timings,state.agent_timings)

    async def test_deep_engine_overhead_is_included_and_manual_approval_does_not_extend_timer(self):
        state=WorkflowState(run_id='deep')
        async def engine(s,agent,operation,persist):
            s.status='PAUSED_AT_GATE3'
            s.agent_statuses[agent]={'status':'AWAITING_APPROVAL'}
            return s
        with patch.object(flow,'deep_agents_enabled',return_value=True),patch.object(flow,'_sync_state'), \
             patch('services.deep_agents_runtime.run_deep_stage',new=AsyncMock(side_effect=engine)), \
             patch('utils.agent_timing.time.monotonic',side_effect=[10.,45.]):
            await flow._run_stage(state,'A7',lambda s:s)
        self.assertEqual(state.agent_timings['A7']['elapsed_seconds'],35.)
        self.assertFalse(state.agent_timings['A7']['running'])
        stop_running_timings(state)
        self.assertEqual(state.agent_timings['A7']['elapsed_seconds'],35.)
        self.assertEqual(state.status,'PAUSED_AT_GATE3')
        self.assertFalse(state.gate3_approved)

    async def test_exception_freezes_clock_without_changing_error_behavior(self):
        state=WorkflowState(run_id='failed')
        def operation(s): raise ValueError('native operation failed')
        with patch.object(flow,'deep_agents_enabled',return_value=False),patch.object(flow,'_sync_state'), \
             patch('utils.agent_timing.time.monotonic',side_effect=[0.,2.]):
            with self.assertRaisesRegex(ValueError,'native operation failed'):
                await flow._run_stage(state,'A3',operation)
        self.assertEqual(state.agent_timings['A3']['elapsed_seconds'],2.)
        self.assertFalse(state.agent_timings['A3']['running'])

    async def test_async_exception_preserves_original_error_and_timer(self):
        state=WorkflowState(run_id='async-failed')
        async def operation(s): raise ValueError('async native operation failed')
        with patch.object(flow,'deep_agents_enabled',return_value=False),patch.object(flow,'_sync_state'), \
             patch('utils.agent_timing.time.monotonic',side_effect=[0.,3.]):
            with self.assertRaisesRegex(ValueError,'async native operation failed'):
                await flow._run_stage(state,'A5',operation)
        self.assertEqual(state.agent_timings['A5']['elapsed_seconds'],3.)
        self.assertFalse(state.agent_timings['A5']['running'])

    async def test_terminated_worker_timer_freezes_once(self):
        state=WorkflowState(run_id='cancelled',agent_timings={'A5':{
            'running':True,'started_at':'2026-11-23T12:00:00+00:00','elapsed_seconds':5.,'attempts':2}})
        with patch('utils.agent_timing.datetime') as clock:
            clock.now.return_value=datetime(2026,11,23,12,0,35,tzinfo=timezone.utc)
            clock.fromisoformat.side_effect=datetime.fromisoformat
            stop_running_timings(state)
            stop_running_timings(state)
        self.assertEqual(state.agent_timings['A5']['elapsed_seconds'],40.)
        self.assertFalse(state.agent_timings['A5']['running'])


if __name__=='__main__':unittest.main()
