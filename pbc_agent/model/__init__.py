"""Typed domain model for the PBC agent.

These objects are the contract between pipeline stages. Every stage is a pure function
over them, which keeps the system testable and replayable. Phase 1 uses standard-library
dataclasses (no third-party dependency); LLM-facing schemas are layered on later.
"""

from pbc_agent.model.documents import (
    Document,
    ExtractedField,
    FieldKind,
    PIIFinding,
    ProvenanceSpan,
    Sheet,
    SourceType,
)

__all__ = [
    "Document",
    "ExtractedField",
    "FieldKind",
    "PIIFinding",
    "ProvenanceSpan",
    "Sheet",
    "SourceType",
]
