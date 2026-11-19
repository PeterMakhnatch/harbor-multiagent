"""The real oracle-check jobs from the first Flotilla runs (2 schedule instances x 3 conditions).

tests/data holds only the files the analysis reads (result.json, the world's JSONL artifacts, the
verifier's score.json, reward.json and world-state.json); the local path prefix was replaced by
/work/flotilla-lab. The task folders are not committed (they hold answer keys and seat tokens): the
test rebuilds them with flotilla/build_tasks.py, which is deterministic for the instances.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.report import build

DATA = Path(__file__).resolve().parent / "data"
BUILD_TASKS = Path(__file__).resolve().parents[2] / "build_tasks.py"


def build_task_dirs(out: Path) -> None:
    spec = importlib.util.spec_from_file_location("flotilla_build_tasks", BUILD_TASKS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BUILD_TASKS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build("schedule", [0, 1], 2, "easy", 1, ("solo", "silent", "talk"), out)


class RealOracleCheck(unittest.TestCase):
    def test_with_rebuilt_task_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_task_dirs(Path(tmp))
            report = build([str(DATA)], task_roots=[Path(tmp)])
        self.assertEqual(report["task_directories"], {"usable": 6})
        self.assertEqual(
            [(j["path"], j["finished_trials"], j["scored_trials"]) for j in report["jobs"]],
            [("data/oracle-check-1seat", 2, 2), ("data/oracle-check-2seat", 4, 4)],
        )
        scores = {r["condition"]: r["mean"]["value"] for r in report["scores"]}
        self.assertEqual(scores, {"solo": 1.0, "silent": 0.0, "talk": 1.0})
        (d,) = report["decomposition"]
        self.assertEqual((d["paired_instances"], d["gain_talk_minus_silent"]["value"]), (2, 1.0))
        self.assertEqual((d["gain_test"]["p_value"], d["gain_test"]["method"]), (0.5, "exact"))
        self.assertEqual(d["recovery"]["value"], 1.0)
        play = {r["condition"]: r for r in report["play"]["conditions"]}
        self.assertEqual(
            (play["talk"]["messages"], play["talk"]["delivered"], play["talk"]["pairs"]), (4, 4, 4)
        )
        self.assertEqual(play["talk"]["seen_unknown"], 4)  # the oracle agent writes no trajectory
        self.assertEqual(play["talk"]["acted_on"], {"yes": 2, "no": 0, "n.a.": 0, "unknown": 0})
        self.assertEqual(play["silent"]["acted_on"]["no"], 2)
        self.assertEqual((play["silent"]["overwrites"], play["silent"]["clobbered_trials"]), (2, 0))
        self.assertEqual(
            report["own_briefing_check"], [{"label": "schedule", "seat_briefings": 4, "sufficient_alone": 0}]
        )
        self.assertEqual({r["world_version"] for r in report["play"]["trials"]}, {"1"})

    def test_without_task_folders(self):
        report = build([str(DATA)])
        self.assertEqual(report["task_directories"], {"task directory not found": 6})
        play = {r["condition"]: r for r in report["play"]["conditions"]}
        self.assertEqual(play["talk"]["acted_on"]["unknown"], 2)  # correct, but sufficiency is unknown
        self.assertEqual(play["silent"]["acted_on"]["no"], 2)  # wrong answers need no task folder
        self.assertEqual(play["silent"]["clobber_known_trials"], 0)
