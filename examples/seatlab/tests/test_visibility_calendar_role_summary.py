from __future__ import annotations

from itertools import product
from typing import cast, override
from unittest import TestCase

from examples.seatlab.visibility.calendar_tools_summary import summarize

_ROLE_POLICIES = ("sender", "receiver")
_INVALID_ZERO = "model_invalid_scored_zero"
type _Key = tuple[int, int, int, str, str]


class TestCalendarRoleSummary(TestCase):
    @override
    def setUp(self) -> None:
        self.rows: dict[_Key, dict[str, object]] = {
            (world, direction, repetition, room, policy): {
                "world": world,
                "direction": direction,
                "repetition": repetition,
                "room": room,
                "policy": policy,
                "native_status": "not_started",
                "protocol_bound": False,
                "reward": None,
                "slot": None,
            }
            for world, direction, repetition, room, policy in product(
                range(12), range(2), range(2), ("blind", "peer"), _ROLE_POLICIES
            )
        }

    def score(
        self, key: _Key, reward: int, slot: int | None, *, bound: bool = True
    ) -> None:
        self.rows[key].update(
            native_status=_INVALID_ZERO if slot is None else "complete",
            protocol_bound=bound,
            reward=reward,
            slot=slot,
        )

    def test_receiver_minus_sender_weights_worlds_in_each_direction(self) -> None:
        for key in self.rows:
            world, direction, repetition, room, policy = key
            if world == 0 and (repetition, room) != (0, "blind"):
                continue
            winner = _ROLE_POLICIES[direction if world == 0 else 1 - direction]
            reward = int(policy == winner)
            self.score(key, reward, reward)

        report = summarize(reversed(self.rows.values()), policies=_ROLE_POLICIES)

        self.assertEqual(report["primary"]["equal_world_mean"], 0)
        directions = report["by_direction"]
        for direction, expected, bounds, wins in (
            ("0", 10 / 12, [10 / 12, 11.5 / 12], (1, 44)),
            ("1", -10 / 12, [-11.5 / 12, -10 / 12], (44, 1)),
        ):
            with self.subTest(direction=direction):
                part = directions[direction]
                primary = part["primary"]
                self.assertEqual((primary["pairs"], primary["worlds"]), (45, 12))
                self.assertEqual(primary["equal_world_mean"], expected)
                self.assertEqual(
                    part["accepted_pairs_only"]["equal_world_mean"], expected
                )
                self.assertEqual(part["all_registered_pair_bounds"], bounds)
                native = part["native_counts"]
                self.assertEqual(tuple(native), _ROLE_POLICIES)
                self.assertEqual(
                    (native["sender"]["wins"], native["receiver"]["wins"]), wins
                )

    def test_rejects_duplicate_teams_and_mixed_design_rows(self) -> None:
        rows = list(self.rows.values())
        with self.assertRaisesRegex(ValueError, "duplicate registered"):
            summarize([*rows[:-1], rows[0]], policies=_ROLE_POLICIES)
        with self.assertRaisesRegex(ValueError, "coordinates"):
            summarize(
                [{**rows[0], "policy": "off"}, *rows[1:]], policies=_ROLE_POLICIES
            )
        with self.assertRaisesRegex(ValueError, "coordinates"):
            summarize(rows)

    def test_rejects_unregistered_arm_tuples(self) -> None:
        invalid: tuple[object, ...] = (
            ("receiver", "sender"),
            ("on", "off"),
            ("sender", "on"),
            ("sender", "sender"),
            ("Sender", "receiver"),
            ("sender", "receiver", "on"),
            ("sender",),
            ("sender", False),
            ["sender", "receiver"],
        )
        for arms in invalid:
            with (
                self.subTest(arms=arms),
                self.assertRaisesRegex(ValueError, "registered policy arms"),
            ):
                summarize(self.rows.values(), policies=cast("tuple[str, str]", arms))

    def test_repeat_denominators_require_bound_matching_cells(self) -> None:
        self.score((0, 0, 0, "blind", "sender"), 1, 2)
        self.score((0, 0, 1, "blind", "sender"), 1, 3)
        self.score((1, 1, 0, "peer", "sender"), 0, 4)
        self.score((1, 1, 1, "peer", "sender"), 0, 4)
        for world, direction, repetition, room in (
            (2, 0, 0, "blind"),
            (2, 1, 1, "blind"),
            (3, 0, 0, "blind"),
            (3, 0, 1, "peer"),
            (4, 0, 0, "blind"),
            (5, 0, 1, "blind"),
        ):
            self.score((world, direction, repetition, room, "sender"), 1, 2)
        self.score((6, 0, 0, "blind", "sender"), 1, 2)
        self.score((6, 0, 1, "blind", "sender"), 1, 2, bound=False)
        self.score((0, 0, 0, "blind", "receiver"), 0, None)
        self.score((0, 0, 1, "blind", "receiver"), 1, 3)
        self.score((1, 1, 0, "peer", "receiver"), 0, None)
        self.score((1, 1, 1, "peer", "receiver"), 0, None)
        self.score((7, 0, 0, "blind", "receiver"), 0, None)

        report = summarize(self.rows.values(), policies=_ROLE_POLICIES)

        self.assertEqual(
            report.get("repeat_agreement"),
            {
                "sender": {
                    "accepted_slot_pairs": 2,
                    "slot_agreements": 1,
                    "slot_disagreements": 1,
                    "native_reward_pairs": 2,
                    "native_reward_agreements": 2,
                    "native_reward_disagreements": 0,
                },
                "receiver": {
                    "accepted_slot_pairs": 0,
                    "slot_agreements": 0,
                    "slot_disagreements": 0,
                    "native_reward_pairs": 2,
                    "native_reward_agreements": 1,
                    "native_reward_disagreements": 1,
                },
            },
        )
