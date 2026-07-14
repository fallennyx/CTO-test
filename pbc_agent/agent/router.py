"""Model-by-cost routing.

The cheap deterministic tools (BM25 match, verifier, parsers, OCR) carry the load for free.
The LLM is used only to *plan* each email, and it runs on the small/cheap model by default;
we escalate to the larger model only when an email is genuinely harder to reason about — many
attachments, or an unusual/ambiguous request. This is the "route models by cost" requirement,
and the chosen model is recorded per email in the trace.
"""

from __future__ import annotations

from pbc_agent.llm.provider import MODEL_LARGE, MODEL_SMALL

_AMBIGUOUS = ("clarify", "not sure", "which", "instead", "supersede", "replace",
              "correction", "revised", "reissue")


def select_model(email_context: dict) -> str:
    """Pick the planning model for one email based on its complexity."""
    attachments = email_context.get("attachments", [])
    body = (email_context.get("body") or "").lower()
    if len(attachments) >= 4:
        return MODEL_LARGE
    if any(k in body for k in _AMBIGUOUS):
        return MODEL_LARGE
    return MODEL_SMALL


def why(email_context: dict) -> str:
    n = len(email_context.get("attachments", []))
    if select_model(email_context) == MODEL_LARGE:
        return f"escalated to large model ({n} attachments / ambiguous request)"
    return "small model (routine email)"
