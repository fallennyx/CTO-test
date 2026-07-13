"""PBC item + structured acceptance-criteria model.

The PBC list expresses acceptance criteria in a small, closed vocabulary
(``period_end``, ``entity=consolidated``, ``signed=True``, ``threshold_usd``,
``min_customers``, ``sample_size``, ``standard=ASC 606`` ...). We model that as a
discriminated set of ``Criterion`` dataclasses so the verifier is a dispatch over a
handful of kinds rather than free-form text matching.

Only the structure lives here; parsing raw PBC-list text into these objects is the job
of ``criteria_loader/pbc_list.py``, and checking a criterion against evidence is the job
of ``verify/checks/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# --- Criterion union -------------------------------------------------------------------


@dataclass
class Criterion:
    """Base class for a single machine-checkable acceptance criterion."""

    #: Verbatim source phrase, for traceability back to the PBC list.
    raw: str = ""


class PeriodKind(str, Enum):
    AS_OF = "as_of"            # a point in time (e.g. year-end balance)
    RANGE = "range"           # an explicit start/end window
    FISCAL_YEAR = "fiscal_year"
    PRIOR_YEAR = "prior_year"


@dataclass
class PeriodCriterion(Criterion):
    kind: PeriodKind = PeriodKind.AS_OF
    start: date | None = None
    end: date | None = None
    #: True when ``end`` is "report_date" (resolved from the engagement, not fixed).
    end_is_report_date: bool = False


@dataclass
class EntityCriterion(Criterion):
    #: Canonical entity names that must ALL appear. ``consolidated``/``all`` expand
    #: to the full engagement entity set at load time.
    required: frozenset[str] = frozenset()
    scope_word: str = ""       # original "consolidated" / "all" / specific entity


@dataclass
class SignatureCriterion(Criterion):
    signed: bool = True
    #: Required signer role, if any ("CFO", "external counsel", ...). None = any signer.
    signer_role: str | None = None


@dataclass
class CountCriterion(Criterion):
    #: Minimum discrete units required (customers, invoices, accounts).
    minimum: int | None = None
    #: Exact sample size, when specified ("last 15 before + first 15 after" = 30).
    sample_size: int | None = None
    #: The unit being counted, for messaging ("customers", "invoices", "accounts").
    unit: str = "items"
    #: True for "must cover all accounts" style completeness (count resolved from data).
    cover_all: bool = False


@dataclass
class ThresholdCriterion(Criterion):
    threshold_usd: float = 0.0
    #: "over"/"above" (>=) is the norm for audit support thresholds.
    direction: str = "over"


@dataclass
class BucketCriterion(Criterion):
    buckets: tuple[str, ...] = ()


@dataclass
class StandardCriterion(Criterion):
    standard: str = ""         # "ASC 606", "ASC 842"


@dataclass
class ReconcilesCriterion(Criterion):
    reconciles_to: str = "gl"  # tie-out target


@dataclass
class HorizonCriterion(Criterion):
    months: int = 12


@dataclass
class IncludeCriterion(Criterion):
    """Required content elements, e.g. must_include=['all subsidiaries', 'adjusting entries']."""

    items: tuple[str, ...] = ()


@dataclass
class DocTypeCriterion(Criterion):
    """Expected document formats. A SOFT signal only — never decisive for status."""

    formats: frozenset[str] = frozenset()   # {"excel", "pdf", "zip", "docx"}


# --- PBC item --------------------------------------------------------------------------


@dataclass
class PBCItem:
    id: str
    category: str
    priority: Priority
    description: str
    criteria: list[Criterion] = field(default_factory=list)
    #: Mutable: reschedule events in the mailbox update this in place.
    expected_by: date | None = None
    owner_hint: str | None = None

    def criteria_of(self, cls: type) -> list[Criterion]:
        """All criteria of a given kind (e.g. ``item.criteria_of(PeriodCriterion)``)."""
        return [c for c in self.criteria if isinstance(c, cls)]
