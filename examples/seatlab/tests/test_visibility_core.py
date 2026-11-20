import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from examples.seatlab.visibility import Event, Trial, measure_seats, project, summarize
from examples.seatlab.visibility.core import EventKind, MessagePair, ReceiptCounts

_PRIVATE = "private-fixture-marker"
_ONE = ("blue", f"{_PRIVATE}-one")
_TWO = ("blue", f"{_PRIVATE}-two")
_THREE = ("blue", f"{_PRIVATE}-three")
_OWN = ("amber", f"{_PRIVATE}-own")
_SYSTEM = ("world", f"{_PRIVATE}-system")


@pytest.fixture
def trial() -> Trial:
    return Trial(
        key=f"{_PRIVATE}-trial",
        arm="fixture/talk",
        seats=("amber", "blue"),
        events=(
            Event("send", "blue", (_ONE, _TWO, _THREE)),
            Event("action", "amber", (_SYSTEM,)),
            Event("available", "amber", (_OWN, _THREE, _SYSTEM)),
            Event("fetch", "amber", (_ONE, _OWN, _SYSTEM)),
            Event("send", "amber", (_OWN,)),
            Event("fetch", "blue", (_OWN,)),
            Event("fetch", "amber", (_ONE, _TWO)),
            Event("bound", "amber", (_TWO,)),
            Event("action", "amber", (_SYSTEM,)),
            Event("fetch", "amber", (_TWO,)),
            Event("fetch", "amber", (_OWN,)),
            Event("fetch", "amber"),
        ),
        coverage={"amber": "complete", "blue": "partial"},
        finished=True,
        sources=(Path(f"/private/{_PRIVATE}.json"),),
    )


def test_projection_removes_only_one_recipients_incoming_peers(trial: Trial) -> None:
    assert project(trial) is trial
    muted = project(trial, "amber")
    assert muted == replace(
        trial,
        events=(
            trial.events[0],
            trial.events[1],
            Event("available", "amber", (_OWN, _SYSTEM)),
            Event("fetch", "amber", (_OWN, _SYSTEM)),
            trial.events[4],
            trial.events[5],
            Event("fetch", "amber"),
            Event("bound", "amber"),
            trial.events[8],
            Event("fetch", "amber"),
            trial.events[10],
            trial.events[11],
        ),
    )
    for index in (0, 1, 4, 5, 8, 10, 11):
        assert muted.events[index] is trial.events[index]
    assert project(muted, "amber") is muted
    assert project(trial, "blue").events[5] == Event("fetch", "blue")


def test_measurements_keep_evidence_repeats_and_known_action_order(trial: Trial) -> None:
    amber, blue = measure_seats(trial)
    assert (amber.seat, blue.seat) == trial.seats
    assert (amber.status, blue.status) == ("changed", "changed")
    assert (amber.coverage, blue.coverage) == ("complete", "partial")
    assert amber.action_events == 2
    assert amber.receipts == {
        "available": ReceiptCounts(1, 1, 1, before_action=1, after_action=0, no_known_action=0),
        "fetch": ReceiptCounts(3, 4, 2, before_action=2, after_action=1, no_known_action=0),
        "bound": ReceiptCounts(1, 1, 1, before_action=1, after_action=0, no_known_action=0),
    }
    assert amber.first_affected is not None
    assert amber.first_affected.event_index == 2
    assert amber.first_affected.kind == "available"
    assert amber.first_affected.followed_by_action
    assert blue.receipts["fetch"] == ReceiptCounts(
        1, 1, 1, before_action=0, after_action=0, no_known_action=1
    )
    assert blue.first_affected is not None
    assert blue.first_affected.timing == "no_known_action"
    assert not blue.first_affected.followed_by_action


def test_aggregate_keeps_coverage_and_availability_separate(trial: Trial) -> None:
    quiet = replace(
        trial,
        key="quiet",
        seats=("amber",),
        events=(Event("action", "amber"), Event("fetch", "amber", (_OWN, _SYSTEM))),
        coverage={"amber": "complete"},
        finished=None,
    )
    send_only = replace(
        quiet, key="send-only", events=(*quiet.events, Event("send", "amber", (_OWN,)))
    )
    unobserved = replace(
        trial,
        key="unobserved",
        events=(),
        coverage={"amber": "partial", "blue": "unavailable"},
        finished=False,
    )
    available_only = replace(
        trial,
        key="available-only",
        events=(Event("action", "amber"), Event("available", "amber", (_THREE,))),
        coverage={"amber": "complete", "blue": "complete"},
    )
    assert project(quiet, "amber") is quiet
    assert project(send_only, "amber") is send_only
    availability, silent = measure_seats(available_only)
    assert availability.first_affected is not None
    assert availability.first_affected.timing == "after_action"
    assert not availability.first_affected.followed_by_action
    assert silent.first_affected is None
    assert availability.receipts["fetch"].events == 0
    assert availability.receipts["bound"].events == 0

    summary = summarize(iter((trial, quiet, send_only, unobserved, available_only)))
    arm = summary["fixture/talk"]
    assert arm["trials"] == 5
    assert arm["trial_status"] == {
        "changed": 2,
        "no_channel_traffic": 1,
        "no_demonstrated_delivery": 2,
    }
    assert arm["seat_coverage"] == {"complete": 5, "partial": 2, "unavailable": 1}
    assert arm["recipient_interventions"] == 8
    assert arm["fetch_or_bound_changed_interventions"] == 2
    assert arm["availability_only_changed_interventions"] == 1
    assert arm["fetch_before_action_interventions"] == 1
    assert arm["fetch_or_bound_before_action_interventions"] == 1
    assert arm["receipts"] == {
        "available": {
            "events": 2,
            "message_occurrences": 2,
            "sum_seat_unique_messages": 2,
            "before_action": 1,
            "after_action": 1,
            "no_known_action": 0,
        },
        "fetch": {
            "events": 4,
            "message_occurrences": 5,
            "sum_seat_unique_messages": 3,
            "before_action": 2,
            "after_action": 1,
            "no_known_action": 1,
        },
        "bound": {
            "events": 1,
            "message_occurrences": 1,
            "sum_seat_unique_messages": 1,
            "before_action": 1,
            "after_action": 0,
            "no_known_action": 0,
        },
    }
    serialized = json.dumps(summary)
    assert not any(value in serialized for value in (_PRIVATE, "amber", "blue", "world"))


def test_bound_input_is_not_counted_as_an_observed_fetch(trial: Trial) -> None:
    recorded_input = replace(
        trial, events=(Event("bound", "amber", (_ONE,)), Event("action", "amber"))
    )
    arm = summarize([recorded_input])["fixture/talk"]
    assert arm["fetch_or_bound_changed_interventions"] == 1
    assert arm["availability_only_changed_interventions"] == 0
    assert arm["fetch_before_action_interventions"] == 0
    assert arm["fetch_or_bound_before_action_interventions"] == 1


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"seats": ("amber", "amber")}, "roster contains duplicate seats"),
        ({"seats": ["amber", "blue"]}, "roster must be a nonempty ordered tuple"),
        ({"coverage": {"amber": "complete"}}, "coverage must have one entry"),
        ({"events": []}, "events must be an ordered tuple"),
        ({"events": (Event(cast(EventKind, _PRIVATE), "amber"),)}, "unknown event kind"),
        ({"events": (Event("action", _PRIVATE),)}, "event seat is not in the roster"),
        (
            {"events": (Event("fetch", "amber", (cast(MessagePair, (_PRIVATE,)),)),)},
            "expected a nonblank sender/message pair",
        ),
    ],
)
def test_validation_does_not_disclose_identifiers(
    trial: Trial, changes: dict[str, object], error: str
) -> None:
    with pytest.raises(ValueError, match=error) as result:
        summarize([replace(trial, **changes)])
    assert _PRIVATE not in str(result.value)


def test_duplicate_trial_keys_are_rejected_across_arms(trial: Trial) -> None:
    with pytest.raises(ValueError, match="trial 1: duplicate trial key") as result:
        summarize([trial, replace(trial, arm="fixture/quiet")])
    assert _PRIVATE not in str(result.value)


def test_mute_requires_a_roster_seat(trial: Trial) -> None:
    with pytest.raises(ValueError, match="muted seat is not in the trial roster") as result:
        project(trial, _PRIVATE)
    assert _PRIVATE not in str(result.value)
