from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities.llm_activities import execute_tool, propose_next_action
    from temporal_app.models import ActionType


@workflow.defn
class ChatWorkflow:
    def __init__(self) -> None:
        self._history: List[Dict] = []
        self._llm_log: List[Dict] = []
        self._state: str = "starting"
        self._pending_input: Optional[str] = None
        self._pending_confirmation: Optional[bool] = None
        self._pending_tool_name: Optional[str] = None
        self._pending_tool_args: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    @workflow.signal
    async def send_user_input(self, message: str) -> None:
        self._pending_input = message

    @workflow.signal
    async def send_confirmation(self, confirmed: bool) -> None:
        self._pending_confirmation = confirmed

    # ------------------------------------------------------------------
    # Query handlers
    # ------------------------------------------------------------------

    @workflow.query
    def get_history(self) -> List[Dict]:
        return self._history

    @workflow.query
    def get_llm_log(self) -> List[Dict]:
        return self._llm_log

    @workflow.query
    def get_state(self) -> Dict:
        return {
            "state": self._state,
            "pending_tool": self._pending_tool_name,
            "pending_tool_args": self._pending_tool_args,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append(self, role: str, content: str) -> None:
        self._history.append({"role": role, "content": content})

    async def _propose_next_action(self, retry: RetryPolicy) -> Dict:
        """Ask the LLM what to do next, log the exchange, and return the action dict."""
        response_dict = await workflow.execute_activity(
            propose_next_action,
            args=[self._history],
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=retry,
        )

        # Store LLM I/O for the debug log before consuming the response
        self._llm_log.append({
            "call": len(self._llm_log) + 1,
            "input": response_dict.pop("_llm_input", []),
            "raw_output": response_dict.pop("_llm_raw_output", ""),
            "action_type": response_dict.get("next_action", {}).get("type", ""),
        })

        self._append("assistant", response_dict["message"])
        return response_dict["next_action"]

    async def _handle_ask_input(self) -> None:
        """Wait for the user to type something and append it to the history."""
        self._state = "waiting_input"
        await workflow.wait_condition(lambda: self._pending_input is not None)
        user_text = self._pending_input
        self._pending_input = None
        self._append("user", user_text)
        self._state = "running"

    async def _handle_ask_confirmation(self, action: Dict, retry: RetryPolicy) -> None:
        """Wait for explicit user approval, then run the tool (or record the decline)."""
        tool_name = action.get("tool_name") or ""
        tool_args = action.get("tool_args") or {}
        self._pending_tool_name = tool_name
        self._pending_tool_args = tool_args
        self._state = "waiting_confirmation"

        await workflow.wait_condition(lambda: self._pending_confirmation is not None)
        confirmed = self._pending_confirmation
        self._pending_confirmation = None

        if confirmed:
            result = await workflow.execute_activity(
                execute_tool,
                args=[tool_name, tool_args],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry,
            )
            # role "tool" — kept for LLM context but hidden from the UI
            self._append("tool", f"Tool '{tool_name}' returned: {result}")
        else:
            self._append("tool", f"User declined to run tool '{tool_name}'.")

        self._pending_tool_name = None
        self._pending_tool_args = None
        self._state = "running"

    async def _handle_execute_tool(self, action: Dict, retry: RetryPolicy) -> None:
        """Run the tool immediately, regardless of confirmation configuration."""
        tool_name = action.get("tool_name") or ""
        tool_args = action.get("tool_args") or {}
        result = await workflow.execute_activity(
            execute_tool,
            args=[tool_name, tool_args],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry,
        )
        self._append("tool", f"Tool '{tool_name}' returned: {result}")
        self._state = "running"

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    @workflow.run
    async def run(
        self,
        kickoff_message: str = "[CHAT_START]",
        require_confirmation: bool = True,
        no_retries: bool = False,
    ) -> str:
        retry = RetryPolicy(maximum_attempts=1) if no_retries else None

        # Seed the conversation so the LLM always has something to respond to
        self._append("user", kickoff_message)
        self._state = "running"

        while True:
            action = await self._propose_next_action(retry)
            action_type: str = action["type"]

            if action_type == ActionType.ASK_INPUT.value:
                await self._handle_ask_input()

            elif action_type == ActionType.ASK_CONFIRMATION.value:
                await self._handle_ask_confirmation(action, retry)

            elif action_type == ActionType.EXECUTE_TOOL.value:
                await self._handle_execute_tool(action, retry)

            elif action_type == ActionType.FINAL_MESSAGE.value:
                self._state = "ended"
                break

            else:
                # Safety: unknown action type — treat as ask_input
                workflow.logger.warning("Unknown action_type %r, falling back to ask_input", action_type)
                await self._handle_ask_input()

        return "Chat session ended"
