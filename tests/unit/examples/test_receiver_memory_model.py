import json
from dataclasses import replace
from typing import Any

import pytest

from examples.seatlab.receiver_memory.model import (
    POLICIES,
    Episode,
    Selector,
    packet,
    qualify,
)

SELECTOR = Selector("GET", "/inbox", ("reports",), "slot")


def _frame(method: str, path: str, body: Any, reply: Any) -> dict[str, Any]:
    return {"method": method, "path": path, "body": body, "status": 200, "reply": reply}


def _read(*slots: str) -> dict[str, Any]:
    return _frame("GET", "/inbox", None, {"reports": [{"slot": s} for s in slots]})


def _episode(name: str, first: str, last: str, winner: str) -> Episode:
    return Episode(
        name,
        1,
        "Choose a slot after acknowledging both reports.",
        (
            _read(first),
            _frame("POST", "/ack", None, {"accepted": True}),
            _read(first, last),
            _frame("POST", "/ack", None, {"accepted": True}),
        ),
        _read(first, last),
        {"choice-one": int(winner == "one"), "choice-two": int(winner == "two")},
    )


def _cases() -> list[Episode]:
    return [
        _episode("hidden-confirm", "A", "A", "one"),
        _episode("hidden-replace", "A", "B", "two"),
    ]


def _decoded(case: Episode, policy: str) -> dict[str, Any]:
    return json.loads(packet(case, policy, SELECTOR))


def test_packets_retain_only_the_requested_reader_context() -> None:
    case = _cases()[1]
    current = _read("current-only")
    distractor = _frame(
        "POST",
        "/inbox",
        {"slot": "request-only"},
        {"reports": [{"slot": "wrong-method"}]},
    )
    case = replace(
        case,
        history=(distractor, *case.history[:-1], current),
        refresh=_read("refresh-only"),
    )
    inputs = {policy: _decoded(case, policy) for policy in POLICIES}
    for policy, observation in inputs.items():
        raw = packet(case, policy, SELECTOR)
        assert not raw.endswith(b"\n")
        assert set(observation) == {
            "instruction",
            "current",
            "memory",
            "additional_read",
        }
        assert observation["instruction"] == case.instruction
        assert observation["current"] == current
        assert observation["additional_read"] == (
            case.refresh if policy == "refresh" else None
        )
        assert b"hidden-replace" not in raw and b"choice-one" not in raw
    assert inputs["full"]["memory"] == list(case.history[:-1])
    assert inputs["oldest"]["memory"] == {"note": "A"}
    assert inputs["newest"]["memory"] == {"note": "B"}
    assert inputs["none"]["memory"] == inputs["refresh"]["memory"] == []
    assert len(packet(case, "oldest", SELECTOR)) == len(
        packet(case, "newest", SELECTOR)
    )


def test_newest_retains_full_value_and_refresh_recovers_it() -> None:
    cases = _cases()
    result = qualify(cases, 1, SELECTOR)
    assert result["values"] == {
        "full": "1",
        "oldest": "1/2",
        "newest": "1",
        "none": "1/2",
        "refresh": "1",
    }
    assert result["equal_note_bytes"] and result["equal_packet_bytes"]
    assert result["retention_pattern"] and result["refresh_restores_full_value"]
    assert result["relations"]["oldest_to_newest"] == "refinement"
    assert (
        result["retention_gain"]
        == result["lost_value"]
        == result["refresh_gain"]
        == "1/2"
    )
    summary = json.dumps(result)
    assert all(
        case.name not in summary and case.instruction not in summary for case in cases
    )
    assert "choice-one" not in summary and "choice-two" not in summary


def test_more_distinctions_without_value_do_not_qualify() -> None:
    first, second = _cases()
    result = qualify([first, replace(second, scores=first.scores)], 1, SELECTOR)
    assert result["relations"]["oldest_to_newest"] == "refinement"
    assert result["values"]["oldest"] == result["values"]["newest"] == "1"
    assert not result["retention_pattern"]
    assert not result["refresh_restores_full_value"]


def test_current_receipt_can_already_contain_the_decision_information() -> None:
    cases = [
        replace(
            case,
            history=(
                *case.history[:-1],
                _frame("POST", "/ack", None, {"receipt": str(i)}),
            ),
        )
        for i, case in enumerate(_cases())
    ]
    result = qualify(cases, 1, SELECTOR)
    assert result["values"]["none"] == "1"
    assert result["relations"]["oldest_to_newest"] == "equivalent"
    assert not result["retention_pattern"] and not result["refresh_restores_full_value"]


def test_prior_receipt_can_contain_information_not_in_a_slot_note() -> None:
    first, second = _cases()
    second = replace(second, history=first.history, refresh=first.refresh)
    receipt = _frame(
        "GET",
        "/inbox",
        None,
        {"reports": [{"slot": "A", "receipt": "prior-private-id"}]},
    )
    second = replace(second, history=(receipt, *second.history[1:]))
    result = qualify([first, second], 1, SELECTOR)
    assert result["values"]["full"] == "1"
    assert result["values"]["newest"] == "1/2"
    assert result["relations"]["newest_to_full"] == "refinement"
    assert not result["retention_pattern"] and not result["refresh_restores_full_value"]
    assert "prior-private-id" not in json.dumps(result)


def test_different_note_widths_do_not_qualify_even_with_a_value_gain() -> None:
    first, _ = _cases()
    second = _episode("hidden-replace", "A", "BB", "two")
    result = qualify([first, second], 1, SELECTOR)
    assert result["values"]["newest"] == "1"
    assert not result["equal_note_bytes"] and not result["equal_packet_bytes"]
    assert not result["retention_pattern"]
    assert result["refresh_restores_full_value"]


def test_reverse_partition_direction_does_not_qualify() -> None:
    first, _ = _cases()
    second = _episode("hidden-replace", "B", "A", "two")
    result = qualify([first, second], 1, SELECTOR)
    assert result["relations"]["oldest_to_newest"] == "coarsening"
    assert result["retention_gain"] == "-1/2"
    assert not result["retention_pattern"]


def test_genuine_empty_refresh_is_not_the_same_as_omission() -> None:
    cases = [replace(case, refresh=_read()) for case in _cases()]
    result = qualify(cases, 1, SELECTOR)
    assert result["retention_pattern"]
    assert result["refresh_gain"] == "0"
    assert not result["refresh_restores_full_value"]
    assert _decoded(cases[0], "none")["additional_read"] is None
    assert _decoded(cases[0], "refresh")["additional_read"] == _read()


@pytest.mark.parametrize(
    "invalid",
    [
        _frame("GET", "/inbox", None, {}),
        _frame("GET", "/inbox", None, {"reports": {"slot": "A"}}),
        _frame("GET", "/inbox", None, {"reports": [{"slot": "A"}, {}]}),
        _frame("GET", "/inbox", None, {"reports": [{"slot": True}]}),
        _read(""),
        {**_read("A"), "status": 404},
        {**_read("A"), "status": True},
        {**_read("A"), "private": "not-reader-context"},
        _frame(
            "GET", "/inbox", None, {"reports": [{"slot": "A", "extra": float("nan")}]}
        ),
        _frame(
            "GET",
            "/inbox",
            None,
            {"reports": [{"slot": "A", "extra": {1: "not-json"}}]},
        ),
    ],
)
def test_malformed_matching_frames_fail_closed(invalid: dict[str, Any]) -> None:
    case = _cases()[0]
    for changed in (
        replace(case, history=(case.history[0], invalid, *case.history[2:])),
        replace(case, history=(*case.history[:-1], invalid)),
        replace(case, refresh=invalid),
    ):
        with pytest.raises(ValueError):
            packet(changed, "none", SELECTOR)


def test_missing_prior_opportunity_cannot_be_replaced_by_current_or_refresh() -> None:
    case = _cases()[0]
    for prior in (_read(), _frame("GET", "/other", None, {"reports": [{"slot": "A"}]})):
        changed = replace(case, history=(prior, _read("current")))
        with pytest.raises(ValueError, match="no prior matching reader candidate"):
            packet(changed, "none", SELECTOR)
    with pytest.raises(ValueError, match="refresh is not a matching reader frame"):
        qualify([replace(case, refresh=case.history[-1])], 1, SELECTOR)


def test_nested_selector_uses_reply_only() -> None:
    case = _cases()[0]
    selector = Selector("GET", "/nested", ("result", "items"), "answer")
    frame = _frame(
        "GET",
        "/nested",
        {"result": {"items": [{"answer": "body"}]}},
        {"result": {"items": [{"answer": "reader"}]}},
    )
    case = replace(case, history=(frame, case.history[-1]), refresh=frame)
    assert json.loads(packet(case, "newest", selector))["memory"] == {"note": "reader"}


def test_invalid_finite_score_matrix_and_unknown_policy_are_refused() -> None:
    first, second = _cases()
    with pytest.raises(ValueError):
        qualify([first, replace(second, scores={"different-choice": 1})], 1, SELECTOR)
    with pytest.raises(ValueError):
        qualify([first, replace(second, mass=True)], 1, SELECTOR)
    with pytest.raises(ValueError):
        qualify([], 1, SELECTOR)
    with pytest.raises(ValueError):
        qualify([first, first], 1, SELECTOR)
    with pytest.raises(ValueError):
        packet(first, "invalid", SELECTOR)
