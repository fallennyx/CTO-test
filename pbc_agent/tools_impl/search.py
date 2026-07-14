"""Candidate matching: which PBC items might a piece of evidence satisfy?

Cheap, deterministic, $0 — a BM25 lexical index over the PBC items (description + criteria +
category), queried with a compact signature of the evidence (filename tokens + a text
excerpt + extracted standards/entities). This is the "route models by cost" first pass: it
narrows 30 items to a handful of candidates so the expensive LLM confirmation (or the
deterministic verifier) only runs on a short list. A weak ``PBC-NN`` filename/if-mentioned
prior nudges ranking but never decides — item numbering is not trustworthy.
"""

from __future__ import annotations

import re

from pbc_agent.model.criteria import PBCItem

_TOKEN = re.compile(r"[a-z0-9]+")
_PBC_REF = re.compile(r"pbc[-\s]?(\d{1,2})", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


class CandidateMatcher:
    """A small BM25 index over PBC items."""

    def __init__(self, items: list[PBCItem]):
        self.items = items
        self._docs = [self._item_text(it) for it in items]
        self._corpus = [_tokenize(d) for d in self._docs]
        self._bm25 = self._build_bm25(self._corpus)

    @staticmethod
    def _item_text(it: PBCItem) -> str:
        crit = " ".join(c.raw for c in it.criteria)
        return f"{it.category} {it.description} {crit}"

    @staticmethod
    def _build_bm25(corpus):
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return None
        return BM25Okapi(corpus) if corpus else None

    def match(self, query: str, top_k: int = 4) -> list[tuple[str, float]]:
        """Return up to ``top_k`` ``(item_id, score)`` candidates for a query string."""
        q = _tokenize(query)
        if self._bm25 is not None and q:
            scores = list(self._bm25.get_scores(q))
        else:
            scores = [self._overlap(q, doc) for doc in self._corpus]

        # Weak prior: an explicit PBC-NN mention gets a small, non-decisive bump.
        for ref in _PBC_REF.findall(query):
            wanted = f"PBC-{int(ref):02d}"
            for i, it in enumerate(self.items):
                if it.id == wanted:
                    scores[i] += max(scores) * 0.25 + 0.5 if scores else 1.0

        ranked = sorted(range(len(self.items)), key=lambda i: scores[i], reverse=True)
        out = [(self.items[i].id, float(scores[i])) for i in ranked[:top_k] if scores[i] > 0]
        return out

    @staticmethod
    def _overlap(q: list[str], doc: list[str]) -> float:
        if not q:
            return 0.0
        dset = set(doc)
        return sum(1 for t in q if t in dset) / len(q)


def evidence_query(filename: str, text: str, standards: list[str] | None = None) -> str:
    """Build a compact BM25 query signature from an evidence document."""
    parts = [filename.replace("_", " ").replace("-", " ")]
    if standards:
        parts.append(" ".join(standards))
    if text:
        parts.append(text[:600])
    return " ".join(parts)
