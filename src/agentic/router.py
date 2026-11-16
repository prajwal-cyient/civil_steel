"""
router.py

FastAPI router exposing endpoints for the Agentic Workflow.
Includes:
- POST /api/v2/agentic/run       -> Executes the 8-agent LangGraph pipeline with loops
- GET  /api/v2/agentic/runs/{id} -> Fetches live run state and agent logs
- POST /api/v2/agentic/accept/{id} -> Human Gate 3 sign-off
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException
from agentic.execution import CONTROLLER
from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
from agentic.state import WorkflowState
from agentic.store import WORKFLOW_STORE

router = APIRouter(prefix="/api/v2/agentic", tags=["Agentic Workflow"])

# Fast in-memory cache synchronized with durable SQLite store
RUN_STORE: Dict[str, WorkflowState] = {}
_RUN_LOCK = asyncio.Lock()
ACTIVE_RUN_ID = None


@router.post("/run")
async def start_agentic_run(payload: Dict[str, Any]):
    """
    Kicks off the autonomous 8-agent design workflow.
    Executes Loop A, Loop B, and Loop C automatically, populating all software results.
    State is persisted to durable SQLite.
    """
    global ACTIVE_RUN_ID
    if _RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="An engineering workflow is already using the desktop tools.")
    try:
        async with _RUN_LOCK:
            ACTIVE_RUN_ID = f"run_{uuid.uuid4().hex[:8]}"
            final_state = await CONTROLLER.execute(payload, run_id=ACTIVE_RUN_ID)
        RUN_STORE[final_state.run_id] = final_state
        WORKFLOW_STORE.save_state(final_state)
        return json.loads(final_state.model_dump_json())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agentic Workflow Execution Failed: {exc}")
    finally:
        ACTIVE_RUN_ID = None


@router.get("/active")
async def get_active_run():
    """Expose saved progress while the long-running POST is still in flight."""
    state = WORKFLOW_STORE.get_state(ACTIVE_RUN_ID) if ACTIVE_RUN_ID else None
    return json.loads(state.model_dump_json()) if state else None


@router.post("/retry-publication/{source_run_id}")
async def retry_publication(source_run_id: str):
    """Reuse unchanged calculations, re-audit them, and retry a failed A7 handoff."""
    global ACTIVE_RUN_ID
    if _RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="An engineering workflow is already using the desktop tools.")
    source = WORKFLOW_STORE.get_state(source_run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source run not found.")
    from agentic.publication_recovery import prepare_publication_retry
    try:
        state = prepare_publication_retry(source, f"run_{uuid.uuid4().hex[:8]}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        async with _RUN_LOCK:
            ACTIVE_RUN_ID = state.run_id
            result = await CONTROLLER.execute(state.raw_input, state.run_id, resume_state=state)
        RUN_STORE[result.run_id] = result
        return json.loads(result.model_dump_json())
    finally:
        ACTIVE_RUN_ID = None


@router.post("/cancel/{run_id}")
async def cancel_run(run_id: str):
    try:
        await CONTROLLER.cancel(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "status": "CANCELLING"}


@router.get("/runs")
async def list_recent_runs(limit: int = 50):
    """
    Returns historical runs stored in the durable SQLite database.
    """
    return WORKFLOW_STORE.list_runs(limit=limit)


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str):
    """
    Returns the real-time state, logs, and software outputs of a run from SQLite.
    """
    state = WORKFLOW_STORE.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    RUN_STORE[run_id] = state
    return json.loads(state.model_dump_json())


@router.post("/accept/{run_id}")
async def accept_run(run_id: str, payload: Dict[str, Any] = None):
    """
    Human Gate 3: Structural engineer reviews calculations and submits final sign-off.
    """
    state = WORKFLOW_STORE.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    engineer_name = (payload or {}).get("engineer_name", "Lead Structural Engineer")
    try:
        updated_state = accept_gate3(state, engineer_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    RUN_STORE[run_id] = updated_state
    WORKFLOW_STORE.save_state(updated_state)
    return json.loads(updated_state.model_dump_json())

