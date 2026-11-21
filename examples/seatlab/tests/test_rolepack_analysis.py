import json
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.rolepack import analysis, evidence, protocol

type _Document = dict[str, Any]
type _Action = int | str


def _record(
    record_id: str,
    task: str,
    world: str,
    field: str,
    items: list[_Document],
    answer: _Document | None,
    *,
    scalar: bool = False,
) -> _Document:
    prompt = f"Use the available records to make a decision for {record_id}.\n"
    return {
        "id": record_id,
        "task": task,
        "world": world,
        "prompt": prompt,
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "peer": None
        if answer is None
        else {
            "answer": answer,
            "run": f"prior-{record_id}",
            "source_sha256": "1" * 64,
            "response_sha256": None,
        },
        "rubric": {"field": field, "scalar": scalar, "items": items},
    }


def _item(key: str, valid: list[_Action], optimal: list[_Action]) -> _Document:
    return {"key": key, "valid": valid, "optimal": optimal}


def _choices(*values: str) -> _Document:
    return {"choices": {f"Q{i + 1}": value for i, value in enumerate(values)}}


def _record_samples(
    record: _Document,
    original: tuple[_Document | None, _Document | None],
    peer: tuple[_Document | None, _Document | None],
) -> list[_Document]:
    return [
        {
            "id": f"{record['id']}:d{draw}:{arm}",
            "input": protocol.prompt(record, arm),
            "output": json.dumps(answer),
            "error": None,
        }
        for arm, answers in (("original", original), ("peer", peer))
        for draw, answer in enumerate(answers)
        if answer is not None
    ]


def test_matched_draw_rates_weight_worlds_equally_with_a_missing_repetition() -> None:
    many_items = [
        _item("Q1", ["A", "B"], ["B"]),
        _item("Q2", ["A", "B", "C"], ["B"]),
        _item("Q3", ["A", "B"], ["B"]),
        _item("Q4", ["A", "B", "C"], ["B", "C"]),
    ]
    single_item = [_item("Q1", ["A", "B"], ["B"])]
    prior = _choices("A", "A", "A", "B")
    a, b = _choices("A"), _choices("B")
    first = _record("ship-s6501-r0", "shipment", "s6501", "choices", many_items, prior)
    second = _record("ship-s6501-r1", "shipment", "s6501", "choices", single_item, b)
    third = _record("ship-s6502-r0", "shipment", "s6502", "choices", single_item, b)
    pack = {"schema": "seatlab.rolepack/v1", "records": [first, second, third]}
    samples = [
        *_record_samples(
            first,
            (prior, None),
            (_choices("B", "C", "A", "A"), _choices("B", "C", "A", "C")),
        ),
        *_record_samples(second, (a, a), (b, b)),
        *_record_samples(third, (b, b), (a, a)),
    ]
    task = analysis.summarize(pack, evidence.from_samples(pack, samples))["tasks"][
        "shipment"
    ]
    assert task["matched_source_draws"] == 5
    assert task["registered_source_draws"] == 6
    assert task["matched_actions_per_arm"] == 8
    assert task["equal_world_peer_minus_original"] == pytest.approx(-1 / 6)
    first_world = task["by_world"]["s6501"]
    assert first_world["peer_minus_original"] == pytest.approx(2 / 3)
    assert first_world["matched_draws"] == [
        {"record_id": "ship-s6501-r0", "draw": 0},
        {"record_id": "ship-s6501-r1", "draw": 0},
        {"record_id": "ship-s6501-r1", "draw": 1},
    ]
    assert task["by_world"]["s6502"]["peer_minus_original"] == -1
    original, peer = (task["by_arm"][arm] for arm in ("original", "peer"))
    assert original["states"]["unclaimed"] == 1
    assert (original["scored_actions"], original["optimal_actions"]) == (8, 3)
    assert (peer["scored_actions"], peer["optimal_actions"]) == (12, 5)
    assert original["repeat"]["comparable_sources"] == 2
    assert peer["repeat"] == {
        "registered_sources": 3,
        "comparable_sources": 3,
        "agreeing_proposals": 2,
        "comparable_actions": 6,
        "agreeing_actions": 5,
    }
    changes = peer["vs_prior"]
    assert changes["copied_proposals"] == 2
    assert (changes["copied"], changes["changed"]) == (4, 8)
    assert (changes["same_wrong"], changes["wrong_different"]) == (2, 2)
    assert (changes["corrected"], changes["harmed"]) == (2, 3)
    assert (changes["same_correct"], changes["right_different"]) == (2, 1)
    assert original["vs_prior"]["same_correct"] == 3
    assert original["vs_prior"]["corrected"] == 0


def test_scalar_calendar_has_separate_effect_and_no_credit_for_prior_success() -> None:
    items = [_item("slot", [0, 1, 2], [0, 2])]
    zero, one, two = {"slot": 0}, {"slot": 1}, {"slot": 2}
    case = _record(
        "cal-rx-w00-d0-r0-blind", "calendar", "w00", "slot", items, zero, scalar=True
    )
    absent = _record(
        "cal-rx-w04-d0-r0-blind", "calendar", "w04", "slot", items, None, scalar=True
    )
    ship = _record(
        "ship-s6501-r0",
        "shipment",
        "s6501",
        "choices",
        [_item("Q1", ["A", "B"], ["B"])],
        _choices("A"),
    )
    pack = {"schema": "seatlab.rolepack/v1", "records": [case, absent, ship]}
    samples = [
        *_record_samples(case, (zero, zero), (two, one)),
        *_record_samples(ship, (_choices("A"), None), (_choices("B"), None)),
    ]
    summary = analysis.summarize(pack, evidence.from_samples(pack, samples))["tasks"]
    task = summary["calendar"]
    assert summary["shipment"]["equal_world_peer_minus_original"] == 1
    assert task["matched_source_draws"] == 2
    assert task["matched_actions_per_arm"] == 2
    assert (task["registered_worlds"], task["represented_worlds"]) == (2, 1)
    assert task["equal_world_peer_minus_original"] is None
    assert task["observed_equal_world_peer_minus_original"] == -0.5
    assert task["by_world"]["w04"]["states_by_arm"]["original"]["unavailable"] == 2
    peer = task["by_arm"]["peer"]
    assert peer["observed_pooled_action_rate"] == 0.5
    assert peer["repeat"]["agreeing_actions"] == 0
    assert peer["vs_prior"]["right_different"] == 1
    assert peer["vs_prior"]["harmed"] == 1
    assert peer["vs_prior"]["corrected"] == 0
    assert task["by_arm"]["original"]["vs_prior"]["copied_proposals"] == 2
    assert task["by_arm"]["original"]["vs_prior"]["corrected"] == 0


def test_summarize_rejects_a_forged_outcome() -> None:
    case = _record(
        "cal-rx-w00-d0-r0-blind",
        "calendar",
        "w00",
        "slot",
        [_item("slot", [0, 1], [1])],
        {"slot": 0},
        scalar=True,
    )
    pack = {"schema": "seatlab.rolepack/v1", "records": [case]}
    samples = _record_samples(case, ({"slot": 0}, None), (None, None))
    report = evidence.from_samples(pack, samples)
    row = next(row for row in report["rows"] if row["state"] == "complete")
    row["outcome"]["correct"] = [True]
    with pytest.raises(ValueError):
        analysis.summarize(pack, report)
