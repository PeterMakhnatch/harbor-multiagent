from __future__ import annotations

import csv
import json
from pathlib import Path

from conftest import FLOTILLA, load

TRACE = load(FLOTILLA / "docs" / "results" / "scripts" / "trace_features.py")
MODEL = "provider/model"
READ = "cat /home/player/expenses.csv\n"
SUBMIT = "curl -s -X POST $WORLD_URL/submit -d '{\"answer\": {\"total\": 10}}'\n"
ACCEPTED = (SUBMIT, '{"accepted": true}')
REFUSED = (SUBMIT, '{"error": "unread messages"}')
CORRECTIONS = ("10 is correct; the other total included a void.", "The total should be 10.")


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def make_task(root, instance, arm):
    name = f"task-{instance}"
    folder = root / "probe.verify" / name
    write_json(folder / "environment/task.json", {"instance": instance, "condition": arm,
                                                   "probe": {"kind": "verify", "correct_value": 10}})
    data = folder / "environment/player/expenses.csv"
    data.parent.mkdir()
    data.write_text("amount\n10\n")
    return {"path": str(folder.relative_to(root)), "family": "probe", "label": "probe.verify",
            "instance": instance, "condition": arm}


def make_trial(jobs, job, root, task, minute, steps, *, messages=(), model=MODEL):
    task_dir = root / task["path"]
    trial = jobs / job / task_dir.name
    start = f"2026-11-24T00:{minute:02}:00Z"
    config = {"task": {"path": f"/old-build/{task_dir.name}"}, "participants": [
        {"seat": "p1", "name": "terminus-2", "model_name": model},
        {"seat": "p2", "name": "oracle"},
    ]}
    write_json(trial / "result.json", {"trial_name": trial.name, "config": config, "started_at": start,
                                       "task_id": {"path": config["task"]["path"]}})
    world = {"messages": [{"from": "p1", "text": text, "t": 3} for text in messages],
             "submissions": [{"seat": "p1", "t": 5, "answer": {"total": 10}}]}
    write_json(trial / "verifier/world-state.json", world)
    write_json(trial / "verifier/score.json", {"instance": task["instance"], "condition": task["condition"],
                                              "probe_kind": "verify", "seats": ["p1", "p2"], "correct": True})
    if steps is not None:
        write_json(trial / "seats/p1/agent/trajectory.json", {"steps": [
            {"source": "agent", "step_id": index, "timestamp": f"2026-11-24T00:{minute:02}:{index:02}Z",
             "tool_calls": [{"arguments": {"keystrokes": command}}], "observation": {"results": [{"content": reply}]}}
            for index, (command, reply) in enumerate(steps, 1)
        ]})


def test_counted_wave_trace_features(tmp_path, capsys):
    jobs, root, plan, output = (tmp_path / name for name in ("jobs", "frozen", "plan.json", "traces.csv"))
    before, after, same, missing = [make_task(root, i, "talk-false" if i < 3 else "talk-quiet") for i in range(4)]
    tasks = {Path(task["path"]).name: task for task in (before, after, same, missing)}
    write_json(root / "manifest.json", {"format": "flotilla-manifest", "tasks": tasks})
    write_json(plan, {"models": [MODEL], "scripted_seats": ["p2"], "task_sets": [
        {"manifest": str(root / "manifest.json"), "instances": list(range(4)), "arms": ["talk-false", "talk-quiet"]}
    ]})
    make_trial(jobs, "rr1-000003", root, before, 2, [(READ, "10"), REFUSED, ACCEPTED], messages=CORRECTIONS)
    make_trial(jobs, "rr1-000001", root, before, 3, [ACCEPTED])
    make_trial(jobs, "rr1-000002", root, before, 1, [ACCEPTED], model="not-in-plan")
    make_trial(jobs, "rr1-000004", root, after, 2, [REFUSED, (READ, "10"), ACCEPTED])
    make_trial(jobs, "rr1-000005", root, same, 2, [(READ + SUBMIT, REFUSED[1]), ACCEPTED])
    make_trial(jobs, "rr1-000006", root, missing, 2, None)
    make_trial(jobs, "rr1-rehearsal-000001", root, after, 1, [(READ, "10"), ACCEPTED], messages=CORRECTIONS)

    assert TRACE.main(["--jobs", str(jobs), "--pattern", "rr1-??????", "--tasks", str(root),
                       "--plan", str(plan), "--summary", "--out", str(output)]) == 0
    with output.open(newline="") as handle:
        rows = {row["job"]: row for row in csv.DictReader(handle)}
    assert set(rows) == {f"rr1-{n:06}" for n in (3, 4, 5, 6)}
    assert all(row["counted"] == "1" for row in rows.values())
    for job, expected in {
        "rr1-000003": ("1", "2", "3", "1", "2"),
        "rr1-000004": ("0", "1", "3", "1", "0"),
        "rr1-000005": ("0", "1", "2", "1", "0"),
        "rr1-000006": ("", "", "", "", "0"),
    }.items():
        row = rows[job]
        assert tuple(row[key] for key in ("read_before_first_submit", "first_submit_step", "final_step",
                                          "files_read", "post_correction")) == expected
    assert capsys.readouterr().out.splitlines()[1:] == [
        f"{MODEL}\ttalk-false\t3\t1/3 = 0.333\t0\t1/3 = 0.333",
        f"{MODEL}\ttalk-quiet\t1\t0/0\t1\t0/1 = 0.000",
    ]
