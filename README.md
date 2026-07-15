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

📄 **[1-page design](docs/ONEPAGER.md)** · 📊 **[measured results](docs/LIVE_RESULTS.md)** · 🎬 **[demo script](docs/DEMO.md)**

**Results:** 13/13 status accuracy **both** offline and live (real Claude), insufficiency F1 = 1.0,
**60/60** unseen adversarial traps caught, ~$0.19 per inbox.

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
  LLM (Haiku, escalating to Sonnet when hard). **~$0.6** per sample inbox (uncached upper-bound
  estimate); the ~90-email held-out projects to **~$1.4**, hard **$5** ceiling. See `pbc bench`.
- **Bespoke localhost web app** (FastAPI + a self-contained SPA, `pbc web`): a polished tracker
  with a slide-in **"Why?"** panel, citations, needs-review/PII/version badges, and follow-up
  review. (A lightweight Streamlit UI, `pbc ui`, is also included.)
- **Config-driven, zero hardcoding**: the PBC list PDF + client profile are the config; swap
  them at review and re-run.

## Quickstart (run it locally in 2 commands)

```bash
pip install -e '.[parse,llm,report,web,dev]'   # deps (also: system `tesseract` for OCR)
python -m pbc_agent.cli web                     # → http://127.0.0.1:8000  (localhost only)
```

Then in the browser:

1. **📥 New audit** — drop in a `.zip` of your audit bundle (a **PBC-list PDF**, a
   **client-profile PDF**, and the mailbox — an `emails/` folder of `.eml` files or a `.mbox`).
   It runs entirely on your machine and shows the live tracker with a "Why?" trace + citations
   for every request.
2. **🧪 Live tests** — paste your `ANTHROPIC_API_KEY` (held in memory only, never written to
   disk) and watch the engine catch fresh, randomly-generated adversarial documents in real time.
   No key → the offline demo engine runs the same cases.

No API key and no data are required to start — with a key the app uses **real Claude**; without
one it uses a faithful offline engine. The tracker verdict is identical either way.

### Command line (optional)

```bash
python -m pbc_agent.cli eval [--live]     # score vs labeled cases — 13/13 offline and live
python -m pbc_agent.cli bench [--live]    # measured cost + held-out projection
python -m tests.test_generalization       # 60/60 randomly-generated unseen traps caught
scripts/smoke_live.sh                      # ~30s live confirmation (needs a key)
# run/ingest a bundle directly:  python -m pbc_agent.cli run --bundle <path> [--mock]
```

`ANTHROPIC_API_KEY` (env or a local git-ignored `.env`) switches the whole system from the
offline mock to real native tool-use — the loop code is identical.

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
