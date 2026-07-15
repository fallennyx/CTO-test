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
import re
from datetime import datetime
from pathlib import Path

from pbc_agent.agent.router import select_model, why
from pbc_agent.agent.tools import Toolbox
from pbc_agent.agent.trace import EmailTrace, ToolCallRecord
from pbc_agent.config.engagement import load_engagement
from pbc_agent.criteria_loader.pbc_list import load_pbc_list
from pbc_agent.ingest import assemble_threads, extract_documents, load_mailbox
from pbc_agent.llm.provider import Provider, get_provider
from pbc_agent.model.assessment import Status
from pbc_agent.model.documents import SourceType
from pbc_agent.state.store import TrackerState


def _is_config_doc(filename: str) -> bool:
    low = filename.lower()
    return "pbc_list" in low or "client_profile" in low or low == "(email body)"


# Strong, unresolved open-item signals (NOT "to follow" — that resolves on later delivery).
_CAVEAT_OPEN = ("still outstanding", "remains outstanding", "under investigation",
                "still investigating", "still pending", "not counter-signed", "to be signed",
                "awaiting", "yet to be", "resolution when done", "still chas",
                "not yet been received", "not yet provided", "still need")
_CAVEAT_RESOLVED = ("immaterial", "fully explained", "explained by", "resolved", "no exception")
_PBC_RE = re.compile(r"pbc[-\s]?(\d{1,2})", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    import re as _re
    return [s for s in _re.split(r"(?<=[.!?])\s+|\n", text or "") if s.strip()]


def _explicit_pbc(sentence: str, items: dict) -> str | None:
    m = _PBC_RE.search(sentence)
    if not m:
        return None
    iid = f"PBC-{int(m.group(1)):02d}"
    return iid if iid in items else None

_MAX_STEPS = 8
_CONTENT_LEAVES = {SourceType.PDF_NATIVE, SourceType.PDF_SCANNED, SourceType.XLSX,
                   SourceType.IMAGE, SourceType.EMAIL_BODY, SourceType.TEXT}

SYSTEM_PROMPT = """You are an audit associate maintaining a live PBC (prepared-by-client) \
request tracker for a financial-statement audit. For each incoming email you must decide what \
to do and call tools to do it: parse attachments, extract citation-backed facts, match them to \
PBC list items, and verify each item's acceptance criteria before setting its status. Never \
trust a filename — verify from content. A document that is the wrong period, missing an entity, \
unsigned when a signature is required, or short of the requested sample is NOT complete. If an \
email only reschedules an item, record the new date (do not flag it late).

CRITICAL: if an email carries attachments that are actual client deliverables, you MUST call \
verify_item for the PBC item each one satisfies BEFORE you call finish — parsing or matching a \
document is not enough; an unverified delivery is a dropped item. Use match_item for candidates \
but choose the correct item_id yourself. The PBC list PDF and client-profile are context, not \
deliverables. Keep going until every delivered document in this email has been verified, then \
call finish. Be economical with tool calls."""


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

    def run(self, max_emails: int | None = None) -> TrackerState:
        for i, message in enumerate(self._messages_in_order()):
            if max_emails is not None and i >= max_emails:
                break
            if self.state.budget.exhausted:
                break
            self._process_email(message)
        # Authoritative, deterministic reconciliation: assign every delivered document to the
        # single item it best satisfies and re-verify — so the verdict is reproducible and free
        # of cross-item contamination, whichever provider drove the planning.
        self._reconcile()
        # Read the threads for "still outstanding / under investigation" caveats a senior would
        # act on, and downgrade the affected items accordingly.
        self._caveat_pass()
        # Final step: draft grouped follow-ups for everything still open.
        self._final_followups()
        return self.state

    def _caveat_pass(self) -> None:
        """Downgrade items an email flags as still open (a senior reads the thread, not just the file).

        Only strong, unresolved open-item language counts — "still outstanding", "under
        investigation", "to be signed", etc. — and explicitly-resolved/immaterial mentions are
        ignored, as are "to follow" promises (those resolve when the file later arrives). Each
        caveat is routed to its PBC item by an explicit PBC-NN reference or by matching the
        sentence against items that already have evidence.
        """
        delivered = {iid for iid, ev in self.state.evidence_by_item.items() if ev}
        if not delivered:
            return
        # Map each delivered document to the item(s) it is evidence for, so a caveat can be
        # routed among only the items that thread actually touched.
        doc_to_items: dict[str, set[str]] = {}
        for iid, ev in self.state.evidence_by_item.items():
            for d in ev:
                doc_to_items.setdefault(d, set()).add(iid)

        for thread in self.threads:
            thread_docs: set[str] = set()
            for m in thread.messages:
                thread_docs |= set(self.leaf_docs_by_source.get(m.source_path, []))
            thread_items = {i for d in thread_docs for i in doc_to_items.get(d, set())}
            for m in thread.messages:
                for sentence in _sentences(m.body_text):
                    low = sentence.lower()
                    if not any(p in low for p in _CAVEAT_OPEN):
                        continue
                    if any(n in low for n in _CAVEAT_RESOLVED):
                        continue
                    iid = _explicit_pbc(sentence, self.state.items)
                    if iid is None and thread_items:
                        cands = self.toolbox.matcher.match(sentence, top_k=1, restrict=thread_items)
                        iid = cands[0][0] if cands else None
                    if iid is None or iid not in delivered:
                        continue
                    self._flag_open(iid, sentence)

    def _flag_open(self, iid: str, sentence: str) -> None:
        a = self.state.assessments[iid]
        note = f"per email: {sentence.strip()[:110]}"
        if note not in a.open_items:
            a.open_items.append(note)
        if a.status in (Status.COMPLETE, Status.RECEIVED):
            a.status = Status.UNDER_REVIEW
            a.reasoning = (a.reasoning + " " if a.reasoning else "") + \
                "Downgraded: the client flagged an outstanding item in this thread."

    def _reconcile(self) -> None:
        """Deterministically assign each delivered document to its single best-matching item.

        Every real attachment goes to exactly one item (its top candidate), which eliminates the
        cross-item contamination that a free-form agent assignment can introduce (e.g. a signed
        legal letter bleeding onto the management-representation item). Email-body evidence the
        agent already captured (e.g. a forwarded 401(k) confirmation) is preserved. Every item
        with evidence is then re-verified, making the final status a reproducible function of the
        evidence — identical whether Claude or the offline mock drove the planning.
        """
        from pbc_agent.tools_impl.assess import assess_item
        from pbc_agent.tools_impl.extract import extract_fields
        from pbc_agent.tools_impl.parse import parse_document
        from pbc_agent.tools_impl.search import evidence_query

        authoritative: dict[str, list[str]] = {}
        # 1) Each attachment -> its single best item.
        for doc in list(self.toolbox.documents.values()):
            if doc.sniffed_type not in _CONTENT_LEAVES or doc.sniffed_type is SourceType.EMAIL_BODY:
                continue
            if _is_config_doc(doc.filename):
                continue
            if not doc.parsed:
                parse_document(doc)
            if not doc.extracted_fields:
                extract_fields(doc, self.toolbox.eng)
            standards = [str(f.value) for f in doc.extracted_fields if f.kind.value == "standard"]
            cands = self.toolbox.matcher.match(
                evidence_query(doc.filename, doc.text, standards), top_k=1)
            if cands:
                authoritative.setdefault(cands[0][0], []).append(doc.doc_id)
        # 2) Preserve email-body evidence the agent already attributed (not attachment-based).
        for iid, ev in self.state.evidence_by_item.items():
            for d in ev:
                doc = self.toolbox.documents.get(d)
                if doc and doc.sniffed_type is SourceType.EMAIL_BODY:
                    authoritative.setdefault(iid, [])
                    if d not in authoritative[iid]:
                        authoritative[iid].append(d)
        # 3) Rebuild evidence + re-verify. Items that lost all evidence revert to Not started
        #    (their earlier assessment was based on contaminating/misassigned documents).
        from pbc_agent.model.assessment import ItemAssessment
        self.state.evidence_by_item = authoritative
        for iid in self.state.items:
            if iid not in authoritative:
                self.state.set_assessment(ItemAssessment(item_id=iid, status=Status.NOT_STARTED,
                                          reasoning="No matching evidence has been received yet."))
        for iid, doc_ids in authoritative.items():
            docs = [self.toolbox.documents[d] for d in doc_ids if d in self.toolbox.documents]
            if docs and iid in self.state.items:
                self.state.set_assessment(
                    assess_item(self.state.items[iid], docs, self.toolbox.eng,
                                source_email="(reconciled)"))

    def _messages_in_order(self):
        msgs = [m for t in self.threads for m in t.messages]
        return sorted(msgs, key=lambda m: (m.date is None, m.date or datetime.max))

    def _process_email(self, message) -> None:
        thread_id = _thread_of(message, self.threads)
        leaf_ids = self.leaf_docs_by_source.get(message.source_path, [])
        attachments = [{"doc_id": d, "filename": self.toolbox.documents[d].filename,
                        "type": self.toolbox.documents[d].sniffed_type.value}
                       for d in leaf_ids]
        from pbc_agent.tools_impl.pii import redact_text
        ctx = {"thread": thread_id, "subject": message.subject,
               "sender": str(message.sender) if message.sender else "",
               "body": redact_text(message.body_text[:1200]), "attachments": attachments}

        self.toolbox.set_email_context(message.subject, message.body_text)
        trace = EmailTrace(thread_id=thread_id, message_source=message.source_path,
                           subject=message.subject, sender=ctx["sender"])
        model = select_model(ctx)
        trace.models_used.append(model)
        trace.plan_notes.append(why(ctx))

        messages = [{"role": "user",
                     "content": [{"type": "text", "text": "EMAIL_CONTEXT: " + json.dumps(ctx)}]}]

        for _ in range(_MAX_STEPS):
            try:
                turn = self.provider.converse(SYSTEM_PROMPT, messages, self.toolbox.specs(), model)
            except Exception as e:   # a model/API hiccup on one email must not kill the run
                trace.plan_notes.append(f"LLM call failed ({type(e).__name__}: {e}); "
                                        f"skipping this email.")
                self.state.trace.email_traces.append(trace)
                return
            # Live providers report real token usage; for the offline mock we estimate from the
            # actual serialized prompt/response sizes (~4 chars/token) so cost is grounded in
            # real prompt volume, not a fixed guess.
            usage_in = turn.usage_in or _estimate_tokens(SYSTEM_PROMPT, messages,
                                                         self.toolbox.specs())
            usage_out = turn.usage_out or _estimate_out_tokens(turn)
            self.state.budget.add(model, usage_in, usage_out)
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


def _estimate_tokens(system: str, messages: list[dict], specs) -> int:
    """Rough token estimate (~4 chars/token) from the actual prompt volume."""
    chars = len(system) + sum(len(s.name) + len(s.description) + len(json.dumps(s.input_schema))
                              for s in specs)
    chars += len(json.dumps(messages))
    return max(1, chars // 4)


def _estimate_out_tokens(turn) -> int:
    chars = len(turn.text or "") + sum(len(json.dumps(c.input)) + len(c.name)
                                       for c in turn.tool_calls)
    return max(1, chars // 4)


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


def run_agent(bundle: str | Path, prefer_mock: bool = False, ceiling_usd: float = 5.0,
              max_emails: int | None = None) -> TrackerState:
    return AgentRunner.from_bundle(bundle, ceiling_usd=ceiling_usd,
                                   prefer_mock=prefer_mock).run(max_emails=max_emails)
