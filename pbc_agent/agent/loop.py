"""THE agent loop — native tool-use, per-email dynamic planning.

For each incoming email (in temporal order), the agent is given the email + its attachment
manifest + the current tracker, and it *plans and calls tools* until it has updated the
tracker. Different emails take different tool paths — an attachment email walks
parse → extract → match → verify; a text-only "it's delayed" email reschedules and stops.
The whole control flow lives in ``_process_email`` below so it is inspectable in one place;
the model (Anthropic native tool-use, or the offline mock) decides, the tools act.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pbc_agent.agent.router import select_model, why
from pbc_agent.agent.tools import Toolbox
from pbc_agent.agent.trace import EmailTrace, ToolCallRecord
from pbc_agent.config.engagement import load_engagement
from pbc_agent.criteria_loader.pbc_list import load_pbc_list
from pbc_agent.ingest import assemble_threads, extract_documents, load_mailbox
from pbc_agent.llm.provider import Provider, get_provider
from pbc_agent.model.documents import SourceType
from pbc_agent.state.store import TrackerState

_MAX_STEPS = 8
_CONTENT_LEAVES = {SourceType.PDF_NATIVE, SourceType.PDF_SCANNED, SourceType.XLSX,
                   SourceType.IMAGE, SourceType.EMAIL_BODY, SourceType.TEXT}

SYSTEM_PROMPT = """You are an audit associate maintaining a live PBC (prepared-by-client) \
request tracker for a financial-statement audit. For each incoming email you must decide what \
to do and call tools to do it: parse attachments, extract citation-backed facts, match them to \
PBC list items, and verify each item's acceptance criteria before setting its status. Never \
trust a filename — verify from content. A document that is the wrong period, missing an entity, \
unsigned when a signature is required, or short of the requested sample is NOT complete. If an \
email only reschedules an item, record the new date (do not flag it late). Keep going until the \
tracker reflects this email, then call finish. Be economical with tool calls."""


class AgentRunner:
    def __init__(self, state: TrackerState, toolbox: Toolbox, provider: Provider,
                 messages_by_thread: list, leaf_docs_by_source: dict[str, list[str]]):
        self.state = state
        self.toolbox = toolbox
        self.provider = provider
        self.threads = messages_by_thread
        self.leaf_docs_by_source = leaf_docs_by_source

    @classmethod
    def from_bundle(cls, bundle: str | Path, provider: Provider | None = None,
                    ceiling_usd: float = 5.0, prefer_mock: bool = False) -> "AgentRunner":
        paths = _resolve(Path(bundle))
        engagement = load_engagement(paths["profile"])
        items = {it.id: it for it in load_pbc_list(paths["pbc_list"], engagement)}
        loaded = load_mailbox(paths["mailbox"])
        threads = assemble_threads([le.message for le in loaded])
        extraction = extract_documents(loaded)

        from pbc_agent.tools_impl.search import CandidateMatcher
        matcher = CandidateMatcher(list(items.values()))
        documents = extraction.documents

        leaf_by_source: dict[str, list[str]] = {}
        for occ in extraction.occurrences:
            doc = documents[occ.doc_id]
            if doc.sniffed_type in _CONTENT_LEAVES:
                lst = leaf_by_source.setdefault(occ.message_source, [])
                if occ.doc_id not in lst:
                    lst.append(occ.doc_id)

        discovered = {}
        for le in loaded:
            for a in [le.message.sender, *le.message.to, *le.message.cc]:
                if a and a.name:
                    discovered[a.name.lower()] = a.email

        state = TrackerState(engagement=engagement, items=items)
        state.budget.ceiling_usd = ceiling_usd
        toolbox = Toolbox(engagement, items, matcher, documents, state, discovered)
        provider = provider or get_provider(prefer_mock=prefer_mock)
        return cls(state, toolbox, provider, threads, leaf_by_source)

    # --- run --------------------------------------------------------------------------

    def run(self) -> TrackerState:
        for message in self._messages_in_order():
            if self.state.budget.exhausted:
                break
            self._process_email(message)
        # Final step: draft grouped follow-ups for everything still open.
        self._final_followups()
        return self.state

    def _messages_in_order(self):
        msgs = [m for t in self.threads for m in t.messages]
        return sorted(msgs, key=lambda m: (m.date is None, m.date or datetime.max))

    def _process_email(self, message) -> None:
        thread_id = _thread_of(message, self.threads)
        leaf_ids = self.leaf_docs_by_source.get(message.source_path, [])
        attachments = [{"doc_id": d, "filename": self.toolbox.documents[d].filename,
                        "type": self.toolbox.documents[d].sniffed_type.value}
                       for d in leaf_ids]
        ctx = {"thread": thread_id, "subject": message.subject,
               "sender": str(message.sender) if message.sender else "",
               "body": message.body_text[:1200], "attachments": attachments}

        self.toolbox.set_email_context(message.subject, message.body_text)
        trace = EmailTrace(thread_id=thread_id, message_source=message.source_path,
                           subject=message.subject, sender=ctx["sender"])
        model = select_model(ctx)
        trace.models_used.append(model)
        trace.plan_notes.append(why(ctx))

        messages = [{"role": "user",
                     "content": [{"type": "text", "text": "EMAIL_CONTEXT: " + json.dumps(ctx)}]}]

        for _ in range(_MAX_STEPS):
            turn = self.provider.converse(SYSTEM_PROMPT, messages, self.toolbox.specs(), model)
            self.state.budget.add(model, turn.usage_in, turn.usage_out)
            if turn.text:
                trace.plan_notes.append(turn.text)
            messages.append({"role": "assistant", "content": _assistant_blocks(turn)})

            if not turn.tool_calls:
                break
            results_blocks = []
            for call in turn.tool_calls:
                result, summary = self.toolbox.dispatch(call.name, call.input,
                                                        message.source_path)
                trace.tool_calls.append(ToolCallRecord(
                    name=call.name, input=call.input, result_summary=summary,
                    ok="error" not in result))
                if call.name == "verify_item" and call.input.get("item_id"):
                    trace.item_ids_touched.append(call.input["item_id"])
                if call.name == "note_schedule" and call.input.get("item_id"):
                    trace.item_ids_touched.append(call.input["item_id"])
                results_blocks.append({"type": "tool_result", "tool_use_id": call.id,
                                       "content": json.dumps(result)[:2000]})
                if call.name == "finish":
                    break
            messages.append({"role": "user", "content": results_blocks})
            if any(c.name == "finish" for c in turn.tool_calls):
                break
            if self.state.budget.exhausted:
                trace.plan_notes.append("budget ceiling reached — stopping LLM planning")
                break

        self.state.trace.email_traces.append(trace)

    def _final_followups(self) -> None:
        result, summary = self.toolbox.dispatch("draft_followups", {}, None)
        trace = EmailTrace(thread_id="—", message_source="(final)",
                           subject="Draft grouped follow-ups")
        trace.tool_calls.append(ToolCallRecord("draft_followups", {}, summary,
                                               "error" not in result))
        self.state.trace.email_traces.append(trace)


def _assistant_blocks(turn) -> list[dict]:
    blocks: list[dict] = []
    if turn.text:
        blocks.append({"type": "text", "text": turn.text})
    for call in turn.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
    return blocks


def _thread_of(message, threads) -> str:
    for t in threads:
        if message in t.messages:
            return t.thread_id
    return "?"


def _resolve(bundle: Path) -> dict:
    profile = next(iter(sorted(bundle.glob("**/Client_Profile*.pdf"))), None)
    pbc_list = next(iter(sorted(bundle.glob("**/PBC_List*.pdf"))), None)
    emails = next((p for p in bundle.glob("**/emails") if p.is_dir()), None)
    mailbox = emails or next(iter(sorted(bundle.glob("**/*.mbox"))), None)
    if not (profile and pbc_list and mailbox):
        raise FileNotFoundError(f"bundle missing spec/mailbox under {bundle}")
    return {"profile": profile, "pbc_list": pbc_list, "mailbox": mailbox}


def run_agent(bundle: str | Path, prefer_mock: bool = False, ceiling_usd: float = 5.0) -> TrackerState:
    return AgentRunner.from_bundle(bundle, ceiling_usd=ceiling_usd,
                                   prefer_mock=prefer_mock).run()
