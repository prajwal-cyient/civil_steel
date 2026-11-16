"""Controlled job lifecycle tests; native results are tested separately."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agentic.idea_calculation import calculate_connection, calculation_budget
from agentic.idea_runtime import _active_solver_ids


class Jobs:
    def start_calculation(self,*args,**kwargs):pass


class IdeaCalculationTests(unittest.TestCase):
    def test_busy_guard_identifies_native_idea_solver_without_stopping_it(self):
        service=Mock()
        service.ExecQuery.side_effect=[[SimpleNamespace(ProcessId=7,ParentProcessId=8)],
                                      [SimpleNamespace(Name='IdeaStatiCa.ConnectionRestApi.exe')]]
        self.assertEqual(_active_solver_ids(service),[7])
        self.assertEqual(service.method_calls[0].args[0],"SELECT ProcessId,ParentProcessId FROM Win32_Process WHERE Name='k2fem64.exe'")

    def test_busy_guard_does_not_claim_an_unrelated_solver_parent(self):
        service=Mock()
        service.ExecQuery.side_effect=[[SimpleNamespace(ProcessId=7,ParentProcessId=8)],
                                      [SimpleNamespace(Name='other-program.exe')]]
        self.assertEqual(_active_solver_ids(service),[])
    def client(self,status='finished',results=None):
        jobs=Jobs()
        job=SimpleNamespace(job_id='owned-job',status=status,message='native progress',error=None,results=results)
        jobs.start_calculation=Mock(return_value=job)
        jobs.get_calculation_job=Mock(return_value=job)
        jobs.cancel_calculation_job=Mock()
        return SimpleNamespace(calculation_jobs=jobs,calculation=Mock())

    def test_finished_job_returns_actual_result_without_second_calculation(self):
        rows=[object()]
        client=self.client(results=rows)
        self.assertIs(calculate_connection(client,'project',1,10,Mock()),rows)
        client.calculation.calculate.assert_not_called()
        client.calculation_jobs.cancel_calculation_job.assert_not_called()

    def test_failed_job_does_not_fallback_or_report_success(self):
        client=self.client(status='failed')
        with self.assertRaisesRegex(RuntimeError,'job failed'):
            calculate_connection(client,'project',1,10,Mock())
        client.calculation.calculate.assert_not_called()

    def test_empty_finished_job_is_not_a_pass(self):
        client=self.client(results=[])
        with self.assertRaisesRegex(RuntimeError,'without valid results'):
            calculate_connection(client,'project',1,10,Mock())

    def test_wrong_job_results_are_rejected_and_owned_job_cancelled(self):
        client=self.client(results=[object()])
        client.calculation_jobs.get_calculation_job.return_value=SimpleNamespace(job_id='other-job')
        with self.assertRaisesRegex(RuntimeError,'different calculation job'):
            calculate_connection(client,'project',1,10,Mock())
        client.calculation_jobs.cancel_calculation_job.assert_called_once_with('project','owned-job',_request_timeout=(10.0,20.0))

    def test_timeout_requests_native_cancellation_of_only_its_job(self):
        client=self.client(status='running')
        with patch('agentic.idea_calculation.time.monotonic',side_effect=[0,11]):
            with self.assertRaises(TimeoutError):calculate_connection(client,'project',1,10,Mock())
        client.calculation_jobs.cancel_calculation_job.assert_called_once_with('project','owned-job',_request_timeout=(10.0,20.0))

    def test_missing_route_uses_bounded_legacy_solve(self):
        client=self.client()
        error=RuntimeError('missing route'); error.status=404
        client.calculation_jobs.start_calculation.side_effect=error
        calculate_connection(client,'project',1,600,Mock())
        client.calculation.calculate.assert_called_once_with('project',[1],_request_timeout=(10.0,600))

    def test_server_error_does_not_start_an_untracked_second_solve(self):
        client=self.client()
        error=RuntimeError('server failed'); error.status=500
        client.calculation_jobs.start_calculation.side_effect=error
        with self.assertRaises(RuntimeError):calculate_connection(client,'project',1,600,Mock())
        client.calculation.calculate.assert_not_called()

    def test_budget_is_bounded_by_total_run_cap(self):
        with patch('agentic.idea_calculation.settings',return_value=SimpleNamespace(run_timeout=3600,agent_timeout=180)), \
             patch.dict('os.environ',{'PLANT_IDEA_CALCULATION_TIMEOUT_SECONDS':'600'}):
            self.assertEqual(calculation_budget(1),600)
            self.assertEqual(calculation_budget(13),2340)
            self.assertEqual(calculation_budget(100),3600)


if __name__=='__main__':unittest.main()
