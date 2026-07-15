# CLAUDE.md — repo guide for future sessions

Read this first; it should save you from re-reading the whole repo.

## What this is

A **PBC (prepared-by-client) email agent** for financial-statement audits (a work-trial build,
spec in `MVP20I.pdf`). It ingests a messy audit inbox (emails + attachments) and maintains a
live, structured **tracker**: for each of the client's requested documents it decides — from the
*contents, not the filename* — whether the client delivered what was asked, with a
PCAOB-defensible reasoning trace, and drafts grouped follow-ups for what's open.

**Working branch:** `claude/cto-test-architecture-8b0y41` (develop + push here).

**Measured results (keep these current if you change behavior):** `pbc eval` = **13/13** offline
AND `pbc eval --live` = **13/13** (real Claude), insufficiency F1 = 1.0, generalization **60/60**
unseen traps, **~$0.19**/inbox live. See `docs/LIVE_RESULTS.md`.

## Architecture (the mental model)

A **native Anthropic tool-use agent loop** drives *dynamic per-email control flow* (the brief
requires an agent, not a hardcoded pipeline). The agent calls **deterministic tools**. Crucially,
the **final verdict is settled by a deterministic reconciliation**, so status is a reproducible
function of the evidence — identical whether Claude or the offline mock drove planning. This is
why live == offline and why it's audit-defensible.

```
inbox → ingest + recursion → [agent loop: per-email plan → parse/OCR → extract → match → verify]
      → deterministic reconciliation (authoritative evidence→item→status)
      → thread caveat pass → follow-up drafting → tracker state + trace → UI / report.json
```

- **The agent contributes:** per-email planning, reschedule detection, caveat-reading, follow-up
  drafting, and the full plan+tool-call trace.
- **Deterministic layers own correctness:** parsing/OCR/extraction (with citations), BM25
  matching, the criterion verifier, reconciliation, PII redaction, anomaly flags, version lineage.

## Module map (`pbc_agent/`)

| Path | Role |
|---|---|
| `agent/loop.py` | **THE loop** (one file). `AgentRunner.run()` → per-email loop, then `_reconcile()` (authoritative matching+verify), then `_caveat_pass()`, then follow-ups. `SYSTEM_PROMPT` here. |
| `agent/tools.py` | 7 native tool schemas + dispatch (`parse_attachment`, `extract_fields`, `match_item`, `verify_item`, `note_schedule`, `draft_followups`, `finish`). |
| `agent/router.py` | model-by-cost (Haiku default, Sonnet on heavy/ambiguous emails). |
| `agent/trace.py` | plan/tool-call/verifier trace. |
| `tools_impl/parse.py` | native PDF (pdfplumber), scanned-PDF auto-detect + OCR (pypdfium2+tesseract), all-sheets XLSX incl. hidden, JPG OCR. Runs PII scan. |
| `tools_impl/extract.py` | citation-backed fields (date/money/entity/signature/standard); document-wide DRAFT/"to be signed" detection. |
| `tools_impl/search.py` | BM25 `CandidateMatcher` + `evidence_query` (filename up-weighted, audit-abbrev expanded). |
| `tools_impl/verify.py` | **the verifier** — per-criterion checks → `Status`. Where status logic lives. |
| `tools_impl/assess.py` | `assess_item` = verify + version + PII + anomaly (shared by loop and tests). |
| `tools_impl/pii.py` `anomaly.py` `version.py` `draft.py` | PII gate; conflict/needs-review; lineage; grouped follow-ups. |
| `ingest/` | mailbox load, threading, **recursion engine** (nested ZIP + `.eml`-in-`.eml`, hash-dedup, magic-byte sniff). |
| `criteria_loader/pbc_list.py` `config/engagement.py` | parse the PBC-list + client-profile PDFs into swappable config (stdlib ASCII85+Flate decoder — no PDF lib for these). |
| `llm/` | `provider.py` (protocol + Budget + `get_provider`), `anthropic_provider.py` (live), `mock_provider.py` (offline, faithful policy), pricing. |
| `state/store.py` | `TrackerState`, `to_report()` (ground-truth-shaped JSON). |
| `webapp/` | FastAPI + self-contained SPA (`static/index.html`): light/dark, status chart, "Why?" panel, follow-ups. |
| `ui/app.py` | lightweight Streamlit UI (alternative). |
| `eval/score.py` `eval/bench.py` `eval/traps/generate.py` | scorer, cost/throughput projection, randomized trap generator. |

## Commands

```bash
pip install -e '.[parse,llm,report,web,dev]'   # + system `tesseract` for OCR
python -m pbc_agent.cli ingest --bundle data/sample_bundle [--show-chains]
python -m pbc_agent.cli run   --bundle data/sample_bundle [--mock] [--max-emails N] [--trace]
python -m pbc_agent.cli web   --bundle data/sample_bundle   # localhost:8000 (127.0.0.1 only)
python -m pbc_agent.cli eval  [--live]      # 13/13 offline and live
python -m pbc_agent.cli bench [--live]      # cost + held-out projection
scripts/smoke_live.sh                        # ~30s live confirmation (needs key)
# tests: python -m tests.<name>  (recursion, tools, traps, generalization, agent, bundle_smoke) or `pytest`
```

## Key decisions (why things are the way they are)

1. **Agent-driven control flow + deterministic verdict.** The brief fails "hardcoded pipelines,"
   so the loop is a real native tool-use agent — but a regulated audit tool needs *reproducible*
   status, so `_reconcile()` makes the verdict deterministic. Best of both; this is what made live
   match offline at 13/13.
2. **Offline mock provider** (`llm/mock_provider.py`) so everything runs with **no API key** (CI,
   eval, UI, demo). `ANTHROPIC_API_KEY` set → live; unset → mock. Same loop code.
3. **Never trust filenames** — type by magic bytes; content decides status; `..._signed.pdf` with
   a DRAFT body is flagged.
4. **Thread caveat-reading** (`loop._caveat_pass`): strong open-item phrases ("still outstanding",
   "under investigation", "to be signed") downgrade items to Under review; "to follow" and
   "immaterial/explained" are ignored; routed to items delivered in the same thread.
5. **Verifier nuances:** entity — naming *some* consolidated entities but missing others FAILs
   (wrong-entity), naming *none* is UNVERIFIABLE (soft); count — a `min_customers` shortfall FAILs
   (partial), a `sample_size` is fine unless the doc *claims* more than present (false-completion
   trap); UNVERIFIABLE soft criteria don't block Complete.
6. **PII redacted before any LLM/UI exposure**; **cost** metered from real API usage (live) or
   grounded prompt-size estimate (offline); hard $5 ceiling + circuit-breaker.

## Conventions & gotchas

- **Secrets:** `data/` and `.env` are git-ignored. **Never commit an API key.** Local runs read a
  `.env` (see `.env.example`). Web app binds `127.0.0.1` only.
- **Model IDs:** `claude-haiku-4-5-20251001` (default), `claude-sonnet-5` (escalation). Pricing in
  `llm/provider.py`.
- **Env quirks:** OCR needs system `tesseract`; PDF rasterization uses `pypdfium2` (no poppler);
  the two spec PDFs are parsed with stdlib (no PDF lib). The proxy CA is already wired via
  `SSL_CERT_FILE` (live API works out of the box). If `cryptography` import breaks, `pip install cffi`.
- **Commit trailers** (every commit): `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and
  the `Claude-Session:` line. Do NOT put the model ID in committed artifacts.
- **Where to change things:** matching → `tools_impl/search.py`; status rules → `tools_impl/verify.py`;
  caveat detection → `loop._caveat_pass`; authoritative assignment → `loop._reconcile`; UI →
  `webapp/static/index.html`. Keep `docs/LIVE_RESULTS.md` numbers honest if behavior changes, and
  re-run `pbc eval` + `python -m tests.test_generalization` (must stay 60/60).

## Recent changes (newest first)

- Deterministic **reconciliation** → live == offline **13/13** (was: live varied 4–10/13 due to
  autonomous matching contamination).
- **Thread caveat-reader** + principled entity/count logic → offline 13/13.
- **Live path verified** end-to-end through the env proxy; `scripts/smoke_live.sh`; graceful
  per-email API-error handling; `--max-emails`/`--trace`.
- **Robustness:** PII gate, anomaly/needs-review, version lineage, **60/60** randomized-trap
  generalization harness.
- **Web app:** bespoke FastAPI+SPA (light/dark, status chart, "Why?" trace); local `.env` support.
- Grounded cost accounting + `pbc bench` projection.
