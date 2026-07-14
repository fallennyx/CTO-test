"""Compose a full item assessment: verify + version lineage + PII + anomaly flags.

This is the single place the deterministic judgment is assembled, so the agent and the
generalization tests exercise identical logic. ``verify_item`` decides the status from
acceptance criteria; the layers here add version supersession, PII redaction flags, and
conflict/anomaly ``needs_review`` signals on top.
"""

from __future__ import annotations

from pbc_agent.config.engagement import Engagement
from pbc_agent.model.assessment import ItemAssessment
from pbc_agent.model.criteria import PBCItem
from pbc_agent.model.documents import Document
from pbc_agent.tools_impl.anomaly import detect_anomalies
from pbc_agent.tools_impl.pii import summarize
from pbc_agent.tools_impl.verify import verify_item
from pbc_agent.tools_impl.version import group_versions


def assess_item(item: PBCItem, docs: list[Document], engagement: Engagement,
                source_email: str | None = None) -> ItemAssessment:
    a = verify_item(item, docs, engagement, source_email=source_email)
    apply_versions(a, docs)
    apply_pii(a, docs)
    apply_anomalies(a, docs, engagement)
    return a


def apply_versions(a: ItemAssessment, docs: list[Document]) -> None:
    groups = group_versions([(d.doc_id, d.filename, None) for d in docs])
    superseded = [f for g in groups for f in g.superseded]
    if superseded:
        latest = ", ".join(g.latest_filename for g in groups if g.superseded)
        a.superseded = superseded
        a.latest_version = next((g.version_label for g in groups if g.superseded), None)
        a.flags.append(f"Using latest version ({latest}); "
                       f"superseded {len(superseded)} earlier file(s).")


def apply_pii(a: ItemAssessment, docs: list[Document]) -> None:
    findings = [f for d in docs for f in d.pii_findings]
    if findings:
        a.needs_review = True
        a.flags.append(f"PII detected and redacted: {summarize(findings)}.")


def apply_anomalies(a: ItemAssessment, docs: list[Document], engagement: Engagement) -> None:
    flags = detect_anomalies(docs, engagement)
    if flags:
        a.needs_review = True
        a.flags.extend(flags)
    if a.confidence < 0.6:
        a.needs_review = True
