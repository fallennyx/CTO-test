"""Score the agent against labeled cases.

Reports the metrics the brief asks for: status accuracy, insufficiency-detection
precision/recall/F1 (did we correctly flag delivered-but-incomplete items rather than
rubber-stamp them?), expected tool-call-sequence match, and measured cost. Runs on the
offline mock by default (so it works in CI); pass ``--live`` to score real native tool-use.

Usage:  python -m pbc_agent.eval.score [--bundle DIR] [--labels FILE] [--live]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pbc_agent.agent.loop import run_agent

_INCOMPLETE = {"Under review", "Insufficient"}
_DEFAULT_LABELS = Path(__file__).parent / "cases" / "sample_labels.json"


def evaluate(bundle: str | Path, labels_path: str | Path, prefer_mock: bool = True) -> dict:
    labels = json.loads(Path(labels_path).read_text())
    state = run_agent(bundle, prefer_mock=prefer_mock)

    status_metrics = _score_statuses(state, labels.get("statuses", {}))
    seq_metrics = _score_tool_sequences(state, labels.get("tool_sequences", {}))
    cost = state.budget.summary()
    return {"status": status_metrics, "tool_sequences": seq_metrics, "cost": cost,
            "provider": "mock" if prefer_mock else "live"}


def _score_statuses(state, expected: dict[str, str]) -> dict:
    got = {iid: state.assessments[iid].status.value for iid in state.assessments}
    n = len(expected)
    correct = sum(1 for iid, exp in expected.items() if got.get(iid) == exp)

    # Insufficiency detection = binary "incomplete vs complete".
    tp = fp = fn = tn = 0
    for iid, exp in expected.items():
        exp_inc = exp in _INCOMPLETE
        got_inc = got.get(iid) in _INCOMPLETE
        if exp_inc and got_inc:
            tp += 1
        elif not exp_inc and got_inc:
            fp += 1
        elif exp_inc and not got_inc:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"labeled": n, "accuracy": round(correct / n, 3) if n else None,
            "correct": correct,
            "insufficiency_precision": round(precision, 3),
            "insufficiency_recall": round(recall, 3),
            "insufficiency_f1": round(f1, 3),
            "mismatches": {iid: {"expected": exp, "got": got.get(iid)}
                           for iid, exp in expected.items() if got.get(iid) != exp}}


def _score_tool_sequences(state, expected: dict[str, list]) -> dict:
    by_source = {t.message_source: t.tool_sequence() for t in state.trace.email_traces}
    matched = 0
    details = {}
    for source, exp_seq in expected.items():
        got_seq = by_source.get(source, [])
        ok = got_seq == exp_seq
        matched += ok
        details[source] = {"match": ok, "expected": exp_seq, "got": got_seq}
    n = len(expected)
    return {"labeled": n, "match_rate": round(matched / n, 3) if n else None, "details": details}


def _print(report: dict) -> None:
    s, seq, cost = report["status"], report["tool_sequences"], report["cost"]
    print(f"\n=== PBC Agent eval ({report['provider']} provider) ===")
    print(f"Status accuracy:        {s['correct']}/{s['labeled']}  ({s['accuracy']})")
    print(f"Insufficiency detection: precision={s['insufficiency_precision']} "
          f"recall={s['insufficiency_recall']} F1={s['insufficiency_f1']}")
    print(f"Tool-call-sequence match: {seq['match_rate']}  ({seq['labeled']} labeled emails)")
    print(f"Cost: ${cost['usd']:.4f}  ({cost['llm_calls']} LLM calls)  ceiling ${cost['ceiling_usd']}")
    if s["mismatches"]:
        print("\nStatus mismatches:")
        for iid, m in s["mismatches"].items():
            print(f"  {iid}: expected {m['expected']}, got {m['got']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score the PBC agent against labeled cases.")
    ap.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"))
    ap.add_argument("--labels", type=Path, default=_DEFAULT_LABELS)
    ap.add_argument("--live", action="store_true", help="use real native tool-use (needs key)")
    args = ap.parse_args(argv)
    report = evaluate(args.bundle, args.labels, prefer_mock=not args.live)
    _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
