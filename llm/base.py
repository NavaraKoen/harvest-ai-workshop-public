from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Message:
    role: str   # "system", "user", "assistant"
    content: str


class BaseLLM(ABC):
    @abstractmethod
    async def chat(self, messages: List[Message]) -> str:
        """Send a list of messages and return the assistant reply."""
        ...
