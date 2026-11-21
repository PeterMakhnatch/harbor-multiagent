from __future__ import annotations

from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from examples.seatlab.visibility.calendar_query_summary import _read_study, analyze

_WORLDS = (1, 2, 3, 5, 6, 7, 9, 10, 11)
_ARMS = ("blind", "peer")


def _rows() -> dict[tuple[int, int], dict[str, Any]]:
    return {
        (world, direction): {
            "world": world,
            "direction": direction,
            "repetition": 0,
            "status": "not_started",
            "receiver": ("p2", "p1")[direction],
            "starts": {"p1": None, "p2": None},
            "revised": dict.fromkeys(_ARMS),
            "arms": {
                name: {"reward": None, "slot": None, "trial_id_sha256": None}
                for name in _ARMS
            },
            "turns": [],
        }
        for world, direction in product(_WORLDS, (0, 1))
    }


def _score(row: dict[str, Any], arm: str, reward: int, slot: int | None) -> None:
    index = 4 * row["world"] + 2 * row["direction"] + _ARMS.index(arm)
    row["arms"][arm].update(reward=reward, slot=slot, trial_id_sha256=f"{index:064x}")
    row["status"] = (
        "complete"
        if all(item["reward"] is not None for item in row["arms"].values())
        else "partial"
    )


def _receipt(identifier: str, role: str) -> dict[str, str]:
    return {
        "id": identifier,
        "role": role,
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
    }


class TestCalendarQuerySummary(TestCase):
    def test_bad_study_path_is_not_a_team_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            study = Path(tmp).resolve()
            self.assertEqual({r["status"] for r in _read_study(study)}, {"not_started"})
            with self.assertRaises(FileNotFoundError):
                _read_study(study / "absent")
            (study / ".git").touch()
            with self.assertRaisesRegex(
                ValueError, "private_study_outside_checkout_required"
            ):
                _read_study(study)

    def test_paired_summary_with_unknowns_and_real_candidate_lists(self) -> None:
        rows = _rows()
        for d in (0, 1):
            _score(rows[1, d], "blind", 0, None if d == 0 else 1)
            _score(rows[1, d], "peer", 1, 4)
        _score(rows[2, 0], "blind", 1, 4)
        _score(rows[2, 0], "peer", 0, 3)
        _score(rows[3, 0], "blind", 0, 3)
        _score(rows[3, 0], "peer", 0, 3)
        _score(rows[5, 0], "blind", 1, 4)
        _score(rows[5, 1], "peer", 1, 4)
        rows[5, 1].update(
            status="invalid_evidence", turns=[_receipt("unbound", "sender")]
        )
        rows[6, 0].update(
            status="invalid_seed", turns=[_receipt("failed-seed", "sender")]
        )
        rows[5, 0]["turns"] = [_receipt("partial-seed", "receiver")]
        rows[1, 0].update(
            starts={"p1": [1], "p2": [2, 4]},
            revised={"blind": [], "peer": [2, 3, 4]},
            turns=[_receipt("seed", "receiver"), _receipt("revision", "sender")],
        )
        rows[1, 1].update(
            starts={"p1": [4], "p2": [1, 2, 3]},
            revised={"blind": [4], "peer": [4]},
            turns=[
                _receipt("rx-w01-d1-r0-blind:cal_decide", "receiver"),
                _receipt("rx-w01-d1-r0-peer:cal_decide", "receiver"),
            ],
        )
        result = analyze(reversed(rows.values()))
        coverage = result["coverage"]
        self.assertEqual(
            (coverage["worlds"], coverage["blocks"], coverage["teams"]), (9, 18, 36)
        )
        self.assertEqual(coverage["block_status"]["invalid_evidence"], 1)
        self.assertEqual(
            result["arms"]["blind"],
            {"scored": 5, "success": 2, "model_invalid_scored_zero": 1, "unknown": 13},
        )
        peer = result["arms"]["peer"]
        self.assertEqual((peer["scored"], peer["success"]), (4, 2))
        self.assertEqual(
            result["paired"], {"blocks": 4, "improved": 2, "harmed": 1, "unchanged": 1}
        )
        self.assertEqual(result["native_slot_equality"], {"comparable": 3, "equal": 1})
        self.assertEqual(
            result["native_final_prompt_equality"], {"comparable": 1, "equal": 1}
        )
        self.assertEqual(
            result["registered_peer_minus_blind_bounds"], [-13 / 18, 14 / 18]
        )
        self.assertEqual(result["equal_world"]["worlds_with_pair"], 3)
        self.assertIsNone(result["equal_world"]["peer_minus_blind"])
        self.assertEqual(
            result["equal_world"]["observed_worlds_only_peer_minus_blind"], 0
        )
        self.assertEqual(result["by_world"]["2"]["paired"]["harmed"], 1)
        self.assertEqual(result["by_direction"]["1"]["paired"]["improved"], 1)

        recommendations = result["recommendations"]
        self.assertEqual(recommendations["blocks_either_seat_exceeded"], 2)
        self.assertEqual(
            recommendations["starts_by_seat"]["p2"]["exceeded_requested"], 1
        )
        overlap = recommendations["overlap_with_original_receiver_candidates"]
        self.assertEqual(
            overlap["arms"]["blind"],
            {"comparable": 2, "with_overlap": 1, "shared_starts": 1},
        )
        self.assertEqual(
            overlap["arms"]["peer"],
            {"comparable": 2, "with_overlap": 2, "shared_starts": 3},
        )
        self.assertEqual(
            overlap["paired"],
            {"blocks": 2, "increased": 1, "decreased": 0, "unchanged": 1},
        )
        self.assertEqual(
            result["response_receipts_by_role"], {"sender": 2, "receiver": 4}
        )

    def test_panel_estimate_weights_worlds_instead_of_available_pairs(self) -> None:
        rows = _rows()
        for world in _WORLDS:
            _score(rows[world, 0], "blind", 1, 4)
            _score(rows[world, 0], "peer", 0, 3)
        _score(rows[1, 1], "blind", 0, 3)
        _score(rows[1, 1], "peer", 1, 4)

        result = analyze(rows.values())

        self.assertEqual(result["equal_world"]["peer_minus_blind"], -8 / 9)
        self.assertEqual(result["registered_peer_minus_blind_bounds"], [-16 / 18, 0])

    def test_rejects_wrong_registration_and_contradictory_evidence(self) -> None:
        rows = list(_rows().values())
        with self.assertRaisesRegex(ValueError, "missing registered"):
            analyze(rows[1:])
        with self.assertRaisesRegex(ValueError, "duplicate registered"):
            analyze([rows[0], *rows])
        with self.assertRaisesRegex(ValueError, "unregistered"):
            analyze([{**rows[0], "world": 4}, *rows[1:]])
        with self.assertRaisesRegex(ValueError, "contradictory block status"):
            analyze([{**rows[0], "status": "complete"}, *rows[1:]])

        _score(rows[0], "blind", 1, 4)
        rows[0]["arms"]["blind"]["trial_id_sha256"] = None
        with self.assertRaisesRegex(ValueError, "lacks a trial digest"):
            analyze(rows)
        _score(rows[0], "blind", 1, 4)
        _score(rows[0], "peer", 0, 4)
        with self.assertRaisesRegex(ValueError, "rewards for the same slot"):
            analyze(rows)
        _score(rows[0], "peer", 0, 3)
        digest = rows[0]["arms"]["blind"]["trial_id_sha256"]
        rows[1]["arms"]["blind"]["trial_id_sha256"] = digest
        with self.assertRaisesRegex(ValueError, "reused native trial identity"):
            analyze(rows)
