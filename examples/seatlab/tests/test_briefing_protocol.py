import json
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases, decide_prompt
from examples.seatlab.briefing.protocol import (
    brief_prompt,
    grid,
    parse_brief,
    parse_push,
    push_prompt,
)

_SEED = 6501
_REP = 1
_BAYS = "ABCD"


def _prompt_parts(prompt: str) -> tuple[str, dict[str, Any]]:
    rules, data = prompt.split("\n", 1)
    return rules, json.loads(data)


@pytest.fixture
def native_submissions() -> tuple[Any, Any, list[dict[str, Any]]]:
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
    rows = world.view("inventory")["cases"][::-1]
    for row in rows:
        previous, current = row["reports"]
        current["available"] = [
            bay for bay in reversed(_BAYS) if bay not in current["available"]
        ]
        row["reports"] = [current, previous]
    original = other.view("inventory")["cases"]
    assert world.submit("inventory", {"rows": rows}) == {"accepted": True}
    assert other.submit("inventory", {"rows": original}) == {"accepted": True}
    return world, other, rows


def test_grid_covers_each_registered_pair_in_counterbalanced_order() -> None:
    cells = grid()
    assert len(cells) == len({cell["cell_id"] for cell in cells}) == 24
    for block, (first, second) in enumerate(zip(cells[::2], cells[1::2], strict=True)):
        seed_index, rep = divmod(block, 2)
        seed = 6501 + seed_index
        arms = ("masked", "brief") if (seed_index + rep) % 2 else ("brief", "masked")
        for cell, arm in zip((first, second), arms, strict=True):
            assert cell == {
                "cell_id": f"brf-s{seed}-r{rep}-{arm}",
                "seed": seed,
                "repetition": rep,
                "condition": "reordered",
                "pairing": "MM",
                "arm": arm,
            }


def test_brief_prompt_uses_native_utilities_but_not_submitted_messages(
    native_submissions: tuple[Any, Any, list[dict[str, Any]]],
) -> None:
    world, other, _ = native_submissions
    cases = world.view("dispatch")["cases"]
    alternative = other.view("dispatch")["cases"]
    for case, original in zip(cases, alternative, strict=True):
        current, previous = case["messages"]
        assert [current["revision"], previous["revision"]] == [2, 1]
        assert current["available"] != original["messages"][0]["available"]
    mapping = aliases(cases, _SEED, _REP)
    prompt = brief_prompt(cases, _SEED, _REP)
    assert brief_prompt(alternative[::-1], _SEED, _REP) == prompt
    _, data = _prompt_parts(prompt)
    assert data == {
        "cases": [
            {"alias": alias, "utilities": cases[native_id]["utilities"]}
            for alias, native_id in mapping.items()
        ]
    }


def test_push_prompt_uses_only_accepted_report_sets_and_released_brief(
    native_submissions: tuple[Any, Any, list[dict[str, Any]]],
) -> None:
    world, _, rows = native_submissions
    dispatch_cases = world.view("dispatch")["cases"]
    evidence = world.evidence()["cases"]
    mapping = aliases(dispatch_cases, _SEED, _REP)
    accepted = [{"id": case["id"], "reports": case["reports"]} for case in evidence]
    card = parse_brief('{"brief":"ABCDBA"}')
    masked_rules, masked_data = _prompt_parts(push_prompt(rows, _SEED, _REP, None))
    prompt = push_prompt(rows, _SEED, _REP, card)
    brief_rules, brief_data = _prompt_parts(prompt)
    assert push_prompt(accepted, _SEED, _REP, card) == prompt
    assert brief_rules == masked_rules
    assert brief_data == masked_data | {"brief": "ABCDBA"}
    assert masked_data == {
        "brief": None,
        "cases": [
            {
                "alias": alias,
                "revision_1": accepted[native_id]["reports"][0]["available"],
                "revision_2": accepted[native_id]["reports"][1]["available"],
            }
            for alias, native_id in mapping.items()
        ],
    }
    for alias, row in zip(mapping, masked_data["cases"], strict=True):
        native_id = mapping[alias]
        assert row["revision_2"] != evidence[native_id]["after"]

    opened = parse_push('{"open":["Q4","Q1"]}')
    assert opened == ("Q4", "Q1")
    _, final_data = _prompt_parts(decide_prompt(dispatch_cases, _SEED, _REP, opened))
    expected = []
    for row in masked_data["cases"]:
        alias = row["alias"]
        final_row = {
            "alias": alias,
            "revision_1": row["revision_1"],
            "utilities": dispatch_cases[mapping[alias]]["utilities"],
        }
        if alias in opened:
            final_row["revision_2"] = row["revision_2"]
        expected.append(final_row)
    assert final_data == {"cases": expected}


@pytest.mark.parametrize(
    "reply",
    [
        '{"brief":"ABCDEA"}',
        '{"brief":"ABCdBA"}',
        '{"brief":"ABCＤBA"}',
        '{"brief":"ABC BA"}',
        '{"brief":"ABCDB"}',
        '{"brief":"ABCDBAA"}',
        '{"brief":"ABCDBA "}',
        '{"brief":["A","B","C","D","B","A"]}',
        '{"brief":null}',
        '{"brief":NaN}',
        '{"brief":"ABCDBA","brief":"ABCDBA"}',
        '{"brief":"ABCDBA","reason":"extra"}',
        '```json\n{"brief":"ABCDBA"}\n```',
    ],
)
def test_brief_rejects_malformed_model_replies(reply: str) -> None:
    with pytest.raises(ValueError):
        parse_brief(reply)


@pytest.mark.parametrize(
    "reply",
    [
        '{"open":["Q1","Q1"]}',
        '{"open":["Q0","Q4"]}',
        '{"open":["Q1","Q7"]}',
        '{"open":["Q1"]}',
        '{"open":["Q1","Q4","Q6"]}',
        '{"open":"Q1,Q4"}',
        '{"open":[1,4]}',
        '{"open":["Q1","Q4"],"open":["Q1","Q4"]}',
        '{"open":["Q1","Q4"],"reason":"extra"}',
        '```json\n{"open":["Q1","Q4"]}\n```',
    ],
)
def test_push_rejects_malformed_model_replies(reply: str) -> None:
    with pytest.raises(ValueError):
        parse_push(reply)


@pytest.mark.parametrize(
    ("revision", "bays"),
    [
        (1, ["A", "C"]),
        (True, ["A", "C"]),
        (3, ["A", "C"]),
        (2, ["A", "A"]),
        (2, ["A", "Z"]),
        (2, ["A"]),
        (2, "AB"),
    ],
)
def test_push_rejects_malformed_submitted_reports(
    native_submissions: tuple[Any, Any, list[dict[str, Any]]],
    revision: object,
    bays: object,
) -> None:
    _, _, rows = native_submissions
    malformed = deepcopy(rows)
    malformed[0]["reports"][0] = {"revision": revision, "available": bays}
    with pytest.raises(ValueError):
        push_prompt(malformed, _SEED, _REP, None)


def test_push_rejects_invalid_case_ids_extra_private_data_and_invalid_relay_brief(
    native_submissions: tuple[Any, Any, list[dict[str, Any]]],
) -> None:
    _, _, rows = native_submissions
    for first in (
        rows[0] | {"id": rows[1]["id"]},
        rows[0] | {"id": 6},
        rows[0] | {"id": True},
        rows[0] | {"utilities": {"A": 4}},
        rows[0] | {"after": ["A", "D"]},
    ):
        with pytest.raises(ValueError):
            push_prompt([first, *rows[1:]], _SEED, _REP, None)
    with pytest.raises(ValueError):
        push_prompt(rows, _SEED, _REP, "ABCB")
