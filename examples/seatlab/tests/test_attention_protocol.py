import json
from copy import deepcopy
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import (
    aliases,
    changed_reports,
    decide,
    decide_prompt,
    grid,
    select,
    select_prompt,
)

_SEED = 6401
_REP = 1
_BAYS = "ABCD"


def _prompt_parts(prompt: str) -> tuple[str, dict[str, Any]]:
    rules, data = prompt.split("\n", 1)
    return rules, json.loads(data)


def test_registered_cells_run_both_arms_in_counterbalanced_order() -> None:
    cells = grid()
    assert isinstance(cells, tuple)
    assert len(cells) == len({cell["cell_id"] for cell in cells}) == 24
    pairs = zip(cells[::2], cells[1::2], strict=True)
    for block, (first, second) in enumerate(pairs):
        index, rep = divmod(block, 2)
        seed = 6401 + index
        arms = ("named", "count") if (index + rep) % 2 == 0 else ("count", "named")
        for cell, arm in zip((first, second), arms, strict=True):
            assert cell == {
                "cell_id": f"attn-s{seed}-r{rep}-{arm}",
                "seed": seed,
                "repetition": rep,
                "condition": "reordered",
                "pairing": "MM",
                "arm": arm,
            }


@pytest.fixture
def native_worlds() -> tuple[Any, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "tasks/dispatch-handoff/environment/seatworld.py"
    )
    world_type = run_path(str(path))["World"]
    settings = {
        "schema": "seatlab.scenario/v1",
        "seed": _SEED,
        "condition": "reordered",
        "episodes": 6,
    }
    world, other = world_type(settings), world_type(settings)
    reports = world.view("inventory")["cases"]
    for case in reports:
        previous, recent = case["reports"]
        wrong = [bay for bay in _BAYS if bay not in recent["available"]]
        recent["available"] = wrong
        if case["id"] == 1:
            previous["available"] = list(reversed(wrong))
    alternative = deepcopy(reports)
    previous, recent = alternative[0]["reports"]
    original_sets = {tuple(previous["available"]), tuple(recent["available"])}
    recent["available"] = list(
        next(pair for pair in combinations(_BAYS, 2) if pair not in original_sets)
    )
    assert world.submit("inventory", {"rows": reports}) == {"accepted": True}
    assert other.submit("inventory", {"rows": alternative}) == {"accepted": True}
    return world, other


def test_selector_and_final_receive_only_registered_report_projections(
    native_worlds: tuple[Any, Any],
) -> None:
    world, other = native_worlds
    cases = world.view("dispatch")["cases"]
    alternative = other.view("dispatch")["cases"]
    ranked_ids = sorted(
        range(6),
        key=lambda native_id: sha256(
            f"seatlab-attn-v1:{_SEED}:{_REP}:{native_id}".encode("utf-8")
        ).hexdigest(),
    )
    mapping = {f"Q{rank}": native_id for rank, native_id in enumerate(ranked_ids, 1)}
    assert list(aliases(cases, _SEED, _REP).items()) == list(mapping.items())
    by_native_id = {native_id: alias for alias, native_id in mapping.items()}
    truth = world.evidence()["cases"]
    changed = changed_reports(cases, _SEED, _REP)
    assert truth[0]["before"] == truth[0]["after"]
    assert truth[1]["before"] != truth[1]["after"]
    assert by_native_id[0] in changed and by_native_id[1] not in changed
    assert changed_reports(alternative, _SEED, _REP) == changed

    count_prompt = select_prompt(cases, _SEED, _REP, "count")
    named_prompt = select_prompt(cases, _SEED, _REP, "named")
    count_rules, count_data = _prompt_parts(count_prompt)
    named_rules, named_data = _prompt_parts(named_prompt)
    expected_rows = [
        {
            "alias": alias,
            "utilities": cases[native_id]["utilities"],
            "revision_1": cases[native_id]["messages"][1]["available"],
        }
        for alias, native_id in mapping.items()
    ]
    assert count_data == {"cases": expected_rows, "changed_count": len(changed)}
    assert named_rules == count_rules
    assert named_data == count_data | {
        "changed_aliases": [alias for alias in mapping if alias in changed]
    }
    for arm, prompt in (("count", count_prompt), ("named", named_prompt)):
        assert select_prompt(alternative, _SEED, _REP, arm) == prompt

    requested = [by_native_id[1], by_native_id[2]]
    opened = select(json.dumps({"open": requested}))
    assert opened == tuple(requested)
    final_prompt = decide_prompt(cases, _SEED, _REP, opened)
    assert decide_prompt(alternative, _SEED, _REP, opened) == final_prompt
    assert decide_prompt(cases, _SEED, _REP, opened[::-1]) == final_prompt
    _, final_data = _prompt_parts(final_prompt)
    for row in expected_rows:
        alias = row["alias"]
        if alias in opened:
            native_id = mapping[alias]
            submitted = cases[native_id]["messages"][0]["available"]
            assert submitted != truth[native_id]["after"]
            row["revision_2"] = submitted
    assert final_data == {"cases": expected_rows}
    open_changed = (by_native_id[0], opened[0])
    assert decide_prompt(cases, _SEED, _REP, open_changed) != decide_prompt(
        alternative, _SEED, _REP, open_changed
    )


def test_final_submits_all_choices_including_a_nonoptimal_choice(
    native_worlds: tuple[Any, Any],
) -> None:
    world, _ = native_worlds
    cases = world.view("dispatch")["cases"]
    mapping = aliases(cases, _SEED, _REP)
    choices = dict(zip(reversed(mapping), "ABCDAB", strict=True))
    native_id = mapping["Q1"]
    utilities = cases[native_id]["utilities"]
    nonoptimal = min(utilities, key=utilities.__getitem__)
    choices["Q1"] = nonoptimal
    payload = decide(json.dumps({"choices": choices}), cases, _SEED, _REP)
    assert world.submit("dispatch", payload) == {"accepted": True}
    evidence = world.evidence()
    assert evidence["complete"]
    assert {case["id"]: case["chosen"] for case in evidence["cases"]} == {
        native_id: choices[alias] for alias, native_id in mapping.items()
    }
    after = evidence["cases"][native_id]["after"]
    assert evidence["cases"][native_id]["chosen"] != max(
        after, key=utilities.__getitem__
    )


@pytest.mark.parametrize(
    "reply",
    [
        '{"open":["Q1","Q1"]}',
        '{"open":["Q0","Q2"]}',
        '{"open":["Q1","Q2","Q3"]}',
        '{"open":["Q1"]}',
        '{"open":"Q1,Q2"}',
        '{"open":["Q1",NaN]}',
        '{"open":["Q1","Q2"],"open":["Q1","Q2"]}',
        '{"open":["Q1","Q2"],"reason":"extra"}',
        '```json\n{"open":["Q1","Q2"]}\n```',
    ],
)
def test_selector_rejects_invalid_replies(reply: str) -> None:
    with pytest.raises(ValueError):
        select(reply)


def test_final_rejects_invalid_replies() -> None:
    cases = [{"id": number} for number in range(6)]
    choices = '{"Q1":"A","Q2":"A","Q3":"A","Q4":"A","Q5":"A","Q6":"A"}'
    for reply in [
        '{"choices":' + choices.replace('"Q6":"A"', '"Q1":"A"') + "}",
        '{"choices":' + choices.replace(',"Q6":"A"', "") + "}",
        '{"choices":' + choices.replace('"Q6"', '"Q7"') + "}",
        '{"choices":' + choices.replace('"Q6":"A"', '"Q6":"AB"') + "}",
        '{"choices":' + choices.replace('"Q6":"A"', '"Q6":NaN') + "}",
        '{"choices":' + choices.replace('"Q6":"A"', '"Q6":Infinity') + "}",
        f'{{"choices":{choices},"choices":{choices}}}',
        f'{{"choices":{choices},"reason":"extra"}}',
    ]:
        with pytest.raises(ValueError):
            decide(reply, cases, _SEED, _REP)
