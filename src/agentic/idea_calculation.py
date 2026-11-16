"""Bounded native IDEA jobs, with explicit legacy-service fallback."""
import os
import time
from agentic.config import settings


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
