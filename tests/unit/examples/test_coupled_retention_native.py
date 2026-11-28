from collections import Counter
from itertools import permutations, product
from typing import Any

import pytest

from examples.seatlab.coupled_retention import native
from examples.seatlab.coupled_retention.policy import render


@pytest.mark.parametrize("start", [0, 321])
def test_balanced_seeds_are_the_first_native_world_for_each_permutation(start):
    seeds = native.balanced_seeds(start)
    incumbent, *alternatives = native.permutation(start)[:4]
    triples = sorted(permutations(alternatives))
    assert len(seeds) == len(set(seeds)) == 6
    assert [native.permutation(seed)[:4] for seed in seeds] == [
        (incumbent, *triple) for triple in triples
    ]
    first = {}
    for seed in range(start, max(seeds) + 1):
        order = native.permutation(seed)
        if order[0] == incumbent and set(order[1:4]) == set(alternatives):
            first.setdefault(order[1:4], seed)
    assert seeds == tuple(first[triple] for triple in triples)
    assert start <= min(seeds) <= max(seeds) < start + 16_384


@pytest.mark.parametrize("sender", ["truthful", "pass"])
def test_native_vote_branches_preserve_the_actual_pre_vote_history(monkeypatch, sender):
    evaluate = native._verify.evaluate
    seen: dict[int, list[dict[str, Any]]] = {}

    def inspect(cut):
        seen.setdefault(cut["seed"], []).append(cut)
        return evaluate(cut)

    monkeypatch.setattr(native._verify, "evaluate", inspect)
    cases = native.native_cases(0, sender)
    assert len(cases) == len(seen) == 6
    for case in cases:
        cuts = seen[case.seed]
        assert len(cuts) == 36
        assert case.actions == ("A", "B", "C", "D", "E", "F")
        history = cuts[0]["events"][:-2]
        assert [(event["role"], event["kind"]) for event in history] == [
            (role, kind)
            for kind in ("endorse", "card", "report", "inbox")
            for role in ("east", "west")
        ]
        assert all(event["value"] is True for event in history[-2:])
        cards = cuts[0]["cards"]
        for role, event in zip(("east", "west"), history[4:6], strict=True):
            assert event["value"] == (cards[role] if sender == "truthful" else [])
        for role, peer in (("east", "west"), ("west", "east")):
            retained = tuple(cards[peer]) if sender == "truthful" else ()
            args = (role, cuts[0]["incumbent"], case.actions, tuple(cards[role]))
            assert getattr(case, role) == (render(*args, None), render(*args, retained))
        for cut, (east, west) in zip(
            cuts, product(case.actions, repeat=2), strict=True
        ):
            assert cut["fixture"] == "both"
            assert cut["channel"] == "deliver"
            assert cut["events"][:-2] == history
            assert cut["events"][-2:] == [
                {"n": 9, "role": "east", "kind": "vote", "value": east},
                {"n": 10, "role": "west", "kind": "vote", "value": west},
            ]
            assert (
                case.scores[case.actions.index(east)][case.actions.index(west)]
                == (evaluate(cut)["reward"])
            )
        winners = [
            (a, b)
            for a, row in enumerate(case.scores)
            for b, reward in enumerate(row)
            if reward
        ]
        assert len(winners) == 1
        assert winners[0][0] == winners[0][1]


def test_packets_hide_the_seed_and_preserve_omitted_card_ambiguity():
    truthful = native.native_cases(0, "truthful")
    passed = native.native_cases(0, "pass")
    assert truthful == native.native_cases(0, "truthful")
    assert [case.seed for case in truthful] == [case.seed for case in passed]
    assert [case.scores for case in truthful] == [case.scores for case in passed]
    for role in ("east", "west"):
        omitted = [getattr(case, role)[0] for case in truthful]
        assert omitted == [getattr(case, role)[0] for case in passed]
        assert sorted(Counter(omitted).values()) == [2, 2, 2]
        assert len({getattr(case, role)[1] for case in truthful}) == 6
        assert sorted(Counter(getattr(case, role)[1] for case in passed).values()) == [
            2,
            2,
            2,
        ]
        for packet in set(omitted):
            possible_winners = {
                next(i for i, row in enumerate(case.scores) if row[i] == 1)
                for case in truthful
                if getattr(case, role)[0] == packet
            }
            assert len(possible_winners) == 2
        assert all(getattr(case, role)[0] != getattr(case, role)[1] for case in passed)


@pytest.mark.parametrize("start", [native.MAX_SEED, native.MAX_SEED - 3])
def test_insufficient_remaining_native_seed_range_fails(start):
    with pytest.raises(ValueError, match="bounded seed scan"):
        native.balanced_seeds(start)


@pytest.mark.parametrize("start", [-1, native.MAX_SEED + 1, True])
def test_invalid_seed_fails(start):
    with pytest.raises(ValueError, match="native seed"):
        native.balanced_seeds(start)


def test_invalid_sender_fails():
    with pytest.raises(ValueError, match="sender"):
        native.native_cases(0, "withhold")
