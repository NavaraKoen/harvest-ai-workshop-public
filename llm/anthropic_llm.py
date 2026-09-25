from typing import List, Optional

from anthropic import AsyncAnthropic

from .base import BaseLLM, Message


class AnthropicLLM(BaseLLM):
    def __init__(
        self,
        model: str = "claude-sonnet-5",
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.client = AsyncAnthropic(api_key=api_key, max_retries=0)

    async def chat(self, messages: List[Message]) -> str:
        system_messages = [m.content for m in messages if m.role == "system"]
        conversation = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system="\n\n".join(system_messages) or None,
            messages=conversation,
        )
        return next((block.text for block in response.content if block.type == "text"), "")