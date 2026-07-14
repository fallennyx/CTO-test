"""Tool schemas + dispatch — the agent's action surface.

Each tool is exposed to the model as a native tool (name + JSON input schema) and dispatched
to a deterministic Phase-2 implementation. The agent chooses which to call; the effects land in
:class:`TrackerState`. Every call returns a compact JSON-able result (fed back to the model)
plus a one-line summary (recorded in the trace).
"""

from __future__ import annotations

import json
import re
from datetime import date

from pbc_agent.config.engagement import Engagement
from pbc_agent.llm.provider import ToolSpec
from pbc_agent.model.criteria import PBCItem
from pbc_agent.model.documents import Document
from pbc_agent.state.store import TrackerState
from pbc_agent.tools_impl.draft import build_contacts, draft_followups
from pbc_agent.tools_impl.assess import assess_item
from pbc_agent.tools_impl.extract import extract_fields
from pbc_agent.tools_impl.parse import parse_document
from pbc_agent.tools_impl.pii import redact_text, summarize
from pbc_agent.tools_impl.search import CandidateMatcher, evidence_query

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


class Toolbox:
    def __init__(self, engagement: Engagement, items: dict[str, PBCItem],
                 matcher: CandidateMatcher, documents: dict[str, Document],
                 state: TrackerState, discovered_emails: dict[str, str] | None = None):
        self.eng = engagement
        self.items = items
        self.matcher = matcher
        self.documents = documents
        self.state = state
        self.discovered = discovered_emails or {}
        self.current_context = ""   # email subject/body context, set per email by the loop

    def set_email_context(self, subject: str, body: str) -> None:
        self.current_context = f"{subject} {body[:200]}"

    # --- schemas ----------------------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("parse_attachment", "Parse one attachment (native PDF text, OCR a scanned "
                     "PDF/photo, or read all sheets of an Excel file) to reveal its contents.",
                     _schema({"doc_id": _str("the attachment's document id")}, ["doc_id"])),
            ToolSpec("extract_fields", "Extract citation-backed facts (dates, money totals, "
                     "entities, signatures, standards) from a parsed document.",
                     _schema({"doc_id": _str("the document id")}, ["doc_id"])),
            ToolSpec("match_item", "Find the PBC list items a document most likely satisfies.",
                     _schema({"doc_id": _str("the document id")}, ["doc_id"])),
            ToolSpec("verify_item", "Check a PBC item's acceptance criteria against the given "
                     "evidence documents and set its status with reasoning + citations.",
                     _schema({"item_id": _str("PBC item id, e.g. PBC-01"),
                              "doc_ids": {"type": "array", "items": {"type": "string"},
                                          "description": "evidence document ids"}},
                             ["item_id", "doc_ids"])),
            ToolSpec("note_schedule", "Record a rescheduled delivery date for a PBC item "
                     "mentioned in an email (updates the expected date; not a late flag).",
                     _schema({"item_id": _str("PBC item id"),
                              "new_date": _str("new expected date, ISO or 'July 14'")},
                             ["item_id", "new_date"])),
            ToolSpec("draft_followups", "Draft grouped follow-up emails for all currently open "
                     "items, one per recipient.", _schema({}, [])),
            ToolSpec("finish", "Finish processing the current email.",
                     _schema({"summary": _str("one-line summary of what was done")}, [])),
        ]

    # --- dispatch ---------------------------------------------------------------------

    def dispatch(self, name: str, inp: dict, source_email: str | None) -> tuple[dict, str]:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}, f"unknown tool {name}"
        try:
            return fn(inp, source_email)
        except Exception as e:   # tools never crash the loop; surface the error to the agent
            return {"error": str(e)}, f"{name} error: {e}"

    def _doc(self, doc_id: str) -> Document | None:
        return self.documents.get(doc_id)

    def _t_parse_attachment(self, inp, _src):
        doc = self._doc(inp.get("doc_id", ""))
        if doc is None:
            return {"error": "no such document"}, "parse: no such doc"
        parse_document(doc)
        pii = f", PII redacted ({summarize(doc.pii_findings)})" if doc.pii_findings else ""
        info = {"doc_id": doc.doc_id, "filename": doc.filename, "type": doc.sniffed_type.value,
                "ocr_used": doc.ocr_used, "pages": len(doc.pages),
                "sheets": [f"{s.name}{'/'+s.state if s.state!='visible' else ''}" for s in doc.sheets],
                "pii_findings": summarize(doc.pii_findings) if doc.pii_findings else "",
                "excerpt": redact_text((doc.text or "")[:400])}
        return info, f"parsed {doc.filename} ({doc.sniffed_type.value}" \
                     + (", OCR" if doc.ocr_used else "") + pii + ")"

    def _t_extract_fields(self, inp, _src):
        doc = self._doc(inp.get("doc_id", ""))
        if doc is None:
            return {"error": "no such document"}, "extract: no such doc"
        if not doc.parsed:
            parse_document(doc)
        fields = extract_fields(doc, self.eng)
        summary = {}
        sample = []
        for f in fields:
            summary[f.kind.value] = summary.get(f.kind.value, 0) + 1
        for f in fields[:8]:
            sample.append({"kind": f.kind.value, "value": str(f.value),
                           "at": f.provenance.locator,
                           "quote": redact_text(f.provenance.snippet[:80])})
        return {"doc_id": doc.doc_id, "counts": summary, "sample": sample}, \
               f"extracted {len(fields)} fields from {doc.filename}"

    def _t_match_item(self, inp, _src):
        doc = self._doc(inp.get("doc_id", ""))
        if doc is None:
            return {"error": "no such document"}, "match: no such doc"
        if not doc.parsed:
            parse_document(doc)
        standards = [str(f.value) for f in doc.extracted_fields if f.kind.value == "standard"]
        q = evidence_query(doc.filename, doc.text, standards, context=self.current_context)
        cands = self.matcher.match(q, top_k=4)
        candidates = [{"item_id": iid, "score": round(score, 2),
                       "description": self.items[iid].description[:70]}
                      for iid, score in cands]
        top = candidates[0]["item_id"] if candidates else "none"
        return {"doc_id": doc.doc_id, "candidates": candidates}, \
               f"matched {doc.filename} -> {top}"

    def _t_verify_item(self, inp, source_email):
        item_id = inp.get("item_id", "")
        item = self.items.get(item_id)
        if item is None:
            return {"error": f"no such item {item_id}"}, f"verify: no item {item_id}"
        doc_ids = inp.get("doc_ids", []) or []
        for d in doc_ids:
            doc = self._doc(d)
            if doc and not doc.parsed:
                parse_document(doc)
            if doc and not doc.extracted_fields:
                extract_fields(doc, self.eng)
        all_ids = self.state.add_evidence(item_id, doc_ids)
        docs = [self.documents[d] for d in all_ids if d in self.documents]
        assessment = assess_item(item, docs, self.eng, source_email=source_email)
        self.state.set_assessment(assessment)
        checks = [{"criterion": c.criterion_kind, "outcome": c.outcome.value, "detail": c.detail}
                  for c in assessment.check_results]
        return ({"item_id": item_id, "status": assessment.status.value,
                 "reasoning": assessment.reasoning, "checks": checks,
                 "open_items": assessment.open_items},
                f"{item_id} -> {assessment.status.value}")

    def _t_note_schedule(self, inp, _src):
        item_id = inp.get("item_id", "")
        d = _parse_loose_date(inp.get("new_date", ""), self.eng.fiscal_year_end.year)
        if d and self.state.reschedule(item_id, d):
            return {"item_id": item_id, "expected_by": d.isoformat()}, \
                   f"rescheduled {item_id} -> {d.isoformat()}"
        return {"error": "could not reschedule"}, f"reschedule failed for {item_id}"

    def _t_draft_followups(self, _inp, _src):
        contacts = build_contacts(self.eng, self.discovered)
        groups = draft_followups(self.state.followup_candidates(), self.eng, contacts)
        self.state.followups = groups
        return {"groups": len(groups),
                "recipients": [g.recipient for g in groups]}, \
               f"drafted {len(groups)} grouped follow-up(s)"

    def _t_finish(self, inp, _src):
        return {"ok": True}, inp.get("summary", "done")

# --- schema + date helpers ------------------------------------------------------------


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _parse_loose_date(s: str, default_year: int) -> date | None:
    s = (s or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"([a-z]+)\s+(\d{1,2})", s, re.IGNORECASE)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return date(default_year, _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None
