import os
import sys
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from api.services import router as services_router
from temporal_app.workflows.chat import ChatWorkflow
from temporal_app.workflows.music import MusicWorkflow

load_dotenv()

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = "chat-task-queue"
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="Temporal Music Recommendations", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(services_router)


async def _client() -> Client:
    return await Client.connect(TEMPORAL_HOST)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StartChatResponse(BaseModel):
    workflow_id: str


class StartMusicRequest(BaseModel):
    preferences: str = Field(default="", max_length=2000)


class UserInputRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ConfirmationRequest(BaseModel):
    confirmed: bool


class ChatStateResponse(BaseModel):
    state: str
    preferences: Optional[str] = None
    pending_tool: Optional[str] = None
    pending_tool_args: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = []
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/chat/start", response_model=StartChatResponse)
async def start_chat(body: StartMusicRequest | None = None) -> StartChatResponse:
    """Start or reconnect to the music loop for the configured Slack channel."""
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    if not channel:
        raise HTTPException(status_code=400, detail="Set SLACK_CHANNEL_ID in .env first")
    client = await _client()
    workflow_id = f"music-{channel}"
    no_retries = os.getenv("NO_RETRIES", "false").lower() == "true"
    await client.start_workflow(
        MusicWorkflow.run,
        args=[channel, body.preferences if body else "", no_retries],
        id=workflow_id,
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return StartChatResponse(workflow_id=workflow_id)


@app.post("/api/chat/{workflow_id}/input")
async def send_input(workflow_id: str, body: UserInputRequest) -> Dict:
    """Signal the workflow with user input text."""
    client = await _client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.signal("send_user_input", body.message)
    except RPCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/api/chat/{workflow_id}/stop")
async def stop_music(workflow_id: str) -> Dict:
    client = await _client()
    try:
        await client.get_workflow_handle(workflow_id).signal(MusicWorkflow.stop)
    except RPCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/api/slack/events")
async def slack_events(request: Request) -> Dict:
    """Verify Slack's signature and durably queue human thread replies as feedback."""
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Set SLACK_SIGNING_SECRET")
    raw = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    try:
        fresh = abs(time.time() - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    expected = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + raw,
                                 hashlib.sha256).hexdigest()
    if not fresh or not hmac.compare_digest(expected, request.headers.get("x-slack-signature", "")):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    payload = json.loads(raw)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}
    event = payload.get("event", {})
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    if (event.get("type") == "message" and event.get("channel") == channel
            and event.get("thread_ts") and event.get("text") and event.get("user")
            and not event.get("bot_id") and not event.get("subtype")):
        client = await _client()
        await client.get_workflow_handle(f"music-{channel}").signal(MusicWorkflow.slack_feedback, {
            "event_id": payload["event_id"], "thread_ts": event["thread_ts"], "text": event["text"],
        })
    return {"ok": True}


@app.post("/api/chat/{workflow_id}/confirm")
async def send_confirmation(workflow_id: str, body: ConfirmationRequest) -> Dict:
    """Signal the workflow with the user's confirm / deny decision."""
    client = await _client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.signal(ChatWorkflow.send_confirmation, body.confirmed)
    except RPCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.get("/api/chat/{workflow_id}/state", response_model=ChatStateResponse)
async def get_state(workflow_id: str) -> ChatStateResponse:
    """Query the workflow for its current state and full history."""
    client = await _client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        description = await handle.describe()
        if description.status in {WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.TIMED_OUT,
                                  WorkflowExecutionStatus.TERMINATED, WorkflowExecutionStatus.CANCELED}:
            return ChatStateResponse(
                state="failed",
                error=f"Workflow {description.status.name.lower()}. Check the worker logs for details.",
            )
        state_dict = await handle.query("get_state")
        history = await handle.query("get_history")
    except RPCError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatStateResponse(
        state=state_dict["state"],
        preferences=state_dict.get("preferences"),
        pending_tool=state_dict.get("pending_tool"),
        pending_tool_args=state_dict.get("pending_tool_args"),
        history=history,
    )


@app.get("/api/chat/{workflow_id}/llm-log")
async def get_llm_log(workflow_id: str) -> Dict:
    """Query the workflow for the full LLM call log."""
    client = await _client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        log = await handle.query("get_llm_log")
    except RPCError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"entries": log}


# ---------------------------------------------------------------------------
# Serve the frontend from /
# ---------------------------------------------------------------------------

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    if "--no-retries" in sys.argv[1:]:
        os.environ["NO_RETRIES"] = "true"

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=True,
    )


if __name__ == "__main__":
    main()
