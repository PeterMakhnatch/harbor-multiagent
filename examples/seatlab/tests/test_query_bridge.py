import json
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase, skipUnless

if __package__:
    from .test_evidence import _TRIAL_ID, _dossier, _native, _write_trial, read_trial
else:
    from test_evidence import _TRIAL_ID, _dossier, _native, _write_trial, read_trial

_SEATLAB = Path(__file__).resolve().parents[1]
_JOB_ID = "1aa78f1f-fb60-4ac5-9912-b352cb673e4f"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_dispatch_fixture(
    trial: Path, job_id: str | None = None, *, trial_id: str = _TRIAL_ID
) -> None:
    native = _native("fresh")
    native["id"] = trial_id
    native["task_name"] = "dispatch-handoff"
    native["trial_name"] = trial.name
    native["trial_uri"] = trial.as_uri()
    native["config"]["job_id"] = job_id
    inventory = next(
        seat for seat in native["seat_results"] if seat["seat"] == "inventory"
    )
    inventory["agent_result"].update(n_input_tokens=11, cost_usd=0.125)
    _write_trial(trial, native, _dossier("fresh"))


def _write_panel(root: Path, trial: Path) -> None:
    cell = {"cell_id": "fresh-17", "condition": "fresh", "seed": 17}
    _write_json(
        root / "plan.json",
        {
            "schema": "seatlab.panel-plan/v1",
            "policy": "fixture",
            "conditions": ["fresh"],
            "seeds": [17],
            "episodes": 2,
            "cells": [cell],
        },
    )
    _write_json(
        root / "runs.json",
        {
            "schema": "seatlab.panel-runs/v1",
            "cells": [
                {
                    **cell,
                    "status": "measured",
                    "trial_path": trial.relative_to(root).as_posix(),
                    "evidence": read_trial(trial),
                }
            ],
        },
    )


@skipUnless(find_spec("evallab") is not None, "run with Eval Lab's Python environment")
class QueryBridgeTest(TestCase):
    def _cli(
        self, scratch: Path, *args: Path | str
    ) -> subprocess.CompletedProcess[str]:
        import evallab

        checkout = Path(evallab.__file__).resolve().parents[2]
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(_SEATLAB / "query_evallab.py"),
                "--evallab-root",
                str(checkout),
                "--scratch-dir",
                str(scratch),
                *map(str, args),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def _query(self, scratch: Path, *args: Path | str) -> dict[str, Any]:
        result = self._cli(scratch, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(scratch.iterdir()), [])
        return json.loads(result.stdout)

    def test_job_uuid_join_counts_outcome_once_with_role_specific_usage(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            job = root / "native-job"
            trial = job / "dispatch-handoff__example"
            _write_dispatch_fixture(trial, _JOB_ID)
            _write_json(
                job / "result.json",
                {
                    "id": _JOB_ID,
                    "n_total_trials": 1,
                    "stats": {"n_completed_trials": 1},
                },
            )
            census_copy = root / "copied-job"
            copytree(job, census_copy)
            report = self._query(
                scratch, "--trial", trial, "--census-root", census_copy
            )

        self.assertEqual(
            report["counts"],
            {
                "dispatch_evidence_trials": 1,
                "seat_usage_rows": 2,
                "census_rows_scanned": 1,
                "census_evidence_trials_by_uuid": 1,
                "census_evidence_trials_by_path": 0,
                "census_evidence_trials_by_job_and_uuid": 1,
                "census_evidence_rewards_verified": 1,
            },
        )
        (row,) = report["dispatch_evidence"]
        self.assertEqual(row["trial_id"], _TRIAL_ID)
        self.assertEqual(row["job_id"], _JOB_ID)
        self.assertEqual(row["census_job_trial_rows"], 1)
        self.assertEqual(row["census_reward"], row["reward"])
        self.assertEqual(row["reward"], 0.5)
        self.assertEqual(row["inventory"]["n_input_tokens"], 11)
        self.assertEqual(row["dispatch"]["n_input_tokens"], 7)
        self.assertEqual(row["inventory"]["cost_usd"], 0.125)
        self.assertEqual(row["dispatch"]["cost_usd"], 0.0)
        self.assertIsNone(row["inventory"]["n_output_tokens"])
        self.assertEqual(row["dispatch"]["n_output_tokens"], 0)
        (summary,) = report["by_condition"]
        self.assertEqual(summary["native_trials"], 1)
        self.assertEqual(summary["episodes"], 2)
        self.assertEqual(summary["optimal"], 1)
        self.assertEqual(summary["stable_optimal"], 0)
        self.assertEqual(summary["changed_optimal"], 1)
        self.assertEqual(summary["mean_trial_reward"], 0.5)

    def test_standalone_census_hit_is_not_a_job_and_empty_census_stays_empty(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            panel = root / "panel"
            trial = panel / "native" / "standalone-trial"
            _write_dispatch_fixture(trial)
            _write_panel(panel, trial)
            report = self._query(scratch, panel)
            counts = report["counts"]
            self.assertEqual(counts["dispatch_evidence_trials"], 1)
            self.assertEqual(counts["census_rows_scanned"], 1)
            self.assertEqual(counts["census_evidence_trials_by_uuid"], 1)
            self.assertEqual(counts["census_evidence_trials_by_path"], 1)
            self.assertEqual(counts["census_evidence_trials_by_job_and_uuid"], 0)
            self.assertEqual(counts["census_evidence_rewards_verified"], 0)
            (row,) = report["dispatch_evidence"]
            self.assertIsNone(row["job_id"])
            self.assertIsNone(row["census_reward"])

            unmatched = self._query(scratch, panel, "--census-root", scratch)
            self.assertEqual(unmatched["counts"]["census_rows_scanned"], 0)
            self.assertEqual(unmatched["counts"]["census_evidence_trials_by_uuid"], 0)
            self.assertEqual(unmatched["counts"]["census_evidence_trials_by_path"], 0)
            self.assertEqual(
                unmatched["counts"]["census_evidence_trials_by_job_and_uuid"], 0
            )
            self.assertEqual(unmatched["counts"]["dispatch_evidence_trials"], 1)
            self.assertEqual(unmatched["by_condition"], report["by_condition"])

    def test_panels_with_the_same_condition_keep_separate_denominators(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            panels = [root / "one", root / "two"]
            for panel, trial_id in zip(panels, (_TRIAL_ID, _JOB_ID), strict=True):
                trial = panel / "native" / "standalone-trial"
                _write_dispatch_fixture(trial, trial_id=trial_id)
                _write_panel(panel, trial)
            report = self._query(scratch, *panels)

        self.assertEqual(report["counts"]["dispatch_evidence_trials"], 2)
        self.assertEqual(
            [
                (row["panel"], row["condition"], row["native_trials"])
                for row in report["by_condition"]
            ],
            [(1, "fresh", 1), (2, "fresh", 1)],
        )

    def test_repeated_native_uuid_does_not_count_twice(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            trial = root / "native-trial"
            _write_dispatch_fixture(trial)
            result = self._cli(scratch, "--trial", trial, "--trial", trial)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reuse a native trial UUID", result.stderr)
        self.assertEqual(result.stdout, "")
