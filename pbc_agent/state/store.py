"""Tracker state — the live audit-request tracker plus run artifacts.

Holds the current :class:`ItemAssessment` per PBC item, the evidence accumulated for each
(across threads, deduped by content hash), version lineages, drafted follow-ups, the agent
trace, and the cost meter. Serializable to the ground-truth-shaped JSON the eval harness and
UI consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pbc_agent.agent.trace import RunTrace
from pbc_agent.config.engagement import Engagement
from pbc_agent.llm.provider import Budget
from pbc_agent.model.assessment import FollowupGroup, ItemAssessment, Status
from pbc_agent.model.criteria import PBCItem


@dataclass
class TrackerState:
    engagement: Engagement
    items: dict[str, PBCItem]
    assessments: dict[str, ItemAssessment] = field(default_factory=dict)
    evidence_by_item: dict[str, list[str]] = field(default_factory=dict)   # item_id -> doc_ids
    followups: list[FollowupGroup] = field(default_factory=list)
    trace: RunTrace = field(default_factory=RunTrace)
    budget: Budget = field(default_factory=Budget)

    def __post_init__(self) -> None:
        # Every item starts Not started; the agent advances it as evidence arrives.
        for iid, item in self.items.items():
            self.assessments.setdefault(iid, ItemAssessment(item_id=iid, status=Status.NOT_STARTED))

    def add_evidence(self, item_id: str, doc_ids: list[str]) -> list[str]:
        cur = self.evidence_by_item.setdefault(item_id, [])
        for d in doc_ids:
            if d not in cur:
                cur.append(d)
        return cur

    def set_assessment(self, assessment: ItemAssessment) -> None:
        self.assessments[assessment.item_id] = assessment

    def reschedule(self, item_id: str, new_date: date) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        item.expected_by = new_date
        return True

    # --- summaries --------------------------------------------------------------------

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Status}
        for a in self.assessments.values():
            counts[a.status.value] += 1
        return counts

    def open_assessments(self) -> list[tuple[PBCItem, ItemAssessment]]:
        out = []
        for iid, a in self.assessments.items():
            if a.status in (Status.UNDER_REVIEW, Status.INSUFFICIENT, Status.NOT_STARTED):
                out.append((self.items[iid], a))
        return out

    def followup_candidates(self) -> list[tuple[PBCItem, ItemAssessment]]:
        """Items that were delivered but are incomplete — the ones worth chasing."""
        out = []
        for iid, a in self.assessments.items():
            if a.status in (Status.UNDER_REVIEW, Status.INSUFFICIENT):
                out.append((self.items[iid], a))
        return out

    def to_report(self) -> dict:
        items = {}
        for iid in sorted(self.items):
            a = self.assessments[iid]
            items[iid] = {
                "status": a.status.value,
                "reasoning": a.reasoning,
                "primary_evidence": a.primary_evidence,
                "open_items": a.open_items,
                "confidence": a.confidence,
                "expected_by": self.items[iid].expected_by.isoformat()
                if self.items[iid].expected_by else None,
                "checks": [
                    {"criterion": c.criterion_kind, "outcome": c.outcome.value,
                     "detail": c.detail,
                     "citations": [{"source": p.doc_id, "at": p.locator, "quote": p.snippet}
                                   for p in c.citations]}
                    for c in a.check_results
                ],
            }
        return {
            "client": self.engagement.client_name,
            "fiscal_year_end": self.engagement.fiscal_year_end.isoformat(),
            "status_counts": self.status_counts(),
            "items": items,
            "followups": [
                {"recipient": g.recipient, "cc": g.cc, "subject": g.subject,
                 "topics": g.topics, "rationale": g.rationale, "body": g.body}
                for g in self.followups
            ],
            "cost": self.budget.summary(),
        }
