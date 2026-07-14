"""Command-line entry point.

Phase 1 exposes ``pbc ingest``, which loads the engagement config + PBC list, reads the
mailbox, assembles threads, and recursively enumerates every attachment — printing a summary
that proves the recursion engine reaches nested ZIP entries and emails-within-emails. Later
phases add ``pbc run`` (full verification -> JSON + dashboard).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from pbc_agent.config.engagement import load_engagement
from pbc_agent.criteria_loader.pbc_list import load_pbc_list
from pbc_agent.ingest import assemble_threads, extract_documents, load_mailbox
from pbc_agent.model.documents import SourceType


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pbc", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Phase 1: load + recursively enumerate a mailbox")
    p_ingest.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"),
                          help="Path to an engagement bundle directory")
    p_ingest.add_argument("--show-chains", action="store_true",
                          help="Print the container chain for every nested document")

    p_run = sub.add_parser("run", help="Run the agent over a mailbox -> tracker JSON")
    p_run.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"))
    p_run.add_argument("--out", type=Path, default=Path("out/report.json"))
    p_run.add_argument("--ceiling", type=float, default=5.0, help="USD budget ceiling")
    p_run.add_argument("--mock", action="store_true",
                       help="Force the offline mock provider (no API key needed)")

    p_ui = sub.add_parser("ui", help="Launch the Streamlit tracker UI")
    p_ui.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"))
    p_ui.add_argument("--port", type=int, default=8501)

    p_eval = sub.add_parser("eval", help="Score the agent against labeled cases")
    p_eval.add_argument("--bundle", type=Path, default=Path("data/sample_bundle"))
    p_eval.add_argument("--live", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "ui":
        return _cmd_ui(args)
    if args.command == "eval":
        from pbc_agent.eval.score import evaluate, _print, _DEFAULT_LABELS
        _print(evaluate(args.bundle, _DEFAULT_LABELS, prefer_mock=not args.live))
        return 0
    return 1


def _cmd_ui(args) -> int:
    import subprocess

    app = Path(__file__).parent / "ui" / "app.py"
    cmd = ["streamlit", "run", str(app), "--server.port", str(args.port),
           "--", "--bundle", str(args.bundle)]
    print("Launching UI:", " ".join(cmd))
    return subprocess.call(cmd)


def _cmd_run(args) -> int:
    import json

    from pbc_agent.agent.loop import run_agent
    from pbc_agent.llm.provider import get_provider

    provider = get_provider(prefer_mock=args.mock)
    print(f"Running agent on {args.bundle} using provider: {provider.name} "
          f"(budget ${args.ceiling:.2f})...")
    state = run_agent(args.bundle, prefer_mock=args.mock, ceiling_usd=args.ceiling)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(state.to_report(), indent=2))

    _rule("TRACKER")
    for status, n in state.status_counts().items():
        print(f"  {status:14s} {n}")
    cost = state.budget.summary()
    print(f"\nCost: ${cost['usd']:.4f}  ({cost['llm_calls']} LLM calls, "
          f"{cost['input_tokens']}+{cost['output_tokens']} tokens)  ceiling ${cost['ceiling_usd']}")
    print(f"Follow-up groups drafted: {len(state.followups)}")
    print(f"Report written to {args.out}")
    return 0


def _cmd_ingest(args) -> int:
    paths = _resolve_bundle(args.bundle)
    if paths is None:
        print(f"error: could not locate bundle files under {args.bundle}", file=sys.stderr)
        return 2

    engagement = load_engagement(paths["profile"])
    items = load_pbc_list(paths["pbc_list"], engagement)
    loaded = load_mailbox(paths["mailbox"])
    threads = assemble_threads([le.message for le in loaded])
    extraction = extract_documents(loaded)

    _print_engagement(engagement)
    _print_pbc_items(items)
    _print_mailbox(loaded, threads)
    _print_extraction(extraction, show_chains=args.show_chains)
    return 0


# --- Bundle resolution ----------------------------------------------------------------


def _resolve_bundle(bundle: Path) -> dict[str, Path] | None:
    if not bundle.exists():
        return None
    profile = _first(bundle, "**/Client_Profile*.pdf")
    pbc_list = _first(bundle, "**/PBC_List*.pdf")
    if not profile or not pbc_list:
        return None
    # Prefer a directory of .eml files; fall back to an .mbox.
    emails_dir = next((p for p in bundle.glob("**/emails") if p.is_dir()), None)
    mailbox = emails_dir or _first(bundle, "**/*.mbox")
    if not mailbox:
        return None
    return {"profile": profile, "pbc_list": pbc_list, "mailbox": mailbox}


def _first(root: Path, pattern: str) -> Path | None:
    return next(iter(sorted(root.glob(pattern))), None)


# --- Reporting ------------------------------------------------------------------------


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _print_engagement(eng) -> None:
    _rule("ENGAGEMENT")
    print(f"Client:            {eng.client_name}")
    print(f"Fiscal year:       {eng.fiscal_year_start} .. {eng.fiscal_year_end}")
    print(f"Currency:          {eng.currency}")
    print(f"Entities ({len(eng.entities)}):     " +
          ", ".join(e.name for e in eng.entities))
    roster = [*eng.audit_team, *eng.client_contacts]
    print("Roster:            " + "; ".join(f"{p.role}: {p.name}" for p in roster))


def _print_pbc_items(items) -> None:
    _rule(f"PBC LIST ({len(items)} items)")
    crit_counts = Counter(type(c).__name__ for it in items for c in it.criteria)
    for it in items[:3]:
        kinds = ", ".join(type(c).__name__.replace("Criterion", "") for c in it.criteria)
        print(f"  {it.id} [{it.category}] {it.priority.value}: {it.description[:64]}...")
        print(f"        criteria: {kinds}")
    print(f"  ... ({len(items)} items total)")
    print("Criterion kinds across all items: " +
          ", ".join(f"{k.replace('Criterion','')}={v}" for k, v in crit_counts.most_common()))


def _print_mailbox(loaded, threads) -> None:
    _rule("MAILBOX")
    print(f"Messages:          {len(loaded)}")
    print(f"Threads:           {len(threads)}")
    for t in threads:
        print(f"  {t.thread_id}: {len(t.messages)} msgs — {t.subject[:56]}")


def _print_extraction(extraction, show_chains: bool) -> None:
    _rule("ATTACHMENT EXTRACTION (recursion engine)")
    by_type = Counter(d.sniffed_type.value for d in extraction.documents.values())
    print(f"Top-level attachments:   {extraction.top_level_attachment_count}")
    print(f"Unique documents:        {len(extraction.documents)} (deduped by content hash)")
    print(f"Leaf artifacts:          {extraction.leaf_document_count}")
    print("By sniffed type:         " +
          ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    nested = [o for o in extraction.occurrences if len(o.container_chain) > 2]
    print(f"\nNested documents (inside a ZIP or forwarded .eml): {len(nested)}")
    highlights = [o for o in nested
                  if any(c.lower().endswith(".eml") for c in o.container_chain[1:-1])
                  or any(c.lower().endswith(".zip") for c in o.container_chain[1:-1])]
    for o in (nested if show_chains else highlights[:12]):
        print(f"    {' > '.join(o.container_chain)}")

    reused = _reused(extraction)
    if reused:
        print(f"\nRe-used files (same content in multiple places): {len(reused)}")
        for doc_id, occs in reused[:6]:
            name = extraction.documents[doc_id].filename
            print(f"    {name}: appears {len(occs)}x "
                  f"[{', '.join(sorted({o.message_source for o in occs}))}]")


def _reused(extraction):
    by_id: dict[str, list] = {}
    for o in extraction.occurrences:
        by_id.setdefault(o.doc_id, []).append(o)
    out = [(k, v) for k, v in by_id.items()
           if len({o.message_source for o in v}) > 1]
    return sorted(out, key=lambda kv: -len(kv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
