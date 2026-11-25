"""Real Windows filesystem and concurrent checkpoint regression coverage."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentic.archive_io import io_path
from agentic.run_archive import write_json, copy_files, checkpoint
from agentic.state import WorkflowState
from agentic.store import SqliteWorkflowStore


class ArchiveIOTests(unittest.TestCase):
    def test_long_json_and_native_filenames_preserve_content_and_hash(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/('connection_'+'x'*100+'.ideaCon')
            source.write_bytes(b'native project evidence')
            destination = Path(d)/('a'*70)/('b'*70)/('c'*60)
            self.assertGreater(len(str(destination/source.name)), 260)
            report = copy_files([source], destination)
            self.assertEqual(report['missing_or_failed_files'], [])
            self.assertEqual(io_path(destination/source.name).read_bytes(), source.read_bytes())
            manifest = json.loads(io_path(destination/'copy_manifest.json').read_text())
            self.assertEqual(manifest['saved_files'], report['saved_files'])
            cleanup = (Path(d)/('a'*70)).resolve()
            self.assertTrue(cleanup.is_relative_to(Path(d).resolve()))
            shutil.rmtree(io_path(cleanup))

    def test_temporary_name_stays_short(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/('long_'+'x'*110+'.json')
            seen = []
            original = Path.replace
            def replace(source, target):
                seen.append(source.name)
                return original(source, target)
            with patch.object(Path, 'replace', replace):
                write_json(path, {'saved':True})
            self.assertLess(len(seen[0]), 24)
            self.assertEqual(json.loads(path.read_text()), {'saved':True})

    def test_transient_replace_lock_is_retried_and_temp_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'output.json'
            original = Path.replace
            attempts = []
            def replace(source, target):
                attempts.append(source)
                if len(attempts)<3:
                    raise PermissionError('temporary sharing violation')
                return original(source, target)
            with patch.object(Path,'replace',replace), patch('agentic.archive_io.time.sleep'):
                write_json(path, {'complete':True})
            self.assertEqual(len(attempts), 3)
            self.assertEqual(list(Path(d).glob('*.tmp')), [])
            self.assertEqual(json.loads(path.read_text()), {'complete':True})

    def test_permanent_lock_leaves_original_json_and_reports_warning_in_database(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteWorkflowStore(str(Path(d)/'runs.db'))
            state = WorkflowState(run_id='run_locked',status='RUNNING')
            store.save_state(state)
            path = Path(state.output_archive['directory'])/'03_final_output/workflow_output.json'
            original = path.read_bytes()
            with patch.object(Path,'replace',side_effect=PermissionError('locked')), patch('agentic.archive_io.time.sleep'):
                state.error = 'latest persisted diagnostic'
                store.save_state(state)
            self.assertEqual(path.read_bytes(), original)
            saved = store.get_state(state.run_id)
            self.assertTrue(any('PermissionError' in w for w in saved.output_archive['warnings']))
            self.assertEqual(saved.error, state.error)

    def test_stale_checkpoint_exports_latest_database_state(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteWorkflowStore(str(Path(d)/'runs.db'))
            state = WorkflowState(run_id='run_stale',status='RUNNING')
            store.save_state(state)
            stale = state.model_copy(deep=True)
            state.status = 'COMPLETED'
            store.save_state(state)
            checkpoint(stale, store.db_path)
            path = Path(state.output_archive['directory'])/'03_final_output/workflow_output.json'
            self.assertEqual(json.loads(path.read_text())['status'], 'COMPLETED')

    def test_independent_store_writers_leave_consistent_database_and_archive(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d)/'runs.db')
            first, second = SqliteWorkflowStore(path), SqliteWorkflowStore(path)
            state = WorkflowState(run_id='run_concurrent',status='RUNNING')
            first.save_state(state)
            def save(pair):
                store, number = pair
                candidate = state.model_copy(deep=True)
                candidate.current_agent = f'A{number}'
                store.save_state(candidate)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(save, [(first,4),(second,5)]))
            latest = first.get_state(state.run_id)
            snapshot = Path(latest.output_archive['directory'])/'03_final_output/workflow_output.json'
            self.assertEqual(json.loads(snapshot.read_text())['current_agent'], latest.current_agent)

    def test_two_processes_can_checkpoint_the_same_run_without_corrupting_json(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteWorkflowStore(str(Path(d)/'runs.db'))
            state = WorkflowState(run_id='run_processes',status='RUNNING')
            store.save_state(state)
            script = '''import sys,json,sqlite3
from agentic.state import WorkflowState
from agentic.run_archive import checkpoint
with sqlite3.connect(sys.argv[1]) as c:
 raw=c.execute("SELECT state_json FROM workflow_runs").fetchone()[0]
state=WorkflowState.model_validate_json(raw)
for _ in range(3):checkpoint(state,sys.argv[1])
'''
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),
                       PLANT_RUNS_DATABASE_PATH=store.db_path, LANGSMITH_TRACING_V2='false')
            processes = [subprocess.Popen([sys.executable,'-c',script,store.db_path],env=env,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
            for process in processes:
                stdout,stderr = process.communicate(timeout=25)
                self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
            root = Path(state.output_archive['directory'])
            self.assertEqual(json.loads((root/'03_final_output/workflow_output.json').read_text())['run_id'], state.run_id)


if __name__ == '__main__':
    unittest.main()
