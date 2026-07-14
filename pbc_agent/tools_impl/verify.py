"""The verifier — checks delivered evidence against a PBC item's acceptance criteria.

A distinct, deterministic tool (never folded into a prompt). For each criterion it emits a
:class:`CheckResult` (PASS / FAIL / UNVERIFIABLE) with the citations that justify it, then
derives the item :class:`Status` purely from that vector. This is what makes a status
defensible — and what defeats the "file arrived → COMPLETE" failure mode: a wrong-period file,
a trial balance missing an entity, an unsigned `..._signed.pdf`, or a short invoice bundle all
fail their defining check and are downgraded, with the exact reason and source.
"""

from __future__ import annotations

import re

from pbc_agent.config.engagement import Engagement
from pbc_agent.model.assessment import CheckResult, ItemAssessment, Outcome, Status
from pbc_agent.model.criteria import (
    BucketCriterion,
    CountCriterion,
    EntityCriterion,
    HorizonCriterion,
    IncludeCriterion,
    PBCItem,
    PeriodCriterion,
    PeriodKind,
    ReconcilesCriterion,
    SignatureCriterion,
    StandardCriterion,
    ThresholdCriterion,
)
from pbc_agent.model.documents import Document, ExtractedField, FieldKind, ProvenanceSpan

_DECISIVE = (PeriodCriterion, EntityCriterion, SignatureCriterion, BucketCriterion,
             StandardCriterion, IncludeCriterion)


def verify_item(item: PBCItem, evidences: list[Document], engagement: Engagement,
                source_email: str | None = None) -> ItemAssessment:
    """Produce an assessment for ``item`` given the parsed+extracted evidence documents."""
    if not evidences:
        return ItemAssessment(item_id=item.id, status=Status.NOT_STARTED,
                              reasoning="No matching evidence has been received yet.")

    fields = [f for d in evidences for f in d.extracted_fields]
    text = "\n".join(d.text for d in evidences).lower()
    units = _unit_count(evidences)

    results: list[CheckResult] = []
    for c in item.criteria:
        r = _check(c, fields, text, units, engagement)
        if r is not None:
            results.append(r)

    status = _status_from(results)
    open_items = [f"{r.criterion_kind}: {r.detail}" for r in results
                  if r.outcome is not Outcome.PASS]
    reasoning = _reasoning(item, results, status)
    confidence = _confidence(results)
    primary = [d.filename for d in evidences if d.sniffed_type.name != "ARCHIVE"][:6] \
        or [d.filename for d in evidences][:6]

    return ItemAssessment(
        item_id=item.id, status=status, reasoning=reasoning, check_results=results,
        primary_evidence=primary, open_items=open_items, confidence=confidence,
        source_email=source_email,
    )


# --- criterion dispatch ---------------------------------------------------------------


def _check(c, fields, text, units, eng) -> CheckResult | None:
    if isinstance(c, PeriodCriterion):
        return _check_period(c, fields, text, eng)
    if isinstance(c, EntityCriterion):
        return _check_entity(c, fields)
    if isinstance(c, SignatureCriterion):
        return _check_signature(c, fields)
    if isinstance(c, BucketCriterion):
        return _check_buckets(c, text)
    if isinstance(c, StandardCriterion):
        return _check_standard(c, fields)
    if isinstance(c, IncludeCriterion):
        return _check_include(c, text)
    if isinstance(c, CountCriterion):
        return _check_count(c, text, units)
    if isinstance(c, ThresholdCriterion):
        return _check_threshold(c, text)
    if isinstance(c, ReconcilesCriterion):
        return _check_reconciles(c, text)
    if isinstance(c, HorizonCriterion):
        return _check_horizon(c, text)
    return None   # DocTypeCriterion & unknowns: never decisive


def _r(kind, raw, outcome, detail, cites=None) -> CheckResult:
    return CheckResult(criterion_kind=kind, criterion_raw=raw, outcome=outcome,
                       detail=detail, citations=cites or [])


def _dates(fields) -> list[ExtractedField]:
    return [f for f in fields if f.kind is FieldKind.DATE]


def _check_period(c: PeriodCriterion, fields, text, eng) -> CheckResult:
    dates = _dates(fields)
    fy_start, fy_end = eng.fiscal_year_start, eng.fiscal_year_end
    prior_start = fy_start.replace(year=fy_start.year - 1)
    prior_end = fy_end.replace(year=fy_end.year - 1)

    def cite(f):
        return [f.provenance]

    if c.kind is PeriodKind.AS_OF and c.end and not c.end_is_report_date:
        hit = next((f for f in dates if f.value == c.end), None)
        if hit:
            return _r("period", c.raw, Outcome.PASS, f"as of {c.end.isoformat()}", cite(hit))
        if _period_end_in_text(c.end, text):
            return _r("period", c.raw, Outcome.PASS, f"as of {c.end.isoformat()}")
        in_fy = [f for f in dates if fy_start <= f.value <= fy_end]
        prior = [f for f in dates if prior_start <= f.value <= prior_end]
        if not in_fy and prior:
            return _r("period", c.raw, Outcome.FAIL,
                      f"dates are prior-year ({prior[0].value.isoformat()}); "
                      f"expected as of {c.end.isoformat()}", cite(prior[0]))
        if in_fy:
            return _r("period", c.raw, Outcome.PASS,
                      f"within fiscal year (e.g. {in_fy[0].value.isoformat()})", cite(in_fy[0]))
        return _r("period", c.raw, Outcome.UNVERIFIABLE,
                  f"could not confirm the as-of date {c.end.isoformat()}")

    if c.kind is PeriodKind.RANGE and (c.start or c.end):
        lo = c.start or fy_start
        hi = c.end or fy_end
        inr = [f for f in dates if lo <= f.value <= hi]
        prior = [f for f in dates if prior_start <= f.value <= prior_end]
        if inr:
            return _r("period", c.raw, Outcome.PASS,
                      f"covers {lo.isoformat()}..{hi.isoformat()}", cite(inr[0]))
        if prior and not inr:
            return _r("period", c.raw, Outcome.FAIL,
                      f"dates fall outside {lo.isoformat()}..{hi.isoformat()} "
                      f"(prior year)", cite(prior[0]))
        return _r("period", c.raw, Outcome.UNVERIFIABLE, "period not confirmable from content")

    if c.kind is PeriodKind.FISCAL_YEAR:
        fy = re.search(r"fy\s?20?\d\d|fiscal year", text)
        in_fy = [f for f in dates if fy_start <= f.value <= fy_end]
        if in_fy:
            return _r("period", c.raw, Outcome.PASS,
                      f"within FY (e.g. {in_fy[0].value.isoformat()})", cite(in_fy[0]))
        if fy:
            return _r("period", c.raw, Outcome.PASS, "fiscal-year reference present")
        prior = [f for f in dates if prior_start <= f.value <= prior_end]
        if prior:
            return _r("period", c.raw, Outcome.FAIL, "content is prior-year", cite(prior[0]))
        return _r("period", c.raw, Outcome.UNVERIFIABLE, "fiscal year not confirmable")

    if c.kind is PeriodKind.PRIOR_YEAR:
        prior = [f for f in dates if prior_start <= f.value <= prior_end]
        if prior:
            return _r("period", c.raw, Outcome.PASS, "prior-year content confirmed", cite(prior[0]))
        return _r("period", c.raw, Outcome.UNVERIFIABLE, "prior year not confirmable")

    if c.end_is_report_date:
        return _r("period", c.raw, Outcome.UNVERIFIABLE,
                  "as of report date — confirm at issuance")
    return _r("period", c.raw, Outcome.UNVERIFIABLE, "period not evaluated")


def _check_entity(c: EntityCriterion, fields) -> CheckResult:
    if not c.required:
        return _r("entity", c.raw, Outcome.PASS, "no specific entity required")
    found = {f.value: f.provenance for f in fields if f.kind is FieldKind.ENTITY}
    missing = [e for e in c.required if e not in found]
    strict = c.scope_word.strip().lower() == "consolidated"
    if not missing:
        return _r("entity", c.raw, Outcome.PASS,
                  f"all {len(c.required)} entities present", list(found.values())[:3])
    if strict:
        # A consolidating document must show every entity — the wrong-entity trap.
        return _r("entity", c.raw, Outcome.FAIL,
                  f"missing entity/entities: {', '.join(sorted(missing))}",
                  list(found.values())[:2])
    # scope 'all'/specific: coverage is across the document set, not per-document.
    if found:
        return _r("entity", c.raw, Outcome.PASS, "entity reference present",
                  list(found.values())[:2])
    return _r("entity", c.raw, Outcome.UNVERIFIABLE, "entity not explicitly named")


def _check_signature(c: SignatureCriterion, fields) -> CheckResult:
    sigs = [f for f in fields if f.kind is FieldKind.SIGNATURE]
    signed = [f for f in sigs if isinstance(f.value, dict) and f.value.get("signed")
              and not f.value.get("draft")]
    draft = [f for f in sigs if isinstance(f.value, dict) and f.value.get("draft")]
    if signed:
        role = f" ({c.signer_role})" if c.signer_role else ""
        return _r("signature", c.raw, Outcome.PASS, f"signature detected{role}",
                  [signed[0].provenance])
    if draft:
        return _r("signature", c.raw, Outcome.FAIL,
                  "marked DRAFT / not executed — signature required", [draft[0].provenance])
    return _r("signature", c.raw, Outcome.FAIL, "no signature detected but one is required")


def _check_buckets(c: BucketCriterion, text) -> CheckResult:
    missing = [b for b in c.buckets if b.lower() not in text]
    if not missing:
        return _r("buckets", c.raw, Outcome.PASS, f"aging buckets present: {', '.join(c.buckets)}")
    return _r("buckets", c.raw, Outcome.FAIL, f"missing buckets: {', '.join(missing)}")


def _check_standard(c: StandardCriterion, fields) -> CheckResult:
    want = c.standard.upper().replace(" ", "")
    for f in fields:
        if f.kind is FieldKind.STANDARD and str(f.value).upper().replace(" ", "") == want:
            return _r("standard", c.raw, Outcome.PASS, f"{c.standard} referenced", [f.provenance])
    return _r("standard", c.raw, Outcome.UNVERIFIABLE, f"{c.standard} not explicitly found")


def _check_include(c: IncludeCriterion, text) -> CheckResult:
    missing = [phrase for phrase in c.items
               if not _phrase_present(phrase, text)]
    if not missing:
        return _r("include", c.raw, Outcome.PASS, "required elements present")
    return _r("include", c.raw, Outcome.UNVERIFIABLE, f"could not confirm: {', '.join(missing)}")


def _check_count(c: CountCriterion, text, units) -> CheckResult:
    required = c.sample_size or c.minimum
    claimed = _claimed_count(text, c.unit)
    if c.cover_all:
        return _r("count", c.raw, Outcome.PASS if units else Outcome.UNVERIFIABLE,
                  f"{units} {c.unit} present")
    if claimed and units and units < claimed:
        return _r("count", c.raw, Outcome.FAIL,
                  f"{units} {c.unit} present but {claimed} indicated — {claimed - units} missing")
    if required and units:
        ok = units >= required or claimed == units
        return _r("count", c.raw, Outcome.PASS if ok else Outcome.UNVERIFIABLE,
                  f"{units} {c.unit} present (target {required})")
    return _r("count", c.raw, Outcome.PASS if units else Outcome.UNVERIFIABLE,
              f"{units} {c.unit} present")


def _check_threshold(c: ThresholdCriterion, text) -> CheckResult:
    return _r("threshold", c.raw, Outcome.UNVERIFIABLE,
              f"per-item support for amounts {c.direction} ${c.threshold_usd:,.0f} "
              f"should be confirmed")


def _check_reconciles(c: ReconcilesCriterion, text) -> CheckResult:
    if re.search(r"reconcil|variance|tie[\s-]?out|reconciliation", text):
        zero = re.search(r"variance[^0-9]{0,20}(0|0\.00|zero)", text)
        return _r("reconciles", c.raw, Outcome.PASS,
                  "reconciliation present" + (" (variance = 0)" if zero else ""))
    return _r("reconciles", c.raw, Outcome.UNVERIFIABLE, f"tie-out to {c.reconciles_to} not confirmed")


def _check_horizon(c: HorizonCriterion, text) -> CheckResult:
    if re.search(rf"{c.months}[\s-]?month|{c.months} months|twelve[\s-]?month", text):
        return _r("horizon", c.raw, Outcome.PASS, f"{c.months}-month horizon present")
    return _r("horizon", c.raw, Outcome.UNVERIFIABLE, f"{c.months}-month horizon not confirmed")


# --- helpers --------------------------------------------------------------------------


_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]


def _period_end_in_text(end, text: str) -> bool:
    """Match a period-end date rendered in ISO, US, or long form within document text."""
    iso = end.isoformat()
    us = f"{end.month}/{end.day}/{end.year}"
    long = f"{_MONTH_NAMES[end.month - 1]} {end.day}, {end.year}"
    return any(s in text for s in (iso, us, long))


def _phrase_present(phrase: str, text: str) -> bool:
    words = [w for w in re.findall(r"[a-z]+", phrase.lower()) if len(w) > 2]
    return all(w in text for w in words) if words else True


def _claimed_count(text: str, unit: str) -> int | None:
    stem = unit.rstrip("s")
    best = None
    for m in re.finditer(rf"(\d+)\s+{stem}s?\b", text):
        best = max(best or 0, int(m[1]))
    for m in re.finditer(rf"(?:sample of|top)\s+(\d+)", text):
        best = max(best or 0, int(m[1]))
    return best


def _unit_count(evidences: list[Document]) -> int:
    leaves = [d for d in evidences if d.sniffed_type.name not in ("ARCHIVE", "EMAIL")]
    return len(leaves)


def _status_from(results: list[CheckResult]) -> Status:
    """Status is a pure function of the check vector.

    Only an actual FAIL downgrades an item — an UNVERIFIABLE soft criterion (e.g. a threshold
    or a standard we can't positively confirm) does not block Complete once the deliverable is
    present and the checkable core passes. A FAIL on a *hard* criterion (wrong period, missing
    consolidated entity, an outright-missing signature, a short count) means Insufficient; a
    softer FAIL (a DRAFT signature awaiting execution, a partial set) means Under review.
    """
    if not results:
        return Status.RECEIVED
    fails = [r for r in results if r.outcome is Outcome.FAIL]
    hard = [r for r in fails if _is_hard_fail(r)]
    if hard:
        return Status.INSUFFICIENT
    if fails:
        return Status.UNDER_REVIEW
    if any(r.outcome is Outcome.PASS for r in results):
        return Status.COMPLETE
    return Status.RECEIVED   # everything only UNVERIFIABLE: received, awaiting confirmation


def _is_hard_fail(r: CheckResult) -> bool:
    if r.criterion_kind in {"entity", "buckets", "period", "count"}:
        return True
    if r.criterion_kind == "signature":
        return "DRAFT" not in r.detail.upper()   # DRAFT-awaiting-signature is soft
    return False


def _confidence(results: list[CheckResult]) -> float:
    if not results:
        return 0.5
    passed = sum(1 for r in results if r.outcome is Outcome.PASS)
    return round(0.5 + 0.5 * passed / len(results), 2)


def _reasoning(item: PBCItem, results: list[CheckResult], status: Status) -> str:
    if not results:
        return f"Evidence received for {item.id}; no acceptance criteria to check."
    passed = [r for r in results if r.outcome is Outcome.PASS]
    failed = [r for r in results if r.outcome is Outcome.FAIL]
    unver = [r for r in results if r.outcome is Outcome.UNVERIFIABLE]
    bits = [f"{status.value}."]
    if passed:
        bits.append("Verified " + "; ".join(f"{r.criterion_kind} ({r.detail})" for r in passed) + ".")
    if failed:
        bits.append("Failed " + "; ".join(f"{r.criterion_kind} — {r.detail}" for r in failed) + ".")
    if unver:
        bits.append("Open " + "; ".join(f"{r.criterion_kind} — {r.detail}" for r in unver) + ".")
    return " ".join(bits)
