from typing import List, Optional

from openai import AsyncOpenAI

from .base import BaseLLM, Message


class OpenAILLM(BaseLLM):
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.model = model
        # max_retries=0 — let Temporal handle retries, not the HTTP client
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    async def chat(self, messages: List[Message]) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        return response.choices[0].message.content or ""
