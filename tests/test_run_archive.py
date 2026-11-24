"""Run archives use isolated fixtures, never live native models."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agentic.state import WorkflowState
from agentic.store import SqliteWorkflowStore
from agentic import orchestrator as flow
from agentic.run_archive import archive_folder, checkpoint, start_idea, record_idea, finish_stage, guarded


class RunArchiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.store=SqliteWorkflowStore(str(self.root/'runs.db'))
        self.state=WorkflowState(run_id='run_12345678',status='RUNNING',raw_input={
            'grid':{'n_long_bays':8,'n_trans_bays':5},'run_policy':{'max_bolt_utilization_percent':90}})
        self.store.save_state(self.state)
        self.folder=archive_folder(self.state)

    def read(self,path):return json.loads(path.read_text(encoding='utf-8'))

    async def test_stable_folder_original_inputs_and_final_acceptance_updates(self):
        self.assertIn('store output run',str(self.folder))
        self.assertTrue(self.folder.name.endswith('_IST'))
        original=self.read(self.folder/'00_input/original_input.json')
        self.state.grid_config={'n_long_bays':8,'n_trans_bays':5,'col_height_mm':5000}
        self.state.run_policy={'max_bolt_utilization_percent':90}
        self.state.status='PAUSED_AT_GATE3'
        self.store.save_state(self.state)
        self.assertEqual(archive_folder(self.state),self.folder)
        self.assertFalse(self.read(self.folder/'03_final_output/engineer_approval.json')['approved'])
        self.state.status='COMPLETED';self.state.gate3_approved=True;self.state.accepted_by='Test Engineer'
        self.store.save_state(self.state)
        self.assertEqual(self.read(self.folder/'00_input/original_input.json'),original)
        self.assertEqual(self.read(self.folder/'00_input/run_policy.json'),self.state.run_policy)
        self.assertTrue(self.read(self.folder/'03_final_output/engineer_approval.json')['approved'])
        self.assertEqual(self.read(self.folder/'03_final_output/workflow_output.json')['status'],'COMPLETED')
        self.assertEqual(len(list((self.folder/'01_agents').iterdir())),8)

    async def test_stage_inputs_outputs_and_retries_preserve_each_attempt(self):
        async def operation(state):
            state.agent_statuses['A5']={'status':'PASSED'}
            state.loop_b_count['test']=state.loop_b_count.get('test',0)+1
            return state
        with patch.object(flow,'_sync_state',self.store.save_state),patch.object(flow,'deep_agents_enabled',return_value=False):
            await flow._run_stage(self.state,'A5',operation)
            first=(self.folder/'01_agents/A5/attempt_001/output.json').read_bytes()
            await flow._run_stage(self.state,'A5',operation)
        base=self.folder/'01_agents/A5'
        self.assertEqual(self.read(base/'attempt_001/input.json')['loop_b_count'],{})
        self.assertEqual(self.read(base/'attempt_001/output.json')['loop_b_count'],{'test':1})
        self.assertEqual(self.read(base/'attempt_002/output.json')['loop_b_count'],{'test':2})
        self.assertEqual((base/'attempt_001/output.json').read_bytes(),first)
        self.assertFalse(self.read(base/'attempt_002/process.json')['timing']['running'])

    async def test_failed_agent_and_unexecuted_agents_are_explicit(self):
        async def operation(state):raise ValueError('test solver failure')
        with patch.object(flow,'_sync_state',self.store.save_state),patch.object(flow,'deep_agents_enabled',return_value=False):
            with self.assertRaises(ValueError):await flow._run_stage(self.state,'A3',operation)
        self.assertIn('test solver failure',self.read(self.folder/'01_agents/A3/attempt_001/process.json')['error'])
        self.state.status='FAILED';self.state.error='test solver failure';self.store.save_state(self.state)
        manifest=self.read(self.folder/'manifest.json')
        self.assertFalse(manifest['agents']['A4']['execution_recorded'])
        self.assertEqual(manifest['agents']['A4']['status'],'NOT_RUN')
        self.assertEqual(self.read(self.folder/'03_final_output/workflow_output.json')['error'],'test solver failure')

    async def test_staad_native_evidence_is_preserved_per_attempt(self):
        source=self.root/'native';source.mkdir()
        model=source/'structure.std';model.write_text('first model')
        (source/'structure.ANL').write_text('analysis')
        (source/'unrelated.std').write_text('must not be copied')
        self.state.readiness['staad_model_path']=str(model)
        self.state.staad_results={'created':168}
        self.state.agent_timings['A4']={'attempts':1}
        finish_stage(self.state,'A4')
        model.write_text('second model');self.state.agent_timings['A4']['attempts']=2
        finish_stage(self.state,'A4')
        base=self.folder/'02_software/STAAD'
        self.assertEqual((base/'attempt_001/native_files/structure.std').read_text(),'first model')
        self.assertEqual((base/'attempt_002/native_files/structure.std').read_text(),'second model')
        self.assertFalse((base/'attempt_001/native_files/unrelated.std').exists())

    async def test_idea_input_exists_before_solve_and_files_are_copied_not_moved(self):
        source=self.root/'connection.ideaCon';source.write_bytes(b'native-idea-project')
        payload={'template_project_path':'fixture','load_effects':[{'N':42}]}
        folder=start_idea(self.state,'inner_node_2_longitudinal_bracings',payload)
        self.assertEqual(self.read(folder/'input.json'),payload)
        result={'saved_project_path':str(source),'check_summary_file':str(self.root/'missing.json'),
                'raw_results_file':None,'raw_results_error':'not generated'}
        record_idea(self.state,'inner_node_2_longitudinal_bracings',payload,result,folder)
        self.assertEqual((folder/'native_files/connection.ideaCon').read_bytes(),source.read_bytes())
        manifest=self.read(folder/'copy_manifest.json')
        self.assertEqual(len(manifest['missing_or_failed_files']),1)
        self.assertIn('raw_results_file',manifest['not_generated'])
        other=start_idea(self.state,'inner_node_2_longitudinal_bracings',payload)
        self.assertNotEqual(other,folder)

    async def test_tekla_model_copy_excludes_locks_and_does_not_claim_reopen_verification(self):
        model=self.root/'TeklaModel';(model/'attributes').mkdir(parents=True)
        (model/'TeklaModel.db1').write_bytes(b'model-database')
        (model/'attributes/connection.j144').write_text('attributes')
        (model/'model.lck').write_text('lock')
        self.state.agent_statuses['A7']={'status':'PASSED'}
        self.state.tekla_results={'model_path':str(model),'created':206}
        finish_stage(self.state,'A7')
        folder=self.folder/'02_software/Tekla/attempt_001'
        self.assertEqual((folder/'model_snapshot/TeklaModel.db1').read_bytes(),b'model-database')
        self.assertTrue((folder/'model_snapshot/attributes/connection.j144').exists())
        self.assertFalse((folder/'model_snapshot/model.lck').exists())
        report=self.read(folder/'copy_manifest.json')
        self.assertEqual(report['snapshot_status'],'COPIED_NOT_REOPEN_VALIDATED')
        self.assertFalse(report['independently_reopen_verified'])
        self.assertEqual(len(report['excluded_files']),1)
        self.assertTrue((model/'model.lck').exists())

    async def test_archive_failure_does_not_change_run_decision(self):
        with patch('agentic.run_archive.write_json',side_effect=OSError('disk unavailable')):
            guarded(self.state,checkpoint)
        self.assertEqual(self.state.status,'RUNNING')
        self.assertIsNone(self.state.error)
        self.assertTrue(any('disk unavailable' in msg for msg in self.state.output_archive['warnings']))

    async def test_fork_gets_distinct_archive_without_rewriting_source(self):
        original=(self.folder/'00_input/original_input.json').read_bytes()
        fork=self.state.model_copy(deep=True);fork.run_id='run_87654321'
        fork.raw_input={'grid':{'n_long_bays':21,'n_trans_bays':5}}
        self.store.save_state(fork)
        self.assertNotEqual(archive_folder(fork),self.folder)
        self.assertEqual((self.folder/'00_input/original_input.json').read_bytes(),original)
        self.assertEqual(self.read(archive_folder(fork)/'00_input/original_input.json'),fork.raw_input)

    async def test_full_eight_stage_fixture_archives_all_agents_and_waits_for_approval(self):
        from contextlib import ExitStack
        import os
        from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
        from agentic.state import GraderResult
        model=self.root/'fixture_model';model.mkdir()
        (model/'fixture_model.db1').write_bytes(b'synthetic fixture, not a real Tekla database')
        def stage(agent):
            def operation(state):
                state.agent_statuses[agent]={'status':'PASSED'}
                if agent=='A4':
                    state.frame_grader_result=GraderResult(passed=True,summary='fixture')
                elif agent=='A6':
                    state.validation_report={'passed':True,'package_hash':'fixture'}
                elif agent=='A7':
                    state.tekla_results={'model_path':str(model),'created':1}
                return state
            return operation
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{'PLANT_DEEP_AGENTS_ENABLED':'false'}))
            stack.enter_context(patch.object(flow,'_sync_state',self.store.save_state))
            for n,name in [(2,'run_a2_readiness'),(3,'run_a3_frame_generator'),(4,'run_a4_staad_analyzer'),
                           (5,'run_a5_ideastatica_analyzer'),(6,'run_a6_package_validator'),(7,'run_a7_tekla_modeler')]:
                stack.enter_context(patch.object(flow,name,stage(f'A{n}')))
            result=await flow.run_agentic_workflow({'grid':{'n_long_bays':8,'n_trans_bays':5}},'run_abcdef12')
        folder=archive_folder(result)
        for n in range(1,9):
            for filename in ['input.json','output.json','process.json']:
                self.assertTrue((folder/'01_agents'/f'A{n}'/'attempt_001'/filename).is_file())
        self.assertEqual(result.status,'PAUSED_AT_GATE3')
        self.assertFalse(self.read(folder/'03_final_output/engineer_approval.json')['approved'])
        self.store.save_state(accept_gate3(result,'Fixture Engineer'))
        self.assertEqual(self.read(folder/'03_final_output/workflow_output.json')['status'],'COMPLETED')
        self.assertTrue(self.read(folder/'03_final_output/engineer_approval.json')['approved'])


if __name__=='__main__':unittest.main()
