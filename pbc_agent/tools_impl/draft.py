"""Grouped follow-up drafting.

Turns the set of still-open items into a *small* number of clean emails — one per recipient —
instead of 30 separate pings. Routing is by item category to the right owner (tax → tax
contact, payroll/HR → HR, CFO-signature items → CFO, everything else → the controller), with
the audit senior cc'd. Bodies are generated deterministically from the assessments' open
items (each already citation-backed), so a draft never invents facts; an LLM polish pass is
optional and layered on later.
"""

from __future__ import annotations

from dataclasses import dataclass

from pbc_agent.config.engagement import Engagement, Person
from pbc_agent.model.assessment import FollowupGroup, ItemAssessment
from pbc_agent.model.criteria import PBCItem

# Which roster role owns each PBC category.
_CATEGORY_ROLE = {
    "tax": "tax",
    "payroll & hr": "hr",
    "payroll and hr": "hr",
}
_SIGNATURE_ROLE = "cfo"   # items whose open item is a required signature route to the CFO


@dataclass
class Contact:
    name: str
    email: str
    role: str


def _norm_role(role: str) -> str:
    r = role.strip().lower()
    if "cfo" in r:
        return "cfo"
    if "controller" in r:
        return "controller"
    if "book" in r:
        return "bookkeeper"
    if "tax" in r:
        return "tax"
    if r in ("hr", "human resources", "people"):
        return "hr"
    return r


def build_contacts(engagement: Engagement, discovered: dict[str, str] | None = None) -> dict[str, Contact]:
    """Map a normalized role -> Contact, using the engagement roster + discovered emails.

    ``discovered`` is an optional ``{lower-name: email}`` map harvested from the mailbox, so
    we address people at their real addresses without hardcoding any.
    """
    discovered = discovered or {}
    contacts: dict[str, Contact] = {}
    people: list[Person] = [*engagement.client_contacts, *engagement.audit_team]
    for p in people:
        role = _norm_role(p.role)
        email = p.email or discovered.get(p.name.lower(), "")
        contacts[role] = Contact(name=p.name, email=email, role=role)
    return contacts


def _senior(engagement: Engagement) -> Contact | None:
    for p in engagement.audit_team:
        if "senior" in p.role.lower():
            return Contact(p.name, p.email or "", "senior")
    return None


def route_item(item: PBCItem, assessment: ItemAssessment, contacts: dict[str, Contact]) -> Contact:
    """Choose the recipient for an item's follow-up."""
    if any("signature" in oi.lower() for oi in assessment.open_items) and _SIGNATURE_ROLE in contacts:
        return contacts[_SIGNATURE_ROLE]
    role = _CATEGORY_ROLE.get(item.category.strip().lower())
    if role and role in contacts:
        return contacts[role]
    return contacts.get("controller") or next(iter(contacts.values()))


def draft_followups(open_items: list[tuple[PBCItem, ItemAssessment]],
                    engagement: Engagement,
                    contacts: dict[str, Contact]) -> list[FollowupGroup]:
    """Produce one grouped follow-up email per recipient covering their open items."""
    senior = _senior(engagement)
    by_recipient: dict[str, list[tuple[PBCItem, ItemAssessment, Contact]]] = {}
    for item, assessment in open_items:
        owner = route_item(item, assessment, contacts)
        by_recipient.setdefault(owner.email or owner.name, []).append((item, assessment, owner))

    groups: list[FollowupGroup] = []
    fy = engagement.fiscal_year_end.year
    for _, rows in by_recipient.items():
        owner = rows[0][2]
        cc = [c.email or c.name for c in [senior] if c]
        topics = [f"{it.id}: {_topic(it, a)}" for it, a, _ in rows]
        groups.append(FollowupGroup(
            recipient=f"{owner.name} <{owner.email}>" if owner.email else owner.name,
            cc=cc,
            topics=topics,
            rationale=f"Grouped for {owner.name} — {len(rows)} open item(s), one email to minimize round-trips.",
            subject=f"Outstanding PBC items — {engagement.client_name} FY{fy} audit",
            body=_body(owner, rows, engagement),
        ))
    groups.sort(key=lambda g: -len(g.topics))
    return groups


def _topic(item: PBCItem, a: ItemAssessment) -> str:
    if a.open_items:
        return "; ".join(oi.split(": ", 1)[-1] for oi in a.open_items[:2])
    return item.description[:60]


def _body(owner: Contact, rows, engagement: Engagement) -> str:
    first = owner.name.split()[0]
    lines = [f"Hi {first},", "",
             f"Following up on a few outstanding items for the {engagement.client_name} "
             f"FY{engagement.fiscal_year_end.year} audit:", ""]
    for item, a, _ in rows:
        need = "; ".join(oi.split(": ", 1)[-1] for oi in a.open_items) or "please provide"
        lines.append(f"  • {item.id} — {item.description.rstrip('.')}. Still needed: {need}.")
    lines += ["", "Happy to hop on a call if easier. Thanks!", ""]
    return "\n".join(lines)
