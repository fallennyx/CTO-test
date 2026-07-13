"""Dependency-free text extraction for ReportLab-generated PDFs.

The engagement spec files in this project (``PBC_List_FY2026.pdf``, ``Client_Profile.pdf``)
are produced by ReportLab, which lays out each visual line as one text-show operator inside
``ASCII85Decode``+``FlateDecode`` content streams. That regular structure lets us pull clean,
line-by-line text with only the standard library — no pdfplumber needed for the spec PDFs.

General attachment PDFs (native/scanned) are handled separately by the Phase-2 parsers; this
util is intentionally narrow and returns "" when it does not recognise the structure.
"""

from __future__ import annotations

import base64
import re
import zlib

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
_STRING_RE = re.compile(rb"\((?:[^()\\]|\\.)*\)", re.DOTALL)

# Common WinAnsi/latin-1 punctuation that ReportLab emits as raw high bytes.
_PUNCT = {
    "\x91": "'", "\x92": "'", "\x93": '"', "\x94": '"',
    "\x96": "-", "\x97": "—", "\x85": "...", "\xa0": " ",
}


def _unescape(s: bytes) -> str:
    out = s[1:-1]  # strip the surrounding parens
    out = out.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
    # Octal escapes like \226
    out = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), out)
    text = out.decode("latin1")
    for bad, good in _PUNCT.items():
        text = text.replace(bad, good)
    return text


def extract_text(data: bytes) -> str:
    """Return the document text, one visual line per newline, or "" if unrecognised."""
    lines: list[str] = []
    for m in _STREAM_RE.finditer(data):
        raw = m.group(1).strip(b"\r\n")
        decoded = _decode_stream(raw)
        if decoded is None:
            continue
        for sm in _STRING_RE.finditer(decoded):
            lines.append(_unescape(sm.group(0)))
    return "\n".join(lines)


def _decode_stream(raw: bytes) -> bytes | None:
    """Try ASCII85+Flate, then plain Flate, then raw. Return None if none apply."""
    # ASCII85 (ReportLab wraps content in <~ ... ~> or bare, terminated by ~>).
    try:
        return zlib.decompress(base64.a85decode(raw, adobe=True))
    except Exception:
        pass
    try:
        return zlib.decompress(raw)
    except Exception:
        pass
    # Some ReportLab streams are ASCII85 without the adobe framing.
    try:
        return zlib.decompress(base64.a85decode(raw))
    except Exception:
        return None
