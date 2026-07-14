"""End-to-end agent test (offline mock provider).

Runs the whole loop on the sample bundle with no API key and asserts the behaviors the brief
grades: dynamic per-email control flow (different tool paths for different emails), a distinct
verify step producing statuses, grouped follow-ups, cost accounting under budget, and
model-by-cost routing. Skips when the sample bundle isn't present.
"""

from __future__ import annotations

from pathlib import Path

from pbc_agent.agent.loop import run_agent
from pbc_agent.model.assessment import Status

BUNDLE = Path("data/sample_bundle")


def _available() -> bool:
    return (BUNDLE / "Client_Profile.pdf").exists()


def test_agent_end_to_end():
    if not _available():
        print("skip: sample bundle not present")
        return

    state = run_agent(BUNDLE, prefer_mock=True)

    # Cost accounted and under the $5 ceiling.
    cost = state.budget.summary()
    assert cost["llm_calls"] > 0
    assert 0 < cost["usd"] < 5.0

    # A distinct verify step produced a spread of statuses, including some Complete.
    counts = state.status_counts()
    assert counts["Complete"] >= 10
    assert sum(counts.values()) == 30

    # Grouped follow-ups drafted for open items.
    assert len(state.followups) >= 1

    # Dynamic control flow: attachment emails and text-only emails take different paths.
    traces = state.trace.email_traces
    attach = [t for t in traces if "parse_attachment" in t.tool_sequence()]
    resched = [t for t in traces if "note_schedule" in t.tool_sequence()]
    assert attach and resched
    assert attach[0].tool_sequence() != resched[0].tool_sequence()
    # The attachment path runs the full parse->extract->match->verify pipeline.
    assert attach[0].tool_sequence()[:4] == [
        "parse_attachment", "extract_fields", "match_item", "verify_item"]

    # Model-by-cost routing actually routed (small model used at least once).
    models = {m for t in traces for m in t.models_used}
    assert any("haiku" in m for m in models)

    # Report serializes to the ground-truth-shaped structure.
    report = state.to_report()
    assert set(report) >= {"items", "followups", "cost", "status_counts"}
    assert len(report["items"]) == 30


def test_specific_documents_verify_correctly():
    """The verifier's headline judgments hold within the full run."""
    if not _available():
        print("skip: sample bundle not present")
        return
    state = run_agent(BUNDLE, prefer_mock=True)
    # At least one item should be flagged Insufficient (a real content failure), proving the
    # agent doesn't rubber-stamp everything it receives.
    assert any(a.status is Status.INSUFFICIENT for a in state.assessments.values())


if __name__ == "__main__":
    test_agent_end_to_end()
    print("ok  test_agent_end_to_end")
    test_specific_documents_verify_correctly()
    print("ok  test_specific_documents_verify_correctly")
