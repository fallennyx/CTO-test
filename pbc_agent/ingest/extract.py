"""The recursion engine — turns messages + attachments into normalized leaf documents.

This is the Phase-1 centerpiece. It walks each message's MIME tree and, critically, recurses
through the containers that real audit mailboxes use:

* **ZIP archives** — customer confirmations, AP-cutoff invoice bundles (recurse into entries).
* **Nested emails** — a forwarded ``.eml`` attached to another email (``message/rfc822``);
  the Vanguard 401(k) confirmation lives one email deep, so we must recurse into it.
* **Nested archives / emails within either** — handled by the same recursion.

Every leaf becomes a :class:`Document` with:

* ``doc_id`` = SHA-256 of its bytes (so the same file re-sent in two threads is ONE document,
  which is how we later avoid double-counting a delivery), and
* ``container_chain`` = the exact nesting path, e.g.
  ``thread07_msg03.eml > FWD_Vanguard_401k_confirmation.eml > vanguard_conf.pdf``.

Type is sniffed from magic bytes, never the filename extension — because in this domain
filenames lie (``..._signed.pdf`` can be unsigned). Text extraction / OCR is deliberately NOT
done here; that is Phase 2. This stage only discovers and identifies artifacts.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy

from pbc_agent.ingest.mailbox import LoadedEmail
from pbc_agent.model.documents import Document, SourceType

_MAX_DEPTH = 8           # guard against pathological nesting / zip quines
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024   # crude zip-bomb guard (total uncompressed)


@dataclass
class Occurrence:
    """One place a document appeared. The same ``doc_id`` may occur more than once."""

    doc_id: str
    filename: str
    container_chain: list[str]
    message_source: str
    is_top_level_attachment: bool


@dataclass
class ExtractionResult:
    #: Unique documents keyed by content hash (re-used files collapse to one entry).
    documents: dict[str, Document] = field(default_factory=dict)
    #: Every appearance of every document (shows re-use across threads).
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def top_level_attachment_count(self) -> int:
        return sum(1 for o in self.occurrences if o.is_top_level_attachment)

    @property
    def leaf_document_count(self) -> int:
        """Unique leaf artifacts (excludes pure containers: archives + nested emails)."""
        return sum(1 for d in self.documents.values()
                   if d.sniffed_type not in (SourceType.ARCHIVE, SourceType.EMAIL))


def extract_documents(loaded: list[LoadedEmail]) -> ExtractionResult:
    """Walk every message and recursively enumerate all contained documents."""
    result = ExtractionResult()
    for item in loaded:
        source = item.message.source_path
        chain_root = [source]
        for part in _attachments(item.raw):
            doc_id = _walk_part(part, chain_root, source, result,
                                depth=0, top_level=True)
            if doc_id and doc_id not in item.message.document_ids:
                item.message.document_ids.append(doc_id)
    return result


# --- Recursion ------------------------------------------------------------------------


def _walk_part(part: EmailMessage, chain_prefix: list[str], message_source: str,
               result: ExtractionResult, depth: int, top_level: bool) -> str | None:
    """Process one MIME attachment part; recurse into containers. Returns its doc_id."""
    name = part.get_filename() or _synth_name(part)
    chain = [*chain_prefix, name]
    ctype = (part.get_content_type() or "").lower()

    if depth > _MAX_DEPTH:
        return None

    # Nested email (forwarded .eml). It may be declared message/rfc822 OR — as happens in
    # real mailboxes — smuggled in as application/octet-stream with a .eml filename. We
    # detect both, because the forwarded email can itself carry the evidence we need.
    if ctype == "message/rfc822":
        inner = _as_email(part)
        if inner is not None:
            return _record_nested_email(inner, name, chain, message_source, result,
                                        depth, top_level)

    data = part.get_payload(decode=True) or b""

    if _is_email_bytes(data, name, ctype):
        inner = _bytes_to_email(data)
        if inner is not None:
            return _record_nested_email(inner, name, chain, message_source, result,
                                        depth, top_level, raw=data)

    sniffed = _sniff(data, name)

    if sniffed is SourceType.ARCHIVE:
        doc = _record(result, data, name, chain, message_source, top_level, sniffed, ctype)
        _walk_zip(data, chain, message_source, result, depth + 1)
        return doc.doc_id

    doc = _record(result, data, name, chain, message_source, top_level, sniffed, ctype)
    return doc.doc_id


def _record_nested_email(inner: EmailMessage, name: str, chain: list[str],
                         message_source: str, result: ExtractionResult, depth: int,
                         top_level: bool, raw: bytes | None = None) -> str:
    """Register a forwarded email as a container and recurse into its contents."""
    data = raw if raw is not None else inner.as_bytes()
    doc = _record(result, data, name, chain, message_source, top_level,
                  SourceType.EMAIL, "message/rfc822")
    _emit_email_body(inner, chain, message_source, result)
    for sub in _attachments(inner):
        _walk_part(sub, chain, message_source, result, depth + 1, top_level=False)
    return doc.doc_id


_EMAIL_HEADER_HINTS = (b"from:", b"received:", b"return-path:", b"message-id:",
                       b"mime-version:", b"date:", b"subject:", b"to:")


def _is_email_bytes(data: bytes, filename: str, ctype: str) -> bool:
    """Recognise a forwarded ``.eml`` even when mislabeled as octet-stream."""
    if ctype == "message/rfc822":
        return True
    if not data:
        return False
    if filename.lower().endswith(".eml"):
        return True
    head = data[:512].lstrip().lower()
    return any(head.startswith(h) for h in _EMAIL_HEADER_HINTS) and b":" in head


def _walk_zip(data: bytes, chain_prefix: list[str], message_source: str,
              result: ExtractionResult, depth: int) -> None:
    if depth > _MAX_DEPTH:
        return
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        if total > _MAX_ARCHIVE_BYTES:
            break
        try:
            entry = zf.read(info)
        except Exception:
            continue
        name = info.filename.split("/")[-1] or info.filename
        chain = [*chain_prefix, name]
        lower = name.lower()

        if lower.endswith(".eml"):
            inner = _bytes_to_email(entry)
            if inner is not None:
                _record(result, entry, name, chain, message_source, False,
                        SourceType.EMAIL, "message/rfc822")
                _emit_email_body(inner, chain, message_source, result)
                for sub in _attachments(inner):
                    _walk_part(sub, chain, message_source, result, depth + 1, False)
                continue

        sniffed = _sniff(entry, name)
        _record(result, entry, name, chain, message_source, False, sniffed, None)
        if sniffed is SourceType.ARCHIVE:
            _walk_zip(entry, chain, message_source, result, depth + 1)


# --- Helpers --------------------------------------------------------------------------


def _record(result: ExtractionResult, data: bytes, filename: str, chain: list[str],
            message_source: str, top_level: bool, sniffed: SourceType,
            declared: str | None) -> Document:
    """Register an occurrence and (de-duplicated by content hash) the document."""
    doc_id = hashlib.sha256(data).hexdigest()
    doc = result.documents.get(doc_id)
    if doc is None:
        doc = Document(
            doc_id=doc_id,
            filename=filename,
            container_chain=list(chain),
            sniffed_type=sniffed,
            declared_media_type=declared,
            size_bytes=len(data),
            raw_bytes=data,
        )
        result.documents[doc_id] = doc
    result.occurrences.append(Occurrence(
        doc_id=doc_id, filename=filename, container_chain=list(chain),
        message_source=message_source, is_top_level_attachment=top_level,
    ))
    return doc


def _emit_email_body(em: EmailMessage, chain: list[str], message_source: str,
                     result: ExtractionResult) -> None:
    """Capture a nested email's body as its own leaf document (evidence text)."""
    body = _body_text(em)
    if not body.strip():
        return
    bchain = [*chain, "(body)"]
    doc = _record(result, body.encode("utf-8", "replace"), "(email body)", bchain,
                  message_source, False, SourceType.EMAIL_BODY, "text/plain")
    if not doc.text:
        doc.text = body


def _attachments(em: EmailMessage):
    try:
        return list(em.iter_attachments())
    except Exception:
        return []


def _as_email(part: EmailMessage) -> EmailMessage | None:
    try:
        content = part.get_content()
    except Exception:
        content = None
    if isinstance(content, EmailMessage):
        return content
    payload = part.get_payload()
    if isinstance(payload, list) and payload and isinstance(payload[0], EmailMessage):
        return payload[0]
    return None


def _bytes_to_email(data: bytes) -> EmailMessage | None:
    try:
        return message_from_bytes(data, policy=default_policy)
    except Exception:
        return None


def _body_text(em: EmailMessage) -> str:
    try:
        part = em.get_body(preferencelist=("plain", "html"))
        content = part.get_content() if part is not None else ""
    except Exception:
        return ""
    return content if isinstance(content, str) else ""


def _synth_name(part: EmailMessage) -> str:
    subtype = (part.get_content_subtype() or "bin")
    return f"unnamed.{subtype}"


def _sniff(data: bytes, filename: str) -> SourceType:
    """Identify a leaf by magic bytes; the filename is only a last-resort tiebreaker."""
    head = data[:8]
    if head.startswith(b"%PDF"):
        return SourceType.PDF_NATIVE          # native vs scanned resolved in Phase 2
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return SourceType.IMAGE
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return SourceType.XLSX if _looks_like_xlsx(data) else SourceType.ARCHIVE
    # Fallback on extension only when magic bytes are inconclusive.
    low = filename.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return SourceType.XLSX
    if low.endswith(".pdf"):
        return SourceType.PDF_NATIVE
    if low.endswith((".jpg", ".jpeg", ".png")):
        return SourceType.IMAGE
    if low.endswith(".zip"):
        return SourceType.ARCHIVE
    if low.endswith((".txt", ".csv")):
        return SourceType.TEXT
    return SourceType.UNKNOWN


def _looks_like_xlsx(data: bytes) -> bool:
    """A .xlsx is a ZIP; distinguish it from a plain archive by its OOXML members."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
    except Exception:
        return False
    return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)
