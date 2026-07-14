"""Deterministic tests for the non-LLM tools: extract, verify, version, search.

Self-contained — builds a tiny in-memory engagement and synthetic documents, so no bundle or
network is needed. This is the "deterministic tests around non-LLM logic" the brief requires.
"""

from __future__ import annotations

from datetime import date, datetime

from pbc_agent.config.engagement import Engagement, Entity
from pbc_agent.model.assessment import Status
from pbc_agent.model.criteria import (
    EntityCriterion,
    PBCItem,
    PeriodCriterion,
    PeriodKind,
    Priority,
    SignatureCriterion,
)
from pbc_agent.model.documents import Document, SourceType
from pbc_agent.tools_impl.extract import extract_fields
from pbc_agent.tools_impl.search import CandidateMatcher, evidence_query
from pbc_agent.tools_impl.verify import verify_item
from pbc_agent.tools_impl.version import base_key, group_versions

ENG = Engagement(
    client_name="Northwind Beverages, Inc.",
    fiscal_year_end=date(2026, 6, 30),
    entities=[
        Entity("Northwind Beverages, Inc.", "parent"),
        Entity("Northwind Distribution LLC", "sub"),
        Entity("Cascade Cold Brew LLC", "sub"),
    ],
)


def _doc(text: str, sniff=SourceType.PDF_NATIVE) -> Document:
    d = Document(doc_id="x", filename="f.pdf", container_chain=["f.pdf"], sniffed_type=sniff)
    d.text = text
    d.pages = [text]
    d.parsed = True
    extract_fields(d, ENG)
    return d


def _item(*criteria) -> PBCItem:
    return PBCItem(id="PBC-99", category="Test", priority=Priority.HIGH,
                   description="test item", criteria=list(criteria))


# --- extract --------------------------------------------------------------------------

def test_extract_dates_money_entities():
    d = _doc("As of June 30, 2026. Total $7,700,000.00 for Cascade Cold Brew LLC.")
    kinds = {f.kind.value for f in d.extracted_fields}
    assert "date" in kinds and "money" in kinds and "entity" in kinds
    assert any(f.value == date(2026, 6, 30) for f in d.extracted_fields if f.kind.value == "date")


def test_extract_signature_draft_vs_signed():
    draft = _doc("MANAGEMENT REP LETTER (DRAFT)\n/s/ [TO BE SIGNED]")
    signed = _doc("Sincerely,\n/s/ David Okafor, CFO")
    dsig = [f for f in draft.extracted_fields if f.kind.value == "signature"]
    ssig = [f for f in signed.extracted_fields if f.kind.value == "signature"]
    assert dsig and all(f.value["draft"] for f in dsig)
    assert ssig and any(f.value["signed"] for f in ssig)


# --- verify ---------------------------------------------------------------------------

def test_verify_consolidated_missing_entity_is_insufficient():
    # Only one of three entities present -> wrong-entity trap -> Insufficient.
    d = _doc("Trial balance for Cascade Cold Brew LLC as of 2026-06-30.")
    item = _item(EntityCriterion(raw="entity=consolidated", scope_word="consolidated",
                                 required=ENG.entity_names))
    a = verify_item(item, [d], ENG)
    assert a.status is Status.INSUFFICIENT
    assert any("missing" in oi.lower() for oi in a.open_items)


def test_verify_all_entities_present_is_complete():
    d = _doc("Consolidated TB as of 2026-06-30 for Northwind Beverages, Inc., "
             "Northwind Distribution LLC, and Cascade Cold Brew LLC.")
    item = _item(EntityCriterion(raw="entity=consolidated", scope_word="consolidated",
                                 required=ENG.entity_names))
    assert verify_item(item, [d], ENG).status is Status.COMPLETE


def test_verify_wrong_period_is_insufficient():
    d = _doc("Balance as of June 30, 2025.")   # prior year
    item = _item(PeriodCriterion(raw="as_of=2026-06-30", kind=PeriodKind.AS_OF,
                                 end=date(2026, 6, 30), start=date(2026, 6, 30)))
    assert verify_item(item, [d], ENG).status is Status.INSUFFICIENT


def test_verify_draft_signature_is_under_review():
    d = _doc("Representation letter (DRAFT). /s/ [TO BE SIGNED]")
    item = _item(SignatureCriterion(raw="signed=True", signed=True))
    assert verify_item(item, [d], ENG).status is Status.UNDER_REVIEW


def test_verify_not_started_without_evidence():
    item = _item(SignatureCriterion(raw="signed=True", signed=True))
    assert verify_item(item, [], ENG).status is Status.NOT_STARTED


# --- version --------------------------------------------------------------------------

def test_version_final_supersedes_earlier():
    docs = [
        ("a", "Financials_Draft.xlsx", datetime(2026, 7, 1)),
        ("b", "Financials_v2.xlsx", datetime(2026, 7, 3)),
        ("c", "Financials_Final_v3_REAL.xlsx", datetime(2026, 7, 5)),
    ]
    groups = group_versions(docs)
    assert len(groups) == 1
    g = groups[0]
    assert g.latest_filename == "Financials_Final_v3_REAL.xlsx"
    assert len(g.superseded) == 2


def test_base_key_ignores_version_and_date():
    assert base_key("Financials_Final_v3_REAL.xlsx") == base_key("Financials_v1.xlsx")


# --- search ---------------------------------------------------------------------------

def test_candidate_matcher_finds_obvious_item():
    items = [
        _item_named("PBC-10", "Revenue & AR", "Aged accounts receivable listing as of year-end"),
        _item_named("PBC-23", "Governance", "Board of directors meeting minutes"),
        _item_named("PBC-30", "Legal & Contracts", "Insurance policy schedule with coverage"),
    ]
    m = CandidateMatcher(items)
    q = evidence_query("AR_Aging_YE_2026-06-30.xlsx", "accounts receivable aging 30 60 90 buckets")
    top = m.match(q, top_k=2)
    assert top and top[0][0] == "PBC-10"


def _item_named(iid, cat, desc) -> PBCItem:
    return PBCItem(id=iid, category=cat, priority=Priority.HIGH, description=desc)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
