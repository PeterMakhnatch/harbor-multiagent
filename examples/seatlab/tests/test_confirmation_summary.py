from hashlib import sha256
from unittest import TestCase

from examples.seatlab.confirmation_summary import summarize


def _receipt():
    return {
        "schema": "seatlab.confirmation-native/v1",
        "studies": {
            study: [
                {
                    "world": world,
                    "arm": arm,
                    "reward": None,
                    "trial_ref": None,
                    "complete": False,
                    "settled_calls": 0,
                    "held_calls": 0,
                }
                for world in worlds
                for arm in arms
            ]
            for study, worlds, arms in (
                ("notices", range(6801, 6809), ("count", "named")),
                ("calendar_answers", range(8), ("withheld", "visible")),
                ("dispatch_answers", range(6901, 6909), ("withheld", "visible")),
            )
        },
    }


def _record(row, reward, settled_calls, *, complete=False):
    key = f"{row['world']}:{row['arm']}"
    row.update(
        reward=reward,
        trial_ref=sha256(key.encode()).hexdigest(),
        settled_calls=settled_calls,
        complete=complete,
    )


class ConfirmationSummaryTest(TestCase):
    def test_partial_bounds_keep_unpaired_and_behavior_incomplete_scores(self):
        receipt = _receipt()
        rows = receipt["studies"]["dispatch_answers"]
        _record(rows[0], 0.166666666666667, 4, complete=True)
        _record(rows[1], 0.333333333333333, 4, complete=True)
        _record(rows[2], 0.333333333333333, 3)
        _record(rows[3], 0.666666666666667, 4, complete=True)
        _record(rows[4], 0.833333333333333, 1)
        _record(rows[7], 0.166666666666667, 1)
        rows[6]["held_calls"] = 1

        result = summarize(receipt)
        panel = result["studies"]["dispatch_answers"]
        self.assertEqual(panel["outcome"], "inconclusive")
        self.assertIsNone(panel["mean"])
        self.assertEqual(panel["known_paired_mean"], "1/4")
        self.assertEqual(
            panel["full_register_bounds"], {"low": "-31/48", "high": "29/48"}
        )
        self.assertEqual(
            panel["sign_test"], {"wins": 1, "losses": 0, "ties": 0, "p_value": "1/2"}
        )
        self.assertEqual(
            [row["difference"] for row in panel["worlds"]],
            ["1/6", "1/3", None, None, None, None, None, None],
        )
        self.assertEqual(panel["coverage"]["complete_pairs"], 1)
        self.assertEqual(panel["coverage"]["observed_pairs"], 2)
        self.assertEqual(panel["coverage"]["observed_native_rewards"], 6)
        self.assertEqual(panel["coverage"]["teams_with_settled_calls"], 6)
        self.assertEqual(panel["coverage"]["teams_with_reservations_only"], 1)
        self.assertFalse(result["compound_answer_pass"])

    def test_both_six_shipment_panels_canonicalize_and_validate_scores(self):
        for study in ("notices", "dispatch_answers"):
            with self.subTest(study=study):
                receipt = _receipt()
                control, treatment = receipt["studies"][study][:2]
                _record(control, 0.6666666666666666, 1)
                _record(treatment, 0.666666666666667, 1)
                world = summarize(receipt)["studies"][study]["worlds"][0]
                self.assertEqual(world["difference"], "0")
                self.assertEqual(world["arms"][control["arm"]]["reward"], "2/3")
                self.assertEqual(world["arms"][treatment["arm"]]["reward"], "2/3")

                control["reward"] = 0.2
                with self.assertRaisesRegex(ValueError, "must be a multiple of 1/6"):
                    summarize(receipt)

    def test_calendar_rejects_a_nonbinary_reward(self):
        receipt = _receipt()
        row = receipt["studies"]["calendar_answers"][0]
        _record(row, 0.5, 1)
        with self.assertRaisesRegex(ValueError, "calendar reward must be 0 or 1"):
            summarize(receipt)
