"""IDEA connection design errors shared by the adapter and request builder."""


class IdeaStaticaJointError(RuntimeError):
    """Raised when a joint cannot be resolved, evaluated, or updated."""

class IdeaStaticaConnectionError(RuntimeError):
    """Raised when the Connection API service can't be reached."""


"""Read-only guard against overlapping legacy native IDEA calculations."""
import sys
import time


def _active_solver_ids(service):
    active = []
    for solver in service.ExecQuery("SELECT ProcessId,ParentProcessId FROM Win32_Process WHERE Name='k2fem64.exe'"):
        parent_id = int(solver.ParentProcessId)
        parents = service.ExecQuery(f"SELECT Name FROM Win32_Process WHERE ProcessId={parent_id}")
        if any(str(parent.Name).lower() in ("ideastatica.connectionrestapi.exe", "ideaconnection.exe") for parent in parents):
            active.append(int(solver.ProcessId))
    return active


def ensure_idea_solver_idle(timeout_seconds=10.0):
    if sys.platform != "win32":
        return
    import pythoncom
    import win32com.client
    initialized = False
    service = None
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pythoncom.com_error as exc:
            if exc.hresult != -2147417850:  # Existing MTA can also query WMI.
                raise
        service = win32com.client.GetObject("winmgmts:")
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            active = _active_solver_ids(service)
            if not active or time.monotonic() >= deadline:
                break
            # Allow an already completed request's native process to exit.
            time.sleep(.2)
    finally:
        service = None  # Release WMI proxies before balancing COM initialization.
        if initialized:
            pythoncom.CoUninitialize()
    if active:
        raise RuntimeError(f"IDEA native solver is still active (processes {active}); no new project or calculation was opened. "
                           "A legacy HTTP timeout does not cancel its native analysis.")

"""Bounded native IDEA jobs, with explicit legacy-service fallback."""
import os
import time
from config.settings import settings


def calculation_budget(effect_count):
    cfg = settings()
    minimum = int(os.getenv("PLANT_IDEA_CALCULATION_TIMEOUT_SECONDS", "600"))
    if minimum <= 0:
        raise ValueError("PLANT_IDEA_CALCULATION_TIMEOUT_SECONDS must be positive.")
    return float(min(cfg.run_timeout, max(minimum, cfg.agent_timeout * max(1, effect_count))))


def calculate_connection(client, project_id, connection_id, budget, progress):
    """A failed/timed-out job never supplies a passing substitute result."""
    jobs = getattr(client, "calculation_jobs", None)
    # Older service releases can expose an SDK class without the HTTP routes.
    if jobs is None or not callable(getattr(type(jobs), "start_calculation", None)):
        progress("IDEA legacy calculation API: bounded synchronous solve")
        return client.calculation.calculate(project_id, [connection_id], _request_timeout=(10.0, budget))
    try:
        job = jobs.start_calculation(project_id, [connection_id], _request_timeout=(10.0, 20.0))
    except Exception as exc:
        if getattr(exc, "status", None) not in (404, 405, 501):
            raise
        progress("IDEA service lacks calculation-job routes: bounded synchronous solve")
        return client.calculation.calculate(project_id, [connection_id], _request_timeout=(10.0, budget))
    if not job.job_id:
        raise RuntimeError("IDEA did not return an asynchronous calculation job ID.")
    job_id = job.job_id
    started = time.monotonic()
    finished = False
    try:
        while time.monotonic() - started < budget:
            job = jobs.get_calculation_job(project_id, job_id, _request_timeout=(10.0, 20.0))
            if job.job_id != job_id:
                raise RuntimeError("IDEA returned a different calculation job; results rejected.")
            status = getattr(job.status, "value", job.status)
            progress(f"IDEA job {job.job_id}: {status}; {job.message or 'calculating'}")
            if status == "finished":
                finished = True
                if job.error or not job.results:
                    raise RuntimeError(f"IDEA job finished without valid results: {job.error}")
                return job.results
            if status in ("failed", "cancelled"):
                finished = True
                raise RuntimeError(f"IDEA calculation job {status}: {job.error or job.message}")
            if status not in ("queued", "running"):
                raise RuntimeError(f"Unknown IDEA calculation job status: {status}")
            time.sleep(min(2.0, max(0.0, budget - (time.monotonic()-started))))
        raise TimeoutError(f"IDEA calculation exceeded its {budget:g}-second budget; cancellation requested.")
    finally:
        if not finished:
            # Do not silently leave a job running after a client-side timeout.
            jobs.cancel_calculation_job(project_id, job_id, _request_timeout=(10.0, 20.0))
