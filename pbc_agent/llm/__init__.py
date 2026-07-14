"""LLM provider abstraction (native tool-use) + cost accounting.

``get_provider()`` returns a live Anthropic provider when ``ANTHROPIC_API_KEY`` is set,
otherwise a deterministic offline mock that drives the *same* tools with a faithful
per-email policy — so the agent, UI, and evals run cold with no key, and the live demo uses
real native tool-use by flipping one env var.
"""

from pbc_agent.llm.provider import (
    AssistantTurn,
    Budget,
    Provider,
    ToolCall,
    ToolSpec,
    get_provider,
)

__all__ = ["AssistantTurn", "Budget", "Provider", "ToolCall", "ToolSpec", "get_provider"]
