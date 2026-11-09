"""Agent interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agentproof.sandbox import AgentContext


@dataclass
class AgentOutcome:
    answer: str
    stop_reason: str = "done"  # "done" or a provider reason such as "refusal" / "max_tokens"


class Agent(ABC):
    """Something that, given an instruction and tools, acts and returns a final answer."""

    name: str = "agent"
    version: str = "0"

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentOutcome: ...

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version}


def estimate_tokens(text: str) -> int:
    """Deterministic rough token count (about four characters per token)."""
    return max(1, (len(text) + 3) // 4)
