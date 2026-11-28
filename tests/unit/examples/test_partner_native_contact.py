from typing import Any
from uuid import UUID

import pytest

from examples.joint_reopen.observation import validate_observation
from examples.seatlab.coupled_retention import native
from examples.seatlab.partner_native import contact


ROLES = ("east", "west")


def _observation(
    seed: int,
    votes: tuple[int | None, int | None],
    before: tuple[str, ...] = (),
    after_east_vote: tuple[str, ...] = (),
    *,
    fixture: str = "both",
    channel: str = "deliver",
) -> dict[str, Any]:
    world = native.Meeting(seed, fixture, channel, str(UUID(int=seed)))
    slots = native.permutation(seed)
    for role in ROLES:
        assert world.handle(role, "POST", "/endorse", {"slot": slots[0]}) == (
            200,
            {"ok": True},
        )
    for role in ROLES:
        assert world.handle(role, "POST", "/report", {"slots": []}) == (
            200,
            {"ok": True},
        )

    def read_inbox(roles: tuple[str, ...]) -> None:
        for role in roles:
            assert world.handle(role, "GET", "/inbox", {}) == (
                200,
                {
                    "delivered": channel == "deliver",
                    "slots": [] if channel == "deliver" else None,
                },
            )

    read_inbox(before)
    for role, vote in zip(ROLES, votes, strict=True):
        if vote is not None:
            assert world.handle(role, "POST", "/vote", {"slot": slots[vote]}) == (
                200,
                {"ok": True},
            )
        if role == "east":
            read_inbox(after_east_vote)
    observation = native._verify.evaluate(world.freeze())
    return validate_observation(observation, seed, fixture, channel)


def test_outcome_by_receipt_uses_the_receiving_roles_own_vote() -> None:
    partial = _observation(19, (1, None), ROLES)
    no_votes = _observation(20, (None, None), ROLES)
    split = _observation(21, (2, 3), ROLES)
    unavailable = _observation(22, (0, 0), after_east_vote=ROLES)
    repaired = _observation(23, (1, 1), ROLES)
    assert partial["roles"]["west"]["peer_report_returned"] is True
    assert partial["roles"]["west"]["peer_report_before_vote"] is None
    assert unavailable["roles"]["east"]["peer_report_returned"] is True
    assert unavailable["roles"]["east"]["peer_report_before_vote"] is False
    assert repaired["roles"]["east"]["report_true"] is None
    assert contact.summarize(
        [partial, no_votes, split, unavailable, repaired, None]
    ) == {
        "registered": 6,
        "observed": 5,
        "unknown": 1,
        "outcomes": {
            "no_vote_pair": 2,
            "split_vote": 1,
            "same_unavailable": 1,
            "repaired": 1,
        },
        "receipt_before_vote": {
            "both": 2,
            "east_only": 1,
            "west_only": 1,
            "neither": 1,
        },
        "outcome_by_receipt": {
            "no_vote_pair": {"both": 0, "east_only": 1, "west_only": 0, "neither": 1},
            "split_vote": {"both": 1, "east_only": 0, "west_only": 0, "neither": 0},
            "same_unavailable": {
                "both": 0,
                "east_only": 0,
                "west_only": 1,
                "neither": 0,
            },
            "repaired": {"both": 1, "east_only": 0, "west_only": 0, "neither": 0},
        },
    }


def test_unobserved_slots_never_count_as_events() -> None:
    zero_receipts = {"both": 0, "east_only": 0, "west_only": 0, "neither": 0}
    assert contact.summarize([None] * 6) == {
        "registered": 6,
        "observed": 0,
        "unknown": 6,
        "outcomes": {
            "no_vote_pair": 0,
            "split_vote": 0,
            "same_unavailable": 0,
            "repaired": 0,
        },
        "receipt_before_vote": zero_receipts,
        "outcome_by_receipt": {
            "no_vote_pair": zero_receipts,
            "split_vote": zero_receipts,
            "same_unavailable": zero_receipts,
            "repaired": zero_receipts,
        },
    }
    repaired = _observation(24, (1, 1))
    matrix = contact.summarize([repaired, None, None, None, None, None])
    assert matrix["outcome_by_receipt"]["repaired"]["neither"] == 1


@pytest.mark.parametrize("count", [0, 5, 7])
def test_requires_six_slots(count: int) -> None:
    with pytest.raises(ValueError, match="six"):
        contact.summarize([None] * count)


@pytest.mark.parametrize(
    ("fixture", "channel"), [("east", "deliver"), ("both", "withhold")]
)
def test_rejects_other_native_conditions(fixture: str, channel: str) -> None:
    observation = _observation(19, (1, 1), ROLES, fixture=fixture, channel=channel)
    with pytest.raises(ValueError, match="unsupported observation condition"):
        contact.summarize([observation, None, None, None, None, None])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("final_joint", 1),
        ("final_feasible", 1),
        ("repaired_invalid_incumbent", 1),
        ("kept_preferred_incumbent", False),
        ("reward", True),
        ("reward", 1.0),
        ("reward", 2),
        ("reward", 0),
        ("final_feasible", None),
    ],
)
def test_rejects_invalid_final_fields(field: str, value: object) -> None:
    observation = _observation(19, (1, 1), ROLES)
    observation[field] = value
    with pytest.raises(ValueError, match="final observation fields"):
        contact.summarize([observation, None, None, None, None, None])


@pytest.mark.parametrize("field", ["reward", "final_joint", "final_feasible", "roles"])
def test_rejects_missing_observation_fields(field: str) -> None:
    observation = _observation(19, (1, 1), ROLES)
    del observation[field]
    with pytest.raises(ValueError, match="observation fields"):
        contact.summarize([observation, None, None, None, None, None])


def test_rejects_missing_or_invalid_receipt_fields() -> None:
    observation = _observation(19, (1, 1), ROLES)
    observation["roles"]["west"]["peer_report_before_vote"] = 1
    with pytest.raises(ValueError, match="peer_report_before_vote"):
        contact.summarize([observation, None, None, None, None, None])
    del observation["roles"]["west"]["peer_report_before_vote"]
    with pytest.raises(ValueError, match="role fields"):
        contact.summarize([observation, None, None, None, None, None])
