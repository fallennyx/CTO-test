# PBC Agent — Audit "Prepared-By-Client" Tracking Agent

An AI agent that reads an audit team's mailbox and decides, for every requested document,
whether the client **actually delivered what was asked** — verified against the item's
acceptance criteria, not the filename — then drafts grouped follow-ups for whatever is still
open.

> **The hard part isn't classification, it's skepticism.** A naive "the file arrived →
> COMPLETE" agent fails the moment a client sends a prior-year file, a trial balance missing
> an entity, a ZIP that looks full but is short a few invoices, or a `..._signed.pdf` that
> was never signed. This system is built as a **content-verifying skeptic**: it checks
> evidence against structured criteria and treats filenames as untrusted.

## What it does

- **Ingests** a mailbox (`.mbox` / `.eml`) with attachments in every real format: native PDF,
  scanned PDF (OCR), single/multi-tab XLSX, JPG phone photos (OCR), nested ZIPs, and even a
  forwarded email attached inside another email.
- **Matches** delivered evidence to the PBC (Prepared-By-Client) list **semantically** — asks
  are often phrased by topic, not by item number.
- **Verifies** each item against its acceptance criteria (period, entity coverage, signature,
  counts/sample size, thresholds, ASC 606/842, ...) and assigns a status:
  `COMPLETE` · `PARTIALLY_COMPLETE` · `INSUFFICIENT` · `NOT_STARTED`, with a defensible,
  provenance-backed reasoning string.
- **Tracks state** across threads and time (a file sent in one thread answers a request in
  another; a rescheduled item updates its due date; a re-sent file is one delivery, not two).
- **Drafts follow-ups** grouped by owner, minimizing round-trips.
- **Stays cheap** — a deterministic-first pipeline reserves the LLM for just two gates
  (semantic matching, status judgment), targeting well under $0.75 per mailbox.

## Architecture

Deterministic pipeline for ingest / parse / OCR / extract / normalize / route; the LLM is
called only to (a) confirm semantic evidence↔item matches and (b) judge status and write
grounded reasoning. Every stage is a pure function over typed objects, so it's testable and
replayable.

```
mailbox load → thread assembly → recursive attachment extraction → per-format parse (+OCR)
  → normalized Evidence + ExtractedFields (with provenance) → PBC items w/ structured criteria
  → candidate match → LLM match-confirm → deterministic verifier → LLM judge
  → cross-thread/temporal reconciliation → status aggregation → follow-up grouping → JSON + HTML
```

See [`pbc_agent/`](pbc_agent/) for the module layout.

## Status

| Phase | Scope | State |
|---|---|---|
| **1. Skeleton + ingest** | Typed model, engagement config, PBC-list parser, mailbox/threading, **recursive attachment extraction** (nested ZIP + `.eml`-in-`.eml`) | ✅ Done |
| 2. Parsers + normalization | Native/scanned PDF + OCR, all-sheets XLSX, JPG OCR, field extraction w/ provenance | ⏳ Next |
| 3. Match + verify + judge | Semantic matching, deterministic criterion checks, LLM judge, cross-thread memory | ⏳ |
| 4. Trap-hardening + dashboard | Supersession, PII gate, trap simulator, eval harness, HTML dashboard | ⏳ |

## Quickstart (Phase 1)

Phase 1 runs on the **standard library alone** — no dependencies to install.

```bash
# Enumerate a bundle: engagement, PBC items, threads, and the full attachment tree.
python -m pbc_agent.cli ingest --bundle data/sample_bundle

# Show the container chain for every nested document (ZIP entries, forwarded-email contents).
python -m pbc_agent.cli ingest --bundle data/sample_bundle --show-chains
```

Example (abridged) output on the sample bundle:

```
ENGAGEMENT   Northwind Beverages, Inc. — FY 2025-07-01 .. 2026-06-30 — 3 entities
PBC LIST     30 items — DocType=30, Period=29, Entity=6, Signature=5, Count=3, ...
MAILBOX      38 messages, 8 threads
EXTRACTION   41 top-level attachments; by type: archive=4, image=2, pdf=39, xlsx=14, email=1
             thread07_msg03.eml > FWD_Vanguard_401k_confirmation.eml > (body)   # email-in-email
             AP_Cutoff_Sample.zip: appears 2x [thread03, thread05]              # re-use detected
```

## Tests

```bash
python -m tests.test_recursion      # self-contained; no fixtures needed
python -m tests.test_bundle_smoke   # skips if the sample bundle isn't present
# (or `pytest` once dev extras are installed)
```

## Data

Sample/engagement data is treated as client IP and is **git-ignored** (`data/`). Place a
bundle at `data/sample_bundle/` to run the commands above.
