# PBC Email Agent

An AI **agent** that lives on an audit inbox and keeps a live, structured **PBC
(prepared-by-client) tracker** current — deciding, for every requested document, whether the
client actually delivered what was asked (verified from **contents, not filenames**), with a
reasoning trace a partner could defend to a PCAOB inspector, and drafting grouped follow-ups
for whatever is still open.

> **The hard part isn't classification, it's skepticism.** A naive "the file arrived →
> Complete" agent fails the moment a client sends a prior-year file, a trial balance missing an
> entity, a ZIP that looks full but is short a few invoices, or a `..._signed.pdf` that was
> never signed. This system is a **content-verifying skeptic**.

📄 **[1-page design](docs/ONEPAGER.md)** · 🎬 **[demo script](docs/DEMO.md)**

## Highlights

- **Native tool-use agent loop** (one file, `agent/loop.py`) with **dynamic per-email control
  flow** — an attachment email runs `parse → extract → match → verify`; a "it's delayed" email
  reschedules and stops. Not a hardcoded pipeline.
- **Deterministic, unit-tested tools** for the heavy lifting: recursive ingest (nested ZIPs,
  `.eml`-in-`.eml`), native + scanned-PDF OCR, all-sheets Excel (incl. hidden tabs), citation-
  backed extraction, BM25 matching, the criterion **verifier**, versioning, and drafting.
- **Auditable**: every status carries a plan + tool-call + verifier trace, and every fact a
  page/sheet citation. Reasoning is composed only from checks that passed/failed — no invented
  numbers.
- **Robust to unseen / adversarial data**: a **PII gate** (detect + redact + flag), a
  **conflict/anomaly detector** (filename-vs-content, wrong period, low confidence →
  `needs_review`), and **version supersession** with lineage. A generalization harness randomly
  generates dozens of unseen traps each run and asserts **100% are caught** (60/60).
- **Cheap & bounded**: BM25 matching and all parsing are $0; only per-email planning hits the
  LLM (Haiku, escalating to Sonnet when hard). **~$0.14** per sample inbox, hard **$5** ceiling.
- **Bespoke localhost web app** (FastAPI + a self-contained SPA, `pbc web`): a polished tracker
  with a slide-in **"Why?"** panel, citations, needs-review/PII/version badges, and follow-up
  review. (A lightweight Streamlit UI, `pbc ui`, is also included.)
- **Config-driven, zero hardcoding**: the PBC list PDF + client profile are the config; swap
  them at review and re-run.

## Quickstart

Phase-1 ingest needs no dependencies. For the full agent + UI:

```bash
pip install -e '.[parse,llm,report,web,dev]'   # deps (also: system `tesseract` for OCR)

# Run the agent over a mailbox -> tracker JSON (offline mock, no key needed)
python -m pbc_agent.cli run --bundle data/sample_bundle --mock

# Launch the bespoke localhost web app  ->  http://127.0.0.1:8000
python -m pbc_agent.cli web --bundle data/sample_bundle
# (or the lightweight Streamlit UI:  python -m pbc_agent.cli ui --bundle data/sample_bundle)

# Score against labeled cases (accuracy / insufficiency-F1 / tool-sequence / cost)
python -m pbc_agent.cli eval --bundle data/sample_bundle

# Prove generalization: dozens of randomly-generated unseen traps, all caught
python -m tests.test_generalization

# Just enumerate a mailbox (recursion engine)
python -m pbc_agent.cli ingest --bundle data/sample_bundle --show-chains
```

Set `ANTHROPIC_API_KEY` to switch from the offline mock to **real native tool-use** — the loop
code is identical.

## How it works

```
inbox → ingest+recursion → [agent loop: plan → parse → extract → match → verify → draft]
      → tracker state + trace → Streamlit UI + report.json
```

The agent (Claude, native tool use) decides *what to do per email*; the tools (deterministic)
*do it*. See the [1-page design](docs/ONEPAGER.md) for the diagram, model-per-step, tool
schemas, guardrails, eval, cost, and 10/100-concurrent scaling notes.

## Layout

```
pbc_agent/
  agent/       loop.py (the tool-use loop) · tools.py · router.py · trace.py
  tools_impl/  parse · extract · search · verify · version · draft   (deterministic tools)
  ingest/      mailbox · threading · extract (recursion engine)
  criteria_loader/ · config/ · model/ · state/ · llm/ · eval/ · ui/
tests/         recursion · tools · traps · agent · bundle smoke
docs/          ONEPAGER.md · DEMO.md
```

## Tests

```bash
python -m tests.test_recursion    # recursion engine (self-contained)
python -m tests.test_tools        # extract / verify / version / search
python -m tests.test_traps        # adversarial trap simulator
python -m tests.test_agent        # end-to-end agent (offline)
# or `pytest`
```

## Data

Sample/engagement data is client IP and is **git-ignored** (`data/`). Place a bundle at
`data/sample_bundle/` (Client_Profile.pdf, PBC_List*.pdf, and an `emails/` dir or `.mbox`).

## Status

| Phase | Scope | State |
|---|---|---|
| 1. Ingest + recursion | mailbox/threading, nested ZIP + `.eml`-in-`.eml`, config parsers | ✅ |
| 2. Tools | PDF/OCR/XLSX parse, cited extraction, matching, verifier, versioning, drafting | ✅ |
| 3. Agent loop | native tool-use, per-email planning, cost router, trace, state, `pbc run` | ✅ |
| 4. UI + evals + docs | Streamlit UI, eval harness, trap simulator, 1-pager, demo script | ✅ |
| Stretch | multitenancy (per-engagement state isolation) | ◻︎ noted in one-pager |
