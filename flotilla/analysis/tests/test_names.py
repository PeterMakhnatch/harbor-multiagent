"""Opaque task names: the analysis learns what a trial's task is from its task folder or a build manifest.

The task folders are real ones from flotilla/build_tasks.py (opaque names by default); the trials are
synthetic (tests/fixtures.py) and point at them through ``task_id.path``, as Harbor's result.json does.
Plain ``assert`` statements, so that a hook that disables unittest's assertions cannot hide a failure.
"""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis import cli
from flotilla_analysis.load import load
from flotilla_analysis.names import Identity, Names, from_name
from tests import fixtures as fx

BUILD_TASKS = Path(__file__).resolve().parents[2] / "build_tasks.py"


def builder():
    spec = importlib.util.spec_from_file_location("flotilla_build_tasks_names", BUILD_TASKS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BUILD_TASKS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = builder()


def manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())["tasks"]


def opaque_trials(job: Path, root: Path) -> dict[str, Identity]:
    """One synthetic trial per task in the build manifest at root, named as Harbor names it; returns
    trial name -> identity."""
    expected = {}
    for number, (name, entry) in enumerate(sorted(manifest(root).items())):
        trial_name = f"{name.split('/')[1]}__trial{number:02d}"
        directory = fx.trial(
            job,
            trial_name,
            label=entry["label"],
            instance=entry["instance"],
            condition=entry["condition"],
            seats=[fx.seat_result(seat) for seat in entry["seats"]],
            reward=0.0,
            task_path=str(root / entry["path"]),
        )
        result = json.loads((directory / "result.json").read_text())
        result["task_name"] = name  # Harbor records the task's own (opaque) name
        fx.write_json(directory / "result.json", result)
        expected[trial_name] = Identity(entry["label"], entry["instance"], entry["condition"])
    return expected


def identities(trials) -> dict[str, Identity]:
    return {t.trial_name: Identity(t.label, t.instance, t.condition) for t in trials}


class OpaqueNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks"
        BUILD.build("schedule", [0, 1], 2, "easy", 1, ("solo", "silent", "talk"), self.tasks)
        BUILD.build("ledger", [0], 2, "easy", 1, ("talk",), self.tasks, variant="hard")
        self.job = self.root / "jobs" / "opaque-2seat"
        self.expected = opaque_trials(self.job, self.tasks)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_task_folder_says_what_an_opaque_task_is(self):
        trials, _jobs, problems = load([self.job])
        assert problems == []
        assert identities(trials) == self.expected and len(trials) == 7
        hard = [t for t in trials if t.label == "ledger.hard"]
        assert [(t.family, t.variant, t.instance, t.condition) for t in hard] == [
            ("ledger", "hard", 0, "talk")
        ]
        assert all(t.task_name.startswith("flotilla/t-") for t in trials)

    def test_a_manifest_says_it_when_the_folder_is_gone(self):
        moved = self.root / "moved"
        self.tasks.rename(moved)
        trials, _jobs, problems = load([self.job])
        assert trials == [] and len(problems) == 7
        assert all(
            "unknown Flotilla task 'flotilla/t-" in p.reason and "--manifest" in p.reason for p in problems
        )
        trials, _jobs, problems = load([self.job], manifests=[moved / "manifest.json"])
        assert problems == [] and identities(trials) == self.expected

    def test_the_manifest_next_to_a_replaced_folder_says_it(self):
        """A rebuild replaces a task's folder (a new opaque name); the build root's manifest keeps the old
        name."""
        BUILD.build("schedule", [0, 1], 2, "easy", 1, ("solo", "silent", "talk"), self.tasks)
        trials, _jobs, problems = load([self.job])
        assert problems == [] and identities(trials) == self.expected
        assert not any(Path(t.task_path).exists() for t in trials if t.label == "schedule")

    def test_a_folder_that_holds_another_task_is_not_read(self):
        task = next(t for t in load([self.job])[0] if t.condition == "solo")
        toml = Path(task.task_path) / "task.toml"
        toml.write_text(toml.read_text().replace(task.task_name, "flotilla/t-000000000000"))
        (self.tasks / "manifest.json").rename(self.root / "manifest.json")
        names = Names()
        assert names.identity(task.task_name, task.task_path) is None
        assert Names([self.root / "manifest.json"]).identity(task.task_name, task.task_path) == Identity(
            task.label, task.instance, task.condition
        )

    def test_readable_names_still_say_it(self):
        """Jobs of tasks built before opaque names: the name says the identity, whatever the folder holds
        now."""
        job = self.root / "jobs" / "old"
        opaque_folder = self.tasks / manifest(self.tasks)[sorted(manifest(self.tasks))[0]]["path"]
        fx.trial(
            job,
            "schedule.liar-007-talk-liar-warned__x",
            label="schedule.liar",
            instance=7,
            condition="talk-liar-warned",
            task_path=str(opaque_folder),
        )
        fx.trial(job, "ledger-003-silent__y", label="ledger", instance=3, condition="silent")
        trials, _jobs, problems = load([job])
        assert problems == []
        assert sorted((t.label, t.variant, t.instance, t.condition) for t in trials) == [
            ("ledger", "", 3, "silent"),
            ("schedule.liar", "liar", 7, "talk-liar-warned"),
        ]
        assert from_name("flotilla/t-0123456789ab") is None and from_name("flotilla/schedule-4-talk") is None

    def test_a_manifest_that_cannot_be_used_is_a_problem(self):
        (self.root / "broken.json").write_text("{")
        (self.root / "other.json").write_text('{"tasks": []}')
        _trials, _jobs, problems = load(
            [self.job],
            manifests=[self.root / "absent.json", self.root / "broken.json", self.root / "other.json"],
        )
        reasons = {Path(p.path).name: p.reason for p in problems}
        assert reasons == {
            "absent.json": "manifest not found",
            "broken.json": "manifest unreadable: JSONDecodeError",
            "other.json": "not a Flotilla build manifest (no tasks object)",
        }

    def test_the_command_line_reads_moved_and_rebuilt_task_folders(self):
        moved = self.root / "moved"
        self.tasks.rename(moved)
        out = self.root / "report.json"
        assert (
            cli.main(
                [
                    str(self.job),
                    "--manifest",
                    str(moved / "manifest.json"),
                    "--format",
                    "json",
                    "--output",
                    str(out),
                ]
            )
            == 0
        )
        report = json.loads(out.read_text())
        assert report["problems"] == []
        assert sorted((r["label"], r["condition"], r["instances"]) for r in report["scores"]) == [
            ("ledger.hard", "talk", 1),
            ("schedule", "silent", 2),
            ("schedule", "solo", 2),
            ("schedule", "talk", 2),
        ]
        assert report["task_directories"] == {"usable": 7}  # the moved folders, under the manifest's root
        BUILD.build(
            "schedule", [0, 1], 2, "easy", 1, ("solo", "silent", "talk"), moved
        )  # new names, same tasks
        assert cli.main([str(self.job), "--tasks", str(moved), "--format", "json", "--output", str(out)]) == 0
        report = json.loads(out.read_text())
        assert report["problems"] == [] and report["task_directories"] == {"usable": 7}
        shutil.rmtree(moved / "schedule")
        assert cli.main([str(self.job), "--tasks", str(moved), "--format", "json", "--output", str(out)]) == 0
        assert json.loads(out.read_text())["task_directories"] == {"task directory not found": 6, "usable": 1}
