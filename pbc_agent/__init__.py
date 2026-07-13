"""PBC Agent — an audit "Prepared-By-Client" tracking agent.

Given an audit team's mailbox, decide for every requested document whether the client
actually delivered what was asked (verified against acceptance criteria, not filenames),
and draft grouped follow-ups for whatever is still open.

Design: a deterministic-first pipeline (ingest / parse / OCR / extract / normalize / route)
with an LLM invoked at only two gates — semantic evidence<->item matching, and final status
judgment. See the plan / README for the full architecture.
"""

__version__ = "0.1.0"
