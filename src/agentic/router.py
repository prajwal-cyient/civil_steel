"""
router.py

FastAPI router exposing endpoints for the Agentic Workflow.
Includes:
- POST /api/v2/agentic/run       -> Executes the 8-agent LangGraph pipeline with loops
- GET  /api/v2/agentic/runs/{id} -> Fetches live run state and agent logs
- POST /api/v2/agentic/accept/{id} -> Human Gate 3 sign-off
"""

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException
from agentic.orchestrator import run_agentic_workflow
from agentic.agents.a8_reporting_acceptance_agent import accept_gate3
from agentic.state import WorkflowState
from agentic.store import WORKFLOW_STORE

router = APIRouter(prefix="/api/v2/agentic", tags=["Agentic Workflow"])

# Fast in-memory cache synchronized with durable SQLite store
RUN_STORE: Dict[str, WorkflowState] = {}


@router.post("/run")
async def start_agentic_run(payload: Dict[str, Any]):
    """
    Kicks off the autonomous 8-agent design workflow.
    Executes Loop A, Loop B, and Loop C automatically, populating all software results.
    State is persisted to durable SQLite.
    """
    try:
        final_state = await run_agentic_workflow(payload)
        RUN_STORE[final_state.run_id] = final_state
        WORKFLOW_STORE.save_state(final_state)
        return final_state.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agentic Workflow Execution Failed: {exc}")


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
    state = RUN_STORE.get(run_id) or WORKFLOW_STORE.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    RUN_STORE[run_id] = state
    return state.model_dump()


@router.post("/accept/{run_id}")
async def accept_run(run_id: str, payload: Dict[str, Any] = None):
    """
    Human Gate 3: Structural engineer reviews calculations and submits final sign-off.
    """
    state = RUN_STORE.get(run_id) or WORKFLOW_STORE.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    engineer_name = (payload or {}).get("engineer_name", "Lead Structural Engineer")
    updated_state = accept_gate3(state, engineer_name)
    RUN_STORE[run_id] = updated_state
    WORKFLOW_STORE.save_state(updated_state)
    return updated_state.model_dump()

