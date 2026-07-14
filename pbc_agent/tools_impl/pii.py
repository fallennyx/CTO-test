"""PII detection + redaction gate.

Sensitive personal data (SSNs, dates of birth, bank/account/routing numbers, card numbers)
must be caught and **redacted before anything is sent to the LLM or shown**, and the item
flagged. This defends the held-out "un-redacted payroll register" trap and, more generally,
keeps the agent from leaking PII no matter what unseen document arrives. Deterministic regex,
so it is reproducible and testable.
"""

from __future__ import annotations

import re

from pbc_agent.model.documents import Document, PIIFinding, ProvenanceSpan

# Ordered: more specific patterns first so redaction labels are accurate.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("DOB", re.compile(r"\b(?:DOB|date of birth)\b[:\s]*"
                       r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE)),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){15,16}\b")),
    ("routing", re.compile(r"\b(?:routing|aba)\b[^\d]{0,12}(\d{9})\b", re.IGNORECASE)),
    ("bank_account", re.compile(r"\b(?:account|acct)\b[^\d]{0,14}(\d{6,17})\b", re.IGNORECASE)),
]


def scan_document(doc: Document) -> list[PIIFinding]:
    """Find PII across a parsed document; records findings on the document."""
    findings: list[PIIFinding] = []
    for locator, text in _segments(doc):
        for pii_type, pattern in _PATTERNS:
            for m in pattern.finditer(text or ""):
                snippet = _mask(m.group(0))
                findings.append(PIIFinding(
                    pii_type=pii_type,
                    provenance=ProvenanceSpan(doc.doc_id, locator, snippet),
                    redacted=True))
    doc.pii_findings = findings
    return findings


def redact_text(text: str) -> str:
    """Return text with all detected PII replaced by ``[REDACTED-<type>]``."""
    if not text:
        return text
    out = text
    for pii_type, pattern in _PATTERNS:
        out = pattern.sub(f"[REDACTED-{pii_type}]", out)
    return out


def redact_document_view(doc: Document) -> str:
    """A redacted copy of the document text, safe to send to the LLM / show in the UI."""
    return redact_text(doc.text)


def summarize(findings: list[PIIFinding]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.pii_type] = counts.get(f.pii_type, 0) + 1
    return ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))


def _mask(value: str) -> str:
    keep = value[-4:] if len(value) > 4 else ""
    return f"***{keep}"


def _segments(doc: Document):
    if doc.sheets:
        return [(f"sheet '{s.name}'", s.text) for s in doc.sheets]
    if doc.pages:
        return [(f"page {i + 1}", p) for i, p in enumerate(doc.pages)]
    return [("body", doc.text)]
