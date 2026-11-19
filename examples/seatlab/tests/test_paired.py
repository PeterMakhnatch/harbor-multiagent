import hashlib
import json
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

_PAIRED = run_path(str(Path(__file__).resolve().parents[1] / "paired.py"))
Panel = _PAIRED["Panel"]
design = _PAIRED["design"]
read_panel = _PAIRED["read_panel"]
summarize = _PAIRED["summarize"]


def _fingerprint(seed: int) -> str:
    return hashlib.sha256(f"latent-batch:{seed}".encode()).hexdigest()


def _trial(
    seed: int,
    condition: str,
    stable: int = 2,
    changed: int = 0,
    *,
    group: str = "oracle",
    reverse_rows: bool = False,
) -> dict[str, Any]:
    rows = [
        {"id": 0, "changed": False, "optimal": stable == 2, "feasible": stable > 0},
        {"id": 1, "changed": True, "optimal": changed == 2, "feasible": changed > 0},
    ]
    if reverse_rows:
        rows.reverse()
    return {
        "trial_id": f"{group}:{condition}:{seed}",
        "seed": seed,
        "condition": condition,
        "fingerprint": _fingerprint(seed),
        "score": {"rows": rows},
    }


class PairedTest(TestCase):
    def test_pair_by_episode_id_and_count_seed_blocks_separately(self) -> None:
        trials = [
            _trial(17, "silent", 2, 0),
            _trial(17, "fresh", 2, 2, reverse_rows=True),
            _trial(29, "silent", 0, 2),
            _trial(29, "fresh", 1, 0),
        ]
        summary = summarize({"control": Panel("oracle", trials)})
        self.assertEqual(
            summary["units"],
            {
                "paired_seed_blocks": 2,
                "native_trials": 4,
                "distinct_latent_batches": 2,
                "latent_episode_rows": {"all": 4, "stable": 2, "changed": 2},
            },
        )
        splits = summary["cells"]["control:fresh"]["splits"]
        stable, changed = splits["stable"], splits["changed"]
        self.assertEqual((stable["optimal"], stable["feasible"]), (1, 2))
        self.assertEqual(
            stable["paired"]["feasible"]["episodes"],
            {"wins": 1, "losses": 0, "ties": 1, "net": 1},
        )
        self.assertEqual(
            changed["paired"]["optimal"]["episodes"],
            {"wins": 1, "losses": 1, "ties": 0, "net": 0},
        )
        self.assertEqual(
            splits["all"]["paired"]["optimal"]["seed_blocks"],
            {"wins": 1, "losses": 1, "ties": 0, "net": 0},
        )

    def test_rejects_swapped_latent_batches_even_with_equal_row_counts(self) -> None:
        fresh_17, fresh_29 = _trial(17, "fresh"), _trial(29, "fresh")
        fresh_17["fingerprint"], fresh_29["fingerprint"] = (
            fresh_29["fingerprint"],
            fresh_17["fingerprint"],
        )
        trials = [_trial(17, "silent"), _trial(29, "silent"), fresh_17, fresh_29]
        with self.assertRaisesRegex(ValueError, "seed 17: latent fingerprint mismatch"):
            summarize({"control": Panel("oracle", trials)})

    def test_rejects_partial_pairs_and_duplicate_coordinates(self) -> None:
        partial = [_trial(17, "silent"), _trial(17, "fresh"), _trial(29, "silent")]
        with self.assertRaisesRegex(
            ValueError, "unpaired cells: control:fresh seed 29"
        ):
            summarize({"control": Panel("oracle", partial)})
        duplicate = _trial(17, "fresh", group="another-attempt")
        with self.assertRaisesRegex(ValueError, "duplicate cell or native trial"):
            summarize({"control": Panel("oracle", [*partial, duplicate])})

    def test_keeps_named_policies_separate_with_common_reference(self) -> None:
        arrival = Panel(
            "arrival", [_trial(17, "silent", 0, 0), _trial(17, "fresh", 2, 2)]
        )
        oracle = Panel(
            "oracle",
            [
                _trial(17, "silent", 2, 0, group="version"),
                _trial(17, "fresh", 2, 0, group="version"),
            ],
        )
        result = summarize({"arrival": arrival, "version": oracle}, "arrival:silent")
        self.assertEqual(result["groups"], {"arrival": "arrival", "version": "oracle"})
        cell = result["cells"]["version:silent"]
        self.assertEqual(cell["reference"], "arrival:silent")
        self.assertEqual(
            cell["splits"]["all"]["paired"]["optimal"]["episodes"]["net"], 1
        )
        self.assertEqual(result["maximin_groups"], ["version"])

    def test_rejects_reusing_a_native_trial_under_a_different_tag(self) -> None:
        panel = Panel("oracle", [_trial(17, "silent"), _trial(17, "fresh")])
        with self.assertRaisesRegex(ValueError, "duplicate cell or native trial"):
            summarize({"first": panel, "second": panel})

    def test_incomplete_receipts_fail_before_reading_any_native_trial(self) -> None:
        cells = [
            {"cell_id": condition, "seed": 17, "condition": condition}
            for condition in ("silent", "fresh")
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plan.json").write_text(
                json.dumps(
                    {
                        "schema": "seatlab.panel-plan/v1",
                        "policy": "oracle",
                        "seeds": [17],
                        "conditions": ["silent", "fresh"],
                        "episodes": 2,
                        "cells": cells,
                    }
                )
            )
            (root / "runs.json").write_text(
                json.dumps(
                    {
                        "schema": "seatlab.panel-runs/v1",
                        "cells": [dict(cells[0], status="measured")],
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, r"missing \['fresh'\]"):
                read_panel(root)


class DesignTest(TestCase):
    def test_exact_joint_counts_and_control_aliases(self) -> None:
        result = design()
        stable, changed = (result["splits"][name] for name in ("stable", "changed"))
        self.assertEqual((stable["states"], changed["states"]), (144, 576))
        self.assertEqual(
            changed["joint_counts_silent_then_stale"],
            {
                "optimal": {"optimal": 144, "feasible_only": 72, "infeasible": 72},
                "feasible_only": {"optimal": 0, "feasible_only": 0, "infeasible": 0},
                "infeasible": {"optimal": 48, "feasible_only": 24, "infeasible": 216},
            },
        )
        self.assertEqual(
            stable["joint_counts_silent_then_stale"],
            {
                "optimal": {"optimal": 72, "feasible_only": 0, "infeasible": 0},
                "feasible_only": {"optimal": 0, "feasible_only": 0, "infeasible": 0},
                "infeasible": {"optimal": 72, "feasible_only": 0, "infeasible": 0},
            },
        )
        self.assertEqual(
            changed["rates"]["highest_old"], {"optimal": "1/3", "feasible": "1/2"}
        )
        policy = result["condition_to_policy"]
        self.assertEqual(policy["reordered_last_arrival"], policy["stale"])
        self.assertEqual(policy["reordered_version_aware"], policy["fresh"])
        self.assertEqual(policy["pooled"], policy["fresh"])
