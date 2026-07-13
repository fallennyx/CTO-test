"""Parse ``PBC_List_FY2026.pdf`` into :class:`PBCItem` objects with structured criteria.

The PBC list states each item's acceptance test in a compact ``key=value`` vocabulary, e.g.::

    PBC-01  [Financial Statements]  Priority: High
       Adjusted trial balance as of fiscal year-end (June 30, 2026), consolidated ...
       Acceptance: period_end=2026-06-30, entity=consolidated, must_include=['all ...']
       Expected documents: excel, pdf

We turn each acceptance token into a typed ``Criterion`` (see ``model/criteria.py``). Entity
scope words (``consolidated`` / ``all``) are expanded to the concrete engagement entity set
here, so the downstream verifier does a plain set-cover with no further lookups.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from pathlib import Path

from pbc_agent.config.engagement import Engagement
from pbc_agent.model.criteria import (
    BucketCriterion,
    CountCriterion,
    Criterion,
    DocTypeCriterion,
    EntityCriterion,
    HorizonCriterion,
    IncludeCriterion,
    PBCItem,
    PeriodCriterion,
    PeriodKind,
    Priority,
    ReconcilesCriterion,
    SignatureCriterion,
    StandardCriterion,
    ThresholdCriterion,
)
from pbc_agent.util.reportlab_pdf import extract_text

_HEADER_RE = re.compile(r"^\s*PBC-(\d+)\s*\[(.*?)\]\s*Priority:\s*(\w+)", re.IGNORECASE)
_FOOTER_RE = re.compile(r"^\s*Confidential\b", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def load_pbc_list(pbc_pdf: str | Path, engagement: Engagement) -> list[PBCItem]:
    """Parse the PBC list PDF into structured items, expanding entity scope via engagement."""
    text = extract_text(Path(pbc_pdf).read_bytes())
    blocks = _split_into_item_blocks(text.splitlines())
    return [_parse_block(b, engagement) for b in blocks]


def _split_into_item_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _FOOTER_RE.match(line):
            continue
        if _HEADER_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_block(block: list[str], engagement: Engagement) -> PBCItem:
    header = _HEADER_RE.match(block[0])
    assert header is not None
    num, category, priority = header.group(1), header.group(2).strip(), header.group(3)
    item_id = f"PBC-{int(num):02d}"

    description_parts: list[str] = []
    acceptance_parts: list[str] = []
    formats: set[str] = set()
    mode = "desc"

    for line in block[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("acceptance:"):
            mode = "acceptance"
            acceptance_parts.append(stripped[len("acceptance:"):].strip())
        elif stripped.lower().startswith("expected documents:"):
            mode = "formats"
            formats |= _parse_formats(stripped.split(":", 1)[1])
        elif mode == "desc":
            description_parts.append(stripped)
        elif mode == "acceptance":
            acceptance_parts.append(stripped)   # wrapped continuation of the clause
        # (lines after 'Expected documents' are ignored)

    criteria = _parse_acceptance(" ".join(acceptance_parts), engagement)
    if formats:
        criteria.append(DocTypeCriterion(raw="expected documents", formats=frozenset(formats)))

    return PBCItem(
        id=item_id,
        category=category,
        priority=_priority(priority),
        description=" ".join(description_parts).strip(),
        criteria=criteria,
    )


# --- Acceptance-clause parsing --------------------------------------------------------


def _parse_acceptance(clause: str, engagement: Engagement) -> list[Criterion]:
    tokens = _split_top_level(clause)
    kv: list[tuple[str, str]] = []
    for tok in tokens:
        if "=" not in tok:
            continue
        key, value = tok.split("=", 1)
        kv.append((key.strip().lower(), value.strip()))

    criteria: list[Criterion] = []
    keys = {k for k, _ in kv}

    # Period: combine period_start/period_end/period/as_of into one criterion.
    period = _period_criterion(dict(kv), keys)
    if period is not None:
        criteria.append(period)

    for key, value in kv:
        raw = f"{key}={value}"
        if key in {"period", "period_start", "period_end", "as_of", "period_around"}:
            continue  # handled by _period_criterion
        elif key == "entity":
            criteria.append(EntityCriterion(
                raw=raw, scope_word=value,
                required=engagement.expand_entity_scope(value)))
        elif key == "must_cover_all_accounts" and _truthy(value):
            criteria.append(CountCriterion(raw=raw, cover_all=True, unit="accounts"))
        elif key == "must_include":
            criteria.append(IncludeCriterion(raw=raw, items=tuple(_parse_list(value))))
        elif key == "signed" and _truthy(value):
            criteria.append(SignatureCriterion(raw=raw, signed=True))
        elif key == "signed_by":
            criteria.append(SignatureCriterion(raw=raw, signed=True, signer_role=value))
        elif key == "threshold_usd":
            criteria.append(ThresholdCriterion(raw=raw, threshold_usd=_num(value)))
        elif key == "min_customers":
            criteria.append(CountCriterion(raw=raw, minimum=int(_num(value)), unit="customers"))
        elif key == "sample_size":
            criteria.append(CountCriterion(raw=raw, sample_size=int(_num(value)), unit="items"))
        elif key == "buckets":
            criteria.append(BucketCriterion(raw=raw, buckets=tuple(_parse_list(value))))
        elif key.startswith("reconciles_to"):
            target = key.replace("reconciles_to_", "") or "gl"
            criteria.append(ReconcilesCriterion(raw=raw, reconciles_to=target))
        elif key == "horizon_months":
            criteria.append(HorizonCriterion(raw=raw, months=int(_num(value))))
        elif key == "standard":
            criteria.append(StandardCriterion(raw=raw, standard=value))
        # Unknown keys are ignored rather than guessed — keeps the model honest.

    return criteria


def _period_criterion(kv: dict[str, str], keys: set[str]) -> PeriodCriterion | None:
    if "as_of" in kv:
        val = kv["as_of"]
        if val.strip().lower() == "report_date":
            return PeriodCriterion(raw=f"as_of={val}", kind=PeriodKind.AS_OF,
                                   end_is_report_date=True)
        d = _date(val)
        return PeriodCriterion(raw=f"as_of={val}", kind=PeriodKind.AS_OF, end=d, start=d)
    if "period_start" in kv or "period_end" in kv:
        start = _date(kv.get("period_start", ""))
        end = _date(kv.get("period_end", ""))
        report = kv.get("period_end", "").strip().lower() == "report_date"
        kind = PeriodKind.RANGE if start else PeriodKind.AS_OF
        return PeriodCriterion(raw="period", kind=kind, start=start, end=end,
                               end_is_report_date=report)
    if "period_around" in kv:
        d = _date(kv["period_around"])
        return PeriodCriterion(raw=f"period_around={kv['period_around']}",
                               kind=PeriodKind.AS_OF, end=d, start=d)
    if "period" in kv:
        val = kv["period"]
        if re.search(r"fy\s*20\d\d", val, re.IGNORECASE):
            year = int(re.search(r"20(\d\d)", val).group(0))
            prior = year == _prior_year_hint(val)
            return PeriodCriterion(
                raw=f"period={val}",
                kind=PeriodKind.PRIOR_YEAR if prior else PeriodKind.FISCAL_YEAR,
            )
    return None


def _prior_year_hint(_val: str) -> int:
    return -1  # placeholder; prior-year detection is refined against engagement in verify


# --- Small value helpers --------------------------------------------------------------


def _split_top_level(clause: str) -> list[str]:
    """Split on commas that are NOT inside [...] or '...' (list/string values)."""
    out, buf, depth, quote = [], [], 0, ""
    for ch in clause:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [t for t in out if t]


def _parse_formats(value: str) -> set[str]:
    return {tok.strip().lower() for tok in value.split(",") if tok.strip()}


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, (list, tuple)):
            return [str(x).strip() for x in parsed]
    except (ValueError, SyntaxError):
        pass
    inner = value.strip("[]")
    return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def _num(value: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", value) or 0)


def _date(value: str) -> date | None:
    m = _DATE_RE.search(value or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _priority(word: str) -> Priority:
    try:
        return Priority(word.capitalize())
    except ValueError:
        return Priority.MEDIUM
