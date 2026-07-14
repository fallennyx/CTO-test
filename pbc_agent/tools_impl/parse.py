"""Document parsing tools: native PDF, scanned PDF (OCR), XLSX, images, email bodies.

``parse_document`` is the dispatcher the agent calls. It fills a :class:`Document`'s ``text``
/ ``pages`` / ``sheets`` and decides native-vs-scanned for PDFs by checking whether the text
layer is substantive; if not, it falls back to OCR. Parsing is idempotent (``doc.parsed``).

Citations: PDF facts cite ``page N``; XLSX facts cite ``sheet 'Name'!A1``; images cite the
image. Extraction (``extract.py``) turns these into precise ``ProvenanceSpan``s.
"""

from __future__ import annotations

import io

from pbc_agent.model.documents import Document, Sheet, SourceType

# Heuristic: a native PDF page should yield at least this many characters of real text,
# otherwise we treat it as scanned and OCR it.
_MIN_NATIVE_CHARS_PER_PAGE = 20


def parse_document(doc: Document) -> Document:
    """Parse ``doc`` in place according to its sniffed type. Idempotent."""
    if doc.parsed:
        return doc
    try:
        if doc.sniffed_type in (SourceType.PDF_NATIVE, SourceType.PDF_SCANNED):
            _parse_pdf(doc)
        elif doc.sniffed_type is SourceType.XLSX:
            _parse_xlsx(doc)
        elif doc.sniffed_type is SourceType.IMAGE:
            _parse_image(doc)
        elif doc.sniffed_type in (SourceType.EMAIL_BODY, SourceType.TEXT):
            if not doc.text and doc.raw_bytes:
                doc.text = doc.raw_bytes.decode("utf-8", "replace")
            doc.pages = [doc.text]
        # ARCHIVE / EMAIL / UNKNOWN: nothing to parse at this level.
        _scan_pii(doc)
    finally:
        doc.parsed = True
    return doc


def _scan_pii(doc: Document) -> None:
    """Detect PII as soon as text exists, so it can be redacted before any LLM/UI exposure."""
    try:
        from pbc_agent.tools_impl.pii import scan_document
        scan_document(doc)
    except Exception:
        pass


# --- PDF ------------------------------------------------------------------------------


def _parse_pdf(doc: Document) -> None:
    pages = _pdf_native_pages(doc.raw_bytes or b"")
    substantive = sum(len(p.strip()) for p in pages)
    if pages and substantive >= _MIN_NATIVE_CHARS_PER_PAGE * max(1, len(pages)) // 2:
        doc.sniffed_type = SourceType.PDF_NATIVE
        doc.pages = pages
        doc.text = "\n".join(pages)
        return
    # Sparse/empty text layer -> scanned. OCR each rasterized page.
    ocr_pages = _ocr_pdf_pages(doc.raw_bytes or b"")
    if ocr_pages:
        doc.sniffed_type = SourceType.PDF_SCANNED
        doc.ocr_used = True
        doc.pages = ocr_pages
        doc.text = "\n".join(ocr_pages)
    else:
        doc.pages = pages
        doc.text = "\n".join(pages)


def _pdf_native_pages(data: bytes) -> list[str]:
    try:
        import pdfplumber
    except ImportError:
        return []
    out: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                out.append(page.extract_text() or "")
    except Exception:
        return []
    return out


def _ocr_pdf_pages(data: bytes) -> list[str]:
    """Rasterize each page (pypdfium2, no system deps) and OCR it (tesseract)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out: list[str] = []
    try:
        pdf = pdfium.PdfDocument(data)
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=2.0)   # ~144 DPI
            pil = bitmap.to_pil()
            out.append(_ocr_image(pil))
    except Exception:
        return out
    return out


# --- XLSX -----------------------------------------------------------------------------


def _parse_xlsx(doc: Document) -> None:
    try:
        import openpyxl
    except ImportError:
        return
    try:
        wb = openpyxl.load_workbook(io.BytesIO(doc.raw_bytes or b""),
                                    data_only=True, read_only=False)
    except Exception:
        return
    sheets: list[Sheet] = []
    text_parts: list[str] = []
    for ws in wb.worksheets:
        rows: list[list[object]] = []
        lines: list[str] = []
        for row in ws.iter_rows(values_only=True):
            values = [c for c in row]
            rows.append(list(values))
            cells = [str(c) for c in values if c is not None]
            if cells:
                lines.append("\t".join(cells))
        state = getattr(ws, "sheet_state", "visible")
        sheet = Sheet(name=ws.title, state=state, text="\n".join(lines), rows=rows)
        sheets.append(sheet)
        tag = "" if state == "visible" else f" [{state}]"
        text_parts.append(f"# Sheet: {ws.title}{tag}\n{sheet.text}")
    doc.sheets = sheets
    doc.text = "\n\n".join(text_parts)


# --- Images ---------------------------------------------------------------------------


def _parse_image(doc: Document) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        img = Image.open(io.BytesIO(doc.raw_bytes or b""))
    except Exception:
        return
    text = _ocr_image(img)
    doc.ocr_used = True
    doc.text = text
    doc.pages = [text]


def _ocr_image(pil_image) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    try:
        return pytesseract.image_to_string(pil_image) or ""
    except Exception:
        return ""


def ocr_available() -> bool:
    """Whether a working tesseract binary is present (for graceful degradation)."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False
