# 10-Minute Demo / Loom Script

Everything below runs cold from a clean checkout. Have the sample bundle at
`data/sample_bundle/` and (for the live segment) `ANTHROPIC_API_KEY` exported.

## 0. Setup (before recording)
```bash
pip install -e '.[parse,llm,report,dev]'      # deps
python -m pbc_agent.cli run --bundle data/sample_bundle --mock   # warm the cache
```

## 1. The problem (30s)
Auditors chase a 40-item PBC list across dozens of email threads with attachments named
`scan001.pdf` and `Final_v3_REAL.xlsx`. Nobody knows what's received, outstanding, or
defensible. Our agent lives on the inbox and keeps the tracker current.

## 2. The UI, as a partner sees it (2 min)  — `python -m pbc_agent.cli web`  (→ localhost:8000)
- Top line: "20 of 30 complete; 3 need follow-up." Status pills + progress.
- Open **PBC-01 → "Why?"**: plain story + the acceptance checks, each with a **citation to the
  'Entity Mapping' sheet**. This is content, not the filename.
- Open a **DRAFT management rep letter** item → **Under review**, because the body says
  "TO BE SIGNED" — the filename said `_signed`. *We never trust filenames.*
- **Follow-ups tab**: one grouped email per recipient, approve/edit/reject.

## 3. The agent loop — the part they grade (4 min)  — open `pbc_agent/agent/loop.py`
- One file. For each email: build context → Claude **plans and calls tools** → tracker updates.
- Show **two similar emails, different paths** (in a trace or `--mock` run):
  - Attachment email → `parse_attachment → extract_fields → match_item → verify_item → finish`.
  - Text-only "expect PBC-14 by July 14" → `note_schedule → finish` (reschedule, not late).
- Show **`agent/tools.py`** (7 native tools) and **`tools_impl/verify.py`** (the deterministic
  verifier — the crown jewel) and **`agent/router.py`** (Haiku default, Sonnet on hard emails).

## 4. Trust & robustness (2 min)  — "it catches data it's never seen"
- `python -m pbc_agent.cli eval` → status accuracy, **insufficiency F1**, tool-sequence match, cost.
- `python -m tests.test_generalization` → **randomly generates 60 unseen traps and catches 100%**
  (wrong period/entity, unsigned, short sample, leaked PII, misleading filename).
- In the UI, open an item with a **⚠ needs-review** or **🔒 PII** badge — the agent flags conflicts
  and redacts PII rather than rubber-stamping.
- Cost meter: **$0.14** this inbox, ceiling $5.

## 5. Live, cold, on unseen data (1 min)
```bash
export ANTHROPIC_API_KEY=...      # switches from mock to real native tool-use
python -m pbc_agent.cli run --bundle <HELD_OUT_BUNDLE>
python -m pbc_agent.cli ui --bundle <HELD_OUT_BUNDLE>
```
Nothing is hardcoded to the client — the PBC list PDF + profile are the config; swap them and
re-run. Close on a trapped item in the "Why?" view: *"naive says Complete; we say Insufficient
because the parent entity never appears — here's the cell."*
```
```
