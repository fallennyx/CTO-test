"""Structured agent trace — the auditable, PCAOB-defensible record.

Every email the agent processes yields an :class:`EmailTrace`: the model's plan text, and each
tool call with its arguments and a short result summary. Per-item, the verifier verdict lives
on the :class:`~pbc_agent.model.assessment.ItemAssessment`. Together these answer "why does the
tracker say this?" for any status — which is what the UI's trace view renders and where the
review spends most of its time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    name: str
    input: dict
    result_summary: str
    ok: bool = True


@dataclass
class EmailTrace:
    thread_id: str
    message_source: str
    subject: str
    sender: str = ""
    plan_notes: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    item_ids_touched: list[str] = field(default_factory=list)

    def tool_sequence(self) -> list[str]:
        return [t.name for t in self.tool_calls]


@dataclass
class RunTrace:
    email_traces: list[EmailTrace] = field(default_factory=list)

    def for_item(self, item_id: str) -> list[EmailTrace]:
        return [t for t in self.email_traces if item_id in t.item_ids_touched]

    def full_tool_sequence(self) -> list[str]:
        return [name for t in self.email_traces for name in t.tool_sequence()]
