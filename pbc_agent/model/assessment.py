"""Assessment model: statuses, per-criterion check results, and follow-up groups.

Statuses use the work-trial's vocabulary (Not started / Received / Under review /
Insufficient / Complete). ``UNDER_REVIEW`` is where an item that was delivered but not yet
fully satisfied lands (our internal "partially complete"). Every :class:`CheckResult` carries
the citations that justify it, so a status is always defensible back to source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pbc_agent.model.documents import ProvenanceSpan


class Status(str, Enum):
    NOT_STARTED = "Not started"
    RECEIVED = "Received"
    UNDER_REVIEW = "Under review"
    INSUFFICIENT = "Insufficient"
    COMPLETE = "Complete"

    @property
    def rank(self) -> int:
        order = [Status.NOT_STARTED, Status.RECEIVED, Status.UNDER_REVIEW,
                 Status.INSUFFICIENT, Status.COMPLETE]
        return order.index(self)


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"


@dataclass
class CheckResult:
    """The result of testing one acceptance criterion against the delivered evidence."""

    criterion_kind: str
    criterion_raw: str
    outcome: Outcome
    detail: str
    citations: list[ProvenanceSpan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class ItemAssessment:
    """The current verdict for one PBC item."""

    item_id: str
    status: Status
    reasoning: str = ""
    check_results: list[CheckResult] = field(default_factory=list)
    primary_evidence: list[str] = field(default_factory=list)   # filenames / doc ids
    open_items: list[str] = field(default_factory=list)
    confidence: float = 1.0
    latest_version: str | None = None
    source_email: str | None = None

    @property
    def open_count(self) -> int:
        return len(self.open_items)


@dataclass
class FollowupGroup:
    """One grouped follow-up email to a single recipient covering several open items."""

    recipient: str
    cc: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    rationale: str = ""
    subject: str = ""
    body: str = ""
