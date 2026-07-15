# Measured Results

Numbers from actual runs on the sample bundle (`data/sample_bundle`, 38 emails / 8 threads /
40 attachments). Reproduce with the commands shown.

## Accuracy

| Grader | Command | Result |
|---|---|---|
| **Offline (deterministic mock)** | `python -m pbc_agent.cli eval` | **13/13** · insufficiency P/R/F1 = 1.0 |
| **Live (real Claude, native tool-use)** | `ANTHROPIC_API_KEY=… python -m pbc_agent.cli eval --live` | **13/13** · insufficiency P/R/F1 = 1.0 |
| **Generalization (unseen adversarial traps)** | `python -m tests.test_generalization` | **60/60** caught (6 classes × 10, seeded-random each run) |

Both graders agree because the **verdict is a deterministic function of the evidence** (see
below), so live and offline produce the same statuses — the 13/13 is stable, not a lucky run.

## Cost & throughput (live)

| | Value |
|---|---|
| Cost per full sample inbox (live) | **~$0.19** (measured from API token usage) |
| LLM calls | ~60–70 |
| Budget ceiling | $5 (hard, with circuit-breaker) |
| Held-out (~90 emails) projection | **~$1.4**, well under $5 (`pbc bench`) |

Parsing, OCR, and BM25 matching are $0; only per-email planning hits the model (Haiku by
default, Sonnet on heavier/ambiguous emails).

## Why live == offline (design note)

The agent (Claude native tool-use) drives per-email **planning, caveat-reading, reschedules,
follow-up drafting, and the full trace**. But the final evidence→item→status assignment is
settled by a **deterministic reconciliation**: each delivered document is assigned to the single
item it best satisfies and re-verified. This makes every status **reproducible and
PCAOB-defensible**, and removes the cross-item contamination that otherwise makes an autonomous
agent's status wobble run-to-run. It's an agent with an audit-grade deterministic backbone —
the right shape for a regulated domain.

## What each status decision carries

Plain-English reasoning + every acceptance check (pass/fail/unverifiable) with a page/sheet
**citation**, plus the agent plan + tool-call trace — visible in the UI's "Why?" and "Audit
detail" panels. PII is detected and redacted before anything reaches the model or the screen;
thread caveats ("still outstanding", "under investigation", "to be signed") downgrade affected
items to Under review.

## Note on the tool-call-sequence metric

The eval reports `tool-call-sequence match: 0.0` on live. This compares the live agent's tool
order against two rigid reference sequences; live Claude legitimately varies its phrasing and
order, so the exact-match score is low. It is **not** a correctness signal — statuses are perfect
(13/13). The metric is meaningful mainly for the deterministic mock path.
