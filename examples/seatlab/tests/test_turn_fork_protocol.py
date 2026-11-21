import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from examples.seatlab.attention import protocol as attention
from examples.seatlab.briefing import protocol as briefing
from examples.seatlab.turn_fork import protocol as fork

_TASK = Path(__file__).resolve().parents[2] / "tasks/dispatch-handoff"
_CARD = "AACDBD"
_BAYS = set("ABCD")


@pytest.fixture(scope="module")
def native_api() -> tuple[Any, Any]:
    world = run_path(str(_TASK / "environment/seatworld.py"))["World"]
    score = run_path(str(_TASK / "tests/score.py"))["score"]
    return world, score


def _scenario(world_type: Any, seed: int, rep: int) -> tuple[Any, dict[str, Any]]:
    world = world_type(
        {
            "schema": "seatlab.scenario/v1",
            "condition": "reordered",
            "seed": seed,
            "episodes": 6,
        }
    )
    rows = world.view("inventory")["cases"]
    for row in rows:
        previous, current = row["reports"]
        after = current["available"]
        other = sorted(_BAYS.difference(after))
        previous["available"] = other if row["id"] == 0 else after
        if row["id"] == 2:
            current["available"] = other
    assert world.submit("inventory", {"rows": rows}) == {"accepted": True}
    return world, {
        "source_id": f"s{seed}-r{rep}",
        "seed": seed,
        "repetition": rep,
        "trial_id": "12345678-1234-1234-1234-123456789abc",
        "world_sha256": "0" * 64,
        "upstream": {
            "inventory": {"prompt_sha256": "1" * 64, "response_sha256": "2" * 64},
            "brief": {"prompt_sha256": "3" * 64, "response_sha256": "4" * 64},
        },
        "card": _CARD,
        "cases": [
            {key: case[key] for key in ("id", "utilities", "reports", "after")}
            for case in world.evidence()["cases"]
        ],
    }


def _payload(prompt: str) -> dict[str, Any]:
    return json.loads(prompt.split("\n", 1)[1])


def _dispatch_reply(prompt: str) -> str:
    choices = {}
    for row in _payload(prompt)["cases"]:
        choices[row["alias"]] = max(
            row.get("revision_2", row["revision_1"]), key=row["utilities"].__getitem__
        )
    return json.dumps({"choices": choices})


def test_registered_orders_balance_each_position_and_identify_fresh_turns() -> None:
    orders = [
        fork.arms(seed, rep) for seed in briefing.SEEDS for rep in briefing.REPETITIONS
    ]
    assert orders[:4] == [
        ("aligned", "shifted", "repeat"),
        ("shifted", "repeat", "aligned"),
        ("shifted", "repeat", "aligned"),
        ("repeat", "aligned", "shifted"),
    ]
    for position in zip(*orders, strict=True):
        assert Counter(position) == {"aligned": 4, "shifted": 4, "repeat": 4}

    digest = "0123456789abcdef" * 4
    ids = {
        fork.run_id(digest, f"s{seed}-r{rep}", arm, stage)
        for seed in briefing.SEEDS
        for rep in briefing.REPETITIONS
        for arm in fork.arms(seed, rep)
        for stage in ("push", "decide")
    }
    assert len(ids) == 72
    assert "tf-0123456789ab-s6501-r0-repeat-push" in ids


@pytest.mark.parametrize("seed", briefing.SEEDS)
@pytest.mark.parametrize("rep", briefing.REPETITIONS)
def test_prompts_match_native_bytes_and_limit_role_visibility(
    native_api: tuple[Any, Any], seed: int, rep: int
) -> None:
    world_type, _ = native_api
    world, source = _scenario(world_type, seed, rep)
    native_dispatch = world.view("dispatch")["cases"]
    accepted = [
        {"id": case["id"], "reports": case["reports"]}
        for case in world.evidence()["cases"]
    ]
    aligned = fork.push_prompt(source, "aligned")
    shifted = fork.push_prompt(source, "shifted")
    assert aligned == briefing.push_prompt(accepted, seed, rep, _CARD)
    assert aligned.encode() == fork.push_prompt(source, "repeat").encode()
    assert shifted == briefing.push_prompt(accepted, seed, rep, "ACDBDA")
    assert _payload(shifted) == _payload(aligned) | {"brief": "ACDBDA"}
    assert Counter(fork.card_for(source, "shifted")) == Counter(_CARD)
    assert set(_payload(aligned)) == {"brief", "cases"}
    assert all(
        set(row) == {"alias", "revision_1", "revision_2"}
        for row in _payload(aligned)["cases"]
    )

    alias = {
        case_id: name
        for name, case_id in attention.aliases(accepted, seed, rep).items()
    }
    opened = (alias[2], alias[1])
    final = fork.decide_prompt(source, opened)
    assert final == attention.decide_prompt(native_dispatch, seed, rep, opened)
    assert set(_payload(final)) == {"cases"}
    for row in _payload(final)["cases"]:
        keys = {"alias", "utilities", "revision_1"}
        if row["alias"] in opened:
            keys.add("revision_2")
        assert row.keys() == keys

    reordered = deepcopy(source)
    reordered["cases"].reverse()
    for case in reordered["cases"]:
        case["after"].reverse()
        for report in case["reports"]:
            report["available"].reverse()
    assert fork.push_prompt(reordered, "aligned") == aligned
    assert fork.decide_prompt(reordered, opened[::-1]) == final

    unreleased = deepcopy(source)
    case = unreleased["cases"][0]
    case["reports"][1]["available"] = case["reports"][0]["available"]
    assert fork.push_prompt(unreleased, "aligned") != aligned
    assert fork.decide_prompt(unreleased, opened) == final
    release = (alias[0], alias[1])
    assert _dispatch_reply(fork.decide_prompt(unreleased, release)) != _dispatch_reply(
        fork.decide_prompt(source, release)
    )


def test_fork_score_matches_native_for_a_corrected_decision(
    native_api: tuple[Any, Any],
) -> None:
    world_type, score = native_api
    seed, rep = 6501, 0
    for open_ids, suboptimal, totals in [
        ((2, 1), False, (4, 4)),
        ((0, 1), False, (6, 6)),
        ((0, 1), True, (5, 6)),
    ]:
        world, source = _scenario(world_type, seed, rep)
        native_cases = world.view("dispatch")["cases"]
        alias = {
            case_id: name
            for name, case_id in attention.aliases(native_cases, seed, rep).items()
        }
        opened = fork.parse_push(json.dumps({"open": [alias[i] for i in open_ids]}))
        reply = _dispatch_reply(fork.decide_prompt(source, opened))
        if suboptimal:
            case = source["cases"][1]
            choices = json.loads(reply)
            choices["choices"][alias[1]] = min(
                case["after"], key=case["utilities"].__getitem__
            )
            reply = json.dumps(choices)
        result = fork.grade(source, opened, reply)
        native_choices = attention.decide(reply, native_cases, seed, rep)
        assert world.submit("dispatch", native_choices) == {"accepted": True}
        native = score(world.evidence())
        assert result == {
            "opened": [alias[i] for i in open_ids],
            "opened_sensitive": 1,
            "optimal": native["optimal"],
            "feasible": native["feasible"],
            "actions": [
                {
                    "id": row["id"],
                    "choice": choice["bay"],
                    "optimal": row["optimal"],
                    "feasible": row["feasible"],
                }
                for row, choice in zip(
                    native["rows"], native_choices["rows"], strict=True
                )
            ],
        }
        assert (result["optimal"], result["feasible"]) == totals
        with pytest.raises(ValueError):
            fork.grade(source, opened, json.dumps(json.loads(reply) | {"optimal": 6}))
