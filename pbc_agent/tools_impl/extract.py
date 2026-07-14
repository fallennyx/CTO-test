"""Field extraction with citations.

Pulls the facts the verifier needs — dates, periods, money totals, entity mentions,
signatures, accounting standards — out of a parsed :class:`Document`, each as an
:class:`ExtractedField` whose :class:`ProvenanceSpan` points at the exact page/sheet and a
verbatim snippet. This is deterministic (regex + the engagement's entity aliases), so it is
cheap, reproducible, and unit-testable, and it gives the LLM judge only citation-backed facts
to reason over — the core hallucination guardrail.
"""

from __future__ import annotations

import re
from datetime import date

from pbc_agent.config.engagement import Engagement
from pbc_agent.model.documents import (
    Document,
    ExtractedField,
    FieldKind,
    ProvenanceSpan,
    SourceType,
)

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LONG_DATE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.IGNORECASE)
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONEY = re.compile(r"\$\s?(-?[\d,]+(?:\.\d{2})?)")
_STANDARD = re.compile(r"\bASC\s?(606|842|8\d\d|\d{3})\b", re.IGNORECASE)
_SIG_TOKENS = re.compile(
    r"(/s/|signed\s*:|signature\s*:|\bby\s*:\s*[A-Z][a-z]+|sincerely,|"
    r"authorized\s+signature|\bexecuted\s+by\b)", re.IGNORECASE)
_DRAFT = re.compile(
    r"\bDRAFT\b|\bnot\s+for\s+(?:execution|signature)\b|\bto\s+be\s+signed\b|"
    r"\bto\s+be\s+finalized\b|\[\s*signature\s*\]|\bunsigned\b", re.IGNORECASE)


def extract_fields(doc: Document, engagement: Engagement) -> list[ExtractedField]:
    """Extract citation-backed fields from a parsed document."""
    fields: list[ExtractedField] = []
    # DRAFT / "to be signed" status is a document-wide property (a watermark or header on one
    # page invalidates a signature placeholder on another), so resolve it once up front.
    doc_is_draft = bool(_DRAFT.search(doc.text or ""))
    for locator, text in _segments(doc):
        if not text:
            continue
        fields += _dates(doc.doc_id, locator, text)
        fields += _money(doc.doc_id, locator, text)
        fields += _entities(doc.doc_id, locator, text, engagement)
        fields += _standards(doc.doc_id, locator, text)
        fields += _signatures(doc.doc_id, locator, text, doc_is_draft)
    doc.extracted_fields = fields
    return fields


def _segments(doc: Document) -> list[tuple[str, str]]:
    """(locator, text) chunks with meaningful citation coordinates."""
    if doc.sheets:
        return [(f"sheet '{s.name}'", s.text) for s in doc.sheets]
    if doc.pages and doc.sniffed_type in (SourceType.PDF_NATIVE, SourceType.PDF_SCANNED):
        return [(f"page {i + 1}", p) for i, p in enumerate(doc.pages)]
    if doc.sniffed_type is SourceType.IMAGE:
        return [("photo", doc.text)]
    return [("body", doc.text)]


def _span(doc_id: str, locator: str, text: str, start: int, end: int) -> ProvenanceSpan:
    lo = max(0, start - 24)
    hi = min(len(text), end + 24)
    snippet = " ".join(text[lo:hi].split())
    return ProvenanceSpan(doc_id=doc_id, locator=locator, snippet=snippet)


def _dates(doc_id, locator, text) -> list[ExtractedField]:
    out = []
    for m in _ISO_DATE.finditer(text):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            out.append(_date_field(doc_id, locator, text, m, d))
    for m in _LONG_DATE.finditer(text):
        d = _safe_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
        if d:
            out.append(_date_field(doc_id, locator, text, m, d))
    for m in _US_DATE.finditer(text):
        d = _safe_date(int(m[3]), int(m[1]), int(m[2]))
        if d:
            out.append(_date_field(doc_id, locator, text, m, d))
    return out


def _date_field(doc_id, locator, text, m, d: date) -> ExtractedField:
    return ExtractedField(kind=FieldKind.DATE, value=d,
                          provenance=_span(doc_id, locator, text, m.start(), m.end()))


def _money(doc_id, locator, text) -> list[ExtractedField]:
    out = []
    for m in _MONEY.finditer(text):
        try:
            val = float(m[1].replace(",", ""))
        except ValueError:
            continue
        out.append(ExtractedField(kind=FieldKind.MONEY, value=val,
                   provenance=_span(doc_id, locator, text, m.start(), m.end())))
    return out


def _entities(doc_id, locator, text, engagement) -> list[ExtractedField]:
    out, low = [], text.lower()
    for entity in engagement.entities:
        for alias in sorted(entity.aliases, key=len, reverse=True):
            idx = low.find(alias)
            if idx >= 0:
                out.append(ExtractedField(
                    kind=FieldKind.ENTITY, value=entity.name,
                    provenance=_span(doc_id, locator, text, idx, idx + len(alias))))
                break
    return out


def _standards(doc_id, locator, text) -> list[ExtractedField]:
    out = []
    for m in _STANDARD.finditer(text):
        out.append(ExtractedField(kind=FieldKind.STANDARD, value=f"ASC {m[1]}",
                   provenance=_span(doc_id, locator, text, m.start(), m.end())))
    return out


def _signatures(doc_id, locator, text, doc_is_draft: bool) -> list[ExtractedField]:
    out = []
    for m in _SIG_TOKENS.finditer(text):
        out.append(ExtractedField(
            kind=FieldKind.SIGNATURE,
            value={"signed": not doc_is_draft, "draft": doc_is_draft},
            provenance=_span(doc_id, locator, text, m.start(), m.end())))
    if doc_is_draft and not out:
        m = _DRAFT.search(text)
        if m:
            out.append(ExtractedField(
                kind=FieldKind.SIGNATURE, value={"signed": False, "draft": True},
                provenance=_span(doc_id, locator, text, m.start(), m.end())))
    return out


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None
