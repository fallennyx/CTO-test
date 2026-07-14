"""Offline mock provider — a deterministic stand-in for the LLM's planning.

It drives the *same* native-tool-use interface as the live model, choosing tools per email
with a faithful policy so the agent runs cold (no API key) for tests, evals, and the UI. The
policy is genuinely *dynamic per email*: an email with attachments walks
parse → extract → match → verify; a text-only "it's delayed" email instead reschedules and
stops. That divergence is exactly what the review asks to see — and with a real key the same
loop hands control to Claude instead.

State is reconstructed from the transcript each call (which tools already ran), so the mock
is stateless and the loop code is identical for mock and live providers.
"""

from __future__ import annotations

import json
import re

from pbc_agent.llm.provider import AssistantTurn, ToolCall, ToolSpec

_RESCHED = re.compile(
    r"(expect|by|instead of).{0,40}?(pbc[-\s]?\d+)?.{0,40}?"
    r"(next\s+\w+|\b\w+day\b|\d{4}-\d{2}-\d{2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2})",
    re.IGNORECASE)
_PBC = re.compile(r"pbc[-\s]?(\d{1,2})", re.IGNORECASE)
_DATE_PHRASE = re.compile(
    r"(\d{4}-\d{2}-\d{2}|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2})", re.IGNORECASE)


class MockProvider:
    name = "mock"
    _counter = 0

    def converse(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 model: str) -> AssistantTurn:
        ctx = _email_context(messages)
        used = _tools_used(messages)
        results = _tool_results(messages)
        attachments = ctx.get("attachments", [])

        turn = AssistantTurn(stop_reason="tool_use", model=model, usage_in=350, usage_out=90)

        # --- text-only emails: reschedule or nothing to do -------------------------------
        if not attachments:
            resched = _detect_reschedule(ctx.get("body", ""))
            if resched and "note_schedule" not in used:
                turn.text = f"Plan: text-only update — reschedule {resched['item_id']}."
                turn.tool_calls = [self._call("note_schedule", resched)]
                return turn
            turn.text = "Plan: no attachments and no actionable request — nothing to do."
            turn.tool_calls = [self._call("finish", {"summary": "No action needed."})]
            turn.stop_reason = "end_turn"
            return turn

        # --- attachment emails: parse -> extract -> match -> verify -> finish ------------
        if "parse_attachment" not in used:
            turn.text = f"Plan: {len(attachments)} attachment(s) — parse each to see contents."
            turn.tool_calls = [self._call("parse_attachment", {"doc_id": a["doc_id"]})
                               for a in attachments]
            return turn
        if "extract_fields" not in used:
            turn.text = "Plan: extract citation-backed fields from each parsed document."
            turn.tool_calls = [self._call("extract_fields", {"doc_id": a["doc_id"]})
                               for a in attachments]
            return turn
        if "match_item" not in used:
            turn.text = "Plan: match each document to candidate PBC items."
            turn.tool_calls = [self._call("match_item", {"doc_id": a["doc_id"]})
                               for a in attachments]
            return turn
        if "verify_item" not in used:
            by_item = _group_by_matched_item(messages, attachments)
            turn.text = ("Plan: verify each matched item's evidence against its acceptance "
                         "criteria.")
            turn.tool_calls = [self._call("verify_item", {"item_id": iid, "doc_ids": docs})
                               for iid, docs in by_item.items()]
            if not turn.tool_calls:
                turn.tool_calls = [self._call("finish", {"summary": "No confident item match."})]
                turn.stop_reason = "end_turn"
            return turn

        turn.text = "Plan: tracker updated for this email."
        turn.tool_calls = [self._call("finish", {"summary": "Processed attachments."})]
        turn.stop_reason = "end_turn"
        return turn

    def _call(self, name: str, inp: dict) -> ToolCall:
        MockProvider._counter += 1
        return ToolCall(id=f"mock_{MockProvider._counter}", name=name, input=inp)


# --- transcript scanning --------------------------------------------------------------


def _email_context(messages: list[dict]) -> dict:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for block in _blocks(msg):
            if block.get("type") == "text" and block.get("text", "").startswith("EMAIL_CONTEXT:"):
                try:
                    return json.loads(block["text"][len("EMAIL_CONTEXT:"):])
                except json.JSONDecodeError:
                    return {}
    return {}


def _tools_used(messages: list[dict]) -> set[str]:
    used = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in _blocks(msg):
                if block.get("type") == "tool_use":
                    used.add(block["name"])
    return used


def _tool_results(messages: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "user":
            for block in _blocks(msg):
                if block.get("type") == "tool_result":
                    out[block.get("tool_use_id", "")] = _as_text(block.get("content"))
    return out


def _group_by_matched_item(messages, attachments) -> dict[str, list[str]]:
    """Correlate match_item calls with their results to assign each doc to its top item."""
    id_to_input: dict[str, dict] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in _blocks(msg):
                if block.get("type") == "tool_use" and block["name"] == "match_item":
                    id_to_input[block["id"]] = block.get("input", {})
    results = _tool_results(messages)
    by_item: dict[str, list[str]] = {}
    for call_id, inp in id_to_input.items():
        doc_id = inp.get("doc_id")
        payload = results.get(call_id, "")
        top = _top_item(payload)
        if doc_id and top:
            by_item.setdefault(top, []).append(doc_id)
    return by_item


def _top_item(payload: str) -> str | None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    cands = data.get("candidates") if isinstance(data, dict) else None
    if cands:
        return cands[0].get("item_id")
    return None


def _detect_reschedule(body: str) -> dict | None:
    if not _RESCHED.search(body or ""):
        return None
    item = _PBC.search(body or "")
    date_m = _DATE_PHRASE.search(body or "")
    if not item or not date_m:
        return None
    return {"item_id": f"PBC-{int(item.group(1)):02d}", "new_date": date_m.group(1)}


def _blocks(msg: dict) -> list[dict]:
    content = msg.get("content", [])
    return content if isinstance(content, list) else []


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)
