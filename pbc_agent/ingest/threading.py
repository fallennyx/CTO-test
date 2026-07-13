"""Thread assembly.

Groups messages into conversations using RFC 5322 ``References`` / ``In-Reply-To`` where
present, and falls back to a normalized subject (stripping ``Re:`` / ``Fwd:`` prefixes).
Union-find keeps the grouping robust when reply chains are partial. Thread ids are derived
from the source filenames (``thread01_msg01.eml`` -> ``thread01``) when that convention is
present, otherwise assigned deterministically by first-message order.
"""

from __future__ import annotations

import re
from datetime import datetime

from pbc_agent.model.messages import Message, Thread

_SUBJECT_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.IGNORECASE)
_THREAD_HINT = re.compile(r"(thread\d+)", re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    s = subject or ""
    while True:
        new = _SUBJECT_PREFIX.sub("", s)
        if new == s:
            break
        s = new
    return s.strip().lower()


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def assemble_threads(messages: list[Message]) -> list[Thread]:
    """Cluster messages into :class:`Thread` objects, temporally ordered within each."""
    uf = _UnionFind()
    by_msgid: dict[str, Message] = {}

    # Seed nodes: use message-id when available, else a synthetic per-message key.
    keys: list[str] = []
    for i, msg in enumerate(messages):
        key = msg.message_id or f"__nomsgid_{i}"
        keys.append(key)
        uf.find(key)
        if msg.message_id:
            by_msgid[msg.message_id] = msg

    # Link by reference headers.
    for key, msg in zip(keys, messages):
        for ref in [msg.in_reply_to, *msg.references]:
            if ref:
                uf.union(key, ref)

    # Link by normalized subject (covers threads whose refs don't fully connect).
    subject_rep: dict[str, str] = {}
    for key, msg in zip(keys, messages):
        norm = _normalize_subject(msg.subject)
        if not norm:
            continue
        if norm in subject_rep:
            uf.union(subject_rep[norm], key)
        else:
            subject_rep[norm] = key

    # Bucket messages by cluster root.
    clusters: dict[str, list[Message]] = {}
    for key, msg in zip(keys, messages):
        clusters.setdefault(uf.find(key), []).append(msg)

    threads: list[Thread] = []
    for members in clusters.values():
        ordered = sorted(members, key=lambda m: (m.date is None, m.date or datetime.max))
        tid = _thread_id(ordered, len(threads))
        subject = _normalize_subject(ordered[0].subject) or tid
        threads.append(Thread(thread_id=tid, subject=subject, messages=ordered))

    threads.sort(key=lambda t: t.thread_id)
    return threads


def _thread_id(messages: list[Message], index: int) -> str:
    """Prefer a ``threadNN`` hint from source filenames; else a stable sequential id."""
    hints: set[str] = set()
    for m in messages:
        hit = _THREAD_HINT.search(m.source_path or "")
        if hit:
            hints.add(hit.group(1).lower())
    if len(hints) == 1:
        return next(iter(hints))
    return f"thread{index + 1:02d}"
