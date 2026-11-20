from __future__ import annotations

from itertools import product
from typing import override
from unittest import TestCase

from examples.seatlab.visibility.calendar_tools_summary import summarize


class TestCalendarToolsSummary(TestCase):
    @override
    def setUp(self) -> None:
        self.rows: dict[tuple[int, int, int, str, str], dict[str, object]] = {
            (w, d, r, room, policy): {
                "world": w,
                "direction": d,
                "repetition": r,
                "room": room,
                "policy": policy,
                "native_status": "not_started",
                "protocol_bound": False,
                "reward": None,
                "slot": None,
            }
            for w, d, r, room, policy in product(
                range(12), range(2), range(2), ("blind", "peer"), ("off", "on")
            )
        }

    def score(
        self,
        key: tuple[int, int, int, str, str],
        reward: int,
        *,
        bound: bool = True,
    ) -> None:
        self.rows[key].update(
            native_status="complete", protocol_bound=bound, reward=reward, slot=0
        )

    def test_missing_bounds_use_one_bound_arm_but_not_raw_unbound_scores(self) -> None:
        self.score((0, 0, 0, "blind", "on"), 1)
        self.score((0, 0, 0, "peer", "off"), 1)
        self.score((0, 0, 1, "blind", "off"), 1, bound=False)
        self.rows[0, 1, 1, "peer", "on"].update(
            native_status="invalid_seed", protocol_bound=True
        )

        report = summarize(self.rows.values())

        self.assertEqual(report["registered_pairs"], 96)
        self.assertEqual(report["primary"]["pairs"], 0)
        self.assertIsNone(report["primary"]["equal_world_mean"])
        bounds = report["all_registered_pair_bounds_by_world"]
        self.assertEqual(bounds["0"], [-7 / 8, 7 / 8])
        self.assertEqual(bounds["4"], [-1, 1])
        self.assertEqual(report["all_registered_pair_bounds"], [-95 / 96, 95 / 96])
        for room, expected in (("blind", [-3 / 4, 1]), ("peer", [-1, 3 / 4])):
            with self.subTest(room=room):
                part = report["by_room"][room]
                self.assertEqual(
                    part["all_registered_pair_bounds_by_world"]["0"], expected
                )
        self.assertEqual(report["native_only"], 1)
        off = report["native_counts"]["off"]
        self.assertEqual((off["wins"], off["unknown"], off["native_only"]), (2, 94, 1))
        on = report["native_counts"]["on"]
        self.assertEqual(
            (on["scored"], on["protocol_bound"], on["unknown"]), (1, 2, 95)
        )

    def test_equal_world_weights_and_accepted_decisions_with_unequal_coverage(
        self,
    ) -> None:
        only_world_zero_pair = (0, 0, 0, "blind", "on")
        for key in self.rows:
            world, _, _, _, policy = key
            self.score(
                key,
                int(key == only_world_zero_pair or (world == 4 and policy == "off")),
                bound=world != 0 or policy == "off" or key == only_world_zero_pair,
            )
        self.rows[4, 0, 0, "blind", "off"]["reward"] = 0
        self.rows[4, 0, 0, "blind", "on"].update(
            native_status="model_invalid_scored_zero", slot=None
        )

        report = summarize(reversed(self.rows.values()))

        primary = report["primary"]
        self.assertEqual((primary["pairs"], primary["worlds"]), (89, 12))
        self.assertEqual(
            (primary["wins"], primary["ties"], primary["losses"]), (1, 81, 7)
        )
        self.assertEqual(primary["world_counts"]["0"], 1)
        self.assertEqual(primary["world_counts"]["4"], 8)
        self.assertEqual(primary["equal_world_mean"], 1 / 96)
        accepted = report["accepted_pairs_only"]
        self.assertEqual(accepted["pairs"], 88)
        self.assertEqual(accepted["world_counts"]["4"], 7)
        self.assertEqual(accepted["ties"], 80)
        self.assertEqual(accepted["equal_world_mean"], 0)
        on = report["native_counts"]["on"]
        self.assertEqual((on["scored"], on["model_invalid_scored_zero"]), (96, 1))
        self.assertEqual(report["native_only"], 7)
        prior, new = (report["by_cohort"][cohort] for cohort in ("prior", "new"))
        self.assertEqual(
            (prior["registered_pairs"], prior["primary"]["pairs"]), (24, 17)
        )
        self.assertEqual((new["registered_pairs"], new["primary"]["pairs"]), (72, 72))
        self.assertEqual(prior["primary"]["equal_world_mean"], 1 / 24)
        for axis in (
            report["by_room"],
            report["by_direction"],
            report["by_repetition"],
        ):
            first, second = axis.values()
            self.assertEqual(first["primary"]["pairs"], 45)
            self.assertEqual(second["primary"]["pairs"], 44)
            self.assertIsNone(second["primary"]["equal_world_mean"])

    def test_rejects_missing_and_duplicate_registration(self) -> None:
        rows = list(self.rows.values())
        with self.assertRaisesRegex(ValueError, "missing registered"):
            summarize(rows[:-1])
        with self.assertRaisesRegex(ValueError, "duplicate registered"):
            summarize([*rows[:-1], rows[0]])

    def test_rejects_invalid_coordinates_and_inconsistent_native_rows(self) -> None:
        rows = list(self.rows.values())
        cases: list[tuple[dict[str, object], str]] = [
            ({"world": True}, "coordinates"),
            ({"world": 12}, "coordinates"),
            ({"direction": 2}, "coordinates"),
            ({"repetition": False}, "coordinates"),
            ({"room": "solo"}, "coordinates"),
            ({"policy": "none"}, "coordinates"),
            ({"protocol_bound": 0}, "boolean"),
            ({"reward": False}, "reward must be binary"),
            ({"slot": True}, "slot must be an integer"),
            ({"slot": 35}, "slot must be an integer"),
            ({"native_status": "complete", "reward": 1}, "inconsistent"),
            ({"native_status": "complete", "slot": 0}, "inconsistent"),
            (
                {"native_status": "model_invalid_scored_zero", "reward": 1},
                "inconsistent",
            ),
            (
                {"native_status": "model_invalid_scored_zero", "reward": 0, "slot": 0},
                "inconsistent",
            ),
            ({"native_status": "invalid_seed", "reward": 0}, "inconsistent"),
            ({"native_status": "unrecognized"}, "inconsistent"),
        ]
        for changes, error in cases:
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(ValueError, error),
            ):
                summarize([{**rows[0], **changes}, *rows[1:]])
