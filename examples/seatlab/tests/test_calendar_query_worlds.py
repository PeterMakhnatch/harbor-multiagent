from __future__ import annotations

from typing import Any
from unittest import TestCase

from examples.seatlab.visibility.calendar_packet import PACKET_SHA256
from examples.seatlab.visibility.calendar_query_worlds import verify_slots


def _row(
    world: int, direction: int, *, private_slot: int, joint_slot: int
) -> dict[str, Any]:
    return {
        "world": world,
        "direction": direction,
        "arms": {
            "blind": {"reward": 0, "slot": private_slot},
            "peer": {"reward": 1, "slot": joint_slot},
        },
    }


class TestCalendarQueryWorlds(TestCase):
    def test_checks_joint_and_private_only_starts_in_each_world(self) -> None:
        # Packet world 1: slot 11 is joint; slot 0 is private to p1.
        # Packet world 2: slot 26 is joint; slot 32 is private to p2.
        rows = [
            _row(1, 1, private_slot=0, joint_slot=11),
            _row(2, 0, private_slot=32, joint_slot=26),
        ]
        self.assertEqual(
            verify_slots(rows),
            {
                "schema": "seatlab.calendar-query-world-check/v1",
                "packet_sha256": PACKET_SHA256,
                "verified_successes": 2,
                "verified_submissions": 4,
                "scored_without_slot": 0,
                "unscored": 0,
            },
        )

    def test_rejects_wrong_reward_in_either_arm(self) -> None:
        # Slot 2 is private to p2 in packet world 1.
        for name, reward in (("blind", 1), ("peer", 0)):
            with self.subTest(arm=name):
                row = _row(1, 0, private_slot=2, joint_slot=11)
                row["arms"][name]["reward"] = reward
                with self.assertRaisesRegex(
                    ValueError, rf"^w01 d0 {name}: reward mismatch$"
                ):
                    verify_slots([row])

    def test_distinguishes_native_zero_without_slot_from_unknown(self) -> None:
        row = _row(1, 0, private_slot=2, joint_slot=11)
        row["arms"]["blind"]["slot"] = None
        row["arms"]["peer"].update(reward=None, slot=None)
        result = verify_slots([row])
        self.assertEqual(result["scored_without_slot"], 1)
        self.assertEqual(result["unscored"], 1)
        self.assertEqual(result["verified_submissions"], 0)
        self.assertEqual(result["verified_successes"], 0)
