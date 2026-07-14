"""Throughput + cost projection.

Times a real run and projects the held-out (~90 emails / ~40 attachments) cost and wall-clock,
so there are no surprises at the review. Cost is **measured** from the provider's reported token
usage when live; **estimated** from real prompt sizes (~4 chars/token) × published per-model
prices when running the offline mock. Timing is real either way (parsing + OCR dominate).

Usage:  python -m pbc_agent.eval.bench [--bundle DIR] [--live] [--held-out-emails 90]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from pbc_agent.agent.loop import AgentRunner


def benchmark(bundle: str | Path, prefer_mock: bool = True,
              held_out_emails: int = 90, held_out_attachments: int = 40) -> dict:
    t0 = time.time()
    runner = AgentRunner.from_bundle(bundle, prefer_mock=prefer_mock)
    t1 = time.time()
    runner.run()
    t2 = time.time()

    state = runner.state
    cost = state.budget.summary()
    emails = max(1, len(state.trace.email_traces))
    attachments = max(1, sum(1 for d in runner.toolbox.documents.values() if d.parsed))

    email_ratio = held_out_emails / emails
    attach_ratio = held_out_attachments / attachments
    proj_cost = cost["usd"] * email_ratio
    proj_process_s = (t2 - t1) * max(email_ratio, attach_ratio)

    return {
        "provider": "live" if not prefer_mock else "mock (estimated)",
        "measured": {
            "ingest_s": round(t1 - t0, 1), "process_s": round(t2 - t1, 1),
            "total_s": round(t2 - t0, 1),
            "emails": emails, "attachments_parsed": attachments,
            "llm_calls": cost["llm_calls"],
            "input_tokens": cost["input_tokens"], "output_tokens": cost["output_tokens"],
            "usd": round(cost["usd"], 4),
        },
        "held_out_projection": {
            "emails": held_out_emails, "attachments": held_out_attachments,
            "projected_usd": round(proj_cost, 3),
            "projected_process_s": round(proj_process_s, 1),
            "ceiling_usd": cost["ceiling_usd"],
            "under_budget": proj_cost < cost["ceiling_usd"],
        },
        "notes": [
            "Cost is measured from real API token usage when --live; otherwise estimated from "
            "actual prompt sizes × list prices.",
            "Projection scales LLM cost ~linearly with email count; live prompt caching on the "
            "static system+tools prefix reduces input cost further (not modeled here → upper bound).",
            "Live wall-clock adds per-call network latency; deterministic parse/OCR time is real.",
        ],
    }


def _print(b: dict) -> None:
    m, p = b["measured"], b["held_out_projection"]
    print(f"\n=== Throughput & cost ({b['provider']}) ===")
    print(f"Measured on sample: {m['emails']} emails, {m['attachments_parsed']} attachments")
    print(f"  time:   ingest {m['ingest_s']}s + process {m['process_s']}s = {m['total_s']}s")
    print(f"  tokens: {m['input_tokens']} in + {m['output_tokens']} out over {m['llm_calls']} calls")
    print(f"  cost:   ${m['usd']}")
    print(f"\nProjected held-out ({p['emails']} emails, {p['attachments']} attachments):")
    verdict = "UNDER" if p["under_budget"] else "OVER"
    print(f"  cost:   ~${p['projected_usd']}   ({verdict} the ${p['ceiling_usd']} ceiling)")
    print(f"  time:   ~{p['projected_process_s']}s of local compute (+ live API latency)")
    print("\nNotes:")
    for n in b["notes"]:
        print(f"  • {n}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Throughput and cost projection.")
    ap.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--held-out-emails", type=int, default=90)
    ap.add_argument("--held-out-attachments", type=int, default=40)
    args = ap.parse_args(argv)
    _print(benchmark(args.bundle, prefer_mock=not args.live,
                     held_out_emails=args.held_out_emails,
                     held_out_attachments=args.held_out_attachments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
