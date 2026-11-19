"""A task folder that is not the one a trial ran (rebuilt or moved) is used only when it matches the run.

Real task folders from flotilla/build_tasks.py; synthetic trials (tests/fixtures.py) that point at them
through ``task_id.path``, with the prompt that a terminus-2 seat sees in its trajectory and the world's
``start`` event. Plain ``assert`` statements, so that a hook that disables unittest's assertions cannot
hide a failure.
"""

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.load import load, load_evidence, load_trajectories
from flotilla_analysis.render import render
from flotilla_analysis.report import build as report
from flotilla_analysis.tasks import ran_here, task_info
from tests import fixtures as fx

FLOTILLA = Path(__file__).resolve().parents[2]
PROMPT = "You are an AI assistant tasked with solving command-line tasks.\n\nTask Description:\n"
REBUILT = "task folder rebuilt with different parameters"


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


BUILD = module(FLOTILLA / "build_tasks.py", "flotilla_build_tasks_rebuilt")
SCHEDULE = module(FLOTILLA / "families" / "schedule.py", "flotilla_schedule_rebuilt")


def entries(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())["tasks"]


def run_trial(job: Path, root: Path, name: str, *, seen: bool = True, limits: bool = True) -> Path:
    """A trial of the task of this name, as Harbor and the world record it. In talk, p1 submits the right
    answer at 25 s and p2 a wrong one at 60 s (the answer was found, then lost)."""
    entry = entries(root)[name]
    folder = root / entry["path"]
    task = json.loads((folder / "environment" / "task.json").read_text())
    key = task["answer_key"]
    wrong = next(
        {"day": day, "start": f"{hour:02d}:00"}
        for day, hour in SCHEDULE.windows()
        if (day, f"{hour:02d}:00") != (key["day"], key["start"])
    )
    talk = entry["condition"].startswith("talk")
    submissions = [{"n": 1, "seat": "p1", "answer": key, "t": 25.0}]
    if talk:
        submissions.append({"n": 2, "seat": "p2", "answer": wrong, "t": 60.0})
    start = {"condition": entry["condition"], "seats": entry["seats"], "world_version": 2}
    if limits:
        start["limits"] = {"max_text": task["max_text"], "max_messages": task["max_messages"]}
    events = [{"seq": 1, "t": 0.0, "seat": None, "kind": "start", "data": start}] + [
        {
            "seq": i + 2,
            "t": s["t"],
            "seat": s["seat"],
            "kind": "submit",
            "data": {"n": s["n"], "answer": s["answer"]},
        }
        for i, s in enumerate(submissions)
    ]
    trajectories = (
        {
            seat: [
                fx.step(
                    1,
                    1.0,
                    source="user",
                    message=PROMPT + (folder / "instructions" / f"{seat}.md").read_text(),
                ),
                fx.step(2, 2.0, keystrokes=["curl -s $WORLD_URL/whoami\n"]),
            ]
            for seat in entry["seats"]
        }
        if seen
        else None
    )
    directory = fx.trial(
        job,
        f"{folder.name}__run",
        label=entry["label"],
        instance=entry["instance"],
        condition=entry["condition"],
        seats=[fx.seat_result(seat) for seat in entry["seats"]],
        reward=0.0 if talk else 1.0,
        submissions=submissions,
        events=events,
        trajectories=trajectories,
        task_path=str(folder),
    )
    result = json.loads((directory / "result.json").read_text())
    result["task_name"] = name  # Harbor records the task's own name
    fx.write_json(directory / "result.json", result)
    return directory


def folders(rep: dict) -> dict:
    return rep["task_directories"]


def found_lost(rep: dict) -> list:
    return [(t["condition"], t["found"], t["lost"]) for t in rep["found_lost"]["trials"]]


class RebuiltFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks"
        self.job = self.root / "jobs" / "run-2seat"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, *, readable: bool = False, **options):
        """schedule, instance 0, in silent and talk: seed 1, 2 seats, easy, default limits unless given."""
        settings = {"n_seats": 2, "difficulty": "easy", "seed": 1} | {
            k: options.pop(k) for k in ("n_seats", "difficulty", "seed") if k in options
        }
        return BUILD.build(
            "schedule",
            [0],
            settings["n_seats"],
            settings["difficulty"],
            settings["seed"],
            ("silent", "talk"),
            self.tasks,
            readable_names=readable,
            **options,
        )

    def trials(self, **kwargs) -> None:
        for name in sorted(entries(self.tasks)):
            run_trial(self.job, self.tasks, name, **kwargs)

    def test_a_folder_rebuilt_with_the_same_parameters_is_used(self):
        self.build()
        self.trials()
        assert folders(report([str(self.job)], resamples=20, draws=20)) == {"usable": 2}
        self.build()  # new tokens and names, the same tasks
        rep = report([str(self.job)], resamples=20, draws=20)
        assert rep["problems"] == [] and folders(rep) == {"usable": 2}
        assert found_lost(rep) == [("talk", True, True)]

    def test_each_parameter_that_differs_makes_the_folder_unusable(self):
        cases = {
            "seed": {"seed": 2},
            "difficulty": {"difficulty": "hard"},
            "team_seats": {"n_seats": 3},
            "max_text": {"max_text": 200},
            "max_messages": {"max_messages": 3},
            "submitter": {"submitter": "p1"},
            "announce": {"announce": True},
        }
        for kind, options in cases.items():
            for seen in (True, False):  # with what the seats saw, and without (only the manifest knows)
                with self.subTest(kind=kind, seen=seen):
                    self.tearDown()
                    self.setUp()
                    self.build()
                    self.trials(seen=seen)
                    assert found_lost(report([str(self.job)], resamples=20, draws=20)) == [
                        ("talk", True, True)
                    ]
                    self.build(**dict(options))
                    rep = report([str(self.job)], resamples=20, draws=20)
                    problems = list(folders(rep))
                    assert all(p.startswith(REBUILT + " (") for p in problems), problems
                    assert all(kind in p for p in problems), problems
                    assert sum(folders(rep).values()) == 2
                    assert found_lost(rep) == [("talk", None, None)]  # unknown, not a wrong found=False
                    details = [p["reason"] for p in rep["problems"]]
                    assert len(details) == 2 and all(d.startswith(REBUILT) for d in details)

    def test_what_a_seat_saw_decides_for_a_readable_name_rebuilt_in_place(self):
        self.build(readable=True)
        self.trials()
        self.build(readable=True)  # in place: the same folder names, the same tasks
        assert folders(report([str(self.job)], resamples=20, draws=20)) == {"usable": 2}
        self.build(readable=True, seed=2)  # in place, another instance; no manifest entry is the run's
        rep = report([str(self.job)], resamples=20, draws=20)
        assert folders(rep) == {REBUILT + " (instruction seen)": 2}
        assert all("instructions/p1.md is not the instruction it saw" in p["reason"] for p in rep["problems"])

    def test_the_world_record_decides_when_nothing_else_is_known(self):
        self.build(readable=True)
        self.trials(seen=False)
        self.build(readable=True, max_text=200, max_messages=3)
        rep = report([str(self.job)], resamples=20, draws=20)
        assert folders(rep) == {REBUILT + " (max_messages, max_text)": 2}
        assert any("max_text 200 (the world recorded 4000)" in p["reason"] for p in rep["problems"])
        self.build(readable=True, n_seats=3)
        rep = report([str(self.job)], resamples=20, draws=20)
        assert folders(rep) == {REBUILT + " (seats)": 2}

    def test_an_instruction_file_that_differs_from_the_run(self):
        self.build()
        self.trials(seen=False)
        self.build()
        talk = next(
            self.tasks / e["path"]
            for e in entries(self.tasks).values()
            if e["condition"] == "talk" and (self.tasks / e["path"]).exists()
        )
        path = talk / "instructions" / "p2.md"
        path.write_text(path.read_text().replace("## The goal", "## The goal\n\nA changed sentence."))
        rep = report([str(self.job)], resamples=20, draws=20)
        assert folders(rep) == {"usable": 1, REBUILT + " (instructions)": 1}

    def test_a_new_answer_example_does_not_hide_what_the_seats_saw(self):
        """The example-leak check reads the answer format from the trajectory, so only the part above it
        must match: a later fix of a family's example keeps an old run's folder usable."""
        self.build()
        self.trials(seen=True)
        self.build()
        for e in entries(self.tasks).values():
            for path in (
                (self.tasks / e["path"] / "instructions").glob("*.md")
                if (self.tasks / e["path"]).exists()
                else []
            ):
                path.write_text(
                    re.sub(r"(## Answer format\n)", r"\1\nA new example: `{}`.\n", path.read_text())
                )
        assert folders(report([str(self.job)], resamples=20, draws=20)) == {"usable": 2}

    def test_a_manifest_from_before_build_parameters(self):
        """Entries written before this check have no build parameters: only the world record and the
        trajectories are compared, and the score check stays."""
        self.build()
        data = json.loads((self.tasks / "manifest.json").read_text())
        for entry in data["tasks"].values():
            del entry["build"], entry["instructions_sha256"]
        (self.tasks / "manifest.json").write_text(json.dumps(data))
        self.trials(seen=True)
        self.build(seed=2)
        assert folders(report([str(self.job)], resamples=20, draws=20)) == {
            REBUILT + " (instruction seen)": 2
        }

    def test_the_folder_a_trial_ran_is_not_checked(self):
        self.build()
        self.trials()
        trial = next(t for t in load([self.job])[0] if t.condition == "talk")
        own = Path(trial.task_path)
        assert ran_here(trial, own)
        tampered = own / "environment" / "task.json"  # even a folder that now disagrees is the one it ran
        data = json.loads(tampered.read_text())
        tampered.write_text(json.dumps(data | {"max_text": 1}))
        evidence = load_evidence(trial)
        assert task_info(trial, evidence, [], trajectories=load_trajectories(trial)).usable

    def test_a_readable_folder_may_have_been_rebuilt_in_place(self):
        self.build(readable=True)
        self.trials()
        trial = next(t for t in load([self.job])[0] if t.condition == "talk")
        assert trial.task_name == "flotilla/schedule-000-talk" and not ran_here(trial, Path(trial.task_path))
        assert folders(report([str(self.job)], resamples=20, draws=20)) == {
            "usable": 2
        }  # checked, and it matches


    def test_no_report_names_a_seed(self):
        """Each build draws a secret seed, so a rebuild without --seed differs in it; the reports (text, markdown,
        JSON) say which build parameter differs, never a seed: a published report must not give the answer keys."""
        run_seed, rebuilt_seed = 4_404_577_120_938_117, 5_505_577_120_938_118  # of the size that builds draw
        self.build(seed=run_seed)
        self.trials(seen=False)
        self.build(seed=rebuilt_seed)
        rep = report([str(self.job)], resamples=20, draws=20)
        assert folders(rep) == {REBUILT + " (instructions, seed)": 2}  # the briefings differ too
        assert all("seed differs" in p["reason"] for p in rep["problems"]) and len(rep["problems"]) == 2
        for fmt in ("text", "markdown", "json"):
            shown = render(rep, fmt)
            assert REBUILT in shown and "seed differs" in shown
            assert str(run_seed) not in shown and str(rebuilt_seed) not in shown, fmt
            assert not re.search(r"\d{13,}", shown), fmt  # no other long number either
