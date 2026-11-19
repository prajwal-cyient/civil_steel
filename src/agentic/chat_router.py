"""Nonblocking chat API. Polling exposes actual tools and worker progress."""
import asyncio
import os
import sqlite3
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from agentic.chat import ChatRuntime, project_input, redact, summary
from agentic.chat_store import ChatStore
from agentic.store import WORKFLOW_STORE
from agentic.chat_attachments import AttachmentStore, MAX_FILE

router = APIRouter(prefix="/api/v2/chat", tags=["Structural Chat"])
CHAT_STORE = ChatStore()
RUNTIME = ChatRuntime(CHAT_STORE)
TASKS = {}
ATTACHMENTS = AttachmentStore()


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
        raise HTTPException(409, "This conversation already has an active turn.") from exc
    except ValueError as exc:
        raise HTTPException(422, redact(str(exc))) from exc
    if inputs is not None and inputs != session["project_input"]:
        session["project_input"] = inputs
        session["run_id"] = None  # Changed inputs cannot silently reuse old solver evidence.
    if request.run_id:
        state = WORKFLOW_STORE.get_state(request.run_id)
        if inputs is not None and state.raw_input != inputs:
            # The UI may have different defaults; it must explicitly choose matching inputs to resume.
            turn.update(status="ERROR", error="Selected run inputs differ from the attached form/JSON. Use its original inputs or start a new run.")
            CHAT_STORE.save_turn(turn)
            raise HTTPException(409, turn["error"])
        session["run_id"] = request.run_id
    if session.get("title", "New chat") == "New chat":
        session["title"] = redact(request.message.strip())[:80]
    CHAT_STORE.save_session(session)
    CHAT_STORE.save_turn(turn)
    task = asyncio.create_task(RUNTIME.respond(turn, request.allow_native_tools))
    TASKS[turn["id"]] = task
    task.add_done_callback(lambda _: TASKS.pop(turn["id"], None))
    return {"session_id": session["id"], "turn_id": turn["id"], "status": "RUNNING"}


@router.get("/sessions/{session_id}")
async def session_history(session_id: str):
    try:
        session = CHAT_STORE.session(session_id)
        turns = CHAT_STORE.history(session_id, limit=None)
        used = {a['id'] for t in turns for a in t.get('attachments', [])}
        return {**session, "turns": turns, 'draft_attachments': ATTACHMENTS.drafts(session_id, used)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/turns/{turn_id}")
async def turn_status(turn_id: str):
    try:
        turn = CHAT_STORE.turn(turn_id)
        session = CHAT_STORE.session(turn["session_id"])
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    run_id = turn.get("run_id") or (session.get("run_id") if turn["status"] == "RUNNING" else None)
    return {**turn, "run_id": run_id, "project_input": session["project_input"],
            "progress": summary(WORKFLOW_STORE.get_state(run_id)) if run_id else None}


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
