"""Randomized adversarial case generator.

Builds fresh, never-before-seen trapped documents each run (seeded for reproducibility) across
the attack classes an unfamiliar dataset might throw at us — wrong period, wrong entity,
unsigned/draft, short sample, leaked PII, misleading filename. Each case carries a predicate
asserting the agent's assessment *caught* it. This is how we show the system holds up on data
it has never seen, not just the fixed sample.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Callable

from pbc_agent.config.engagement import Engagement, Entity
from pbc_agent.model.assessment import ItemAssessment, Status
from pbc_agent.model.criteria import (
    CountCriterion,
    EntityCriterion,
    PBCItem,
    PeriodCriterion,
    PeriodKind,
    Priority,
    SignatureCriterion,
)
from pbc_agent.model.documents import Document, SourceType
from pbc_agent.tools_impl.extract import extract_fields
from pbc_agent.tools_impl.pii import scan_document

ENGAGEMENT = Engagement(
    client_name="Acme Holdings, Inc.", fiscal_year_end=date(2026, 6, 30),
    entities=[Entity("Acme Holdings, Inc.", "parent"),
              Entity("Acme Logistics LLC", "sub"),
              Entity("Summit Foods LLC", "sub")])


@dataclass
class TrapCase:
    name: str
    item: PBCItem
    docs: list[Document]
    caught: Callable[[ItemAssessment], bool]


def _doc(text: str, filename: str, eng: Engagement,
         sniff: SourceType = SourceType.PDF_NATIVE) -> Document:
    d = Document(doc_id=filename, filename=filename, container_chain=[filename], sniffed_type=sniff)
    d.text = text
    d.pages = [text]
    d.parsed = True
    extract_fields(d, eng)
    scan_document(d)
    return d


def _item(*criteria) -> PBCItem:
    return PBCItem(id="PBC-XX", category="Test", priority=Priority.HIGH,
                   description="generated trap item", criteria=list(criteria))


def _wrong_period(rng: random.Random, eng: Engagement) -> TrapCase:
    prior = eng.fiscal_year_end.year - rng.randint(1, 3)
    doc = _doc(f"Consolidated balances as of June 30, {prior}.", f"TB_FY{eng.fiscal_year_end.year%100}.pdf", eng)
    item = _item(PeriodCriterion(raw="as_of", kind=PeriodKind.AS_OF,
                                 end=eng.fiscal_year_end, start=eng.fiscal_year_end))
    return TrapCase("wrong_period", item, [doc], lambda a: a.status is Status.INSUFFICIENT)


def _wrong_entity(rng: random.Random, eng: Engagement) -> TrapCase:
    only = rng.choice([e for e in eng.entities if e.role != "parent"])
    doc = _doc(f"Trial balance for {only.name} as of 2026-06-30.", "Consolidated_TB.xlsx", eng,
               SourceType.XLSX)
    item = _item(EntityCriterion(raw="entity=consolidated", scope_word="consolidated",
                                 required=eng.entity_names))
    return TrapCase("wrong_entity", item, [doc], lambda a: a.status is Status.INSUFFICIENT)


def _unsigned_draft(rng: random.Random, eng: Engagement) -> TrapCase:
    doc = _doc("Representation letter (DRAFT)\n/s/ [TO BE SIGNED]",
               rng.choice(["MgmtRep.pdf", "RepLetter_FINAL.pdf"]), eng)
    item = _item(SignatureCriterion(raw="signed=True", signed=True))
    return TrapCase("unsigned_draft", item, [doc],
                    lambda a: a.status is Status.UNDER_REVIEW or a.needs_review)


def _short_count(rng: random.Random, eng: Engagement) -> TrapCase:
    required = rng.choice([20, 25, 30])
    present = rng.randint(5, required - 3)
    docs = [_doc(f"Invoice {i} dated 2026-06-2{i%10}", f"inv_{i}.pdf", eng) for i in range(present)]
    docs[0].text += f"\nSample of {required} invoices enclosed."
    item = _item(CountCriterion(raw="sample_size", sample_size=required, unit="invoices"))
    return TrapCase("short_count", item, docs, lambda a: a.status is Status.UNDER_REVIEW)


def _pii_leak(rng: random.Random, eng: Engagement) -> TrapCase:
    ssn = f"{rng.randint(100,899)}-{rng.randint(10,99)}-{rng.randint(1000,9999)}"
    doc = _doc(f"Payroll for FY2026. Employee SSN {ssn}. Wages reconcile to GL.",
               "Payroll_Register_FY26.xlsx", eng, SourceType.XLSX)
    item = _item(PeriodCriterion(raw="period=FY2026", kind=PeriodKind.FISCAL_YEAR))
    return TrapCase("pii_leak", item, [doc],
                    lambda a: a.needs_review and any("PII" in f for f in a.flags))


def _misleading_filename(rng: random.Random, eng: Engagement) -> TrapCase:
    doc = _doc("Engagement letter (DRAFT) — not for execution.",
               "Engagement_Letter_signed_FINAL.pdf", eng)
    item = _item(SignatureCriterion(raw="signed=True", signed=True))
    return TrapCase("misleading_filename", item, [doc],
                    lambda a: a.needs_review or a.status in (Status.UNDER_REVIEW, Status.INSUFFICIENT))


_BUILDERS = [_wrong_period, _wrong_entity, _unsigned_draft, _short_count, _pii_leak,
             _misleading_filename]


def generate_cases(n: int = 48, seed: int = 7,
                   engagement: Engagement = ENGAGEMENT) -> list[TrapCase]:
    """Generate ``n`` randomized trap cases (round-robin across attack classes)."""
    rng = random.Random(seed)
    cases: list[TrapCase] = []
    for i in range(n):
        builder = _BUILDERS[i % len(_BUILDERS)]
        cases.append(builder(rng, engagement))
    return cases
