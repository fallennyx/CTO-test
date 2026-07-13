"""Ingest: load a mailbox, assemble threads, and recursively extract attachments."""

from pbc_agent.ingest.extract import ExtractionResult, extract_documents
from pbc_agent.ingest.mailbox import LoadedEmail, load_mailbox
from pbc_agent.ingest.threading import assemble_threads

__all__ = [
    "ExtractionResult",
    "LoadedEmail",
    "assemble_threads",
    "extract_documents",
    "load_mailbox",
]
