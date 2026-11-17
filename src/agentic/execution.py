"""Run desktop integrations in a cancellable process, keeping the API responsive."""

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from agentic.config import ROOT, settings
from agentic.state import WorkflowState, fail_workflow
from agentic.store import WORKFLOW_STORE


class RunController:
    def __init__(self):
        self.run_id = None
        self.process = None
        self._cancelled = False

    async def _terminate(self):
        process = self.process
        if process is None or process.returncode is not None:
            return
        if sys.platform == "win32":
            # Kill only this worker's process tree, including the venv launcher.
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()

    async def cancel(self, run_id: str):
        if self.run_id != run_id:
            raise ValueError("This run is not active in the current server.")
        self._cancelled = True
        await self._terminate()

    async def execute(self, payload: dict, run_id: str, resume_state: WorkflowState | None = None) -> WorkflowState:
        cfg = settings()
        state = resume_state or WorkflowState(run_id=run_id, status="RUNNING", raw_input=payload)
        if state.run_id != run_id or state.status != "RUNNING":
            raise ValueError("Worker input does not match the requested active run.")
        WORKFLOW_STORE.save_state(state)
        self.run_id, self._cancelled = run_id, False
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        log_dir = ROOT / "data" / "worker_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        started = last_progress = time.monotonic()
        progress_key = None
        try:
            with (log_dir / f"{run_id}.log").open("wb") as log:
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", "-m", "agentic.worker", "--run-id", run_id,
                    *(["--resume-publication"] if resume_state else []),
                    cwd=str(ROOT), env=environment, stdout=log, stderr=log, **options,
                )
                if self._cancelled:
                    await self._terminate()
                while self.process.returncode is None:
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
                    state = WORKFLOW_STORE.get_state(run_id) or state
                    key = (state.current_agent, len(state.logs), str(state.agent_statuses.get(state.current_agent)),
                           state.deep_agent_model_call_count)
                    if key != progress_key:
                        last_progress, progress_key = time.monotonic(), key
                    now = time.monotonic()
                    # IDEA solves several load effects serially in one request.
                    # Its declared calculation budget is bounded by the run cap.
                    operation_timeout = state.agent_statuses.get(state.current_agent, {}).get("operation_timeout_seconds", cfg.agent_timeout)
                    if type(operation_timeout) not in (int, float) or operation_timeout <= 0:
                        operation_timeout = cfg.agent_timeout
                    operation_timeout = min(operation_timeout, cfg.run_timeout)
                    if now - started > cfg.run_timeout or now - last_progress > operation_timeout:
                        await self._terminate()
                        state = fail_workflow(state, state.current_agent, "Calculation timed out; worker stopped. Inspect desktop software before retrying.")
                        WORKFLOW_STORE.save_state(state)
                        return state
            state = WORKFLOW_STORE.get_state(run_id) or state
            if self._cancelled:
                state.status, state.error = "CANCELLED", "Run stopped by user."
                state.agent_statuses[state.current_agent] = {"status": "CANCELLED", "message": state.error}
            elif state.status not in ("FAILED", "CANCELLED", "PAUSED_AT_GATE3", "COMPLETED") or self.process.returncode:
                state = fail_workflow(state, state.current_agent, "Worker exited without a completed result. See the worker log.")
            WORKFLOW_STORE.save_state(state)
            return state
        except BaseException:
            await self._terminate()
            state = WORKFLOW_STORE.get_state(run_id) or state
            fail_workflow(state, state.current_agent, "Worker execution interrupted.")
            WORKFLOW_STORE.save_state(state)
            raise
        finally:
            self.process, self.run_id = None, None


CONTROLLER = RunController()
