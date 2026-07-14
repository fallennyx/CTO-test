"""PBC Tracker — a client-friendly Streamlit app.

Designed for a non-technical partner: plain English, color-coded statuses, and every claim
linked to its source. The jargon (tool calls, models, tokens) lives behind an "Audit detail"
expander for when a reviewer wants the full, PCAOB-defensible trace.

Run:  streamlit run pbc_agent/ui/app.py -- --bundle data/sample_bundle
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when launched via `streamlit run pbc_agent/ui/app.py`
# from a clean checkout (no `pip install` required).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from pbc_agent.agent.loop import run_agent  # noqa: E402
from pbc_agent.model.assessment import Status  # noqa: E402

_STATUS_COLOR = {
    "Complete": ("#e6f4ea", "#137333"),
    "Received": ("#e8f0fe", "#1a56c4"),
    "Under review": ("#fef7e0", "#a56300"),
    "Insufficient": ("#fce8e6", "#c5221f"),
    "Not started": ("#f1f3f4", "#5f6368"),
}
_STATUS_HELP = {
    "Complete": "Received and verified against every acceptance criterion.",
    "Received": "Received; still confirming some acceptance criteria.",
    "Under review": "Received but not yet fully satisfied — see open items.",
    "Insufficient": "What arrived does not meet the request (wrong period/entity, unsigned, ...).",
    "Not started": "No matching document received yet.",
}


def _bundle_arg() -> str:
    if "--bundle" in sys.argv:
        return sys.argv[sys.argv.index("--bundle") + 1]
    return "data/sample_bundle"


@st.cache_resource(show_spinner="Reading the audit inbox and updating the tracker…")
def _load(bundle: str):
    return run_agent(bundle, prefer_mock=True)


def _pill(status: str) -> str:
    bg, fg = _STATUS_COLOR.get(status, ("#eee", "#333"))
    return (f"<span style='background:{bg};color:{fg};padding:2px 10px;border-radius:12px;"
            f"font-size:0.85em;font-weight:600;white-space:nowrap'>{status}</span>")


def main() -> None:
    st.set_page_config(page_title="PBC Tracker", page_icon="📋", layout="wide")
    state = _load(_bundle_arg())
    eng = state.engagement
    counts = state.status_counts()
    total = sum(counts.values())
    done = counts["Complete"]
    need = counts["Under review"] + counts["Insufficient"]

    # --- header --------------------------------------------------------------------
    st.title("📋 Audit Request Tracker")
    st.caption(f"{eng.client_name} — fiscal year ending {eng.fiscal_year_end:%B %d, %Y}")
    st.markdown(
        f"**{done} of {total} requests complete.** "
        f"{counts['Received']} received and under confirmation; "
        f"**{need} need follow-up**; {counts['Not started']} not started.")
    st.progress(done / total if total else 0.0)

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, s in zip((c1, c2, c3, c4, c5), Status):
        col.markdown(_pill(s.value), unsafe_allow_html=True)
        col.markdown(f"### {counts[s.value]}")

    tab_tracker, tab_followups, tab_about = st.tabs(
        ["📋 Tracker", f"✉️ Follow-ups ({len(state.followups)})", "ℹ️ How this works"])

    with tab_tracker:
        _render_tracker(state)
    with tab_followups:
        _render_followups(state)
    with tab_about:
        _render_about(state)

    cost = state.budget.summary()
    st.sidebar.header("This run")
    st.sidebar.metric("Processing cost", f"${cost['usd']:.2f}", help="Real LLM/API spend for the whole inbox.")
    st.sidebar.metric("Emails processed", len(state.trace.email_traces))
    st.sidebar.caption(f"Budget ceiling ${cost['ceiling_usd']:.2f} · "
                       f"{cost['llm_calls']} model calls")
    st.sidebar.caption("Statuses are decided by content, never filenames. "
                       "Open any item's “Why?” for the full audit trail.")


def _render_tracker(state) -> None:
    options = ["All"] + [s.value for s in Status]
    choice = st.selectbox("Show", options, index=0)
    st.write("")
    for iid in sorted(state.items):
        a = state.assessments[iid]
        if choice != "All" and a.status.value != choice:
            continue
        item = state.items[iid]
        left, mid, right = st.columns([0.13, 0.62, 0.25])
        left.markdown(_pill(a.status.value), unsafe_allow_html=True)
        mid.markdown(f"**{iid}** · {item.description}")
        version = a.latest_version or "—"
        right.caption(f"conf {int(a.confidence * 100)}% · {version}")
        with st.expander("Why? — what we checked and why"):
            _render_why(state, iid)
        st.divider()


def _render_why(state, iid: str) -> None:
    item = state.items[iid]
    a = state.assessments[iid]
    st.markdown(f"**Request:** {item.description}")
    st.markdown(f"**Status:** {_pill(a.status.value)} &nbsp; _{_STATUS_HELP[a.status.value]}_",
                unsafe_allow_html=True)
    if a.primary_evidence:
        st.markdown("**What the client sent:** " + ", ".join(f"`{e}`" for e in a.primary_evidence))
    if a.reasoning:
        st.markdown(f"**Our reading:** {a.reasoning}")

    if a.check_results:
        st.markdown("**What we checked:**")
        for c in a.check_results:
            icon = {"pass": "✅", "fail": "❌", "unverifiable": "❔"}.get(c.outcome.value, "•")
            line = f"{icon} **{c.criterion_kind}** — {c.detail}"
            st.markdown(line)
            for cite in c.citations[:2]:
                st.caption(f"   ↳ source: {cite.locator} — “{cite.snippet[:120]}”")
    if a.open_items and a.status is not Status.COMPLETE:
        st.warning("Still needed: " + "; ".join(oi.split(': ', 1)[-1] for oi in a.open_items))

    with st.expander("Audit detail (agent plan + tool calls)"):
        traces = state.trace.for_item(iid)
        if not traces:
            st.caption("No agent activity recorded for this item.")
        for t in traces:
            st.markdown(f"**Email:** `{t.message_source}` — {t.subject}")
            if t.models_used:
                st.caption("Model: " + ", ".join(t.models_used))
            for note in t.plan_notes:
                st.caption(f"• {note}")
            for call in t.tool_calls:
                mark = "✓" if call.ok else "✗"
                st.code(f"{mark} {call.name}({_short(call.input)}) → {call.result_summary}",
                        language="text")


def _render_followups(state) -> None:
    if not state.followups:
        st.success("Nothing outstanding — no follow-ups needed. 🎉")
        return
    st.caption("One grouped email per recipient, so the client gets a single clean ask. "
               "Review and approve — “Send” is mocked in this demo.")
    for i, g in enumerate(state.followups):
        with st.container(border=True):
            st.markdown(f"**To:** {g.recipient}")
            if g.cc:
                st.caption("cc: " + ", ".join(c for c in g.cc if c))
            st.markdown(f"**Subject:** {g.subject}")
            st.caption(g.rationale)
            st.text_area("Draft", g.body, height=220, key=f"draft_{i}")
            a, b, c = st.columns(3)
            if a.button("✅ Approve & send", key=f"send_{i}"):
                st.success("Sent (mocked).")
            b.button("✏️ Edit", key=f"edit_{i}")
            c.button("🗑️ Reject", key=f"reject_{i}")


def _render_about(state) -> None:
    st.markdown(
        """
This assistant reads the audit inbox and keeps the request tracker current. For every
requested document it decides — from the **contents, not the filename** — whether the client
delivered what was asked, and drafts follow-ups for anything still open.

**How to read a row**
- The colored label is the status. Open **“Why?”** to see what was asked, what arrived, and
  each acceptance check with its exact source (page or spreadsheet cell).
- **“Audit detail”** shows the full step-by-step trace — defensible to a peer reviewer.

**What it will not do**
- Trust a filename. A file called `..._signed.pdf` that isn't actually signed is flagged.
- Mark something complete on the wrong period, the wrong entity, or a short document set.
        """)
    st.caption(f"Client config and PBC list are swappable — nothing here is hardcoded to "
               f"{state.engagement.client_name}.")


def _short(inp: dict) -> str:
    parts = []
    for k, v in inp.items():
        sv = v if isinstance(v, str) else (f"[{len(v)}]" if isinstance(v, list) else str(v))
        parts.append(f"{k}={sv}")
    return ", ".join(parts)[:80]


main()   # Streamlit executes the script top-to-bottom; there is no separate entrypoint.
