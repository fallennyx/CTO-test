"""Provider protocol, tool/turn dataclasses, and the $ budget meter.

The agent loop talks to *any* provider through :meth:`Provider.converse`, passing Anthropic
-style content blocks (``text`` / ``tool_use`` / ``tool_result``). This keeps the loop itself
provider-agnostic and inspectable in one file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"      # "tool_use" while the agent is still calling tools
    model: str = ""
    usage_in: int = 0
    usage_out: int = 0


# --- Model tiers + pricing (USD per 1M tokens; approximate list prices) ---------------

MODEL_SMALL = "claude-haiku-4-5-20251001"
MODEL_LARGE = "claude-sonnet-5"

_PRICING = {
    MODEL_SMALL: (0.80, 4.00),
    MODEL_LARGE: (3.00, 15.00),
    "claude-opus-4-8": (15.00, 75.00),
}


@dataclass
class Budget:
    """Tracks token spend and enforces a hard USD ceiling (the brief: ≤ $5 / PBC list)."""

    ceiling_usd: float = 5.0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    def add(self, model: str, usage_in: int, usage_out: int) -> None:
        pin, pout = _PRICING.get(model, _PRICING[MODEL_SMALL])
        self.usd += usage_in / 1_000_000 * pin + usage_out / 1_000_000 * pout
        self.input_tokens += usage_in
        self.output_tokens += usage_out
        self.calls += 1

    @property
    def exhausted(self) -> bool:
        return self.usd >= self.ceiling_usd

    def summary(self) -> dict:
        return {"usd": round(self.usd, 4), "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "llm_calls": self.calls,
                "ceiling_usd": self.ceiling_usd}


class Provider(Protocol):
    name: str

    def converse(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 model: str) -> AssistantTurn:
        ...


def get_provider(prefer_mock: bool = False) -> Provider:
    """Live Anthropic provider when a key is present (and not forced to mock); else the mock."""
    if not prefer_mock and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from pbc_agent.llm.anthropic_provider import AnthropicProvider
            return AnthropicProvider()
        except Exception:
            pass
    from pbc_agent.llm.mock_provider import MockProvider
    return MockProvider()
