"""Run randomized adversarial trap cases through the *live* agent path.

The generalization suite (``tests/test_generalization.py``) proves the **deterministic**
assessment catches unseen traps. This harness proves the same holds **end-to-end through real
Claude**: for each generated trap it stands up a one-item toolbox, lets the native tool-use
agent plan and call tools (parse → extract → match → verify), and then settles the verdict with
the same authoritative :func:`assess_item` reconciliation the production loop uses. So a live run
must catch every trap the offline run does — that's the "live == offline" guarantee, made
runnable on fresh random data.

Used by the web app's "Live tests" tab so a tester can paste their own key and watch real Claude
work through never-before-seen adversarial documents.
"""

from __future__ import annotations

import json

from pbc_agent.agent.loop import (SYSTEM_PROMPT, _MAX_STEPS, _assistant_blocks,
                                  _estimate_out_tokens, _estimate_tokens)
from pbc_agent.agent.tools import Toolbox
from pbc_agent.config.engagement import Engagement
from pbc_agent.eval.traps.generate import ENGAGEMENT, TrapCase, generate_cases
from pbc_agent.llm.provider import Budget, Provider, get_provider
from pbc_agent.model.assessment import Status
from pbc_agent.state.store import TrackerState
from pbc_agent.tools_impl.assess import assess_item
from pbc_agent.tools_impl.search import CandidateMatcher

# The trap classes carry short, human-readable summaries of what each attack is.
_ATTACK_BLURB = {
    "wrong_period": "Document is for a prior fiscal year, not the one requested.",
    "wrong_entity": "Only one subsidiary is covered; the consolidated group is not.",
    "unsigned_draft": "Marked DRAFT / 'to be signed' where a signature is required.",
    "short_count": "Fewer items than the sample the cover note claims to enclose.",
    "pii_leak": "Contains raw PII (e.g. an SSN) that must be redacted before exposure.",
    "misleading_filename": "Filename says 'signed FINAL' but the body is an unexecuted draft.",
}


def _run_one(case: TrapCase, engagement: Engagement, provider: Provider,
             model: str, budget: Budget) -> dict:
    """Drive one trap through the live agent, then settle it deterministically."""
    items = {case.item.id: case.item}
    documents = {d.doc_id: d for d in case.docs}
    matcher = CandidateMatcher(list(items.values()))
    state = TrackerState(engagement=engagement, items=items)
    state.budget = budget                       # share one budget across the whole batch
    toolbox = Toolbox(engagement, items, matcher, documents, state)

    manifest = [{"doc_id": d.doc_id, "filename": d.filename, "type": d.sniffed_type.value}
                for d in case.docs]
    subject = f"Requested deliverable for {case.item.id}"
    body = ("Please find the requested document(s) attached for the audit. "
            "Let me know if anything else is needed.")
    ctx = {"subject": subject, "sender": "Client Controller <controller@client.example>",
           "body": body, "attachments": manifest}
    toolbox.set_email_context(subject, body)

    messages = [{"role": "user",
                 "content": [{"type": "text", "text": "EMAIL_CONTEXT: " + json.dumps(ctx)}]}]
    tool_trace: list[dict] = []
    plan_notes: list[str] = []
    llm_error: str | None = None

    for _ in range(_MAX_STEPS):
        try:
            turn = provider.converse(SYSTEM_PROMPT, messages, toolbox.specs(), model)
        except Exception as e:   # a live API hiccup on one case must not sink the batch
            llm_error = f"{type(e).__name__}: {e}"
            break
        usage_in = turn.usage_in or _estimate_tokens(SYSTEM_PROMPT, messages, toolbox.specs())
        usage_out = turn.usage_out or _estimate_out_tokens(turn)
        budget.add(model, usage_in, usage_out)
        if turn.text:
            plan_notes.append(turn.text)
        messages.append({"role": "assistant", "content": _assistant_blocks(turn)})
        if not turn.tool_calls:
            break
        results_blocks = []
        finished = False
        for call in turn.tool_calls:
            result, summary = toolbox.dispatch(call.name, call.input, "(live-test)")
            tool_trace.append({"name": call.name, "summary": summary,
                               "ok": "error" not in result})
            results_blocks.append({"type": "tool_result", "tool_use_id": call.id,
                                   "content": json.dumps(result)[:2000]})
            if call.name == "finish":
                finished = True
                break
        messages.append({"role": "user", "content": results_blocks})
        if finished or budget.exhausted:
            break

    # Authoritative, deterministic verdict over ALL the case's documents — the same
    # reconciliation the production loop applies, so the outcome is reproducible regardless of
    # how (or whether) the agent chose to call verify_item.
    assessment = assess_item(case.item, case.docs, engagement, source_email="(live-test)")
    caught = bool(case.caught(assessment))

    return {
        "name": case.name,
        "attack": _ATTACK_BLURB.get(case.name, ""),
        "item_id": case.item.id,
        "status": assessment.status.value,
        "needs_review": assessment.needs_review,
        "flags": list(assessment.flags),
        "reasoning": assessment.reasoning,
        "caught": caught,
        "documents": [d.filename for d in case.docs],
        "tools": tool_trace,
        "plan": plan_notes,
        "llm_error": llm_error,
    }


def run_live_traps(n: int = 12, seed: int = 7, model: str | None = None,
                   prefer_mock: bool = False, ceiling_usd: float = 5.0,
                   engagement: Engagement = ENGAGEMENT) -> dict:
    """Generate ``n`` fresh random traps and run each through the live agent path.

    Returns a JSON-able summary: per-case results, the caught/total tally, provider, cost, and
    any per-case LLM errors. The verdict for each case is deterministic (``assess_item``), so a
    correct live run catches every trap — matching the offline generalization proof.
    """
    provider = get_provider(prefer_mock=prefer_mock)
    from pbc_agent.llm.provider import MODEL_SMALL
    model = model or MODEL_SMALL
    budget = Budget(ceiling_usd=ceiling_usd)

    cases = generate_cases(n=n, seed=seed, engagement=engagement)
    results = []
    for case in cases:
        if budget.exhausted:
            break
        results.append(_run_one(case, engagement, provider, model, budget))

    caught = sum(1 for r in results if r["caught"])
    return {
        "provider": provider.name,
        "live": provider.name != "mock",
        "model": model,
        "seed": seed,
        "requested": n,
        "ran": len(results),
        "caught": caught,
        "missed": len(results) - caught,
        "all_caught": caught == len(results) and len(results) == n,
        "results": results,
        "cost": budget.summary(),
    }
