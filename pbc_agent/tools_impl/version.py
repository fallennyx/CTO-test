"""Document versioning & lineage.

When a client sends `Draft_Financials.xlsx`, then `Financials_v2.xlsx`, then
`Financials_Final_v3_REAL.xlsx` for the same underlying deliverable, only the latest is
"current" — but the lineage must be kept so a reviewer can see what superseded what. This is
deliberately lightweight (the brief: "don't over-engineer this"): group by a version-stripped
filename stem, then rank within a group by explicit version markers and delivery recency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_VERSION_TOKENS = re.compile(
    r"\b(v\d+|version\s*\d+|final|real|draft|copy|updated|revised|rev\d*|clean)\b",
    re.IGNORECASE)
_DATE_TOKENS = re.compile(r"\d{4}[-_]?\d{2}[-_]?\d{2}|\d{1,2}[-_]\d{1,2}[-_]\d{2,4}")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_VNUM = re.compile(r"v(?:ersion)?\s*(\d+)", re.IGNORECASE)


@dataclass
class VersionGroup:
    key: str
    latest_doc_id: str
    latest_filename: str
    superseded: list[str] = field(default_factory=list)   # filenames, oldest→newest
    version_label: str = "v1"


def base_key(filename: str) -> str:
    """A stable identity for a deliverable, independent of *version* decoration.

    Dates are deliberately KEPT: two differently-dated files (e.g. board minutes from
    different meetings) are distinct deliverables, not versions of one — merging them would
    hide evidence. Only explicit version markers (v1/v2/final/draft/…) are stripped, so a
    ``Final_v3_REAL`` groups with its earlier ``v1``/``v2`` but never with a different date.
    """
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"[_\-]+", " ", stem)          # underscores are \w, so split first...
    stem = _VERSION_TOKENS.sub(" ", stem)        # ...then \b version-token removal works
    return _NONALNUM.sub("", stem.lower())


def _rank(filename: str, when: datetime | None) -> tuple:
    low = filename.lower()
    vnum = int(_VNUM.search(low).group(1)) if _VNUM.search(low) else 0
    finality = (2 if "final" in low or "real" in low else
                (-1 if "draft" in low else 0))
    ts = when.timestamp() if when else 0.0
    return (finality, vnum, ts)


def group_versions(docs: list[tuple[str, str, datetime | None]]) -> list[VersionGroup]:
    """Group ``(doc_id, filename, delivered_at)`` tuples into version lineages.

    Returns one :class:`VersionGroup` per underlying deliverable, newest as ``latest``.
    """
    buckets: dict[str, list[tuple[str, str, datetime | None]]] = {}
    for doc_id, filename, when in docs:
        buckets.setdefault(base_key(filename), []).append((doc_id, filename, when))

    groups: list[VersionGroup] = []
    for key, members in buckets.items():
        members.sort(key=lambda m: _rank(m[1], m[2]))
        latest = members[-1]
        superseded = [m[1] for m in members[:-1]]
        label = f"v{len(members)}" if len(members) > 1 else "v1"
        groups.append(VersionGroup(
            key=key, latest_doc_id=latest[0], latest_filename=latest[1],
            superseded=superseded, version_label=label))
    return groups
