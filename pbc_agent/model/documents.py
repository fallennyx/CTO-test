"""Document, provenance, and extracted-field models.

A ``Document`` is the normalized result of parsing one leaf artifact (a PDF, one XLSX,
one image, an email body). The recursion engine (``ingest/extract.py``) produces a
``Document`` for every leaf it reaches, each carrying a ``container_chain`` that records
exactly how it was nested — e.g. ``thread07_msg03.eml > FWD_Vanguard.eml > confirmation.pdf``.

Provenance is first-class: any fact the LLM judge is later allowed to cite must be an
``ExtractedField`` with a ``ProvenanceSpan`` pointing back to the verbatim source. That is
what makes the agent's reasoning auditable rather than hallucinated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceType(str, Enum):
    """How a leaf document was sniffed (by magic bytes, never by file extension)."""

    PDF_NATIVE = "pdf_native"        # PDF with a real text layer
    PDF_SCANNED = "pdf_scanned"      # image-only PDF; needs OCR
    XLSX = "xlsx"
    IMAGE = "image"                  # JPG/PNG photo; needs OCR
    EMAIL = "email"                  # a nested message/rfc822 (.eml attached to an email)
    EMAIL_BODY = "email_body"        # the text/plain (or html) body of a message
    ARCHIVE = "archive"              # a ZIP container
    TEXT = "text"
    UNKNOWN = "unknown"


class FieldKind(str, Enum):
    """The kinds of normalized facts we extract from documents for criterion checking."""

    DATE = "date"
    PERIOD = "period"
    ENTITY = "entity"
    MONEY = "money"
    COUNT = "count"
    SIGNATURE = "signature"
    STANDARD = "standard"            # e.g. "ASC 606", "ASC 842"
    ACCOUNT = "account"              # a bank / GL account reference
    OTHER = "other"


@dataclass(frozen=True)
class ProvenanceSpan:
    """A precise, verbatim pointer back into a source document.

    ``locator`` is a human-readable coordinate within the document (e.g. ``"page 2"``,
    ``"sheet 'Entity Mapping'!B4"``, ``"chars 120-180"``). ``snippet`` is the exact text
    as it appears in the source; the judge may only cite facts backed by such a snippet.
    """

    doc_id: str
    locator: str
    snippet: str


@dataclass
class ExtractedField:
    """A single normalized fact pulled from a document, with its provenance."""

    kind: FieldKind
    value: object
    provenance: ProvenanceSpan
    confidence: float = 1.0          # <1.0 for OCR / fuzzy extractions -> UNVERIFIABLE-leaning


@dataclass
class PIIFinding:
    """A detected piece of PII (SSN, DOB, bank account, ...) requiring redaction."""

    pii_type: str
    provenance: ProvenanceSpan
    redacted: bool = False


@dataclass
class Sheet:
    """One worksheet of an XLSX.

    ``state`` mirrors openpyxl's sheet state (``visible`` / ``hidden`` / ``veryHidden``);
    the agent deliberately reads hidden sheets so the held-out "hidden tab" trap cannot
    conceal data from the verifier.
    """

    name: str
    state: str = "visible"
    text: str = ""                   # flattened cell text for search / OCR-parity
    rows: list[list[object]] = field(default_factory=list)


@dataclass
class Document:
    """A normalized leaf artifact reached by the recursion engine.

    ``doc_id`` is the SHA-256 of the raw bytes, so an identical file sent in two threads
    resolves to one document (defeating double-counting). ``raw_bytes`` is retained only
    until parsing; parsers populate ``text`` / ``sheets`` / ``extracted_fields``.
    """

    doc_id: str
    filename: str
    container_chain: list[str]
    sniffed_type: SourceType = SourceType.UNKNOWN
    declared_media_type: str | None = None      # what the container *claimed* (may lie)
    size_bytes: int = 0
    raw_bytes: bytes | None = None

    # Populated by Phase-2 parsers:
    text: str = ""
    ocr_used: bool = False
    sheets: list[Sheet] = field(default_factory=list)
    extracted_fields: list[ExtractedField] = field(default_factory=list)
    pii_findings: list[PIIFinding] = field(default_factory=list)

    @property
    def chain_str(self) -> str:
        """Human-readable container path, e.g. ``a.eml > b.eml > c.pdf``."""
        return " > ".join(self.container_chain)
