import os
import sys
import uuid
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from api.services import router as services_router
from temporal_app.workflows.chat import ChatWorkflow

load_dotenv()

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = "chat-task-queue"
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="Temporal Chat API", version="0.1.0")

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


class UserInputRequest(BaseModel):
    message: str


class ConfirmationRequest(BaseModel):
    confirmed: bool


class ChatStateResponse(BaseModel):
    state: str
    pending_tool: Optional[str] = None
    pending_tool_args: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = []
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/chat/start", response_model=StartChatResponse)
async def start_chat() -> StartChatResponse:
    """Start a new ChatWorkflow and return its ID."""
    client = await _client()
    workflow_id = f"chat-{uuid.uuid4()}"
    require_confirmation = os.getenv("CONFIRMATION", "true").lower() != "false"
    no_retries = os.getenv("NO_RETRIES", "false").lower() == "true"
    await client.start_workflow(
        ChatWorkflow.run,
        args=["[CHAT_START]", require_confirmation, no_retries],
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return StartChatResponse(workflow_id=workflow_id)


@app.post("/api/chat/{workflow_id}/input")
async def send_input(workflow_id: str, body: UserInputRequest) -> Dict:
    """Signal the workflow with user input text."""
    client = await _client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.signal(ChatWorkflow.send_user_input, body.message)
    except RPCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


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
        if description.status == WorkflowExecutionStatus.FAILED:
            return ChatStateResponse(
                state="failed",
                error="The chat workflow failed. Check the worker logs for details.",
            )
        state_dict = await handle.query(ChatWorkflow.get_state)
        history = await handle.query(ChatWorkflow.get_history)
    except RPCError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatStateResponse(
        state=state_dict["state"],
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
        log = await handle.query(ChatWorkflow.get_llm_log)
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
