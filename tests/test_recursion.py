"""Self-contained tests for the recursion engine (no external fixtures required).

Builds a synthetic email carrying (a) a ZIP of two PDFs and (b) a forwarded ``.eml``
mislabeled as ``application/octet-stream`` that itself contains a PDF — the two nesting
patterns the real audit mailbox uses. Asserts the engine reaches every leaf with the right
container chain and de-duplicates identical content by hash.

Runnable directly (``python -m tests.test_recursion``) or under pytest.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from email.message import EmailMessage

from pbc_agent.ingest.extract import extract_documents
from pbc_agent.ingest.mailbox import LoadedEmail
from pbc_agent.model.documents import SourceType
from pbc_agent.model.messages import Message

PDF_A = b"%PDF-1.4\nAAA invoice one\n%%EOF"
PDF_B = b"%PDF-1.4\nBBB invoice two\n%%EOF"
PDF_C = b"%PDF-1.4\nCCC vanguard confirmation\n%%EOF"


def _zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _inner_eml() -> bytes:
    m = EmailMessage()
    m["From"] = "Vanguard <no-reply@vanguard.example>"
    m["To"] = "hr@client.example"
    m["Subject"] = "401(k) confirmation"
    m.set_content("Total employer match contributions: $922,400.00\n")
    m.add_attachment(PDF_C, maintype="application", subtype="pdf",
                     filename="vanguard_conf.pdf")
    return m.as_bytes()


def _outer_email() -> LoadedEmail:
    m = EmailMessage()
    m["From"] = "Jenna <jenna@client.example>"
    m["Subject"] = "Cutoff + 401k"
    m.set_content("See attached.")
    m.add_attachment(_zip_of({"Invoice_A.pdf": PDF_A, "Invoice_B.pdf": PDF_B}),
                     maintype="application", subtype="zip", filename="AP_Cutoff.zip")
    # A forwarded email deliberately mislabeled as octet-stream (real-world smuggling).
    m.add_attachment(_inner_eml(), maintype="application", subtype="octet-stream",
                     filename="FWD_Vanguard.eml")
    msg = Message(source_path="thread01_msg01.eml", message_id="<a@x>", in_reply_to=None,
                  references=[], subject="Cutoff + 401k", sender=None, to=[], cc=[],
                  date=None, body_text="See attached.")
    return LoadedEmail(message=msg, raw=m)


def test_zip_entries_are_reached():
    ext = extract_documents([_outer_email()])
    chains = {" > ".join(o.container_chain) for o in ext.occurrences}
    assert "thread01_msg01.eml > AP_Cutoff.zip > Invoice_A.pdf" in chains
    assert "thread01_msg01.eml > AP_Cutoff.zip > Invoice_B.pdf" in chains


def test_nested_eml_recurses_even_when_octet_stream():
    ext = extract_documents([_outer_email()])
    chains = {" > ".join(o.container_chain) for o in ext.occurrences}
    # The forwarded email is recognised as an email...
    assert any(c.endswith("FWD_Vanguard.eml") for c in chains)
    # ...its body is captured...
    assert any(c.endswith("FWD_Vanguard.eml > (body)") for c in chains)
    # ...and the PDF one level deeper inside it is reached.
    assert "thread01_msg01.eml > FWD_Vanguard.eml > vanguard_conf.pdf" in chains


def test_email_body_carries_evidence_text():
    ext = extract_documents([_outer_email()])
    bodies = [d for d in ext.documents.values()
              if d.sniffed_type is SourceType.EMAIL_BODY]
    assert any("922,400" in d.text for d in bodies)


def test_dedup_by_content_hash():
    # The same file sent in two separate messages collapses to one document.
    e1, e2 = _outer_email(), _outer_email()
    e2.message.source_path = "thread05_msg04.eml"
    ext = extract_documents([e1, e2])
    doc_id = hashlib.sha256(PDF_A).hexdigest()
    assert doc_id in ext.documents
    occ = [o for o in ext.occurrences if o.doc_id == doc_id]
    assert len({o.message_source for o in occ}) == 2   # appears in both threads
    # but only one unique Document exists for it
    assert sum(1 for d in ext.documents.values() if d.doc_id == doc_id) == 1


def test_xlsx_distinguished_from_plain_zip():
    from pbc_agent.ingest.extract import _sniff
    xlsx_bytes = _zip_of({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<w/>"})
    plain_zip = _zip_of({"a.txt": b"hi"})
    assert _sniff(xlsx_bytes, "mystery.bin") is SourceType.XLSX
    assert _sniff(plain_zip, "mystery.bin") is SourceType.ARCHIVE


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
