"""Generalization proof — the agent catches adversarial data it has never seen.

Generates dozens of randomized trapped documents (a different engagement, different entities,
random years/counts/SSNs) and asserts the deterministic assessment downgrades or flags every
one. This is the "run a dataset I've not seen and still pass" guarantee, made testable.
"""

from __future__ import annotations

from collections import Counter

from pbc_agent.eval.traps.generate import ENGAGEMENT, generate_cases
from pbc_agent.tools_impl.assess import assess_item


def test_unseen_traps_are_all_caught():
    cases = generate_cases(n=60, seed=13)
    missed = []
    by_class = Counter()
    caught_by_class = Counter()
    for case in cases:
        by_class[case.name] += 1
        a = assess_item(case.item, case.docs, ENGAGEMENT)
        if case.caught(a):
            caught_by_class[case.name] += 1
        else:
            missed.append((case.name, a.status.value, a.needs_review, a.flags))

    for name in by_class:
        rate = caught_by_class[name] / by_class[name]
        print(f"  {name}: {caught_by_class[name]}/{by_class[name]} caught ({rate:.0%})")
    assert not missed, f"missed traps: {missed[:5]}"


def test_generation_is_reproducible():
    a = [c.name for c in generate_cases(n=12, seed=1)]
    b = [c.name for c in generate_cases(n=12, seed=1)]
    assert a == b


if __name__ == "__main__":
    test_unseen_traps_are_all_caught()
    print("ok  test_unseen_traps_are_all_caught")
    test_generation_is_reproducible()
    print("ok  test_generation_is_reproducible")
