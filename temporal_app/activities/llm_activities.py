import json
import os
from dataclasses import asdict
from typing import Any, Dict, List

from temporalio import activity
from temporalio.exceptions import ApplicationError

from llm import Message, create_llm
from temporal_app.activities.llm_parser import parse_llm_response
from temporal_app.activities.system_prompt import build_system_prompt


@activity.defn
async def propose_next_action(history: List[Dict]) -> Dict:
    """Ask the LLM what to do next and return a validated LLMResponse dict."""
    llm = create_llm()

    system_content = build_system_prompt()
    messages = [Message(role="system", content=system_content)]

    for entry in history:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        # "tool" is not a universal role; map it to "user" with a prefix
        if role == "tool":
            role = "user"
            content = f"[Tool result] {content}"
        if role == "system":
            continue  # skip stored system entries; we inject our own above
        messages.append(Message(role=role, content=content))

    raw = await llm.chat(messages)

    if os.getenv("LOG_LLM_CALLS", "false").lower() == "true":
        activity.logger.info("LLM INPUT:\n%s", json.dumps([{"role": m.role, "content": m.content} for m in messages], indent=2))
        activity.logger.info("LLM OUTPUT:\n%s", raw)

    try:
        response = parse_llm_response(raw)
    except (ValueError, KeyError) as exc:
        raise ApplicationError(
            f"LLM returned invalid JSON: {exc}",
            type="InvalidLLMResponse",
            non_retryable=False,  # allow retry — the model may do better next time
        ) from exc

    result = asdict(response)
    # Always include I/O so the frontend LLM Log tab can display them
    result["_llm_input"] = [{"role": m.role, "content": m.content} for m in messages]
    result["_llm_raw_output"] = raw
    return result


@activity.defn
async def execute_tool(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """Run a registered tool and return its string result."""
    from config.tools import TOOLS

    tool = TOOLS.get(tool_name)
    if tool is None:
        raise ApplicationError(
            f"Unknown tool: {tool_name!r}",
            type="UnknownTool",
            non_retryable=True,
        )
    if tool.handler is None:
        raise ApplicationError(
            f"Tool {tool_name!r} has no handler configured",
            type="NoToolHandler",
            non_retryable=True,
        )

    try:
        import inspect
        result = tool.handler(**(tool_args or {}))
        if inspect.isawaitable(result):
            result = await result
        return str(result)
    except ApplicationError:
        raise
    except Exception as exc:
        raise ApplicationError(
            f"Tool {tool_name!r} raised an error: {exc}",
            type="ToolExecutionError",
            non_retryable=False,
        ) from exc
