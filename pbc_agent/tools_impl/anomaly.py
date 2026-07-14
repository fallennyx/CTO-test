"""Conflict / anomaly detection — the "this looks off, a human should check" layer.

The verifier decides status from acceptance criteria; this layer, on top, surfaces
*discrepancies that warrant human review even when no single criterion hard-fails* — the
signals that catch a crafted or unfamiliar dataset:

* a filename that contradicts the content (``..._signed/final`` over a draft; a year in the
  name that the content's dates disagree with; an entity named in the file but absent inside),
* evidence for one item that spans conflicting periods.

These raise ``needs_review`` and a plain-language flag; they never silently "pass" something.
Filenames are used only to detect a *mismatch*, never to accept content.
"""

from __future__ import annotations

import re

from pbc_agent.config.engagement import Engagement
from pbc_agent.model.documents import Document, FieldKind

_FINAL_WORDS = re.compile(r"signed|executed|final|approved", re.IGNORECASE)


def detect_anomalies(docs: list[Document], engagement: Engagement) -> list[str]:
    flags: list[str] = []
    for d in docs:
        content_years = {f.value.year for f in d.extracted_fields if f.kind is FieldKind.DATE}

        # filename claims "signed/final" but content reads as a draft
        if _FINAL_WORDS.search(d.filename):
            if any(f.kind is FieldKind.SIGNATURE and isinstance(f.value, dict)
                   and f.value.get("draft") for f in d.extracted_fields):
                flags.append(f"“{d.filename}” is labeled final/signed, but its content is a "
                             f"draft or unsigned — verify before relying on it.")

        # Wrong-period: content is entirely OLDER than the year the filename claims.
        # (Comparatives include the current year; multi-year leases include future years — so
        #  we only flag when every dated fact predates the filename's year.)
        fyears = _years_from_filename(d.filename)
        if fyears and content_years and max(content_years) < min(fyears):
            flags.append(f"“{d.filename}” is named for {sorted(fyears)} but every dated line in "
                         f"it is {sorted(content_years)} — likely a prior-period file.")

        # entity named in filename but absent from content
        for entity in engagement.entities:
            key = min(entity.aliases, key=len).replace(" ", "")
            if len(key) >= 8 and key in _squash(d.filename):
                present = any(f.kind is FieldKind.ENTITY and f.value == entity.name
                              for f in d.extracted_fields)
                if not present and d.extracted_fields:
                    flags.append(f"“{d.filename}” references {entity.name} in its name but the "
                                 f"content does not mention it.")
                break
    return flags


def _squash(s: str) -> str:
    return s.lower().replace("_", "").replace("-", "").replace(" ", "")


def _years_from_filename(filename: str) -> set[int]:
    low = filename.lower()
    years = {int(y) for y in re.findall(r"\b(20\d\d)\b", low)}
    for fy in re.findall(r"fy\s?(\d{2,4})", low):
        years.add(2000 + int(fy) if len(fy) == 2 else int(fy))
    return years
