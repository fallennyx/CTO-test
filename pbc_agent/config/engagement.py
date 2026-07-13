"""Engagement configuration — parsed from ``Client_Profile.pdf``.

This is the verifier's correctness oracle. Crucially it defines which entities the word
``consolidated`` expands to (all three Northwind entities), so an ``entity=consolidated``
criterion becomes a concrete set-cover check. It also carries the routing roster used when
grouping follow-ups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pbc_agent.util.reportlab_pdf import extract_text


@dataclass
class Entity:
    name: str
    role: str = ""            # "parent" / "sub" / ...
    note: str = ""

    @property
    def aliases(self) -> set[str]:
        """Lower-cased surface forms used to detect this entity in document text."""
        forms = {self.name.lower()}
        # Drop the corporate suffix so "Northwind Distribution" also matches.
        stripped = re.sub(r",?\s+(inc\.?|llc|l\.l\.c\.|corp\.?|co\.?)$", "", self.name,
                          flags=re.IGNORECASE).strip().lower()
        if stripped:
            forms.add(stripped)
        return forms


@dataclass
class Person:
    role: str
    name: str
    email: str | None = None


@dataclass
class Engagement:
    client_name: str
    fiscal_year_end: date
    currency: str = "USD"
    ein: str | None = None
    state_of_incorporation: str | None = None
    headquarters: str | None = None
    entities: list[Entity] = field(default_factory=list)
    audit_team: list[Person] = field(default_factory=list)
    client_contacts: list[Person] = field(default_factory=list)
    #: Report/issuance date, if known (drives ``as_of=report_date`` criteria). Often unset.
    report_date: date | None = None

    @property
    def fiscal_year_start(self) -> date:
        """First day of the fiscal year (day after the prior FYE)."""
        e = self.fiscal_year_end
        return date(e.year - 1, e.month, e.day) + _one_day()

    @property
    def entity_names(self) -> frozenset[str]:
        return frozenset(e.name for e in self.entities)

    def expand_entity_scope(self, scope_word: str) -> frozenset[str]:
        """Resolve 'consolidated'/'all' to the full entity set, else match a named entity."""
        w = scope_word.strip().lower()
        if w in {"consolidated", "all", "all entities", "all subsidiaries"}:
            return self.entity_names
        for e in self.entities:
            if w in e.aliases:
                return frozenset({e.name})
        return frozenset()

    def find_person(self, name_or_email: str) -> Person | None:
        key = name_or_email.strip().lower()
        for p in (*self.audit_team, *self.client_contacts):
            if key and (key == (p.name or "").lower() or key == (p.email or "").lower()):
                return p
        return None


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


# --- Parsing ---------------------------------------------------------------------------

_FIELD_RE = re.compile(r"^\s*([A-Za-z ]+?):\s+(.*\S)\s*$")
_ENTITY_RE = re.compile(r"^\s*-\s+(.+?)\s*(?:\(([^)]*)\))?\s*$")
_ROSTER_RE = re.compile(r"^\s*([A-Za-z ]+?):\s+([A-Za-z].*?)(?:,\s*CPA)?\s*$")


def load_engagement(profile_pdf: str | Path) -> Engagement:
    """Parse an engagement ``Client_Profile.pdf`` into an :class:`Engagement`."""
    text = extract_text(Path(profile_pdf).read_bytes())
    lines = text.splitlines()

    fields: dict[str, str] = {}
    entities: list[Entity] = []
    audit_team: list[Person] = []
    client_contacts: list[Person] = []

    section: str | None = None
    for line in lines:
        low = line.strip().lower()
        if low.startswith("consolidated entities"):
            section = "entities"
            continue
        if low.startswith("audit team"):
            section = "audit"
            continue
        if low.startswith("client contacts"):
            section = "contacts"
            continue

        if section == "entities":
            m = _ENTITY_RE.match(line)
            if m:
                name = m.group(1).strip()
                note = (m.group(2) or "").strip()
                role = "parent" if "parent" in note.lower() else ("sub" if "sub" in note.lower() else "")
                entities.append(Entity(name=name, role=role, note=note))
                continue
        if section in {"audit", "contacts"}:
            m = _ROSTER_RE.match(line)
            if m:
                role, name = m.group(1).strip(), m.group(2).strip()
                person = Person(role=role, name=name)
                (audit_team if section == "audit" else client_contacts).append(person)
                continue

        # Top-level "Key: value" metadata (only before the list sections).
        if section is None:
            m = _FIELD_RE.match(line)
            if m:
                fields[m.group(1).strip().lower()] = m.group(2).strip()

    fye = _parse_date(fields.get("fiscal year end", ""))
    if fye is None:
        raise ValueError(f"Could not parse Fiscal Year End from {profile_pdf}")

    return Engagement(
        client_name=fields.get("legal entity name") or fields.get("client") or "Unknown",
        fiscal_year_end=fye,
        currency=fields.get("base currency", "USD"),
        ein=fields.get("ein"),
        state_of_incorporation=fields.get("state of incorporation"),
        headquarters=fields.get("headquarters"),
        entities=entities,
        audit_team=audit_team,
        client_contacts=client_contacts,
    )


def _parse_date(s: str) -> date | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
