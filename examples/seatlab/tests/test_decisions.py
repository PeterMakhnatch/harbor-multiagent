import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

_DECISIONS = Path(__file__).resolve().parents[1] / "decisions.py"
_ARRIVALS = {
    "silent": (),
    "stale": (0,),
    "fresh": (1,),
    "reordered": (1, 0),
    "pooled": (),
}
_MASKS = (("AB", "AB"), ("AC", "AD"), ("AB", "AB"), ("AB", "BC"))
_PRIVATE_TEXT = "private-host-or-agent-data"


def _trial_id(index: int) -> str:
    return f"10e6cc6c-3107-4ad4-86cf-{index:012x}"


def _write_trial(
    root: Path,
    condition: str,
    index: int,
    choices: str,
    reward: float,
    *,
    wrong_current_report: bool = False,
) -> Path:
    cases = []
    for episode, ((before, after), choice) in enumerate(
        zip(_MASKS, choices, strict=True)
    ):
        reported_after = "CD" if wrong_current_report and episode == 2 else after
        reports = [
            {"revision": 1, "available": list(before)},
            {"revision": 2, "available": list(reported_after)},
        ]
        cases.append(
            {
                "id": episode,
                "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
                "before": list(before),
                "after": list(after),
                "chosen": choice,
                "reports": None if condition == "pooled" else reports,
                "visible": [reports[i] for i in _ARRIVALS[condition]],
            }
        )
    dossier = {
        "schema": "seatlab.world/v1",
        "settings": {
            "schema": "seatlab.scenario/v1",
            "condition": condition,
            "seed": 17,
            "episodes": len(cases),
        },
        "complete": True,
        "cases": cases,
    }
    inventory = "nop" if condition == "pooled" else "oracle"
    native = {
        "schema_version": 1,
        "id": _trial_id(index),
        "finished_at": "2026-10-01T00:00:02Z",
        "reward_valid": True,
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": reward}},
        "config": {
            "participants": [
                {"seat": "inventory", "name": inventory},
                {"seat": "dispatch", "import_path": "seat_agent:SeatAgent"},
            ],
        },
        "seat_results": [
            {
                "seat": seat,
                "under_test": True,
                "agent_info": {"name": name, "version": _PRIVATE_TEXT},
                "agent_result": {},
                "exception_info": None,
                "agent_execution": {
                    "started_at": "2026-10-01T00:00:00Z",
                    "finished_at": "2026-10-01T00:00:01Z",
                },
            }
            for seat, name in (("inventory", inventory), ("dispatch", "seatlab"))
        ],
    }
    artifact = root / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(dossier), encoding="utf-8")
    (root / "result.json").write_text(json.dumps(native), encoding="utf-8")
    return root


def _run(*trials: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_DECISIONS), *map(str, trials)],
        capture_output=True,
        text=True,
        check=False,
    )


class DecisionCutTest(TestCase):
    def _rows(self, *trials: Path) -> list[dict[str, Any]]:
        result = _run(*trials)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(_PRIVATE_TEXT, result.stdout)
        self.assertNotIn(str(trials[0]), result.stdout)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_supply_change_report_accuracy_and_choice_agreement_are_separate(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            trial = _write_trial(
                Path(directory) / "reordered",
                "reordered",
                1,
                "BACA",
                0.25,
                wrong_current_report=True,
            )
            wrong, changed_same_best, wrong_report, matches_old = self._rows(trial)

        self.assertTrue(wrong["nonoptimal_with_correct_current_report"])
        self.assertFalse(wrong["highest_revision"]["choice_matches_best"])
        self.assertFalse(wrong["last_arrival"]["choice_matches_best"])
        self.assertTrue(changed_same_best["changed"])
        self.assertTrue(changed_same_best["report_rules_agree"])
        self.assertTrue(changed_same_best["highest_revision"]["choice_matches_best"])
        self.assertTrue(changed_same_best["last_arrival"]["choice_matches_best"])
        self.assertEqual(changed_same_best["truth_optimal"], "A")

        self.assertEqual(
            wrong_report["highest_revision"],
            {
                "revision": 2,
                "best": "C",
                "report_matches_revision": False,
                "choice_matches_best": True,
            },
        )
        self.assertFalse(wrong_report["changed"])
        self.assertFalse(wrong_report["report_rules_agree"])
        self.assertFalse(wrong_report["nonoptimal_with_correct_current_report"])
        self.assertFalse(wrong_report["is_optimal"])
        self.assertEqual(wrong_report["truth_optimal"], "A")
        self.assertEqual(wrong_report["chosen"], "C")
        self.assertTrue(matches_old["choice_matches_revision_1"])
        self.assertFalse(matches_old["report_rules_agree"])
        self.assertTrue(matches_old["last_arrival"]["report_matches_revision"])
        self.assertFalse(matches_old["highest_revision"]["choice_matches_best"])

    def test_paired_arrival_choices_match_stale_and_oracle_matches_current(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rows = self._rows(
                _write_trial(root / "oracle", "reordered", 1, "AAAB", 1),
                _write_trial(root / "arrival", "reordered", 2, "AAAA", 0.75),
                _write_trial(root / "stale", "stale", 3, "AAAA", 0.75),
                _write_trial(root / "fresh", "fresh", 4, "AAAB", 1),
            )
        self.assertEqual(len(rows), 16)
        self.assertEqual({row["seed"] for row in rows}, {17})
        self.assertEqual(len({row["fingerprint"] for row in rows}), 1)
        self.assertRegex(rows[0]["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [(row["trial_id"], row["id"]) for row in rows],
            [
                (_trial_id(index), episode)
                for index in range(1, 5)
                for episode in range(4)
            ],
        )
        oracle, arrival, stale, fresh = (rows[i : i + 4] for i in (0, 4, 8, 12))
        self.assertTrue(
            all(row["highest_revision"]["choice_matches_best"] for row in oracle)
        )
        self.assertTrue(
            all(row["last_arrival"]["choice_matches_best"] for row in arrival)
        )
        self.assertEqual(
            [row["chosen"] for row in stale], [row["chosen"] for row in arrival]
        )
        self.assertEqual(
            [row["chosen"] for row in fresh], [row["chosen"] for row in oracle]
        )
        self.assertEqual(stale[3]["highest_revision"]["revision"], 1)
        self.assertIsNone(stale[3]["nonoptimal_with_correct_current_report"])
        self.assertEqual(fresh[3]["last_arrival"]["revision"], 2)
        self.assertIsNone(fresh[3]["choice_matches_revision_1"])

    def test_silent_has_no_report_optimum_and_pooled_uses_direct_availability(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rows = self._rows(
                _write_trial(root / "silent", "silent", 1, "AAAA", 0.75),
                _write_trial(root / "pooled", "pooled", 2, "AAAB", 1),
            )
        for row in rows:
            with self.subTest(condition=row["condition"], episode=row["id"]):
                self.assertIsNone(row["highest_revision"])
                self.assertIsNone(row["last_arrival"])
                self.assertIsNone(row["report_rules_agree"])
                self.assertIsNone(row["nonoptimal_with_correct_current_report"])
                self.assertIsNone(row["choice_matches_revision_1"])
                if row["condition"] == "silent":
                    self.assertIsNone(row["direct_best"])
                else:
                    self.assertEqual(row["direct_best"], row["truth_optimal"])
        self.assertFalse(rows[3]["is_optimal"])
        self.assertEqual(rows[7]["direct_best"], "B")

    def test_cli_rejects_unverified_trials_without_emitting_partial_or_raw_data(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = _run(
                _write_trial(root / "valid", "fresh", 1, "AAAB", 1),
                _write_trial(root / _PRIVATE_TEXT, "fresh", 2, "AAAB", 0),
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("trial 2:", result.stderr)
        self.assertNotIn(_PRIVATE_TEXT, result.stderr)
