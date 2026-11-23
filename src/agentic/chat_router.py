"""Nonblocking chat API. Polling exposes actual tools and worker progress."""
import asyncio
import os
import sqlite3
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from agentic.chat import ChatRuntime, project_input, redact, summary
from agentic.chat_store import ChatStore, now
from agentic.store import WORKFLOW_STORE
from agentic.chat_attachments import AttachmentStore, MAX_FILE
from agentic.chat_guardrails import quick_scope, SCOPE_MESSAGE, CLARIFY_MESSAGE
from agentic.chat_input_lifetime import reset_finished_inputs

router = APIRouter(prefix="/api/v2/chat", tags=["Structural Chat"])
CHAT_STORE = ChatStore()
RUNTIME = ChatRuntime(CHAT_STORE)
TASKS = {}
ATTACHMENTS = AttachmentStore()


def sync_approval(turn):
    """Only actual, persisted manual workflow acceptance can complete this request."""
    if turn['status'] != 'AWAITING_APPROVAL' or not turn.get('run_id'):
        return turn
    state = WORKFLOW_STORE.get_state(turn['run_id'])
    if (state and state.status == 'COMPLETED' and state.gate3_approved
            and state.agent_statuses.get('A8', {}).get('status') == 'PASSED'):
        completed = CHAT_STORE.complete_approval(turn['id'], state.run_id, redact(state.accepted_by), state.acceptance_timestamp)
        session = CHAT_STORE.session(turn['session_id'])
        reset_finished_inputs(session, CHAT_STORE, WORKFLOW_STORE, state)
        return completed
    return turn


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=80)
    project_input: dict | None = None
    run_id: str | None = Field(default=None, pattern=r"^run_[a-f0-9]{8,32}$")
    allow_native_tools: bool = False
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)


@router.post('/sessions/{session_id}/attachments', status_code=201)
async def upload_attachment(session_id: str, request: Request, filename: str = Query(min_length=1, max_length=240)):
    try:
        CHAT_STORE.session(session_id)
        CHAT_STORE.admit_request(session_id)
        turns = CHAT_STORE.history(session_id, limit=None)
        used = {a['id'] for t in turns for a in t.get('attachments', [])}
        if len(ATTACHMENTS.drafts(session_id, used)) >= 8:
            raise ValueError('At most eight draft attachments per chat. Remove a file or send the message first.')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_FILE:
                raise HTTPException(413, 'Each attachment must be at most 20 MB.')
        value = await asyncio.to_thread(ATTACHMENTS.save, session_id, filename, bytes(data))
        return {**ATTACHMENTS.public(value), 'project_input': value.get('project_input')}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete('/sessions/{session_id}/attachments/{attachment_id}')
async def remove_attachment(session_id: str, attachment_id: str):
    try:
        CHAT_STORE.session(session_id)
        if any(a['id'] == attachment_id for t in CHAT_STORE.history(session_id, limit=None) for a in t.get('attachments', [])):
            raise HTTPException(409, 'This file is part of a sent message and remains in its conversation history.')
        ATTACHMENTS.delete(attachment_id, session_id)
        return {'removed': attachment_id}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)


@router.get("/sessions")
async def list_sessions():
    return {"sessions": CHAT_STORE.list_sessions()}


@router.post("/sessions", status_code=201)
async def create_session():
    try:
        CHAT_STORE.admit_request("_new_sessions")
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
    return CHAT_STORE.session()


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, request: RenameRequest):
    if not request.title.strip():
        raise HTTPException(422, "Enter a chat title.")
    try:
        return CHAT_STORE.rename_session(session_id, redact(request.title.strip()))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/config")
async def chat_config():
    from agentic.config import settings
    enabled = os.getenv("PLANT_CHAT_ENABLED", "true").lower() == "true"
    ready = enabled and bool(os.getenv("OPENAI_API_KEY")) and bool(settings().model)
    return {"enabled": ready, "model": settings().model,
            "reason": "" if ready else "Configure the backend model and API key, and enable PLANT_CHAT_ENABLED. Never enter credentials in chat.",
            "live_tools_default": False}


@router.post("/messages", status_code=202)
async def send_message(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(422, "Enter a message.")
    if not (await chat_config())["enabled"]:
        raise HTTPException(503, "Chat model is not configured or chat is disabled. Existing engineering endpoints remain available.")
    try:
        inputs = project_input(request.project_input) if request.project_input is not None else None
        session = CHAT_STORE.session(request.session_id)
        CHAT_STORE.admit_request(session["id"])
        for pending in CHAT_STORE.history(session['id'], limit=None):
            sync_approval(pending)
        files = [ATTACHMENTS.load(ident, session['id']) for ident in dict.fromkeys(request.attachment_ids)]
        if sum(f['size'] for f in files) > 40 * 1024 * 1024:
            raise ValueError('Combined attachments must be at most 40 MB per message.')
        if request.run_id and WORKFLOW_STORE.get_state(request.run_id) is None:
            raise ValueError("Selected engineering run was not found.")
        turn = CHAT_STORE.new_turn(session["id"], redact(request.message.strip()))
        turn['attachments'] = [ATTACHMENTS.public(f) for f in files]
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This conversation has an active request or is waiting for manual engineer approval.") from exc
    except ValueError as exc:
        raise HTTPException(429 if "limit reached" in str(exc) else 422, redact(str(exc))) from exc
    if request.run_id:
        state = WORKFLOW_STORE.get_state(request.run_id)
        if inputs is not None and (
                {k: v for k, v in state.raw_input.items() if k != "run_policy"} !=
                {k: v for k, v in inputs.items() if k != "run_policy"} or
                ("run_policy" in inputs and inputs["run_policy"] != state.run_policy)):
            # The UI may have different defaults; it must explicitly choose matching inputs to resume.
            turn.update(status="ERROR", error="Selected run inputs differ from the attached form/JSON. Use its original inputs or start a new run.")
            CHAT_STORE.save_turn(turn)
            raise HTTPException(409, turn["error"])
    if session.get("title", "New chat") == "New chat":
        session["title"] = redact(request.message.strip())[:80]
    CHAT_STORE.save_session(session)
    turn["request_project_input"] = inputs
    turn["request_run_id"] = request.run_id
    quick = quick_scope(turn["message"], bool(session.get("run_id")))
    if quick is not None and quick.status != "ALLOW":
        turn.update(status="COMPLETED", scope=quick.model_dump(),
                    outcome="BLOCKED_OUT_OF_SCOPE" if quick.status == "BLOCK" else "NEEDS_CLARIFICATION",
                    assistant=quick.reason or (SCOPE_MESSAGE if quick.status == "BLOCK" else CLARIFY_MESSAGE))
        CHAT_STORE.save_turn(turn)
        return {"session_id": session["id"], "turn_id": turn["id"], "status": "COMPLETED"}
    CHAT_STORE.save_turn(turn)
    task = asyncio.create_task(RUNTIME.respond(turn, request.allow_native_tools))
    TASKS[turn["id"]] = task
    task.add_done_callback(lambda _: TASKS.pop(turn["id"], None))
    return {"session_id": session["id"], "turn_id": turn["id"], "status": "RUNNING"}


@router.get("/sessions/{session_id}")
async def session_history(session_id: str):
    try:
        session = CHAT_STORE.session(session_id)
        turns = [sync_approval(turn) for turn in CHAT_STORE.history(session_id, limit=None)]
        session = CHAT_STORE.session(session_id)
        reset_finished_inputs(session, CHAT_STORE, WORKFLOW_STORE)
        used = {a['id'] for t in turns for a in t.get('attachments', [])}
        return {**session, "turns": turns, "pending_changes": CHAT_STORE.pending_plans(session_id), 'draft_attachments': ATTACHMENTS.drafts(session_id, used)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/turns/{turn_id}")
async def turn_status(turn_id: str):
    try:
        turn = sync_approval(CHAT_STORE.turn(turn_id))
        session = CHAT_STORE.session(turn["session_id"])
        reset_finished_inputs(session, CHAT_STORE, WORKFLOW_STORE)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    # A running conversation can fork a new candidate for each engineering action.
    # The turn records the last completed tool; the session tracks the current one.
    # Keep finished turns tied to their own historical result.
    run_id = session.get("run_id") if turn["status"] == "RUNNING" else turn.get("run_id")
    return {**turn, "run_id": run_id, "project_input": session["project_input"],
            "progress": summary(WORKFLOW_STORE.get_state(run_id)) if run_id else None}


class ChangeApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    allow_native_tools: bool = False


@router.get("/sessions/{session_id}/changes/{proposal_id}")
async def change_proposal(session_id: str, proposal_id: str):
    try:
        return CHAT_STORE.plan(proposal_id, session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/sessions/{session_id}/changes/{proposal_id}/reject")
async def reject_change(session_id: str, proposal_id: str):
    try:
        CHAT_STORE.admit_request(session_id)
        return CHAT_STORE.transition_plan(proposal_id, session_id, "PENDING", "REJECTED")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sessions/{session_id}/changes/{proposal_id}/approve", status_code=202)
async def approve_change(session_id: str, proposal_id: str, approval: ChangeApproval):
    from agentic.chat_changes import validate_proposal
    from agentic.execution import CONTROLLER
    from agentic.router import _RUN_LOCK
    turn = None
    try:
        CHAT_STORE.admit_request(session_id)
        session = CHAT_STORE.session(session_id)
        plan = CHAT_STORE.plan(proposal_id, session_id)
        if plan["status"] != "PENDING" or plan["digest"] != approval.digest:
            raise ValueError("Proposal is stale, already handled, or approval does not match its preview.")
        if _RUN_LOCK.locked() or CONTROLLER.run_id:
            raise ValueError("Another engineering run owns the desktop tools. No change was applied.")
        if plan["action"] not in ("configuration_only", "preview_structure") and not approval.allow_native_tools:
            raise ValueError("Live engineering must be explicitly enabled for this approval.")
        from agentic.config import settings
        if plan['action'] not in ('configuration_only','preview_structure') and not settings().live_enabled:
            raise ValueError('Live engineering is disabled in backend configuration.')
        source = WORKFLOW_STORE.get_state(plan.get("source_run_id") or "") if plan["kind"] == "joint_parameters" else None
        validate_proposal(plan, session, source)
        turn = CHAT_STORE.new_turn(session_id, "Apply approved project change " + proposal_id)
        turn["proposal_id"] = proposal_id
        plan = CHAT_STORE.transition_plan(proposal_id, session_id, "PENDING", "EXECUTING", approved_at=now(), approval_turn_id=turn["id"])
        CHAT_STORE.save_turn(turn)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        if turn is not None:
            turn.update(status="ERROR", error=redact(str(exc)))
            CHAT_STORE.save_turn(turn)
        raise HTTPException(409, redact(str(exc))) from exc
    task = asyncio.create_task(RUNTIME.execute_approved(turn, plan, approval.allow_native_tools))
    TASKS[turn["id"]] = task
    task.add_done_callback(lambda _: TASKS.pop(turn["id"], None))
    return {"session_id": session_id, "turn_id": turn["id"], "status": "RUNNING"}


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: str):
    task = TASKS.get(turn_id)
    if task is None:
        raise HTTPException(409, "No active chat task. Check the saved run before retrying.")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # A task cancelled before its coroutine starts cannot persist its own outcome.
        turn = CHAT_STORE.turn(turn_id)
        turn.update(status="CANCELLED", error="Turn cancelled before execution.")
        CHAT_STORE.save_turn(turn)
    return {"status": "CANCELLED", "turn_id": turn_id}
