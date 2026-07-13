"""End-to-end smoke test against the sample bundle.

Skips automatically when the (git-ignored) sample data isn't present, so CI stays green
without shipping client materials. When the bundle IS present, it pins the headline Phase-1
facts: 3 entities, 30 PBC items, 38 messages, 8 threads, the 4 ZIPs, and the AP-cutoff
re-use across two threads.
"""

from __future__ import annotations

from pathlib import Path

from pbc_agent.config.engagement import load_engagement
from pbc_agent.criteria_loader.pbc_list import load_pbc_list
from pbc_agent.ingest import assemble_threads, extract_documents, load_mailbox

BUNDLE = Path("data/sample_bundle")


def _available() -> bool:
    return (BUNDLE / "Client_Profile.pdf").exists()


def test_bundle_phase1_facts():
    if not _available():
        print("skip: sample bundle not present")
        return

    eng = load_engagement(BUNDLE / "Client_Profile.pdf")
    assert len(eng.entities) == 3
    assert eng.expand_entity_scope("consolidated") == eng.entity_names
    assert eng.fiscal_year_end.isoformat() == "2026-06-30"

    items = load_pbc_list(BUNDLE / "PBC_List_FY2026.pdf", eng)
    assert len(items) == 30
    assert all(it.criteria for it in items)  # every item has at least one criterion

    loaded = load_mailbox(BUNDLE / "sample_v2" / "emails")
    assert len(loaded) == 38
    threads = assemble_threads([le.message for le in loaded])
    assert len(threads) == 8

    ext = extract_documents(loaded)
    types = {}
    for d in ext.documents.values():
        types[d.sniffed_type.value] = types.get(d.sniffed_type.value, 0) + 1
    assert types.get("archive") == 4          # the four ZIP bundles
    assert types.get("xlsx", 0) >= 14
    assert types.get("email") == 1            # the forwarded Vanguard .eml

    # AP cutoff bundle is re-used across thread 3 and thread 5 (one delivery, not two).
    reused_sources = {}
    for o in ext.occurrences:
        if o.filename == "AP_Cutoff_Sample.zip":
            reused_sources.setdefault(o.doc_id, set()).add(o.message_source)
    assert any(len(s) >= 2 for s in reused_sources.values())


if __name__ == "__main__":
    test_bundle_phase1_facts()
    print("ok  test_bundle_phase1_facts")
