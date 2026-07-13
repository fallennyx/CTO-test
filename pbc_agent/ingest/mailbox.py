"""Mailbox loading: ``.eml`` files, a directory of them, or a single ``.mbox``.

Produces a domain :class:`~pbc_agent.model.messages.Message` for routing/threading plus the
raw :class:`email.message.EmailMessage` (kept for the recursion engine to walk attachments).
Uses only the standard library ``email`` / ``mailbox`` machinery with the modern default
policy, which transparently handles RFC 2047 header encoding and quoted-printable bodies.
"""

from __future__ import annotations

import mailbox
from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from pbc_agent.model.messages import Address, Message


@dataclass
class LoadedEmail:
    message: Message          # domain object (threading / routing)
    raw: EmailMessage         # raw MIME tree (attachment recursion)


def load_mailbox(path: str | Path) -> list[LoadedEmail]:
    """Load emails from a directory of ``.eml``, a single ``.eml``, or an ``.mbox`` file."""
    p = Path(path)
    if p.is_dir():
        raw_items = [(f.name, f.read_bytes()) for f in sorted(p.glob("*.eml"))]
    elif p.suffix.lower() == ".mbox":
        box = mailbox.mbox(str(p))
        raw_items = [(f"{p.name}#{i}", msg.as_bytes()) for i, msg in enumerate(box)]
    else:
        raw_items = [(p.name, p.read_bytes())]

    out: list[LoadedEmail] = []
    for source_name, raw_bytes in raw_items:
        raw = message_from_bytes(raw_bytes, policy=default_policy)
        out.append(LoadedEmail(message=_to_message(raw, source_name), raw=raw))
    return out


def _to_message(raw: EmailMessage, source_path: str) -> Message:
    return Message(
        source_path=source_path,
        message_id=_clean_id(raw["message-id"]),
        in_reply_to=_clean_id(raw["in-reply-to"]),
        references=_id_list(raw["references"]),
        subject=str(raw["subject"] or "").strip(),
        sender=_first_address(raw["from"]),
        to=_addresses(raw["to"]),
        cc=_addresses(raw["cc"]),
        date=_parse_date(raw["date"]),
        body_text=_body_text(raw),
    )


def _body_text(raw: EmailMessage) -> str:
    try:
        part = raw.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        return ""
    return content if isinstance(content, str) else ""


def _addresses(header_value) -> list[Address]:
    if not header_value:
        return []
    return [Address(name=name.strip(), email=addr.strip())
            for name, addr in getaddresses([str(header_value)]) if addr]


def _first_address(header_value) -> Address | None:
    addrs = _addresses(header_value)
    return addrs[0] if addrs else None


def _parse_date(header_value):
    if not header_value:
        return None
    try:
        return parsedate_to_datetime(str(header_value))
    except (TypeError, ValueError):
        return None


def _clean_id(value) -> str | None:
    if not value:
        return None
    return str(value).strip().strip("<>").strip() or None


def _id_list(value) -> list[str]:
    if not value:
        return []
    return [tok.strip("<>") for tok in str(value).split() if tok.strip()]
