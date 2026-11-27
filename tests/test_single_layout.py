"""The supported workflow is A1-A7 only; old stored IDs are never reinterpreted."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pydantic import ValidationError
from orchestration.state import WorkflowState, workflow_stage
from chatbot.tool_registry import execution_plan
from services.workflow_store import SqliteWorkflowStore
from services.deep_agents_runtime import ROLES


class SingleLayoutTests(unittest.TestCase):
    def test_state_only_accepts_layout_two_and_cannot_change_it(self):
        state = WorkflowState(run_id='single')
        self.assertEqual(state.layout_version, 2)
        for version in (1, 0, 3, '1'):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                WorkflowState(run_id='unsupported', layout_version=version)
        with self.assertRaises(ValidationError):
            state.layout_version = 1
        self.assertEqual(workflow_stage(state, 'package'), 'A5c')
        self.assertEqual(workflow_stage(state, 'tekla'), 'A6')
        self.assertEqual(workflow_stage(state, 'report'), 'A7')

    def test_ai_roles_and_plans_have_one_set_of_stage_meanings(self):
        self.assertEqual(set(ROLES), {'A1', 'A2', 'A3', 'A4', 'A5', 'A5c', 'A6', 'A7'})
        self.assertEqual(execution_plan('run_complete_workflow'),
                         ['A1', 'A2', 'A3', 'A4', 'A5', 'A5c:package', 'A6', 'A7:manual_approval'])
        self.assertEqual(execution_plan('publish_selected_connections'),
                         ['A5:selected', 'A5c:package', 'A6:selected', 'A7:report'])

    def test_historical_records_stay_on_disk_but_are_not_loaded_or_listed(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'runs.db'
            store = SqliteWorkflowStore(str(db))
            current = WorkflowState(run_id='current').model_dump()
            old = {**current, 'run_id': 'old', 'layout_version': 1, 'current_agent': 'A8'}
            unversioned = {**old, 'run_id': 'unversioned'}
            del unversioned['layout_version']
            with closing(sqlite3.connect(db)) as conn, conn:
                for index, record in enumerate((current, old, unversioned)):
                    conn.execute('INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?)',
                                 (record['run_id'], 'INITIALIZED', record['current_agent'],
                                  '2026-01-01', f'2026-01-0{index+1}', json.dumps(record)))
            self.assertIsNone(store.get_state('old'))
            self.assertIsNone(store.get_state('unversioned'))
            self.assertEqual(store.get_state('current').layout_version, 2)
            self.assertEqual([row['run_id'] for row in store.list_runs(limit=1)], ['current'])
            with closing(sqlite3.connect(db)) as conn:
                saved = json.loads(conn.execute('SELECT state_json FROM workflow_runs WHERE run_id=?', ('old',)).fetchone()[0])
            self.assertEqual(saved, old)


if __name__ == '__main__':
    unittest.main()
