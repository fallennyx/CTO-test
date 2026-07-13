"""Email message + thread models.

These are the parsed, structured form of the raw ``.eml`` / ``.mbox`` input. Attachments
are not stored inline here; the recursion engine walks each message and emits ``Document``
objects. A ``Message`` keeps the metadata needed for routing (sender/recipients), temporal
reasoning (``date``), and thread assembly (``message_id`` / ``in_reply_to`` / ``references``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Address:
    name: str
    email: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else self.email


@dataclass
class Message:
    source_path: str
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    sender: Address | None
    to: list[Address]
    cc: list[Address]
    date: datetime | None
    body_text: str
    #: doc_ids of leaf documents reached from this message (filled by the extractor).
    document_ids: list[str] = field(default_factory=list)

    @property
    def all_recipients(self) -> list[Address]:
        return [*self.to, *self.cc]


@dataclass
class Thread:
    thread_id: str
    subject: str
    messages: list[Message] = field(default_factory=list)

    def sorted_messages(self) -> list[Message]:
        """Messages in temporal order (undated messages sort last, stably)."""
        return sorted(
            self.messages,
            key=lambda m: (m.date is None, m.date or datetime.max),
        )
