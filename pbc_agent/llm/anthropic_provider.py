"""Live Anthropic provider — native tool use.

Thin wrapper over ``client.messages.create`` with ``tools=[...]``. The model plans and emits
``tool_use`` blocks; the loop executes them and feeds back ``tool_result`` blocks. Temperature
0 for reproducibility. This is the "agent loop written directly against Anthropic's native
tool-use API" the brief prefers.
"""

from __future__ import annotations

from pbc_agent.llm.provider import AssistantTurn, ToolCall, ToolSpec


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, max_tokens: int = 1024):
        import anthropic
        self._client = anthropic.Anthropic()
        self._max_tokens = max_tokens

    def converse(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 model: str) -> AssistantTurn:
        resp = self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            temperature=0,
            system=system,
            tools=[{"name": t.name, "description": t.description,
                    "input_schema": t.input_schema} for t in tools],
            messages=messages,
        )
        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            model=model,
            usage_in=getattr(resp.usage, "input_tokens", 0),
            usage_out=getattr(resp.usage, "output_tokens", 0),
        )
