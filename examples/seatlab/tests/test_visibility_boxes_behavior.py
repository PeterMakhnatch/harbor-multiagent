import importlib
import json
from pathlib import Path
from uuid import UUID

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    boxes = importlib.import_module("visibility.boxes_behavior")
    muster = importlib.import_module("visibility.muster")


def _panel(tmp_path):
    for arm in ("indep-3", "team-3"):
        job = tmp_path / arm
        job_id = str(UUID(int=1 if arm == "indep-3" else 2))
        tasks = []
        for instance in range(1, 11):
            checksum = f"{instance:064x}"
            lock_digest = f"{instance + 16:064x}"
            task = {
                "path": f"/tasks/{arm}/boxes-i{instance:02d}",
                "git_url": None,
                "git_commit_id": None,
                "name": None,
                "ref": None,
                "overwrite": False,
                "download_dir": None,
                "source": None,
            }
            tasks.append(task)
            for attempt in range(3):
                trial_id = str(
                    UUID(
                        int=1000
                        + instance * 10
                        + attempt
                        + (100 if arm == "team-3" else 0)
                    )
                )
                trial = job / f"boxes-i{instance:02d}__{attempt}"
                trial.mkdir(parents=True)
                (trial / "lock.json").write_text(
                    json.dumps(
                        {
                            "task": {
                                "name": f"boxes-i{instance:02d}",
                                "type": "local",
                                "digest": f"sha256:{lock_digest}",
                                "path": task["path"],
                            }
                        }
                    )
                )
                opens = []
                for seat_index, seat in enumerate(("p1", "p2", "p3")):
                    box = instance if arm == "indep-3" else seat_index + 1
                    opens.append(
                        {
                            "seq": len(opens) + 1,
                            "kind": "open",
                            "method": "POST",
                            "seat": seat,
                            "ok": True,
                            "request": {"box": box},
                            "response": {
                                "box": box,
                                "room": 1,
                                "flag": True,
                                "budget_left": 0,
                            },
                        }
                    )
                opens.append(
                    {
                        "seq": 4,
                        "kind": "admin.close",
                        "method": "POST",
                        "seat": None,
                        "ok": True,
                        "request": {},
                        "response": {"closed": True, "events": 4},
                    }
                )
                world = trial / "verifier/events.jsonl"
                world.parent.mkdir()
                world.write_text("".join(json.dumps(row) + "\n" for row in opens))
                (trial / "result.json").write_text(
                    json.dumps(
                        {
                            "id": trial_id,
                            "task_name": f"muster/boxes-i{instance:02d}",
                            "trial_name": trial.name,
                            "task_checksum": checksum,
                            "task_id": {"path": task["path"]},
                            "config": {
                                "job_id": job_id,
                                "task": dict(task),
                                "participants": [
                                    {"seat": seat} for seat in ("p1", "p2", "p3")
                                ],
                            },
                            "seat_results": [
                                {"seat": seat} for seat in ("p1", "p2", "p3")
                            ],
                            "reward_valid": True,
                            "exception_info": None,
                            "finished_at": "2026-11-20T00:00:00",
                            "verifier_result": {"rewards": {"reward": 0.5}},
                        }
                    )
                )
        (job / "config.json").write_text(
            json.dumps(
                {
                    "job_name": arm,
                    "n_attempts": 3,
                    "tasks": tasks,
                    "participants": [
                        {
                            "seat": seat,
                            "resume_trajectory": False,
                            "load_trajectory": None,
                        }
                        for seat in ("p1", "p2", "p3")
                    ],
                }
            )
        )
        (job / "result.json").write_text(
            json.dumps(
                {
                    "id": job_id,
                    "n_total_trials": 30,
                    "finished_at": "2026-11-20T00:00:00",
                }
            )
        )


def test_equal_instance_means_do_not_pair_attempts(tmp_path, monkeypatch):
    _panel(tmp_path)
    monkeypatch.setattr(muster, "_ARMS", ("e1/indep-3", "e1/team-3"))
    result = boxes.analyze(tmp_path)
    silent = result["arms"]["e1/indep-3"]
    talk = result["arms"]["e1/team-3"]
    assert silent["native_trials"] == talk["native_trials"] == 30
    assert silent["mean_cross_seat_repeat_share"] == boxes.Fraction(2, 3)
    assert talk["mean_cross_seat_repeat_share"] == 0
    assert all(row["reward_dispersion"][0] == 0 for row in silent["instances"])
    assert result["talk"]["no_peer_fetch"]["accepted_opens"] == 90
    assert result["talk"]["before_first_peer_fetch"]["accepted_opens"] == 0
    assert result["talk"]["seats_with_peer_fetch"] == 0
    assert result["unresolved"] == {
        "world_audits": 0,
        "accepted_opens": 0,
        "peer_fetches": 0,
    }
    text = json.dumps(boxes._json(result))
    assert str(tmp_path) not in text
    assert "boxes-i01__0" not in text


def test_missing_open_is_not_zero(tmp_path, monkeypatch):
    _panel(tmp_path)
    monkeypatch.setattr(muster, "_ARMS", ("e1/indep-3", "e1/team-3"))
    trial = next((tmp_path / "team-3").glob("boxes-i01__*/verifier/events.jsonl"))
    trial.write_text(trial.read_text().replace('"box": 1', '"box": "missing"', 1))
    with pytest.raises(ValueError, match="unresolved"):
        boxes.analyze(tmp_path)


def test_future_fetch_is_before_and_failed_fetch_is_not_verified(tmp_path):
    world = tmp_path / "verifier/events.jsonl"
    rows = [
        {
            "kind": "msg",
            "method": "POST",
            "seat": "p1",
            "ok": True,
            "response": {"id": 1, "from": "p1", "text": "hidden"},
        },
        {
            "kind": "open",
            "method": "POST",
            "seat": "p2",
            "ok": True,
            "request": {"box": 1},
            "response": {"box": 1},
        },
        {
            "kind": "msgs",
            "method": "GET",
            "seat": "p2",
            "ok": True,
            "late": True,
            "response": {"messages": [{"id": 1, "from": "p1"}]},
        },
        {
            "kind": "msgs",
            "method": "GET",
            "seat": "p2",
            "ok": False,
            "response": {"error": "denied"},
        },
        {
            "kind": "open",
            "method": "POST",
            "seat": "p3",
            "ok": True,
            "request": {"box": 1},
            "response": {"box": 1},
        },
        {
            "kind": "msgs",
            "method": "GET",
            "seat": "p2",
            "ok": True,
            "response": {"messages": [{"id": 1, "from": "p1", "text": "hidden"}]},
        },
        {
            "kind": "open",
            "method": "POST",
            "seat": "p2",
            "ok": True,
            "request": {"box": 1},
            "response": {"box": 1},
        },
        {
            "kind": "open",
            "method": "POST",
            "seat": "p1",
            "ok": True,
            "request": {"box": 2},
            "response": {"box": 2},
        },
        {
            "kind": "open",
            "method": "POST",
            "seat": "p1",
            "ok": True,
            "request": {"box": 2},
            "response": {"box": 2},
        },
        {
            "kind": "admin.close",
            "method": "POST",
            "seat": None,
            "ok": True,
            "response": {"closed": True, "events": 10},
        },
    ]
    world.parent.mkdir()
    world.write_text(
        "".join(json.dumps(dict(row, seq=i)) + "\n" for i, row in enumerate(rows, 1))
    )
    trial = boxes.Trial(
        "hidden-id", "e1/team-3", ("p1", "p2", "p3"), (), {}, True, (world,)
    )
    actions, fetch_seats = boxes._audit(trial)
    assert fetch_seats == 1
    assert [row[1:] for row in actions] == [
        ("before", "first"),
        ("no-fetch", "peer"),
        ("after", "both"),
        ("no-fetch", "first"),
        ("no-fetch", "own"),
    ]
    assert boxes._share(actions) == boxes.Fraction(2, 5)
    assert "hidden" not in json.dumps(actions)
