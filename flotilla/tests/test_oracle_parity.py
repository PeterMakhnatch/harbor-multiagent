"""oracle_parity.py on a synthetic finished oracle rehearsal: one pass, then each fault fails closed."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from conftest import FLOTILLA, load
from test_run_wave import commit_plan, git, init_repo, task_tree

PARITY = load(FLOTILLA / "oracle_parity.py")
RUN = PARITY.RUN
SEATS = ("p1", "p2")
DIGEST = "sha256:" + "ab" * 32


def oracle_plan() -> dict:
    return {
        "name": "rehearsal",
        "prereg": {"commit": "placeholder", "doc": "docs/plan.md", "section": "How to run a wave"},
        "task_sets": [{"tasks_dir": "tasks", "manifest": "tasks/manifest.json", "instances": [0, 1],
                       "arms": ["solo", "team"]}],
        "agent": "oracle",
        "make_jobs": {"max_turns": 4, "env": "docker", "concurrency": 2, "start_spacing_s": 0.0},
        "budget": {"cap_usd": 0.0, "reserve_per_trial_usd": 0.0, "provider_floors": {}},
        "order_seed": 7,
    }


def write_trial(jobs: Path, job: str, cell: dict, reward: float) -> Path:
    trial = jobs / job / "t-x__abc"
    (trial / "verifier").mkdir(parents=True)
    participants = [{"seat": seat, "name": "oracle", "model_name": None} for seat in SEATS]
    result = {"trial_name": trial.name, "task_id": {"path": cell["task"]}, "reward_valid": True,
              "agent_info": {"name": "oracle"}, "exception_info": None,
              "config": {"trial_name": trial.name, "trials_dir": str(jobs / job), "task": {"path": cell["task"]},
                         "agent": {"name": "oracle"}, "participants": participants},
              "seat_results": [{"seat": seat, "agent_info": {"name": "oracle"}, "exception_info": None}
                               for seat in SEATS],
              "verifier_result": {"rewards": {"reward": reward}}}
    (trial / "result.json").write_text(json.dumps(result))
    (trial / "lock.json").write_text(json.dumps({"task": {"path": cell["task"], "digest": DIGEST}}))
    score = {"error": None, "instance": cell["instance"], "condition": cell["arm"], "reward": reward}
    (trial / "verifier" / "score.json").write_text(json.dumps(score))
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": reward}))
    (trial / "verifier" / "world-state.json").write_text("{}")
    return trial


def rehearsal(tmp: Path) -> dict:
    repo = init_repo(tmp)
    task_tree(repo, [0, 1], ["solo", "team"], seats=SEATS)
    path = commit_plan(repo, oracle_plan())
    wave = RUN.load_plan(path)
    order = RUN.order_of(wave, RUN.resolve_paths(wave, repo), RUN.scripted_of(wave, repo, need_tasks=True))
    state = RUN.initial_state(wave, order, hashlib.sha256(path.read_bytes()).hexdigest())
    run_dir, rewards = tmp / "run", {}
    jobs = run_dir / "jobs"
    (run_dir / "configs").mkdir(parents=True)
    for index, cell in enumerate(order):
        job = f"rehearsal-{index:06x}"
        config = RUN.job_config(job, jobs, cell["task"], "oracle", None, 4, "docker")
        (run_dir / "configs" / f"{job}.json").write_text(json.dumps(config, indent=2))
        rewards[Path(cell["task"]).name] = 1.0 if cell["arm"] == "team" else 0.5
        write_trial(jobs, job, cell, rewards[Path(cell["task"]).name])
        attempt = {"job": job, "valid": True, "problem": None}
        state["finished"][cell["key"]] = {"attempts": [attempt], "valid": True, "job": job,
                                          "models": "oracle (no model)"}
    RUN.save_state(run_dir / "state.json", state)
    return {"repo": repo, "plan": path, "run": run_dir, "jobs": jobs, "order": order, "rewards": rewards}


def fake_sim(rewards: dict, schedule_shift: float = 0.0):
    def simulate(path: Path, policies, schedule: int) -> dict:
        assert policies is None
        return {"reward": rewards[path.name] - schedule_shift * schedule, "invalid": None, "stalled": False,
                "score": {}, "policies": dict.fromkeys(SEATS, "careful")}
    return simulate


def check(case: dict, **options) -> dict:
    options.setdefault("simulate", fake_sim(case["rewards"]))
    options.setdefault("digest_of", lambda task: DIGEST)
    return PARITY.verify(case["plan"], case["run"], **options)


def first_trial(case: dict) -> tuple[str, Path]:
    state = json.loads((case["run"] / "state.json").read_text())
    job = state["finished"][case["order"][0]["key"]]["job"]
    return job, next((case["jobs"] / job).iterdir())


def test_a_complete_matching_rehearsal_passes_and_names_no_private_path(tmp_path):
    case = rehearsal(tmp_path)
    receipt = check(case)
    assert receipt["verdict"] == "PASS", receipt
    assert receipt["planned"] == receipt["matched"] == 4
    assert {row["oracle"] for row in receipt["cells"]} == {0.5, 1.0}
    assert all(row["policies"] == ["careful"] for row in receipt["cells"])
    text = json.dumps(receipt) + PARITY.text_of(receipt)
    assert str(case["repo"]) not in text and "order_seed" not in text
    assert any("Docker image" in line for line in receipt["not_bound"])


def test_a_missing_reward_fails_and_is_never_zero(tmp_path):
    case = rehearsal(tmp_path)
    _job, trial = first_trial(case)
    result = json.loads((trial / "result.json").read_text())
    result["verifier_result"] = None
    (trial / "result.json").write_text(json.dumps(result))
    case["rewards"][Path(case["order"][0]["task"]).name] = 0.0
    receipt = check(case)
    row = receipt["cells"][0]
    assert receipt["verdict"] == "FAIL"
    assert row["oracle"] is None and row["sim"] is None and "no valid verifier reward" in row["problem"]


def drop_cell(case):
    state = json.loads((case["run"] / "state.json").read_text())
    del state["finished"][case["order"][-1]["key"]]
    RUN.save_state(case["run"] / "state.json", state)


def extra_job(case):
    (case["jobs"] / "rehearsal-ffffff" / "t-y__def").mkdir(parents=True)


def second_trial(case):
    _job, trial = first_trial(case)
    shutil.copytree(trial, trial.with_name("t-x__other"))


def other_reward(case):
    case["rewards"][Path(case["order"][1]["task"]).name] = 0.25


def verifier_disagrees(case):
    _job, trial = first_trial(case)
    (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 0.0}))


def wrong_agent(case):
    _job, trial = first_trial(case)
    result = json.loads((trial / "result.json").read_text())
    result["seat_results"][1]["agent_info"]["name"] = "terminus-2"
    (trial / "result.json").write_text(json.dumps(result))


def edited_config(case):
    job, _trial = first_trial(case)
    path = case["run"] / "configs" / f"{job}.json"
    config = json.loads(path.read_text())
    config["participants"][0]["kwargs"]["max_turns"] = 99
    path.write_text(json.dumps(config))


def two_valid_attempts(case):
    state = json.loads((case["run"] / "state.json").read_text())
    row = state["finished"][case["order"][0]["key"]]
    row["attempts"].insert(0, {**row["attempts"][0], "job": "rehearsal-eeeeee"})
    RUN.save_state(case["run"] / "state.json", state)


FAULTS = [
    (drop_cell, "missing: no finished attempt", None),
    (extra_job, None, "unaccounted: job rehearsal-ffffff"),
    (second_trial, "ambiguous: 2 trial folders", None),
    (other_reward, "mismatch: oracle reward differs", None),
    (verifier_disagrees, "verifier reward.json reward differs", None),
    (wrong_agent, "seat p2 ran 'terminus-2', not the oracle agent", None),
    (edited_config, "stored job config differs", None),
    (two_valid_attempts, "ambiguous: 2 valid attempts", None),
]


@pytest.mark.parametrize(("fault", "cell_problem", "run_problem"), FAULTS, ids=[item[0].__name__ for item in FAULTS])
def test_each_fault_fails_closed(tmp_path, fault, cell_problem, run_problem):
    case = rehearsal(tmp_path)
    fault(case)
    receipt = check(case)
    assert receipt["verdict"] == "FAIL"
    if cell_problem:
        assert any(cell_problem in (row["problem"] or "") for row in receipt["cells"]), receipt["cells"]
    if run_problem:
        assert any(run_problem in item for item in receipt["problems"]), receipt["problems"]


def test_a_resolved_retry_counts_once(tmp_path):
    case = rehearsal(tmp_path)
    state = json.loads((case["run"] / "state.json").read_text())
    row = state["finished"][case["order"][0]["key"]]
    config = RUN.job_config("rehearsal-dddddd", case["jobs"], case["order"][0]["task"], "oracle", None, 4, "docker")
    (case["run"] / "configs" / "rehearsal-dddddd.json").write_text(json.dumps(config))
    row["attempts"].insert(0, {"job": "rehearsal-dddddd", "valid": False, "problem": "no trial result"})
    RUN.save_state(case["run"] / "state.json", state)
    receipt = check(case)
    assert receipt["verdict"] == "PASS", receipt
    assert receipt["planned"] == receipt["matched"] == 4 and receipt["cells"][0]["attempts"] == 2


def test_schedule_dependence_and_changed_task_bytes_fail(tmp_path):
    case = rehearsal(tmp_path)
    shifted = check(case, simulate=fake_sim(case["rewards"], schedule_shift=0.1))
    assert all("depends on the schedule seed" in row["problem"] for row in shifted["cells"])
    changed = check(case, digest_of=lambda task: "sha256:" + "cd" * 32)
    assert all("task folder bytes changed" in row["problem"] for row in changed["cells"])


def test_the_plan_must_be_the_bytes_the_run_started_from(tmp_path):
    case = rehearsal(tmp_path)
    old = git(case["repo"], "rev-parse", "HEAD")
    plan = json.loads(case["plan"].read_text())
    plan["notes"] = "edited after the run"
    case["plan"].write_text(json.dumps(plan))
    git(case["repo"], "commit", "-am", "edit plan")
    assert check(case)["problems"] == ["refused: state.json was started from other plan bytes"]
    assert check(case, rev=old)["verdict"] == "PASS"


def test_no_digest_checker_fails_closed(tmp_path, monkeypatch):
    case = rehearsal(tmp_path)
    monkeypatch.setattr(PARITY, "task_digest_fn", lambda: None)
    receipt = PARITY.verify(case["plan"], case["run"], simulate=fake_sim(case["rewards"]))
    assert receipt["verdict"] == "FAIL" and receipt["cells"] == [] and receipt["task_digest"] is None
    assert any("task digest is not importable" in item for item in receipt["problems"])


def test_a_helper_refusal_names_no_absolute_path(tmp_path):
    case = rehearsal(tmp_path)
    (Path(case["order"][0]["task"]) / "task.toml").write_text("[agents]\n")
    receipt = check(case)
    text = json.dumps(receipt) + PARITY.text_of(receipt)
    assert receipt["verdict"] == "FAIL" and "<path> has no seats" in receipt["problems"][0]
    assert str(tmp_path) not in text and str(tmp_path.resolve()) not in text
    assert PARITY.scrub("refused: ~/x/t and /a/b c, see flotilla/plans/a.json") == \
        "refused: <path> and <path> c, see flotilla/plans/a.json"


def test_cli_without_state_prints_a_failing_receipt(tmp_path, capsys):
    case = rehearsal(tmp_path)
    (case["run"] / "state.json").unlink()
    assert PARITY.main([str(case["plan"]), str(case["run"]), "--json"]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["verdict"] == "FAIL" and receipt["problems"] == ["refused: run dir has no state.json"]
