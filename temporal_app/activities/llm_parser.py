import json
import re

from temporal_app.models import ActionType, LLMResponse, NextAction

# ---------------------------------------------------------------------------
# JSON extraction / validation
# ---------------------------------------------------------------------------


def extract_json(text: str) -> dict:
    """Extract a JSON object from an LLM reply that may contain extra text."""
    text = text.strip()

    # Fast path: entire response is JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Grab the outermost {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in LLM response: {text[:300]!r}")


def _fallback_response(text: str) -> LLMResponse:
    """Best-effort recovery: show whatever text we have and ask the user to
    continue, instead of failing the whole activity/workflow."""
    plain_text = (text or "").strip()
    if not plain_text:
        raise ValueError("LLM response was empty and could not be recovered")
    return LLMResponse(
        message=plain_text,
        next_action=NextAction(type=ActionType.ASK_INPUT.value),
    )


def _build_response(data: dict) -> LLMResponse:
    if "message" not in data:
        raise ValueError("LLM JSON missing required 'message' field")
    if "next_action" not in data:
        raise ValueError("LLM JSON missing required 'next_action' field")

    action_data = data["next_action"]
    raw_type = action_data.get("type", "")
    valid_types = {a.value for a in ActionType}
    if raw_type not in valid_types:
        raise ValueError(
            f"Invalid next_action.type {raw_type!r}. Must be one of {sorted(valid_types)}"
        )

    return LLMResponse(
        message=data["message"],
        next_action=NextAction(
            type=raw_type,
            tool_name=action_data.get("tool_name") or None,
            tool_args=action_data.get("tool_args") or None,
        ),
    )


def parse_llm_response(raw: str) -> LLMResponse:
    try:
        data = extract_json(raw)
    except ValueError:
        # Model returned plain text instead of JSON — show it verbatim and
        # keep the conversation going by treating it as a normal reply.
        return _fallback_response(raw)

    try:
        return _build_response(data)
    except ValueError:
        # Model returned JSON but with a broken/unexpected schema — don't
        # fail the activity, just surface whatever message we can find so
        # the conversation keeps going instead of erroring out the workflow.
        return _fallback_response(data.get("message") or raw)
