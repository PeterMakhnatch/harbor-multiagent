import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase
from uuid import UUID

_SEATLAB = Path(__file__).resolve().parents[1]
_EVIDENCE_FIXTURE = run_path(str(Path(__file__).with_name("test_evidence.py")))
_VIEW_HASH = run_path(str(_SEATLAB / "capsule.py"))["view_hash"]
_PRIVATE_TEXT = "/private/retained-provider-trace"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_panel(
    root: Path,
    *,
    first_id: int,
    conditions: tuple[str, ...] = ("fresh",),
    seeds: tuple[int, ...] = (17,),
    changes: dict[tuple[int, str], Callable[[dict[str, Any]], None]] | None = None,
    reuse_id: bool = False,
) -> dict[str, Any]:
    root.mkdir()
    planned, completed = [], []
    for seed in seeds:
        for condition in conditions:
            cell_id = f"{seed}-{condition}"
            cell = {"cell_id": cell_id, "seed": seed, "condition": condition}
            dossier = _EVIDENCE_FIXTURE["_dossier"](condition)
            dossier["settings"]["seed"] = seed
            if change := (changes or {}).get((seed, condition)):
                change(dossier)
            native = _EVIDENCE_FIXTURE["_native"](condition)
            native["id"] = str(UUID(int=first_id + (0 if reuse_id else len(planned))))
            native["unrelated_private_text"] = _PRIVATE_TEXT
            score = _EVIDENCE_FIXTURE["score"](dossier)
            native["verifier_result"]["rewards"]["reward"] = score["optimal"] / 2
            trial = root / cell_id
            _EVIDENCE_FIXTURE["_write_trial"](trial, native, dossier)
            planned.append(cell)
            completed.append(
                {
                    **cell,
                    "status": "measured",
                    "trial_path": cell_id,
                    "evidence": _EVIDENCE_FIXTURE["read_trial"](trial),
                }
            )
    _write_json(
        root / "plan.json",
        {
            "schema": "seatlab.panel-plan/v1",
            "policy": "oracle",
            "seeds": list(seeds),
            "conditions": list(conditions),
            "episodes": 2,
            "cells": planned,
        },
    )
    runs = {"schema": "seatlab.panel-runs/v1", "cells": completed}
    _write_json(root / "runs.json", runs)
    return runs


def _acknowledge_dispatch(world: dict[str, Any], *, reverse: bool = False) -> None:
    view = {
        "role": "dispatch",
        "condition": world["settings"]["condition"],
        "cases": [
            {
                "id": case["id"],
                "utilities": case["utilities"],
                "messages": case["visible"][::-1] if reverse else case["visible"],
                "pooled_available": None,
            }
            for case in world["cases"]
        ],
    }
    world["observation_receipts"] = {"dispatch": {"ack_sha256": _VIEW_HASH(view)}}


class ReplayComparisonTest(TestCase):
    def _compare(
        self, baseline: Path, candidate: Path, status: int, *options: str
    ) -> dict[str, Any]:
        run = subprocess.run(
            [
                sys.executable,
                str(_SEATLAB / "compare_replay.py"),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                *options,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, status, run.stderr or run.stdout)
        self.assertEqual(run.stderr, "")
        for private in (str(baseline), str(candidate), _PRIVATE_TEXT):
            self.assertNotIn(private, run.stdout)
        return json.loads(run.stdout)

    def test_equal_rewards_and_exact_scores_do_not_hide_different_actions(self) -> None:
        def choose(bay: str) -> Callable[[dict[str, Any]], None]:
            def change(world: dict[str, Any]) -> None:
                world["cases"][0]["chosen"] = bay

            return change

        with TemporaryDirectory() as directory:
            baseline, candidate = Path(directory) / "a", Path(directory) / "b"
            _write_panel(baseline, first_id=1, changes={(17, "fresh"): choose("C")})
            _write_panel(candidate, first_id=2, changes={(17, "fresh"): choose("D")})
            result = self._compare(baseline, candidate, 1)

        seed = result["seeds"][0]
        self.assertEqual(seed["seed"], 17)
        self.assertEqual(seed["divergent_stages"], ["dispatch_choice"])
        stages = seed["cells"][0]["stages"]
        self.assertEqual(
            stages["dispatch_choice"], {"status": "drift", "episode_ids": [0]}
        )
        self.assertEqual(stages["score"]["status"], "match")
        self.assertTrue(stages["score"]["scalar_reward_matches"])

    def test_different_silent_reports_are_not_in_either_actor_view(self) -> None:
        def change_report(world: dict[str, Any]) -> None:
            world["cases"][1]["reports"][0]["available"] = ["A", "D"]

        def change_latent(world: dict[str, Any]) -> None:
            ranks = world["cases"][0]["utilities"]
            ranks["C"], ranks["D"] = ranks["D"], ranks["C"]

        with TemporaryDirectory() as directory:
            baseline, candidate = Path(directory) / "a", Path(directory) / "b"
            _write_panel(
                baseline, first_id=1, conditions=("silent", "fresh"), seeds=(29, 17)
            )
            _write_panel(
                candidate,
                first_id=10,
                conditions=("fresh", "silent"),
                seeds=(17, 29),
                changes={(29, "silent"): change_report, (29, "fresh"): change_latent},
            )
            result = self._compare(
                baseline,
                candidate,
                1,
                "--intentional-drift",
                "silent:inventory_reports",
            )

        unchanged, changed = result["seeds"]
        self.assertEqual(unchanged["seed"], 17)
        self.assertEqual(unchanged["divergent_stages"], [])
        self.assertEqual(changed["seed"], 29)
        fresh, silent = changed["cells"]
        self.assertEqual(fresh["condition"], "fresh")
        self.assertEqual(fresh["stages"]["latent"]["episode_ids"], [0])
        self.assertEqual(fresh["stages"]["dispatch_view"]["status"], "drift")
        self.assertEqual(silent["condition"], "silent")
        self.assertEqual(silent["stages"]["inventory_view"]["status"], "match")
        self.assertEqual(silent["stages"]["dispatch_view"]["status"], "match")
        self.assertEqual(
            silent["stages"]["inventory_reports"],
            {"status": "intentional_drift", "episode_ids": [1]},
        )

    def test_compares_ordered_views_and_does_not_equate_absence_with_binding(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            baseline, candidate, invalid = (Path(directory) / name for name in "abc")
            _write_panel(baseline, first_id=1, conditions=("reordered", "pooled"))
            _write_panel(
                candidate,
                first_id=10,
                conditions=("pooled", "reordered"),
                changes={(17, "reordered"): _acknowledge_dispatch},
            )
            _write_panel(
                invalid,
                first_id=20,
                conditions=("pooled", "reordered"),
                changes={
                    (17, "reordered"): lambda world: _acknowledge_dispatch(
                        world, reverse=True
                    )
                },
            )
            result = self._compare(baseline, candidate, 0)
            error = self._compare(baseline, invalid, 2)["error"]

        pooled, reordered = result["seeds"][0]["cells"]
        parked = {"status": "not_applicable", "episode_ids": None}
        self.assertEqual(pooled["stages"]["inventory_view"], parked)
        self.assertEqual(pooled["stages"]["inventory_reports"], parked)
        stage = reordered["stages"]["dispatch_view"]
        self.assertEqual(stage["status"], "match")
        self.assertEqual(stage["baseline"]["acknowledgment"], "missing")
        self.assertEqual(stage["candidate"]["acknowledgment"], "verified")
        self.assertIsNone(stage["acknowledged_match"])
        self.assertEqual(error["code"], "invalid_actor_observation_receipt")
        self.assertEqual(error["condition"], "reordered")

    def test_rejects_duplicate_native_trials_within_or_across_panels(self) -> None:
        with TemporaryDirectory() as directory:
            baseline, copied, reused = (Path(directory) / name for name in "abc")
            _write_panel(baseline, first_id=1, conditions=("fresh", "reordered"))
            _write_panel(copied, first_id=1, conditions=("fresh", "reordered"))
            _write_panel(
                reused, first_id=5, conditions=("fresh", "reordered"), reuse_id=True
            )
            for candidate in (copied, reused):
                with self.subTest(candidate=candidate.name):
                    error = self._compare(baseline, candidate, 2)["error"]
                    self.assertEqual(error["code"], "duplicate_native_trial_id")
                    self.assertEqual(error["panel"], "candidate")

    def test_incomplete_panel_is_invalid_without_echoing_incoming_errors(self) -> None:
        with TemporaryDirectory() as directory:
            baseline, candidate = Path(directory) / "a", Path(directory) / "b"
            _write_panel(baseline, first_id=1)
            runs = _write_panel(candidate, first_id=2)
            runs["cells"][0].update(status="unmeasured", error=_PRIVATE_TEXT)
            _write_json(candidate / "runs.json", runs)
            error = self._compare(baseline, candidate, 2)["error"]
        self.assertEqual(
            error, {"code": "invalid_or_incomplete_panel", "panel": "candidate"}
        )
