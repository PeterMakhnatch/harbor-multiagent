from copy import deepcopy
from importlib import import_module

import pytest

from examples.seatlab.physical_dispatch import certificate as c
from examples.seatlab.physical_dispatch import policies
from examples.seatlab.physical_dispatch.worlds import select


def test_hidden_map_preserves_the_natural_draw_and_is_an_involution():
    validate = import_module(
        "examples.tasks.exhibit-relay.environment.exhibit_relay.cases"
    ).validate_case
    for seed in range(48):
        case = c.make_case(seed, "fork", layout="draw")
        original = deepcopy(case)
        mapped = select(case, "swapped")
        validate(mapped)
        assert case == original
        assert select(mapped, "swapped") == case
        assert {k: v for k, v in mapped.items() if k != "depot"} == {
            k: v for k, v in case.items() if k != "depot"
        }
        for before, after in zip(case["depot"], mapped["depot"], strict=True):
            assert {k: v for k, v in before.items() if k != "value"} == {
                k: v for k, v in after.items() if k != "value"
            }
        assert (
            len(
                {
                    card["value"]
                    for card, other in zip(case["depot"], mapped["depot"], strict=True)
                    if card["value"] != other["value"]
                }
            )
            == 2
        )
        assert all(
            select({**case, "realized": brief["id"]}, "swapped")["depot"]
            == mapped["depot"]
            for brief in case["briefs"]
        )


def test_pre_reveal_actions_do_not_use_the_draw_or_hidden_map():
    for seed in range(12):
        case = c.make_case(seed, "fork", layout="draw")
        expected = {}
        for draw in (brief["id"] for brief in case["briefs"]):
            for map_name in ("original", "swapped"):
                for policy in ("other", "relevant"):
                    session = c.Session(
                        select({**case, "realized": draw}, map_name), "linked"
                    )
                    initial = session.view("loader")
                    assert initial["order"] is None
                    first = policies.nominate(initial)
                    session.apply("loader", first)
                    sender = session.view("registrar")
                    assert sender["order"] is None
                    second = policies.registrar(sender)
                    session.apply("registrar", second)
                    peer = session.view("depot")
                    assert peer["order"] is None
                    third = policies.depot(peer, policy)
                    assert (first, second, third) == expected.setdefault(
                        policy, (first, second, third)
                    )
                    assert len(second["ids"]) == len(third["ids"]) == 2
        assert expected["other"][2] != expected["relevant"][2]


@pytest.mark.parametrize("start", [0, 7, 107])
def test_physical_certificates_use_equal_full_histories_and_the_native_payoff(start):
    result = c.certify(start)
    assert result["initial_loader_inputs_identical"]
    assert result["native_commits"] == 4 * 8**2
    assert result["model_calls"] == result["harbor_trials"] == 0
    assert result["development_seed"] == start + result["candidates_checked"] - 1
    assert 1 <= result["candidates_checked"] <= 128
    seed = result["development_seed"]
    for skipped in range(start, seed):
        base = c.make_case(skipped, "fork", layout="draw")
        assert base["realized"] != min(b["id"] for b in base["briefs"])
    assert result["comparison"]["gain"] == "1/2"
    assert result["comparison"]["relation"] == "refinement"
    for name, expected in (("other", "1/2"), ("relevant", "1")):
        arm = result["arms"][name]
        assert (
            arm["analysis"]["actions"] == 64 and arm["analysis"]["observed"] == expected
        )
        assert all(row["successful_actions"] == 1 for row in arm["counts"].values())
        assert all(
            row["follow_reward"] == (1 if name == "relevant" else None)
            for row in arm["counts"].values()
        )


def test_complete_routes_score_on_destinations_not_support():
    seed, _, case, _ = c._candidate(9)
    assert seed >= 9
    for map_name in ("original", "swapped"):
        mapped = select(case, map_name)
        yes, view = c._prefix(mapped, "relevant")
        no, missing = c._prefix(mapped, "other")
        good = policies.follow(view)
        assert good is not None and policies.follow(missing) is None
        unsupported = c.grade(c._commit(mapped, no, good))
        assert unsupported["reward"] == 1 and unsupported["chains_supported"] == 0
        left, right = tuple(good["routes"])
        wrong = {
            "op": "commit",
            "routes": {left: good["routes"][right], right: good["routes"][left]},
        }
        supported = c.grade(c._commit(mapped, yes, wrong))
        assert supported["reward"] == 0 and supported["chains_supported"] == 2


def test_seed_search_does_not_silently_redraw_or_expand_the_population(monkeypatch):
    called = []

    def nonfocal(seed, profile, *, layout):
        called.append(seed)
        return {"briefs": [{"id": "a"}, {"id": "b"}], "realized": "b"}

    monkeypatch.setattr(c, "make_case", nonfocal)
    with pytest.raises(ValueError, match="no naturally focal"):
        c._candidate(17)
    assert called == list(range(17, 17 + 128))
    called.clear()
    with pytest.raises(ValueError, match="no naturally focal"):
        c._candidate(2**63 - 1)
    assert called == [2**63 - 1]
    with pytest.raises(ValueError, match="invalid"):
        c._candidate(2**63)
