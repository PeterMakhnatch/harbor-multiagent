"""Seeded simulation of a paid wave: a fake Harbor, fake prices, and injected faults."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

RUN = load(FLOTILLA / "run_wave.py")
PRICING = load(FLOTILLA / "pricing.py")
BUILD = load(FLOTILLA / "build_tasks.py")
DEEPSEEK = "deepseek/deepseek-flash"
OPENROUTER = "openrouter/qwen/qwen3.8-27b"
OFF_PEAK = "2026-11-21T16:00:00+00:00"
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
HARBOR_SCRIPT = r"""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
config = json.loads(Path(args[args.index("-c") + 1]).read_text())
task = config["tasks"][0]["path"]
names = [item["model_name"] for item in config["participants"] if item.get("model_name")]
model = names[0] if names else None
oracles = [item["seat"] for item in config["participants"] if item["name"] == "oracle"]
control = json.loads(Path(__CONTROL__).read_text())
spec = control.get(task + "|" + "|".join(names)) or control[task + "|" + (model or "oracle")]
log = Path(__LOG__)
gate = Path(__GATE__)
keys = sorted(name for name in os.environ if name.endswith("_API_KEY"))
with log.open("a") as handle:
    handle.write(json.dumps({"event": "start", "task": task, "model": model, "keys": keys,
                             "env": sorted(os.environ), "job": config["job_name"],
                             "openrouter_key": os.environ.get("OPENROUTER_API_KEY"),
                             "concurrent": config["n_concurrent_trials"], "t": time.time()}) + "\n")
if spec["behavior"] == "slow":
    with gate.open("a") as handle:
        handle.write("slow\n")
    deadline = time.time() + 2
    while time.time() < deadline and gate.read_text().count("slow") < 2:
        time.sleep(0.01)
if spec["behavior"] == "hold":
    time.sleep(0.3)
if spec["behavior"] == "crash":
    with log.open("a") as handle:
        handle.write(json.dumps({"event": "end", "task": task, "model": model, "t": time.time()}) + "\n")
    sys.exit(1)
job = Path(config["jobs_dir"]) / config["job_name"] / "trial"
job.mkdir(parents=True)
tokens = spec["tokens"]
oracle_agent = "terminus-2" if spec["behavior"] == "wrong-agent" else "oracle"
tested = [] if "p1" in oracles else [
    {"seat": "p1", "exception_info": spec.get("exception"),
     "agent_result": {"n_input_tokens": tokens["miss"], "n_cache_tokens": tokens["hit"],
                      "n_output_tokens": tokens["out"], "cost_usd": spec.get("cost_usd")}}]
result = {"started_at": spec["started_at"], "exception_info": spec.get("exception"),
          "seat_results": tested
                         + [{"seat": seat, "under_test": False, "exception_info": None,
                             "agent_info": {"name": oracle_agent, "model_info": None},
                             "agent_result": {"n_input_tokens": None, "n_cache_tokens": None,
                                              "n_output_tokens": None, "cost_usd": spec.get("oracle_cost")}}
                            for seat in oracles]}
(job / "result.json").write_text(json.dumps(result))
if spec["behavior"] != "invalid":
    (job / "verifier").mkdir()
    score = {"error": None, "instance": spec["instance"], "condition": spec["arm"]}
    used = spec.get("model_name") or model
    if spec["behavior"] == "wrong-score":
        score["instance"] = spec["instance"] + 1
    if spec["behavior"] == "wrong-model":
        used = "other/model"
    assigned = spec.get("assignment")
    if assigned:
        used_models = list(assigned)
        if spec["behavior"] == "wrong-model":
            used_models[0] = "other/model"
        rows = [{"seat": f"p{index}", "model_name": item} for index, item in enumerate(used_models, 1)]
    else:
        rows = [{"seat": item["seat"], "model_name": used} for item in config["participants"]
                if item["name"] != "oracle"]
    rows += [{"seat": seat, "name": oracle_agent, "model_name": None} for seat in oracles]
    (job / "config.json").write_text(json.dumps({"participants": rows}))
    (job / "verifier" / "score.json").write_text(json.dumps(score))
    (job / "verifier" / "world-state.json").write_text("{}")
with log.open("a") as handle:
    handle.write(json.dumps({"event": "end", "task": task, "model": model, "t": time.time()}) + "\n")
"""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                            env={**os.environ, **GIT})
    return result.stdout.strip()


def init_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    return repo


def commit_plan(repo: Path, plan: dict) -> Path:
    (repo / "docs").mkdir()
    (repo / "docs" / "plan.md").write_text("# How to run a wave\n")
    path = repo / "plan.json"
    path.write_text(json.dumps(plan))
    git(repo, "add", "docs/plan.md", "plan.json")
    git(repo, "commit", "-m", "plan")
    plan["prereg"]["commit"] = git(repo, "rev-parse", "HEAD")
    path.write_text(json.dumps(plan))
    git(repo, "add", "plan.json")
    git(repo, "commit", "-m", "cite prereg")
    return path


def task_tree(repo: Path, instances: list[int], arms: list[str], seats: tuple[str, ...] = ("p1",)) -> None:
    tasks: dict[str, dict] = {}
    root = repo / "tasks"
    agents = "[agents]\n" + "".join(f"{seat} = {{}}\n" for seat in seats)
    for instance in dict.fromkeys(instances):
        for arm in arms:
            name = f"t-{instance:06x}-{arm}"
            folder = root / "split" / name
            folder.mkdir(parents=True)
            (folder / "task.toml").write_text(agents)
            tasks[f"flotilla/{name}"] = {"instance": instance, "condition": arm, "path": f"split/{name}",
                                         "seats": list(seats)}
    (root / "manifest.json").write_text(json.dumps({"format": "flotilla-manifest", "version": 1, "tasks": tasks}))


def plan_for(instances, arms, models, *, concurrency=2, env="docker", cap=10.0, reserve=0.05, seed=1,
             spacing=0.0) -> dict:
    floors = {}
    if any(model.startswith("deepseek/") for model in models):
        floors["deepseek"] = 1.0
    if any(model.startswith("openrouter/") for model in models):
        floors["openrouter"] = 1.0
    return {
        "name": "wave",
        "prereg": {"commit": "placeholder", "doc": "docs/plan.md", "section": "How to run a wave"},
        "task_sets": [{"tasks_dir": "tasks", "manifest": "tasks/manifest.json", "instances": instances, "arms": arms}],
        "models": models, "agent": "terminus-2",
        "make_jobs": {"max_turns": 4, "env": env, "concurrency": concurrency, "start_spacing_s": spacing},
        "budget": {"cap_usd": cap, "reserve_per_trial_usd": reserve, "provider_floors": floors},
        "order_seed": seed,
    }


def write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Sources:
    def __init__(self, prices: dict, balance, payload: dict | None = None, zdr: dict | None = None):
        self._prices = prices
        self._balance = balance
        self._payload = payload
        self._zdr = zdr
        self.price_calls = 0
        self.catalog_calls = 0
        self.zdr_calls = 0
        self.balance_calls = []

    def prices(self, models):
        self.price_calls += 1
        return self._prices

    def balance(self, provider):
        self.balance_calls.append(provider)
        return self._balance(provider, len(self.balance_calls))

    def catalog(self):
        self.catalog_calls += 1
        if self._payload is None:
            raise RuntimeError("offline")
        return self._payload

    def zdr(self):
        self.zdr_calls += 1
        if self._zdr is None:
            raise RuntimeError("offline")
        return self._zdr


def catalog() -> dict:
    return {OPENROUTER: {"prompt": "0.0000001", "completion": "0.000002", "input_cache_read": "0.00000001"}}


def spec_for(behavior: str, *, cost_usd=0.02, miss=1_000) -> dict:
    return {"behavior": behavior, "started_at": OFF_PEAK, "cost_usd": cost_usd,
            "tokens": {"hit": 0, "miss": miss, "out": 0},
            "exception": "boom" if behavior == "invalid" else None}


def namer_box(tmp: Path) -> int:
    counter = tmp / "names"
    value = int(counter.read_text()) + 1 if counter.is_file() else 1
    counter.write_text(str(value))
    return value


def launch(tmp: Path, plan: dict, behaviors: dict[str, dict], sources: Sources, seats: tuple[str, ...] = ("p1",),
            tree=None, env_extra: dict[str, str] | None = None, **extra):
    repo = init_repo(tmp)
    if tree is None:
        task_tree(repo, plan["task_sets"][0]["instances"], plan["task_sets"][0]["arms"], seats=seats)
    else:
        tree(repo)
    path = commit_plan(repo, plan)
    harbor, wrapper = tmp / "harbor", tmp / "wrapper"
    script = (HARBOR_SCRIPT.replace("__CONTROL__", json.dumps(str(tmp / "control.json")))
              .replace("__LOG__", json.dumps(str(tmp / "harbor.jsonl")))
              .replace("__GATE__", json.dumps(str(tmp / "gate"))))
    write_executable(harbor, script)
    write_executable(wrapper, f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {json.dumps(str(tmp / 'wrapper.log'))}\nexec \"$@\"\n")
    preview = plan if "models" in plan or plan.get("seat_assignments") else {**plan, "models": [None]}
    order = RUN.order_of(preview, RUN.resolve_paths(preview, repo))
    mapped = {}
    for cell in order:
        spec = dict(behaviors[cell["key"]])
        spec["instance"] = cell["instance"]
        spec["arm"] = cell["arm"]
        if cell.get("assignment"):
            spec["assignment"] = cell["assignment"]
        models = cell.get("assignment") or [cell["model"]]
        mapped[cell["task"] + "|" + "|".join(model or "oracle" for model in models)] = spec
    (tmp / "control.json").write_text(json.dumps(mapped))
    (tmp / "gate").write_text("")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "LC_ALL": "C", "MODAL_TOKEN_ID": "modal",
           "DEEPSEEK_API_KEY": "ds", "OPENROUTER_API_KEY": "or", "OPENAI_API_KEY": "no",
           "HF_TOKEN": "secret", "SSH_AUTH_SOCK": "/tmp/agent.sock", **(env_extra or {})}
    events = RUN.run(path, run_dir=tmp / "run", jobs_dir=tmp / "jobs", harbor=harbor, wrapper=wrapper,
                     sources=sources, base_env=env, namer=lambda _n: f"{namer_box(tmp):06x}", **extra)
    return events, order, path, env


def assert_invariants(events, *, concurrency: int, cap: float, reserve: float) -> None:
    in_flight = 0
    for event in events:
        if event["kind"] == "start":
            in_flight += 1
            assert event["in_flight"] == in_flight
            assert in_flight <= concurrency
            assert event["projected"] <= cap + 1e-9
        elif event["kind"] == "end":
            in_flight -= 1
            assert in_flight >= 0


def test_order_is_a_function_of_the_seed_and_blocks_stay_together():
    plan = plan_for([0, 1], ["silent", "talk"], [DEEPSEEK, OPENROUTER], seed=1)
    plan["task_sets"].append({**plan["task_sets"][0], "tasks_dir": "other", "manifest": "other/manifest.json"})
    first = RUN.order_of(plan)
    assert RUN.order_of(plan) == first
    assert RUN.order_of({**plan, "order_seed": 2}) != first
    seen = []
    for cell in first:
        block = (cell["task_set"], cell["instance"])
        if not seen or seen[-1] != block:
            assert block not in seen
            seen.append(block)
    assert any(blocks_of(seed) != sorted(blocks_of(seed)) for seed in range(1, 30))
    sets = [block[0] for block in blocks_of(1)]
    assert sets == [sets[0], 1 - sets[0]] * (len(sets) // 2)
    assert {blocks_of(seed)[0][0] for seed in range(1, 40)} == {0, 1}
    wide = plan_for([0, 1, 2, 3], ["silent"], [DEEPSEEK], seed=7)
    wide["task_sets"].append({"tasks_dir": "other", "manifest": "other/manifest.json",
                              "instances": [0, 1], "arms": ["silent"]})
    wide_blocks = []
    for cell in RUN.order_of(wide):
        block = (cell["task_set"], cell["instance"])
        if not wide_blocks or wide_blocks[-1] != block:
            wide_blocks.append(block)
    assert [item[0] for item in wide_blocks] == [0, 1, 0, 0, 1, 0]


def blocks_of(seed: int) -> list[tuple[int, int]]:
    plan = plan_for([0, 1], ["silent", "talk"], [DEEPSEEK, OPENROUTER], seed=seed)
    plan["task_sets"].append({**plan["task_sets"][0], "tasks_dir": "other", "manifest": "other/manifest.json"})
    blocks = []
    for cell in RUN.order_of(plan):
        block = (cell["task_set"], cell["instance"])
        if not blocks or blocks[-1] != block:
            blocks.append(block)
    return blocks


def test_simulation_holds_the_invariants(tmp_path):
    plan = plan_for([0, 1], ["silent", "talk"], [DEEPSEEK, OPENROUTER], concurrency=2, cap=10.0, reserve=0.05)
    repo = init_repo(tmp_path / "preview")
    task_tree(repo, [0, 1], ["silent", "talk"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, repo))
    behaviors = {cell["key"]: spec_for("ok", cost_usd=0.02) for cell in order}
    behaviors[order[0]["key"]] = spec_for("slow")
    behaviors[order[1]["key"]] = spec_for("slow")
    behaviors[order[2]["key"]] = spec_for("crash")
    behaviors[order[3]["key"]] = spec_for("invalid", cost_usd=0.03)
    behaviors[order[4]["key"]] = spec_for("spike", cost_usd=0.40, miss=2_000_000)
    sources = Sources(catalog(), lambda _provider, n: 10.0 if n < 7 else 0.2)
    events, order, _path, _env = launch(tmp_path / "sim", plan, behaviors, sources)
    assert_invariants(events, concurrency=2, cap=10.0, reserve=0.05)
    starts = [event for event in events if event["kind"] == "start"]
    ends = {event["key"]: event for event in events if event["kind"] == "end"}
    assert [event["key"] for event in starts] == [cell["key"] for cell in order[:6]]
    assert max(event["in_flight"] for event in starts) == 2
    assert events[-1]["reason"] == "floor"
    assert sources.price_calls == 1
    assert ends[order[2]["key"]]["valid"] is False and ends[order[2]["key"]]["source"] == "reserve"
    assert ends[order[3]["key"]]["valid"] is False
    spike = ends[order[4]["key"]]
    if order[4]["model"] == OPENROUTER:
        assert spike["cost"] == 0.40 and spike["source"] == "recorded" and spike["estimate"] != 0.40
    else:
        expected = PRICING.deepseek_cost({"hit": 0, "miss": 2_000_000, "out": 0}, datetime.fromisoformat(OFF_PEAK))
        assert spike["cost"] == expected and spike["source"] == "deepseek"
    harbor_log = [json.loads(line) for line in (tmp_path / "sim" / "harbor.jsonl").read_text().splitlines()]
    assert all(row["keys"] == ["DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"] for row in harbor_log if row["event"] == "start")
    assert all(row["concurrent"] == 1 for row in harbor_log if row["event"] == "start")
    spans = {}
    for row in harbor_log:
        spans.setdefault(row["task"] + "|" + row["model"], {})[row["event"]] = row["t"]
    left, right = order[0], order[1]
    a, b = spans[left["task"] + "|" + left["model"]], spans[right["task"] + "|" + right["model"]]
    assert a["start"] < b["end"] and b["start"] < a["end"]
    assert str(tmp_path / "sim" / "harbor") in (tmp_path / "sim" / "wrapper.log").read_text()
    assert all(event["command"][0] == str(tmp_path / "sim" / "wrapper") for event in starts)
    missing = plan_for([0], ["silent"], [OPENROUTER], concurrency=1, cap=1.0, reserve=0.01)
    probe = Sources(catalog(), lambda *_args: 10.0)
    preview = init_repo(tmp_path / "missing-preview")
    task_tree(preview, [0], ["silent"])
    only = RUN.order_of(missing, RUN.resolve_paths(missing, preview))
    flagged, _order, _path, _env = launch(
        tmp_path / "missing", missing, {only[0]["key"]: spec_for("ok", cost_usd=0, miss=1_000)}, probe)
    end = next(event for event in flagged if event["kind"] == "end")
    assert end["source"] == "token-estimate" and end["flag"] == "missing-recorded"
    assert end["cost"] == PRICING.openrouter_cost(OPENROUTER, {"hit": 0, "miss": 1_000, "out": 0}, catalog())


def test_cap_stops_before_a_start_and_resume_skips_finished_cells(tmp_path):
    plan = plan_for([0, 1, 2], ["silent", "talk"], [DEEPSEEK], concurrency=1, cap=0.25, reserve=0.10)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0, 1, 2], ["silent", "talk"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    behaviors = {cell["key"]: spec_for("ok", miss=1_000) for cell in order}
    behaviors[order[0]["key"]] = spec_for("crash")
    behaviors[order[1]["key"]] = spec_for("invalid", miss=333_333)
    behaviors[order[2]["key"]] = spec_for("spike", miss=2_000_000)
    events, order, path, env = launch(tmp_path / "cap", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert_invariants(events, concurrency=1, cap=0.25, reserve=0.10)
    assert events[-1]["reason"] == "cap"
    assert len([event for event in events if event["kind"] == "start"]) == 3
    assert events[-1]["spent"] > 0.25
    before = (tmp_path / "cap" / "harbor.jsonl").read_text().count('"event": "start"')
    again = RUN.run(path, run_dir=tmp_path / "cap" / "run", jobs_dir=tmp_path / "cap" / "jobs",
                    harbor=tmp_path / "cap" / "harbor", wrapper=tmp_path / "cap" / "wrapper",
                    sources=Sources({}, lambda *_args: 10.0), base_env=env,
                    namer=lambda _n: f"{namer_box(tmp_path / 'cap'):06x}")
    assert again[-1]["reason"] == "cap"
    assert [event for event in again if event["kind"] == "start"] == []
    assert (tmp_path / "cap" / "harbor.jsonl").read_text().count('"event": "start"') == before


def test_retry_invalid_reruns_only_invalid_cells(tmp_path):
    plan = plan_for([0], ["silent", "talk"], [DEEPSEEK], concurrency=1, cap=5.0, reserve=0.05)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["silent", "talk"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    behaviors = {order[0]["key"]: spec_for("invalid"), order[1]["key"]: spec_for("ok")}
    events, order, path, env = launch(tmp_path / "retry", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert events[-1]["reason"] == "done"
    skipped = RUN.run(path, run_dir=tmp_path / "retry" / "run", jobs_dir=tmp_path / "retry" / "jobs",
                      harbor=tmp_path / "retry" / "harbor", wrapper=tmp_path / "retry" / "wrapper",
                      sources=Sources({}, lambda *_args: 10.0), base_env=env, namer=lambda _n: "000099")
    assert [event for event in skipped if event["kind"] == "start"] == []
    env = {**env, "WAVE_HARBOR_LOG": str(tmp_path / "retry" / "retry.jsonl")}
    retried = RUN.run(path, run_dir=tmp_path / "retry" / "run", jobs_dir=tmp_path / "retry" / "jobs",
                      harbor=tmp_path / "retry" / "harbor", wrapper=tmp_path / "retry" / "wrapper",
                      sources=Sources({}, lambda *_args: 10.0), base_env=env, namer=lambda _n: "000100",
                      retry_invalid=True)
    assert [event["key"] for event in retried if event["kind"] == "start"] == [order[0]["key"]]
    again = RUN.run(path, run_dir=tmp_path / "retry" / "run", jobs_dir=tmp_path / "retry" / "jobs",
                    harbor=tmp_path / "retry" / "harbor", wrapper=tmp_path / "retry" / "wrapper",
                    sources=Sources({}, lambda *_args: 10.0), base_env=env, namer=lambda _n: "000101",
                    retry_invalid=True)
    assert [event for event in again if event["kind"] == "start"] == []
    saved = json.loads((tmp_path / "retry" / "run" / "state.json").read_text())
    attempts = saved["finished"][order[0]["key"]]["attempts"]
    assert len(attempts) == 2 and attempts[0]["job"] != attempts[1]["job"]
    assert all(item["valid"] is False for item in attempts)


def test_modal_is_not_wrapped_and_may_exceed_two(tmp_path):
    plan = plan_for([0], ["silent"], [OPENROUTER], concurrency=12, env="modal", cap=1.0, reserve=0.01)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["silent"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    events, _order, _path, _env = launch(
        tmp_path / "modal", plan, {order[0]["key"]: spec_for("ok", cost_usd=0.01)},
        Sources(catalog(), lambda *_args: 10.0))
    assert events[-1]["reason"] == "done"
    assert events[0]["command"][0].endswith("harbor")
    assert not (tmp_path / "modal" / "wrapper.log").exists()


def test_refuses_uncommitted_plan_and_a_prereg_that_is_not_an_ancestor(tmp_path):
    repo = init_repo(tmp_path)
    task_tree(repo, [0], ["silent"])
    plan = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1)
    path = commit_plan(repo, plan)
    path.write_text(path.read_text() + "\n")
    try:
        RUN.run(path, run_dir=tmp_path / "run", jobs_dir=tmp_path / "jobs")
    except SystemExit as exc:
        assert "differs from HEAD" in str(exc)
    else:
        raise AssertionError("an uncommitted plan must be refused")
    path.write_text(json.dumps(plan))
    git(repo, "checkout", "-b", "side")
    (repo / "docs" / "plan.md").write_text("# How to run a wave\nside\n")
    git(repo, "add", "docs/plan.md")
    git(repo, "commit", "-m", "side")
    side = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    plan["prereg"]["commit"] = side
    path.write_text(json.dumps(plan))
    git(repo, "add", "plan.json")
    git(repo, "commit", "-m", "bad prereg")
    try:
        RUN.run(path, run_dir=tmp_path / "run2", jobs_dir=tmp_path / "jobs")
    except SystemExit as exc:
        assert "not an ancestor" in str(exc)
    else:
        raise AssertionError("a prereg commit that is not an ancestor must be refused")


def test_docker_concurrency_above_two_is_refused(tmp_path):
    blob = tmp_path / "plan.json"
    blob.write_text(json.dumps(plan_for([0], ["silent"], [DEEPSEEK], concurrency=3)))
    try:
        RUN.load_plan(blob)
    except SystemExit as exc:
        assert "concurrency must be <= 2" in str(exc)
    else:
        raise AssertionError("docker concurrency 3 must be refused")


def test_openrouter_model_is_passed_through_to_terminus(tmp_path):
    out = tmp_path / "tasks"
    BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), out)
    subprocess.run(
        [sys.executable, str(FLOTILLA / "make_jobs.py"), "--tasks", str(out / "*" / "*"), "--agent", "terminus-2",
         "--model", "openrouter/deepseek/deepseek-v4-flash", "--name", "check", "--max-turns", "40",
         "--out", str(tmp_path / "configs"), "--jobs-dir", str(tmp_path / "jobs")],
        check=True, capture_output=True, text=True)
    config = json.loads(next((tmp_path / "configs").glob("*.json")).read_text())
    assert config["participants"]
    assert all(seat["model_name"] == "openrouter/deepseek/deepseek-v4-flash" for seat in config["participants"])
    assert all(seat["name"] == "terminus-2" for seat in config["participants"])
    assert "base_url" not in config["participants"][0]


def test_missing_cache_price_charges_the_input_price():
    tokens = {"hit": 1_000, "miss": 2_000, "out": 3}
    prices = {"openai/gpt-oss-120b": {"prompt": "0.0000002", "completion": "0.000001"}}
    assert PRICING.openrouter_cost("openrouter/openai/gpt-oss-120b", tokens, prices) == (
        1_000 * 0.0000002 + 2_000 * 0.0000002 + 3 * 0.000001)
    payload = {"data": [{"id": "openai/gpt-oss-120b", "pricing": {"prompt": "0.0000002", "completion": "0.000001"}}]}
    assert "input_cache_read" not in PRICING.parse_openrouter_models(payload)["openai/gpt-oss-120b"]
    assert PRICING.parse_deepseek_balance({"balance_infos": [{"total_balance": "2.54"}]}) == 2.54
    assert PRICING.parse_openrouter_credits({"data": {"total_credits": 50, "total_usage": 27.5}}) == 22.5
    peak = datetime(2026, 11, 23, 2, 30, tzinfo=UTC)
    off = datetime(2026, 11, 21, 16, 0, tzinfo=UTC)
    assert PRICING.peak_factor(peak) == 2.0 and PRICING.peak_factor(off) == 1.0


def forecast_block(out: str) -> list[str]:
    """The cost forecast lines of a dry run: one titled block, right after the key line and before the cells."""
    lines = out.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("cost forecast"))
    assert lines[start - 1].startswith("OpenRouter key: ")
    cells = [index for index, line in enumerate(lines) if line[:1].isdigit()]
    assert cells and cells == list(range(cells[0], len(lines))) and start < cells[0]
    block = lines[start:cells[0]]
    assert all(line.startswith("  ") for line in block[1:])
    return block


def test_help_and_example_dry_run(tmp_path, monkeypatch, capsys):
    help_run = subprocess.run([sys.executable, str(FLOTILLA / "run_wave.py"), "--help"], capture_output=True, text=True)
    assert help_run.returncode == 0 and "dry-run" in help_run.stdout
    assert "--smoke" in help_run.stdout and "--history-jobs" in help_run.stdout
    offline = Sources({}, lambda *_args: 10.0)
    monkeypatch.setattr(RUN, "HttpSources", lambda _env: offline)
    assert RUN.main(["--dry-run", str(FLOTILLA / "plans" / "example.json"), "--history-jobs", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "reserve $0.10 per trial" in out
    assert "openrouter/qwen/qwen3.8-27b" in out
    assert "start spacing 10.0 s" in out
    assert forecast_block(out)
    assert offline.price_calls == 0 and offline.balance_calls == []


def test_starts_are_spaced_in_a_fresh_run_and_on_retry(tmp_path):
    plan = plan_for([0, 1], ["silent", "talk"], [OPENROUTER], concurrency=4, env="modal", cap=5.0, reserve=0.05,
                    spacing=10.0)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0, 1], ["silent", "talk"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    behaviors = {cell["key"]: spec_for("ok", cost_usd=0.01) for cell in order}
    behaviors[order[1]["key"]] = spec_for("invalid", cost_usd=0.01)
    behaviors[order[3]["key"]] = spec_for("invalid", cost_usd=0.01)
    now, sleeps = [0.0], []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    events, order, path, env = launch(tmp_path / "spaced", plan, behaviors, Sources(catalog(), lambda *_args: 10.0),
                                      sleep=sleep, clock=lambda: now[0])
    assert events[-1]["reason"] == "done"
    assert len([event for event in events if event["kind"] == "start"]) == 4
    assert sleeps == [10.0, 10.0, 10.0]
    log = (tmp_path / "spaced" / "run" / "progress.log").read_text()
    assert log.count("start spacing 10.0 s") == 1
    now[0] += 3.0
    sleeps.clear()
    retried = RUN.run(path, run_dir=tmp_path / "spaced" / "run", jobs_dir=tmp_path / "spaced" / "jobs",
                      harbor=tmp_path / "spaced" / "harbor", wrapper=tmp_path / "spaced" / "wrapper",
                      sources=Sources(catalog(), lambda *_args: 10.0), base_env=env,
                      namer=lambda _n: f"{namer_box(tmp_path / 'spaced'):06x}", retry_invalid=True,
                      sleep=sleep, clock=lambda: now[0])
    assert [event["key"] for event in retried if event["kind"] == "start"] == [order[1]["key"], order[3]["key"]]
    assert sleeps == [10.0]
    assert (tmp_path / "spaced" / "run" / "progress.log").read_text().count("start spacing 10.0 s") == 2
    for bad in (-1, True, "10", 601):
        blob = tmp_path / f"bad-{bad}.json"
        blob.write_text(json.dumps(plan_for([0], ["silent"], [DEEPSEEK], concurrency=1, spacing=bad)))
        try:
            RUN.load_plan(blob)
        except SystemExit as exc:
            assert "start_spacing_s" in str(exc)
        else:
            raise AssertionError(f"start_spacing_s {bad!r} must be refused")


def test_cap_drains_in_flight_before_it_stops(tmp_path):
    plan = plan_for([0, 1], ["silent", "talk"], [DEEPSEEK], concurrency=2, cap=0.25, reserve=0.10)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0, 1], ["silent", "talk"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    behaviors = {cell["key"]: spec_for("ok", miss=66_667) for cell in order}
    events, _order, _path, _env = launch(tmp_path / "drain", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert_invariants(events, concurrency=2, cap=0.25, reserve=0.10)
    starts = [event for event in events if event["kind"] == "start"]
    assert len(starts) == 4 and events[-1]["reason"] == "done"
    third = [index for index, event in enumerate(events) if event["kind"] == "start"][2]
    assert any(event["kind"] == "end" for event in events[:third])


def test_balance_retries_twice_and_a_failed_read_does_not_kill_in_flight(tmp_path):
    plan = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1, cap=1.0, reserve=0.05)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["silent"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    sleeps = []

    def balance(_provider, n):
        if n <= 2:
            raise RuntimeError("down")
        return 10.0

    events, _order, _path, _env = launch(
        tmp_path / "retry-balance", plan, {order[0]["key"]: spec_for("ok")},
        Sources({}, balance), sleep=lambda delay: sleeps.append(delay))
    assert sleeps == [2, 5]
    assert events[-1]["reason"] == "done" and any(event["kind"] == "start" for event in events)
    held = plan_for([0], ["silent", "talk"], [DEEPSEEK], concurrency=2, cap=1.0, reserve=0.05)
    preview2 = init_repo(tmp_path / "preview2")
    task_tree(preview2, [0], ["silent", "talk"])
    held_order = RUN.order_of(held, RUN.resolve_paths(held, preview2))
    behaviors = {cell["key"]: spec_for("ok") for cell in held_order}
    behaviors[held_order[0]["key"]] = spec_for("hold")

    def fail_later(_provider, n):
        if n == 1:
            return 10.0
        raise RuntimeError("down")

    drained, _order, _path, _env = launch(
        tmp_path / "drain-balance", held, behaviors, Sources({}, fail_later), sleep=lambda _delay: None)
    assert drained[-1]["reason"] == "balance"
    assert [event["key"] for event in drained if event["kind"] == "start"] == [held_order[0]["key"]]
    assert any(event["kind"] == "end" and event["key"] == held_order[0]["key"] for event in drained)
    alone = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1)
    try:
        launch(tmp_path / "no-balance", alone, {order[0]["key"]: spec_for("ok")},
               Sources({}, lambda *_args: (_ for _ in ()).throw(RuntimeError("down"))), sleep=lambda _delay: None)
    except SystemExit as exc:
        assert "could not read" in str(exc)
    else:
        raise AssertionError("a failed balance read with nothing in flight must be refused")


def test_harbor_env_drops_host_secrets(tmp_path):
    plan = plan_for([0], ["silent"], [OPENROUTER], concurrency=1, env="modal", cap=1.0, reserve=0.01)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["silent"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    launch(tmp_path / "env", plan, {order[0]["key"]: spec_for("ok", cost_usd=0.01)},
           Sources(catalog(), lambda *_args: 10.0))
    row = json.loads((tmp_path / "env" / "harbor.jsonl").read_text().splitlines()[0])
    assert "HF_TOKEN" not in row["env"] and "SSH_AUTH_SOCK" not in row["env"]
    assert "OPENAI_API_KEY" not in row["env"] and "OPENROUTER_API_KEY" in row["env"]
    assert {"PATH", "HOME", "LC_ALL", "MODAL_TOKEN_ID"} <= set(row["env"])


def test_a_score_for_another_instance_is_invalid(tmp_path):
    plan = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1, cap=1.0, reserve=0.05)
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["silent"])
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    events, order, _path, _env = launch(
        tmp_path / "mismatch", plan, {order[0]["key"]: spec_for("wrong-score")}, Sources({}, lambda *_args: 10.0))
    assert events[-1]["reason"] == "done"
    assert next(event for event in events if event["kind"] == "end")["valid"] is False


def test_prereg_section_comes_from_the_cited_commit(tmp_path):
    repo = init_repo(tmp_path)
    task_tree(repo, [0], ["silent"])
    plan = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1)
    path = commit_plan(repo, plan)
    (repo / "docs" / "plan.md").write_text("# something else\n")
    assert RUN.run(path, dry_run=True, sources=Sources({}, lambda *_args: 10.0), history_jobs=tmp_path / "none") == []
    bare = init_repo(tmp_path / "bare")
    (bare / "docs").mkdir()
    (bare / "docs" / "plan.md").write_text("# not the section\n")
    git(bare, "add", "docs/plan.md")
    git(bare, "commit", "-m", "empty")
    cited = git(bare, "rev-parse", "HEAD")
    (bare / "docs" / "plan.md").write_text("# How to run a wave\n")
    missing = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1)
    missing["prereg"]["commit"] = cited
    blob = bare / "plan.json"
    blob.write_text(json.dumps(missing))
    git(bare, "add", "plan.json")
    git(bare, "commit", "-m", "plan")
    try:
        RUN.run(blob, dry_run=True)
    except SystemExit as exc:
        assert "not in" in str(exc)
    else:
        raise AssertionError("a section that is only in the working tree must be refused")


def test_xm1_dry_run_alternates_families(tmp_path, monkeypatch, capsys):
    plan = json.loads((FLOTILLA / "plans" / "xm1.json").read_text())
    sets = []
    for cell in RUN.order_of(plan):
        if not sets or sets[-1] != cell["task_set"]:
            sets.append(cell["task_set"])
    assert sets == [sets[0], 1 - sets[0]] * (len(sets) // 2)
    assert len(sets) == sum(len(item["instances"]) for item in plan["task_sets"])
    monkeypatch.setattr(RUN, "HttpSources", lambda _env: Sources({}, lambda *_args: 10.0))
    assert RUN.main(["--dry-run", str(FLOTILLA / "plans" / "xm1.json"), "--history-jobs", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert forecast_block(out)
    lines = [line for line in out.splitlines() if line[:1].isdigit()]
    families = []
    for line in lines:
        family = int(line.split("set ")[1].split(" ")[0])
        if not families or families[-1][0] != family:
            families.append([family, 1])
        else:
            families[-1][1] += 1
    assert [item[0] for item in families] == sets
    expected = {len(item.get("arms") or item["conditions"]) * len(plan["models"]) for item in plan["task_sets"]}
    assert {item[1] for item in families} == expected



def assignment_plan(seed: int = 1) -> dict:
    plan = plan_for([0, 1], ["silent", "talk"], [DEEPSEEK], seed=seed)
    plan["seat_assignments"] = [[DEEPSEEK, OPENROUTER], [OPENROUTER, DEEPSEEK],
                                [DEEPSEEK, DEEPSEEK], [OPENROUTER, OPENROUTER]]
    plan["task_sets"].append({**plan["task_sets"][0], "tasks_dir": "other", "manifest": "other/manifest.json"})
    return plan


def test_assignments_replace_models_and_stay_inside_blocks():
    plan = assignment_plan(3)
    first = RUN.order_of(plan)
    assert RUN.order_of(plan) == first
    assert RUN.order_of({**plan, "order_seed": 9}) != first
    assert len(first) == 2 * 2 * 2 * 4
    assert all("assignment" in cell for cell in first)
    counts = {}
    blocks = []
    for cell in first:
        counts[tuple(cell["assignment"])] = counts.get(tuple(cell["assignment"]), 0) + 1
        block = (cell["task_set"], cell["instance"])
        if not blocks or blocks[-1][0] != block:
            blocks.append([block, []])
        blocks[-1][1].append(tuple(cell["assignment"]))
    assert set(counts.values()) == {8}
    assert len(blocks) == 4
    assert all(len(item[1]) == 8 for item in blocks)
    sets = [item[0][0] for item in blocks]
    assert sets == [sets[0], 1 - sets[0]] * (len(sets) // 2)
    plain = plan_for([0], ["silent"], [DEEPSEEK, OPENROUTER])
    assert "assignment" not in RUN.order_of(plain)[0]


def test_unknown_plan_keys_and_unpriced_models_are_refused(tmp_path):
    plan = plan_for([0], ["silent"], [DEEPSEEK])
    plan["extra"] = 1
    blob = tmp_path / "plan.json"
    blob.write_text(json.dumps(plan))
    try:
        RUN.load_plan(blob)
    except SystemExit as exc:
        assert "unknown plan field extra" in str(exc)
    else:
        raise AssertionError("an unknown plan field must be refused")
    del plan["extra"]
    plan["seat_assignments"] = [["not-a-model", DEEPSEEK]]
    blob.write_text(json.dumps(plan))
    try:
        RUN.load_plan(blob)
    except SystemExit as exc:
        assert "pricing.py" in str(exc)
    else:
        raise AssertionError("a model pricing.py cannot price must be refused")


def test_assignment_length_is_refused_before_a_trial(tmp_path):
    plan = plan_for([0], ["silent"], [DEEPSEEK], concurrency=1, cap=1.0, reserve=0.05)
    plan["budget"]["provider_floors"]["openrouter"] = 1.0
    plan["seat_assignments"] = [[DEEPSEEK, OPENROUTER]]
    repo = init_repo(tmp_path / "bad")
    task_tree(repo, [0], ["silent"])
    path = commit_plan(repo, plan)
    try:
        RUN.run(path, run_dir=tmp_path / "bad" / "run", jobs_dir=tmp_path / "bad" / "jobs",
                harbor=tmp_path / "missing-harbor", wrapper=tmp_path / "missing-wrapper")
    except SystemExit as exc:
        assert "has 1 seats" in str(exc)
    else:
        raise AssertionError("a short assignment must be refused")
    assert not (tmp_path / "bad" / "harbor.jsonl").exists()


def test_a_seat_on_the_wrong_model_is_invalid(tmp_path):
    plan = plan_for([0], ["talk"], [DEEPSEEK], concurrency=1, cap=1.0, reserve=0.05)
    plan["budget"]["provider_floors"]["openrouter"] = 1.0
    plan["seat_assignments"] = [[DEEPSEEK, OPENROUTER]]
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, [0], ["talk"], seats=("p1", "p2"))
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    events, _order, path, env = launch(
        tmp_path / "cross", plan, {order[0]["key"]: spec_for("wrong-model")}, Sources({}, lambda *_args: 10.0),
        seats=("p1", "p2"))
    assert next(event for event in events if event["kind"] == "end")["valid"] is False
    saved = json.loads((tmp_path / "cross" / "run" / "state.json").read_text())
    assert saved["order"][0]["assignment"] == [DEEPSEEK, OPENROUTER]
    assert "models p1=" in (tmp_path / "cross" / "run" / "progress.log").read_text()
    config = json.loads(next((tmp_path / "cross" / "run" / "configs").glob("*.json")).read_text())
    assert [row["model_name"] for row in config["participants"]] == [DEEPSEEK, OPENROUTER]
    assert all(row["kwargs"]["max_turns"] == 4 for row in config["participants"])
    matched = {order[0]["key"]: spec_for("ok")}
    again = RUN.run(path, run_dir=tmp_path / "cross" / "run", jobs_dir=tmp_path / "cross" / "jobs",
                    harbor=tmp_path / "cross" / "harbor", wrapper=tmp_path / "cross" / "wrapper",
                    sources=Sources({}, lambda *_args: 10.0), base_env=env, namer=lambda _n: "000200",
                    retry_invalid=True)
    assert [event["key"] for event in again if event["kind"] == "start"] == [order[0]["key"]]


def test_the_estimate_prices_each_seat():
    haiku, luna = "openrouter/anthropic/claude-haiku-5.5", "openrouter/openai/gpt-6-luna"
    prices = {haiku: {"prompt": "0.000001", "completion": "0.000001"},
              luna: {"prompt": "0.000002", "completion": "0.000002"}}
    result = {"started_at": "2026-11-21T16:00:00+00:00", "seat_results": [
        {"seat": "p1", "agent_result": {"n_input_tokens": 1_000, "n_cache_tokens": 0, "n_output_tokens": 0,
                                        "cost_usd": 0.02}},
        {"seat": "p2", "agent_result": {"n_input_tokens": 2_000, "n_cache_tokens": 0, "n_output_tokens": 0,
                                        "cost_usd": 0.03}},
    ]}
    cell = {"model": haiku, "assignment": [haiku, luna]}
    bill = RUN.charge(cell, result, prices, 0.10)
    assert bill["cost"] == 0.05
    assert bill["estimate"] == 1_000 * 0.000001 + 2_000 * 0.000002
    assert bill["source"] == "recorded"


def test_seat_agent_override_keeps_max_turns(tmp_path):
    out = tmp_path / "tasks"
    BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), out)
    subprocess.run(
        [sys.executable, str(FLOTILLA / "make_jobs.py"), "--tasks", str(out / "*" / "*"), "--agent", "terminus-2",
         "--model", "openrouter/anthropic/claude-haiku-5.5", "--name", "cross", "--max-turns", "40",
         "--seat-agent", "p2=terminus-2:openrouter/openai/gpt-6-luna",
         "--out", str(tmp_path / "configs"), "--jobs-dir", str(tmp_path / "jobs")],
        check=True, capture_output=True, text=True)
    config = json.loads(next((tmp_path / "configs").glob("*.json")).read_text())
    by_seat = {row["seat"]: row for row in config["participants"]}
    assert by_seat["p2"]["model_name"] == "openrouter/openai/gpt-6-luna"
    assert by_seat["p1"]["kwargs"]["max_turns"] == 40
    assert by_seat["p2"]["kwargs"]["max_turns"] == 40


def scripted_build(repo: Path) -> None:
    """The schedule.liar build: p1 and p2 under test, p3 scripted (`under_test = false`)."""
    subprocess.run([sys.executable, str(FLOTILLA / "build_tasks.py"), "--family", "schedule", "--scripted",
                    "--instances", "0", "--out", str(repo / "tasks")], check=True, capture_output=True, text=True)


def scripted_plan(models: list[str]) -> dict:
    return plan_for([0], ["talk", "talk-liar"], models, concurrency=1, cap=1.0, reserve=0.05)


def refusal(call) -> str:
    try:
        call()
    except SystemExit as exc:
        return str(exc)
    raise AssertionError("the call must be refused")


def scripted_order(tmp: Path, plan: dict) -> list[dict]:
    preview = init_repo(tmp)
    scripted_build(preview)
    return RUN.order_of(plan, RUN.resolve_paths(plan, preview))


def test_scripted_seat_runs_the_oracle_and_costs_nothing(tmp_path):
    plan = scripted_plan([OPENROUTER])
    plan["scripted_seats"] = ["p3"]
    order = scripted_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: spec_for("ok", cost_usd=0.01) for cell in order}
    events, order, _path, _env = launch(tmp_path / "wave", plan, behaviors, Sources(catalog(), lambda *_args: 10.0),
                                        tree=scripted_build)
    assert events[-1]["reason"] == "done"
    ends = [event for event in events if event["kind"] == "end"]
    assert len(ends) == 2 and all(event["valid"] for event in ends)
    assert all(event["source"] == "recorded" and event["flag"] is None and event["cost"] == 0.01 for event in ends)
    saved = json.loads((tmp_path / "wave" / "run" / "state.json").read_text())
    assert abs(saved["spent_usd"] - 0.02) < 1e-12
    assert all(cell["scripted"] == ["p3"] for cell in saved["order"])
    for config_path in (tmp_path / "wave" / "run" / "configs").glob("*.json"):
        by_seat = {row["seat"]: row for row in json.loads(config_path.read_text())["participants"]}
        assert sorted(by_seat) == ["p1", "p2", "p3"]
        assert by_seat["p3"]["name"] == "oracle" and "model_name" not in by_seat["p3"]
        assert all(by_seat[seat]["name"] == "terminus-2" and by_seat[seat]["model_name"] == OPENROUTER
                   for seat in ("p1", "p2"))
    log = (tmp_path / "wave" / "run" / "progress.log").read_text()
    assert "scripted seats (oracle agent, no model, cost 0): set 0 p3" in log


def test_dry_run_derives_and_prints_the_scripted_seats(tmp_path, monkeypatch, capsys):
    repo = init_repo(tmp_path)
    scripted_build(repo)
    path = commit_plan(repo, scripted_plan([DEEPSEEK]))
    monkeypatch.setattr(RUN, "HttpSources", lambda _env: Sources({}, lambda *_args: 10.0))
    assert RUN.main(["--dry-run", str(path)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[1] == "scripted seats (oracle agent, no model, cost 0): set 0 p3"
    assert lines[2] == ("guards: stop after 6 invalid trials in a row, overall or for one model; "
                        "provider floors deepseek $1.00 (account balance)")
    assert lines[3] == "OpenRouter key: not used"
    assert lines[4].startswith("cost forecast") and forecast_block(out)[0] == lines[4]
    assert sorted(line.split(" ", 1)[1] for line in lines if line[:1].isdigit()) == [
        f"set 0 instance 0 talk {DEEPSEEK}", f"set 0 instance 0 talk-liar {DEEPSEEK}"]
    unbuilt = scripted_plan([DEEPSEEK])
    unbuilt["task_sets"][0]["manifest"] = "missing/manifest.json"
    assert RUN.scripted_line(unbuilt, RUN.scripted_of(unbuilt, repo, need_tasks=False)).endswith(
        "set 0 unknown (no manifest)")
    assert "is missing" in refusal(lambda: RUN.scripted_of(unbuilt, repo, need_tasks=True))


def test_scripted_seats_must_match_every_task(tmp_path):
    repo = init_repo(tmp_path)
    scripted_build(repo)
    plan = scripted_plan([DEEPSEEK])
    for wrong in (["p2"], []):
        plan["scripted_seats"] = wrong
        assert f"plan scripted_seats {wrong} but task set 0 tasks have ['p3']" in refusal(
            lambda: RUN.scripted_of(plan, repo, need_tasks=True))
    plan["scripted_seats"] = ["p3"]
    assert RUN.scripted_of(plan, repo, need_tasks=True) == [["p3"]]
    path = commit_plan(repo, {**plan, "scripted_seats": ["p2"]})
    assert "plan scripted_seats ['p2']" in refusal(lambda: RUN.run(path, dry_run=True))
    blob = tmp_path / "bad.json"
    blob.write_text(json.dumps({**plan, "scripted_seats": "p3"}))
    assert "must be a list" in refusal(lambda: RUN.load_plan(blob))
    first, second = sorted((repo / "tasks").glob("*/*/task.toml"))
    second.write_text(second.read_text().replace("under_test = false\n", ""))
    assert "task set 0 mixes scripted seats" in refusal(lambda: RUN.scripted_of(plan, repo, need_tasks=True))
    second.write_text(first.read_text().replace("[agents.p1]\n", "[agents.p1]\nunder_test = false\n")
                      .replace("[agents.p2]\n", "[agents.p2]\nunder_test = false\n"))
    assert "no seat under test" in refusal(lambda: RUN.roster_of(second.parent))


def write_trial(root: Path, participants: list[dict], seat_results: list[dict] | None = None):
    trial = root / "trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "score.json").write_text(json.dumps({"error": None, "instance": 0, "condition": "talk"}))
    (trial / "verifier" / "world-state.json").write_text("{}")
    (trial / "config.json").write_text(json.dumps({"participants": participants}))
    return trial, {"seat_results": seat_results or []}


def test_an_oracle_seat_is_valid_only_when_it_ran_the_oracle(tmp_path):
    def row(seat, name="terminus-2", model=DEEPSEEK):
        return {"seat": seat, "name": name, "model_name": model}

    oracle = row("p3", "oracle", None)
    cell = {"instance": 0, "arm": "talk", "model": DEEPSEEK, "scripted": ["p3"]}
    ran_other = [{"seat": "p3", "agent_info": {"name": "terminus-2"}}]
    cases = [
        ("good", [row("p1"), row("p2"), oracle], None, cell, True),
        ("ran-oracle", [row("p1"), row("p2"), oracle], [{"seat": "p3", "agent_info": {"name": "oracle"}}], cell, True),
        ("llm-in-scripted-seat", [row("p1"), row("p2"), row("p3")], None, cell, False),
        ("scripted-seat-missing", [row("p1"), row("p2")], None, cell, False),
        ("ran-something-else", [row("p1"), row("p2"), oracle], ran_other, cell, False),
        ("wrong-model", [row("p1"), row("p2", model=OPENROUTER), oracle], None, cell, False),
        ("plan-without-scripted", [row("p1"), row("p2"), oracle], None,
         {key: value for key, value in cell.items() if key != "scripted"}, False),
        ("assignment", [row("p1"), row("p2", model=OPENROUTER), oracle], None,
         {**cell, "assignment": [DEEPSEEK, OPENROUTER]}, True),
        ("assignment-swapped", [row("p1", model=OPENROUTER), row("p2"), oracle], None,
         {**cell, "assignment": [DEEPSEEK, OPENROUTER]}, False),
    ]
    for name, participants, seat_results, case_cell, expected in cases:
        trial, result = write_trial(tmp_path / name, participants, seat_results)
        assert RUN.trial_valid(trial, result, case_cell) is expected, name


def test_the_charge_skips_scripted_seats():
    haiku, luna = "openrouter/anthropic/claude-haiku-5.5", "openrouter/openai/gpt-6-luna"
    prices = {haiku: {"prompt": "0.000001", "completion": "0.000001"},
              luna: {"prompt": "0.000002", "completion": "0.000002"}}
    empty = {"n_input_tokens": None, "n_cache_tokens": None, "n_output_tokens": None, "cost_usd": None}
    result = {"started_at": OFF_PEAK, "seat_results": [
        {"seat": "p1", "agent_result": {"n_input_tokens": 1_000, "n_cache_tokens": 0, "n_output_tokens": 0,
                                        "cost_usd": 0.02}},
        {"seat": "p2", "agent_result": {"n_input_tokens": 2_000, "n_cache_tokens": 0, "n_output_tokens": 0,
                                        "cost_usd": 0.03}},
        {"seat": "p3", "under_test": False, "agent_info": {"name": "oracle"}, "agent_result": empty},
    ]}
    bill = RUN.charge({"model": haiku, "scripted": ["p3"]}, result, prices, 0.10)
    assert (bill["cost"], bill["source"], bill["flag"]) == (0.05, "recorded", None)
    assert bill["estimate"] == 3_000 * 0.000001
    unscripted = RUN.charge({"model": haiku}, result, prices, 0.10)
    assert unscripted["flag"] == "missing-recorded"
    assigned = RUN.charge({"model": haiku, "assignment": [haiku, luna], "scripted": ["p3"]}, result, prices, 0.10)
    assert (assigned["cost"], assigned["source"], assigned["flag"]) == (0.05, "recorded", None)
    assert assigned["estimate"] == 1_000 * 0.000001 + 2_000 * 0.000002
    direct = RUN.charge({"model": DEEPSEEK, "scripted": ["p3"]}, result, {}, 0.10)
    assert direct["cost"] == PRICING.deepseek_cost({"hit": 0, "miss": 3_000, "out": 0}, datetime.fromisoformat(OFF_PEAK))
    assert RUN.charge({"model": DEEPSEEK, "scripted": ["p3"]}, None, {}, 0.10)["cost"] == 0.10


def test_seat_assignments_list_models_for_the_seats_under_test(tmp_path):
    plan = scripted_plan([DEEPSEEK])
    plan["budget"]["provider_floors"]["openrouter"] = 1.0
    plan["seat_assignments"] = [[DEEPSEEK, OPENROUTER]]
    order = scripted_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: spec_for("ok") for cell in order}
    events, order, _path, _env = launch(tmp_path / "cross", plan, behaviors, Sources(catalog(), lambda *_args: 10.0),
                                        tree=scripted_build)
    assert all(event["valid"] for event in events if event["kind"] == "end")
    config = json.loads(next((tmp_path / "cross" / "run" / "configs").glob("*.json")).read_text())
    by_seat = {row["seat"]: (row["name"], row.get("model_name")) for row in config["participants"]}
    assert by_seat == {"p1": ("terminus-2", DEEPSEEK), "p2": ("terminus-2", OPENROUTER), "p3": ("oracle", None)}
    assert f"models p1={DEEPSEEK},p2={OPENROUTER}" in (tmp_path / "cross" / "run" / "progress.log").read_text()
    assert RUN.seat_models({"assignment": ["a", "b"], "scripted": ["p1"]}) == {"p2": "a", "p3": "b"}
    wide = init_repo(tmp_path / "wide")
    scripted_build(wide)
    plan["seat_assignments"] = [[DEEPSEEK, OPENROUTER, DEEPSEEK]]
    path = commit_plan(wide, plan)
    assert "has 2 seats under test" in refusal(lambda: RUN.run(path, dry_run=True))


def test_openrouter_balance_is_the_lower_of_account_and_key_limit(monkeypatch):
    key = {"data": {"label": "x", "limit": 100, "usage": 99.15, "limit_remaining": 0.85}}
    credits = {"data": {"total_credits": 130, "total_usage": 122.2}}
    assert PRICING.parse_openrouter_key({"data": {"limit": None, "limit_remaining": None}}) is None
    assert PRICING.parse_openrouter_key(key) == 0.85
    for broken in ({}, {"data": {}}, {"data": {"limit": 100}}, {"data": {"limit": 100, "limit_remaining": "1"}}):
        try:
            PRICING.parse_openrouter_key(broken)
        except ValueError as exc:
            assert "OpenRouter key response" in str(exc)
        else:
            raise AssertionError(f"{broken} must be refused")
    try:
        PRICING.parse_openrouter_credits({"data": {"total_credits": 5}})
    except ValueError as exc:
        assert "total_usage" in str(exc)
    else:
        raise AssertionError("credits without total_usage must be refused")
    assert PRICING.openrouter_balance(7.8, 0.85) == (0.85, "key limit (account $7.80, key limit left $0.85)")
    assert PRICING.openrouter_balance(0.5, 3.0) == (0.5, "account balance (account $0.50, key limit left $3.00)")
    assert PRICING.openrouter_balance(2.5, None) == (2.5, "account balance (account $2.50, no key limit)")
    pages = {"https://openrouter.ai/api/v1/credits": credits, "https://openrouter.ai/api/v1/key": key}
    seen = []

    def fetch(url, headers):
        seen.append((url, headers["Authorization"] == "Bearer or-token"))
        return pages[url]

    monkeypatch.setattr(RUN, "_fetch_json", fetch)
    sources = RUN.HttpSources({"OPENROUTER_API_KEY": "or-token"})
    balance = sources.balance("openrouter")
    assert abs(balance.amount - 0.85) < 1e-12 and balance.detail.startswith("key limit (account $7.80")
    assert sorted(seen) == [("https://openrouter.ai/api/v1/credits", True), ("https://openrouter.ai/api/v1/key", True)]
    pages["https://openrouter.ai/api/v1/key"] = {"data": {"limit": None}}
    assert sources.balance("openrouter").detail == "account balance (account $7.80, no key limit)"
    pages["https://openrouter.ai/api/v1/key"] = {"error": {"code": 401}}
    money, error = RUN.read_balance(sources, "openrouter", lambda _delay: None)
    assert money is None and "no data.limit" in str(error)


def plain_order(tmp: Path, plan: dict) -> list[dict]:
    preview = init_repo(tmp)
    task_tree(preview, plan["task_sets"][0]["instances"], plan["task_sets"][0]["arms"])
    return RUN.order_of(plan, RUN.resolve_paths(plan, preview))


def test_a_key_limit_below_the_floor_stops_and_says_so(tmp_path):
    plan = plan_for([0, 1], ["silent"], [OPENROUTER], concurrency=1, env="modal", cap=1.0, reserve=0.01)
    plan["budget"]["provider_floors"]["openrouter"] = 2.0
    order = plain_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: spec_for("ok", cost_usd=0.01) for cell in order}
    detail = "key limit (account $7.80, key limit left $0.85)"
    sources = Sources(catalog(), lambda _provider, n: RUN.Balance(7.8 if n == 1 else 0.85, detail))
    events, _order, _path, _env = launch(tmp_path / "key", plan, behaviors, sources)
    assert events[-1]["reason"] == "floor"
    assert len([event for event in events if event["kind"] == "start"]) == 1
    log = (tmp_path / "key" / "run" / "progress.log").read_text()
    assert f"stop: openrouter balance $0.85 is below floor $2.00 (set by the {detail})" in log


def test_invalid_streak_stops_new_starts_drains_and_resumes(tmp_path, monkeypatch):
    plan = plan_for([0, 1, 2, 3], ["silent", "talk"], [DEEPSEEK], concurrency=2, cap=5.0, reserve=0.05)
    plan["budget"]["max_invalid_streak"] = 3
    order = plain_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: spec_for("invalid") for cell in order}
    events, order, path, env = launch(tmp_path / "streak", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert_invariants(events, concurrency=2, cap=5.0, reserve=0.05)
    assert events[-1]["reason"] == "invalid"
    starts = [event["key"] for event in events if event["kind"] == "start"]
    ends = [event for event in events if event["kind"] == "end"]
    assert sorted(event["key"] for event in ends) == sorted(starts)
    assert 3 <= len(starts) <= 4 < len(order)
    assert all(not event["valid"] and event["problem"] == "boom" for event in ends)
    run_dir = tmp_path / "streak" / "run"
    log = (run_dir / "progress.log").read_text()
    stops = [line for line in log.splitlines() if "invalid trials in a row" in line]
    assert len(stops) == 1 and re.search(
        r"stop: [34] invalid trials in a row \(last: boom; boom; boom\); draining in-flight jobs$", stops[0])
    assert "stop invalid: spent" in log
    saved = json.loads((run_dir / "state.json").read_text())
    assert sorted(saved["finished"]) == sorted(starts)
    assert all(saved["finished"][key]["attempts"][0]["problem"] == "boom" for key in starts)
    control = tmp_path / "streak" / "control.json"
    mapped = json.loads(control.read_text())
    control.write_text(json.dumps({name: {**spec, "behavior": "ok", "exception": None}
                                   for name, spec in mapped.items()}))
    common = {"run_dir": run_dir, "jobs_dir": tmp_path / "streak" / "jobs", "harbor": tmp_path / "streak" / "harbor",
              "wrapper": tmp_path / "streak" / "wrapper", "sources": Sources({}, lambda *_args: 10.0),
              "base_env": env, "namer": lambda _n: f"{namer_box(tmp_path / 'streak'):06x}"}
    resumed = RUN.run(path, **common)
    assert resumed[-1]["reason"] == "done"
    assert sorted(event["key"] for event in resumed if event["kind"] == "start") == sorted(
        cell["key"] for cell in order if cell["key"] not in starts)
    retried = RUN.run(path, retry_invalid=True, **common)
    assert sorted(event["key"] for event in retried if event["kind"] == "start") == sorted(starts)
    assert all(event["valid"] for event in retried if event["kind"] == "end")
    monkeypatch.setattr(RUN, "run", lambda *_args, **_kw: [{"kind": "stop", "reason": "invalid"}])
    assert RUN.main([str(path)]) == RUN.EXIT_INVALID_STREAK == 3
    monkeypatch.setattr(RUN, "run", lambda *_args, **_kw: [{"kind": "stop", "reason": "floor"}])
    assert RUN.main([str(path)]) == 0


def slow_invalid() -> dict:
    """Ends invalid (a trial exception) about 0.3 s after its start."""
    return {**spec_for("hold"), "exception": "boom"}


def test_a_streak_filled_while_waiting_for_the_cap_wins_over_the_balance_read(tmp_path):
    def floor_later(_provider, n):
        return 10.0 if n == 1 else 0.5

    def fail_later(_provider, n):
        if n == 1:
            return 10.0
        raise RuntimeError("down")

    for name, balance in (("floor", floor_later), ("read", fail_later)):
        plan = plan_for([0, 1], ["silent"], [DEEPSEEK], concurrency=2, cap=0.15, reserve=0.10)
        plan["budget"]["max_invalid_streak"] = 1
        order = plain_order(tmp_path / name / "preview", plan)
        behaviors = {cell["key"]: slow_invalid() for cell in order}
        sources = Sources({}, balance)
        events, _order, _path, _env = launch(tmp_path / name / "wave", plan, behaviors, sources,
                                             sleep=lambda _delay: None)
        assert events[-1]["reason"] == "invalid", name
        assert len([event for event in events if event["kind"] == "start"]) == 1
        assert sources.balance_calls == ["deepseek"]
        log = (tmp_path / name / "wave" / "run" / "progress.log").read_text()
        assert "stop: 1 invalid trials in a row (last: boom); draining in-flight jobs" in log
        assert "below floor" not in log and "balance read failed" not in log


def test_a_streak_formed_in_the_final_drain_exits_3(tmp_path):
    plan = plan_for([0, 1], ["silent"], [DEEPSEEK], concurrency=2, cap=1.0, reserve=0.05)
    plan["budget"]["max_invalid_streak"] = 2
    order = plain_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: slow_invalid() for cell in order}
    events, _order, _path, _env = launch(tmp_path / "wave", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert len([event for event in events if event["kind"] == "start"]) == 2
    assert [event["valid"] for event in events if event["kind"] == "end"] == [False, False]
    assert events[-1]["reason"] == "invalid"
    log = (tmp_path / "wave" / "run" / "progress.log").read_text()
    assert "stop: 2 invalid trials in a row (last: boom; boom); found after the in-flight jobs finished" in log
    assert "stop invalid: spent" in log
    healthy = plan_for([0, 1], ["silent"], [DEEPSEEK], concurrency=2, cap=1.0, reserve=0.05)
    healthy["budget"]["max_invalid_streak"] = 3
    events, _order, _path, _env = launch(tmp_path / "under", healthy, behaviors, Sources({}, lambda *_args: 10.0))
    assert events[-1]["reason"] == "done"


THREE = [DEEPSEEK, "deepseek/deepseek-pro", "deepseek/deepseek-lite"]


def one_model_plan(seed: int) -> dict:
    plan = plan_for(list(range(10)), ["silent"], THREE, concurrency=1, cap=5.0, reserve=0.05, seed=seed)
    plan["budget"]["max_invalid_streak"] = 6
    return plan


def longest_run(cells: list[dict], bad) -> int:
    run, longest = 0, 0
    for cell in cells:
        run = run + 1 if bad(cell) else 0
        longest = max(longest, run)
    return longest


def main_exit(tmp: Path, path: Path, monkeypatch) -> tuple[int, str]:
    """run_wave.main on a launched plan, in a fresh run and jobs dir, with fake balances and the fake Harbor."""
    monkeypatch.setattr(RUN, "HttpSources", lambda _env: Sources({}, lambda *_args: 10.0))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    code = RUN.main([str(path), "--run-dir", str(tmp / "main-run"), "--jobs-dir", str(tmp / "main-jobs"),
                     "--harbor", str(tmp / "harbor"), "--docker-wrapper", str(tmp / "wrapper")])
    return code, (tmp / "main-run" / "progress.log").read_text()


@pytest.mark.parametrize("concurrency", [1, 2])
def test_one_model_failing_between_working_models_stops_the_wave(tmp_path, monkeypatch, concurrency):
    plan = one_model_plan(seed=11)
    plan["make_jobs"]["concurrency"] = concurrency
    bad = THREE[1]
    order = plain_order(tmp_path / "preview", plan)
    behaviors = {cell["key"]: spec_for("invalid" if cell["model"] == bad else "ok") for cell in order}
    sixth = [index for index, cell in enumerate(order) if cell["model"] == bad][5]
    assert longest_run(order, lambda cell: cell["model"] == bad) <= 6 - 2, "the seed must interleave the models"
    events, order, path, _env = launch(tmp_path / "wave", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert_invariants(events, concurrency=concurrency, cap=5.0, reserve=0.05)
    assert events[-1]["reason"] == "invalid"
    starts = [event["key"] for event in events if event["kind"] == "start"]
    assert starts == [cell["key"] for cell in order[:len(starts)]]
    assert sorted(event["key"] for event in events if event["kind"] == "end") == sorted(starts)  # in-flight ones end
    assert sixth + 1 <= len(starts) <= sixth + concurrency < len(order)
    count = "6" if concurrency == 1 else "[67]"
    stop = (rf"stop: {count} invalid trials in a row for {re.escape(bad)} "
            r"\(last: boom; boom; boom\); draining in-flight jobs$")
    assert re.search(stop, (tmp_path / "wave" / "run" / "progress.log").read_text(), re.MULTILINE)
    code, log = main_exit(tmp_path / "wave", path, monkeypatch)
    assert code == 3 and re.search(stop, log, re.MULTILINE)
    assert sixth + 1 <= len(re.findall(r" ET start wave-", log)) <= sixth + concurrency


def test_one_failing_seat_assignment_row_stops_the_wave(tmp_path, monkeypatch):
    lite, pro = THREE[2], THREE[1]
    plan = plan_for(list(range(10)), ["silent"], [DEEPSEEK], concurrency=1, cap=5.0, reserve=0.05, seed=5)
    plan["seat_assignments"] = [[DEEPSEEK, pro], [pro, DEEPSEEK], [DEEPSEEK, DEEPSEEK], [lite, lite]]
    plan["budget"]["max_invalid_streak"] = 6
    preview = init_repo(tmp_path / "preview")
    task_tree(preview, list(range(10)), ["silent"], seats=("p1", "p2"))
    order = RUN.order_of(plan, RUN.resolve_paths(plan, preview))
    failing = [lite, lite]
    behaviors = {cell["key"]: spec_for("invalid" if cell["assignment"] == failing else "ok") for cell in order}
    assert longest_run(order, lambda cell: cell["assignment"] == failing) < 6
    events, order, path, _env = launch(tmp_path / "wave", plan, behaviors, Sources({}, lambda *_args: 10.0),
                                       seats=("p1", "p2"))
    assert events[-1]["reason"] == "invalid"
    sixth = [index for index, cell in enumerate(order) if cell["assignment"] == failing][5]
    assert [event["key"] for event in events if event["kind"] == "start"] == [cell["key"] for cell in order[:sixth + 1]]
    label = f"models p1={lite},p2={lite}"
    line = f"stop: 6 invalid trials in a row for {label} (last: boom; boom; boom); draining in-flight jobs"
    assert line in (tmp_path / "wave" / "run" / "progress.log").read_text()
    code, log = main_exit(tmp_path / "wave", path, monkeypatch)
    assert code == 3 and line in log


def test_a_valid_trial_resets_one_models_streak(tmp_path):
    plan = one_model_plan(seed=11)
    bad = THREE[1]
    order = plain_order(tmp_path / "preview", plan)
    bad_keys = [cell["key"] for cell in order if cell["model"] == bad]
    behaviors = {cell["key"]: spec_for("ok") for cell in order}
    for position, key in enumerate(bad_keys):
        if position != 5:
            behaviors[key] = spec_for("invalid")
    events, _order, _path, _env = launch(tmp_path / "wave", plan, behaviors, Sources({}, lambda *_args: 10.0))
    assert events[-1]["reason"] == "done"
    ends = [event for event in events if event["kind"] == "end" and event["model"] == bad]
    assert [event["valid"] for event in ends] == [False] * 5 + [True] + [False] * 4
    assert "invalid trials in a row" not in (tmp_path / "wave" / "run" / "progress.log").read_text()


def test_max_invalid_streak_defaults_to_6_and_is_validated(tmp_path):
    plan = plan_for([0], ["silent"], [DEEPSEEK])
    blob = tmp_path / "plan.json"
    blob.write_text(json.dumps(plan))
    assert RUN.streak_of(RUN.load_plan(blob)) == 6
    for bad in (0, -1, True, "6", 2.5):
        blob.write_text(json.dumps({**plan, "budget": {**plan["budget"], "max_invalid_streak": bad}}))
        assert "max_invalid_streak" in refusal(lambda: RUN.load_plan(blob))
    blob.write_text(json.dumps({**plan, "budget": {**plan["budget"], "max_invalid_streak": 2}}))
    assert RUN.streak_of(RUN.load_plan(blob)) == 2
    assert RUN.guard_line(RUN.load_plan(blob)) == (
        "guards: stop after 2 invalid trials in a row, overall or for one model; "
        "provider floors deepseek $1.00 (account balance)")


FREE = "openrouter/qwen/qwen3.8-27b:free"
OTHER_FREE = "openrouter/meta/llama-5-8b:free"
SHARED_SECRET = "sk-or-shared-0f9e8d7c"
FLOTILLA_SECRET = "sk-or-flotilla-7a6b5c4d"
BOTH_KEYS = {"OPENROUTER_API_KEY": SHARED_SECRET, "FLOTILLA_OPENROUTER_API_KEY": FLOTILLA_SECRET}


class Every(dict):
    """Behaviors for a fake Harbor: the same spec for every cell."""

    def __init__(self, spec: dict):
        super().__init__()
        self.spec = spec

    def __missing__(self, key):
        return self.spec


def smoke_plan_for(instances, arms, *, models=None, agent="oracle", concurrency=8, env="modal") -> dict:
    """A plan a smoke accepts: the oracle agent with models omitted, or :free models. Modal and concurrency 8 show the
    smoke moving it to Docker and 2 jobs; the cap, reserve and floor show the smoke replacing them."""
    plan = plan_for(instances, arms, [], concurrency=concurrency, env=env, cap=10.0, reserve=0.5)
    plan["agent"] = agent
    plan["budget"]["provider_floors"] = {"openrouter": 1.0}
    if models is None:
        del plan["models"]
    else:
        plan["models"] = models
    return plan


def assert_no_secret(*roots: Path, text: str = "") -> None:
    for root in roots:
        for item in root.rglob("*"):
            if item.is_file():
                body = item.read_bytes()
                assert SHARED_SECRET.encode() not in body and FLOTILLA_SECRET.encode() not in body, item
    assert SHARED_SECRET not in text and FLOTILLA_SECRET not in text


def harbor_starts(tmp: Path) -> list[dict]:
    rows = [json.loads(line) for line in (tmp / "harbor.jsonl").read_text().splitlines()]
    return [row for row in rows if row["event"] == "start"]


def test_oracle_smoke_runs_the_first_instances_on_docker_for_free(tmp_path):
    plan = smoke_plan_for([2, 0, 2, 1], ["silent", "talk"])
    sources = Sources({}, lambda *_args: RUN.Balance(0.0))
    events, _order, _path, _env = launch(tmp_path / "oracle", plan, Every(spec_for("ok")), sources, smoke=2)
    assert events[-1]["reason"] == "done" and events[-1]["spent"] == 0
    assert_invariants(events, concurrency=2, cap=0.0, reserve=0.0)
    starts = [event for event in events if event["kind"] == "start"]
    cells = sorted(tuple(json.loads(event["key"])) for event in starts)
    assert cells == [(0, 0, "silent", None), (0, 0, "talk", None), (0, 2, "silent", None), (0, 2, "talk", None)]
    assert max(event["in_flight"] for event in starts) <= 2
    assert all(event["command"][0] == str(tmp_path / "oracle" / "wrapper") for event in starts)
    assert all(re.fullmatch(r"smoke-wave-[0-9a-f]{6}", event["job"]) for event in starts)
    assert not any(fnmatchcase(event["job"], "wave-*") for event in starts)
    assert (sources.price_calls, sources.catalog_calls, sources.balance_calls) == (0, 0, [])
    ends = [event for event in events if event["kind"] == "end"]
    assert all(event["valid"] and event["cost"] == 0 and event["source"] == "oracle" for event in ends)
    assert {event["model"] for event in ends} == {"oracle (no model)"}
    run_dir = tmp_path / "oracle" / "run-smoke"
    assert not (tmp_path / "oracle" / "run").exists()
    saved = json.loads((run_dir / "state.json").read_text())
    assert (saved["plan"], saved["mode"], saved["smoke"], saved["spent_usd"]) == ("wave-smoke", "smoke", 2, 0.0)
    for config_path in (run_dir / "configs").glob("*.json"):
        config = json.loads(config_path.read_text())
        assert config["environment"]["type"] == "docker"
        assert [(row["name"], row.get("model_name")) for row in config["participants"]] == [("oracle", None)]
    for row in harbor_starts(tmp_path / "oracle"):
        assert row["keys"] == [] and row["openrouter_key"] is None
        assert "MODAL_TOKEN_ID" not in row["env"] and "HF_TOKEN" not in row["env"]
    log = (run_dir / "progress.log").read_text()
    assert "OpenRouter key: not used" in log
    assert "smoke: first 2 instances of each task set, local docker, concurrency 2, cap $0.00" in log


def test_an_oracle_cell_is_valid_only_when_every_seat_ran_the_oracle(tmp_path):
    plan = smoke_plan_for([0], ["silent"])
    events, _order, _path, _env = launch(tmp_path / "agent", plan, Every(spec_for("wrong-agent")),
                                         Sources({}, lambda *_args: 10.0), smoke=1)
    end = next(event for event in events if event["kind"] == "end")
    assert end["valid"] is False and end["problem"] == "seat model mismatch"
    cell = {"instance": 0, "arm": "talk", "model": None}
    oracle = {"seat": "p1", "name": "oracle", "model_name": None}
    cases = [
        ("oracle", [oracle], None, True),
        ("ran-oracle", [oracle], [{"seat": "p1", "agent_info": {"name": "oracle"}}], True),
        ("llm-agent", [{**oracle, "name": "terminus-2"}], None, False),
        ("model", [{**oracle, "model_name": FREE}], None, False),
        ("ran-other", [oracle], [{"seat": "p1", "agent_info": {"name": "terminus-2"}}], False),
        ("no-seat", [], None, False),
    ]
    for name, participants, seat_results, expected in cases:
        trial, result = write_trial(tmp_path / name, participants, seat_results)
        assert RUN.trial_valid(trial, result, cell) is expected, name
    charged = {"seat_results": [{"seat": "p1", "agent_result": {"cost_usd": 0.01}},
                                {"seat": "p2", "agent_result": {"cost_usd": None}}]}
    assert RUN.smoke_charge({"model": None}, charged) == {"cost": 0.01, "estimate": 0.0, "source": "oracle",
                                                         "flag": "charged"}
    assert RUN.smoke_charge({"model": FREE, "scripted": ["p1"]}, charged)["cost"] == 0.01
    assert RUN.smoke_charge({"model": None}, None)["cost"] == 0.0


def test_a_free_smoke_passes_the_flotilla_key_and_never_writes_a_key(tmp_path, capsys):
    plan = smoke_plan_for([0, 1], ["silent", "talk"], models=[FREE], agent="terminus-2", concurrency=2, env="docker")
    sources = Sources({}, lambda *_args: RUN.Balance(0.0))
    events, _order, path, env = launch(tmp_path / "free", plan, Every(spec_for("ok", cost_usd=0.0)), sources,
                                       env_extra=BOTH_KEYS, smoke=1)
    assert events[-1]["reason"] == "done" and events[-1]["spent"] == 0
    ends = [event for event in events if event["kind"] == "end"]
    assert len(ends) == 2 and all(event["valid"] and event["cost"] == 0 for event in ends)
    assert (sources.price_calls, sources.catalog_calls, sources.balance_calls) == (0, 0, [])
    for row in harbor_starts(tmp_path / "free"):
        assert row["openrouter_key"] == FLOTILLA_SECRET and row["keys"] == ["OPENROUTER_API_KEY"]
        assert "FLOTILLA_OPENROUTER_API_KEY" not in row["env"] and "MODAL_TOKEN_ID" not in row["env"]
    run_dir = tmp_path / "free" / "run-smoke"
    for config_path in (run_dir / "configs").glob("*.json"):
        rows = json.loads(config_path.read_text())["participants"]
        assert [(row["name"], row["model_name"], row["kwargs"]) for row in rows] == [
            ("terminus-2", FREE, {"max_turns": 4})]
    log = (run_dir / "progress.log").read_text()
    assert "OpenRouter key: FLOTILLA_OPENROUTER_API_KEY (Harbor gets it as OPENROUTER_API_KEY)" in log
    assert RUN.run(path, dry_run=True, smoke=1, sources=sources, base_env=env, history_jobs=tmp_path / "none") == []
    captured = capsys.readouterr()
    assert "OpenRouter key: FLOTILLA_OPENROUTER_API_KEY" in captured.out
    assert sources.catalog_calls == 0
    assert_no_secret(run_dir, tmp_path / "free" / "jobs", text=captured.out + captured.err)


def test_a_smoke_that_records_spend_stops_new_starts_and_exits_4(tmp_path, monkeypatch):
    plan = smoke_plan_for([0, 1], ["silent", "talk"], models=[FREE], agent="terminus-2", concurrency=2, env="docker")
    events, _order, path, env = launch(tmp_path / "paid", plan, Every(spec_for("hold", cost_usd=0.01)),
                                       Sources({}, lambda *_args: 10.0), env_extra=BOTH_KEYS, smoke=2)
    assert events[-1]["reason"] == "charged" and events[-1]["spent"] > 0
    starts = [event for event in events if event["kind"] == "start"]
    assert 1 <= len(starts) <= 2
    run_dir = tmp_path / "paid" / "run-smoke"
    assert "stop: smoke recorded spend $0.0" in (run_dir / "progress.log").read_text()
    again = RUN.run(path, run_dir=tmp_path / "paid" / "run", jobs_dir=tmp_path / "paid" / "jobs",
                    harbor=tmp_path / "paid" / "harbor", wrapper=tmp_path / "paid" / "wrapper",
                    sources=Sources({}, lambda *_args: 10.0), base_env=env, smoke=2,
                    namer=lambda _n: f"{namer_box(tmp_path / 'paid'):06x}")
    assert again[-1]["reason"] == "charged"
    assert [event for event in again if event["kind"] == "start"] == []
    monkeypatch.setattr(RUN, "run", lambda *_args, **_kw: [{"kind": "stop", "reason": "charged"}])
    assert RUN.main([str(path), "--smoke"]) == RUN.EXIT_SMOKE_CHARGED == 4


def test_a_smoke_refuses_paid_unknown_and_repeated_subjects(tmp_path):
    blob = tmp_path / "plan.json"

    def refused(plan: dict, smoke: bool = True) -> str:
        blob.write_text(json.dumps(plan))
        return refusal(lambda: RUN.load_plan(blob, smoke=smoke))

    def loaded(plan: dict) -> dict:
        blob.write_text(json.dumps(plan))
        return RUN.load_plan(blob, smoke=True)

    free = smoke_plan_for([0], ["silent"], models=[FREE], agent="terminus-2")
    for paid in (OPENROUTER, DEEPSEEK, FREE + " ", "openrouter/:free", "qwen/qwen3.8-27b:free", OPENROUTER + ":freebie",
                 None):
        assert "is not free" in refused({**free, "models": [paid]}), paid
    assert "only on terminus-2, not claude-code" in refused({**free, "agent": "claude-code"})
    oracle = smoke_plan_for([0], ["silent"])
    assert loaded(oracle)["models"] == [None]
    assert loaded({**oracle, "models": [None]})["models"] == [None]
    assert "takes no model" in refused({**oracle, "models": [FREE]})
    assert "takes no model" in refused({**oracle, "seat_assignments": [[FREE]]})
    assert "plan missing models" in refused(oracle, smoke=False)
    assert "None is not a model pricing.py can price" in refused({**oracle, "models": [None]}, smoke=False)
    assert "lists a model twice" in refused({**free, "models": [FREE, FREE]})
    assert "in seat_assignments[0] and [1]" in refused({**free, "seat_assignments": [[FREE, OTHER_FREE],
                                                                                      [OTHER_FREE, FREE]]})
    assert "in seat_assignments[0] and [1]" in refused({**free, "seat_assignments": [[FREE, OTHER_FREE],
                                                                                      [FREE, OTHER_FREE]]})
    assert loaded({**free, "seat_assignments": [[FREE, FREE], [OTHER_FREE, OTHER_FREE]]})
    twice = {**free, "task_sets": [{**free["task_sets"][0], "arms": ["silent", "silent"]}]}
    assert "lists an arm twice" in refusal(lambda: RUN.smoke_plan(twice, 1))
    docker = {"type": "docker"}
    RUN.assert_free_config({"environment": docker, "participants": [
        {"seat": "p1", "name": "oracle"}, {"seat": "p2", "name": "terminus-2", "model_name": FREE,
                                           "kwargs": {"max_turns": 4}}]})
    for seat in ({"seat": "p1", "name": "terminus-2", "model_name": OPENROUTER},
                 {"seat": "p1", "name": "terminus-2"},
                 {"seat": "p1", "name": "oracle", "model_name": FREE},
                 {"seat": "p1", "name": "claude-code", "model_name": FREE},
                 {"seat": "p1", "name": "terminus-2", "model_name": FREE, "kwargs": {"api_base": "http://x"}},
                 {"seat": "p1", "name": "terminus-2", "model_name": FREE, "model_info": {}}):
        assert "is not free" in refusal(lambda seat=seat: RUN.assert_free_config(
            {"environment": docker, "participants": [seat]}))
    assert "must run on docker" in refusal(lambda: RUN.assert_free_config(
        {"environment": {"type": "modal"}, "participants": [{"seat": "p1", "name": "oracle"}]}))


def test_a_smoke_still_needs_the_committed_plan_its_tasks_the_wrapper_and_a_key(tmp_path):
    repo = init_repo(tmp_path)
    task_tree(repo, [0], ["silent"])
    path = commit_plan(repo, smoke_plan_for([0], ["silent"]))
    wrapper = tmp_path / "wrapper"
    write_executable(wrapper, "#!/bin/sh\nexit 9\n")
    common = {"run_dir": tmp_path / "run", "jobs_dir": tmp_path / "jobs", "harbor": tmp_path / "harbor",
              "wrapper": wrapper, "sources": Sources({}, lambda *_args: 10.0), "base_env": {"PATH": os.environ["PATH"]}}
    for bad in (0, -1, True, 1.5):
        assert "whole number" in refusal(lambda bad=bad: RUN.run(path, smoke=bad, **common))
    assert "docker wrapper" in refusal(lambda: RUN.run(path, smoke=1, **{**common, "wrapper": tmp_path / "missing"}))
    path.write_text(path.read_text() + "\n")
    assert "differs from HEAD" in refusal(lambda: RUN.run(path, smoke=1, **common))
    git(repo, "checkout", "--", "plan.json")
    (repo / "tasks" / "manifest.json").rename(repo / "tasks" / "gone.json")
    assert "is missing" in refusal(lambda: RUN.run(path, smoke=1, **common))
    free = init_repo(tmp_path / "free")
    task_tree(free, [0], ["silent"])
    free_path = commit_plan(free, smoke_plan_for([0], ["silent"], models=[FREE], agent="terminus-2"))
    for keys in ({}, {"OPENROUTER_API_KEY": "", "FLOTILLA_OPENROUTER_API_KEY": ""}):
        assert "a :free smoke needs" in refusal(lambda keys=keys: RUN.run(
            free_path, smoke=1, **{**common, "base_env": {"PATH": os.environ["PATH"], **keys}}))
    assert not (tmp_path / "run-smoke").exists() and not (tmp_path / "run").exists()


def test_smoke_state_stays_apart_from_the_wave_and_from_other_plans(tmp_path):
    plan = smoke_plan_for([0, 1], ["silent"], models=[FREE], agent="terminus-2", concurrency=1, env="docker")
    events, _order, path, env = launch(tmp_path / "a", plan, Every(spec_for("ok", cost_usd=0.0)),
                                       Sources({}, lambda *_args: 10.0), env_extra=BOTH_KEYS, smoke=1)
    assert events[-1]["reason"] == "done"
    run_dir, smoke_dir = tmp_path / "a" / "run", tmp_path / "a" / "run-smoke"
    assert smoke_dir.is_dir() and not run_dir.exists()
    common = {"jobs_dir": tmp_path / "a" / "jobs", "harbor": tmp_path / "a" / "harbor",
              "wrapper": tmp_path / "a" / "wrapper", "base_env": env,
              "namer": lambda _n: f"{namer_box(tmp_path / 'a'):06x}"}
    again = RUN.run(path, run_dir=run_dir, smoke=1, sources=Sources({}, lambda *_args: 10.0), **common)
    assert again[-1]["reason"] == "done" and [event for event in again if event["kind"] == "start"] == []
    assert "holds a smoke run of 1" in refusal(lambda: RUN.run(
        path, run_dir=run_dir, smoke=2, sources=Sources({}, lambda *_args: 10.0), **common))
    wave_sources = Sources({FREE: {"prompt": "0", "completion": "0"}}, lambda *_args: 10.0)
    assert "holds a smoke run of 1" in refusal(lambda: RUN.run(path, run_dir=smoke_dir, sources=wave_sources, **common))
    assert (wave_sources.price_calls, wave_sources.balance_calls) == (0, [])
    other = init_repo(tmp_path / "b")
    task_tree(other, [0], ["silent"])
    other_path = commit_plan(other, {**smoke_plan_for([0], ["silent"]), "name": "batch"})
    assert "belongs to plan wave-smoke" in refusal(lambda: RUN.run(
        other_path, run_dir=run_dir, smoke=1, sources=Sources({}, lambda *_args: 10.0), **common))
    same_name = init_repo(tmp_path / "c")
    task_tree(same_name, [0], ["silent"])
    same_path = commit_plan(same_name, smoke_plan_for([0], ["silent"]))
    assert "plan file changed" in refusal(lambda: RUN.run(
        same_path, run_dir=run_dir, smoke=1, sources=Sources({}, lambda *_args: 10.0), **common))
    assert not run_dir.exists()


def test_the_flotilla_key_replaces_the_shared_key_for_harbor_and_the_balance(tmp_path, monkeypatch):
    shared = {"PATH": "/bin", "OPENROUTER_API_KEY": SHARED_SECRET}
    both = {"PATH": "/bin", **BOTH_KEYS}
    assert RUN.key_name(both, "openrouter") == "FLOTILLA_OPENROUTER_API_KEY"
    assert RUN.key_name({**both, "FLOTILLA_OPENROUTER_API_KEY": ""}, "openrouter") == "OPENROUTER_API_KEY"
    assert RUN.key_name(both, "deepseek") == "DEEPSEEK_API_KEY"
    assert RUN.harbor_env(both, [OPENROUTER]) == {"PATH": "/bin", "OPENROUTER_API_KEY": FLOTILLA_SECRET}
    assert RUN.harbor_env(shared, [OPENROUTER]) == {"PATH": "/bin", "OPENROUTER_API_KEY": SHARED_SECRET}
    assert RUN.harbor_env(both, [DEEPSEEK]) == {"PATH": "/bin"} == RUN.harbor_env(both, [None])
    assert RUN.harbor_env({"MODAL_TOKEN_ID": "m"}, [None], keep_modal=False) == {}
    assert RUN.key_line(both, [OPENROUTER]) == (
        "OpenRouter key: FLOTILLA_OPENROUTER_API_KEY (Harbor gets it as OPENROUTER_API_KEY)")
    assert RUN.key_line(shared, [DEEPSEEK, OPENROUTER]) == "OpenRouter key: OPENROUTER_API_KEY"
    assert RUN.key_line({}, [OPENROUTER]) == (
        "OpenRouter key: not set (FLOTILLA_OPENROUTER_API_KEY or OPENROUTER_API_KEY)")
    assert RUN.key_line(both, [DEEPSEEK]) == RUN.key_line(both, [None]) == "OpenRouter key: not used"
    pages = {"https://openrouter.ai/api/v1/credits": {"data": {"total_credits": 10, "total_usage": 2}},
             "https://openrouter.ai/api/v1/key": {"data": {"limit": None}}}
    seen = []

    def fetch(url, headers):
        seen.append(headers.get("Authorization"))
        return pages[url]

    monkeypatch.setattr(RUN, "_fetch_json", fetch)
    assert RUN.HttpSources(both).balance("openrouter").amount == 8
    assert seen == ["Bearer " + FLOTILLA_SECRET] * 2
    seen.clear()
    RUN.HttpSources(shared).balance("openrouter")
    assert seen == ["Bearer " + SHARED_SECRET] * 2
    assert "OPENROUTER_API_KEY is not set" in refusal(lambda: RUN.HttpSources({}).balance("openrouter"))
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, *_args):
            return b'{"data": []}'

    def urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr(RUN.urllib.request, "urlopen", urlopen)
    assert RUN.HttpSources(both).catalog() == {"data": []}
    assert requests[0].full_url == RUN.CATALOG_URL and not requests[0].header_items()

    def down(request, timeout):
        raise OSError("down")

    monkeypatch.setattr(RUN.urllib.request, "urlopen", down)
    with pytest.raises(RuntimeError, match="could not read"):
        RUN.HttpSources(both).catalog()
    monkeypatch.undo()
    plan = plan_for([0], ["silent"], [OPENROUTER], concurrency=1, env="modal", cap=1.0, reserve=0.01)
    order = plain_order(tmp_path / "preview", plan)
    sources = Sources(catalog(), lambda *_args: 10.0)
    events, _order, _path, _env = launch(tmp_path / "paid", plan, {order[0]["key"]: spec_for("ok", cost_usd=0.01)},
                                         sources, env_extra=BOTH_KEYS)
    assert events[-1]["reason"] == "done"
    row = harbor_starts(tmp_path / "paid")[0]
    assert row["openrouter_key"] == FLOTILLA_SECRET and row["keys"] == ["OPENROUTER_API_KEY"]
    assert "FLOTILLA_OPENROUTER_API_KEY" not in row["env"] and "MODAL_TOKEN_ID" in row["env"]
    assert sources.balance_calls == ["openrouter"] and sources.price_calls == 1
    log = (tmp_path / "paid" / "run" / "progress.log").read_text()
    assert "OpenRouter key: FLOTILLA_OPENROUTER_API_KEY (Harbor gets it as OPENROUTER_API_KEY)" in log
    assert_no_secret(tmp_path / "paid" / "run", tmp_path / "paid" / "jobs")


def test_dry_run_prints_the_key_name_then_the_forecast_block_then_the_cells(tmp_path, monkeypatch, capsys):
    repo = init_repo(tmp_path)
    task_tree(repo, [0, 1], ["silent", "talk"])
    path = commit_plan(repo, smoke_plan_for([0, 1], ["silent", "talk"], models=[FREE], agent="terminus-2"))
    seen = {}

    def forecast(wave, order, repo_dir, history, *, fetch_catalog):
        seen.update(wave=wave, order=order, repo=repo_dir, history=history, fetch=fetch_catalog)
        return ["cost forecast (fake)", "  row"]

    monkeypatch.setattr(RUN.PRICING, "forecast_lines", forecast, raising=False)
    sources = Sources({}, lambda *_args: 10.0, payload={"data": []},
                      zdr={"data": [{"model_id": "qwen/qwen3.8-27b", "provider_name": "Paid"}]})
    assert RUN.run(path, dry_run=True, smoke=1, sources=sources, base_env={"PATH": "/bin", **BOTH_KEYS},
                   history_jobs=tmp_path / "history") == []
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("wave-smoke: 2 cells, reserve $0.00 per trial, cap $0.00")
    assert lines[1].startswith("smoke: first 1 instances of each task set, local docker, concurrency 2")
    assert lines[3].endswith("provider floors none")
    assert lines[4] == "OpenRouter key: FLOTILLA_OPENROUTER_API_KEY (Harbor gets it as OPENROUTER_API_KEY)"
    block = forecast_block(out)
    assert block[:2] == ["cost forecast (fake)", "  row"] and lines[5:9] == block
    assert block[2].startswith("  zero-data-retention list: fetched ")
    assert block[3] == f"  ZDR warning: {FREE}: no zero-data-retention endpoint: an account with ZDR on gets 404"
    assert len(lines) == 11 and all("instance 0 " in line and line.endswith(FREE) for line in lines[9:])
    wave = seen["wave"]
    assert (wave["smoke"], wave["budget"]["cap_usd"], wave["make_jobs"]["env"]) == (1, 0.0, "docker")
    assert len(seen["order"]) == 2 and seen["history"] == tmp_path / "history"
    assert Path(seen["repo"]).resolve() == repo.resolve()
    assert "a smoke reads no price catalog" in refusal(seen["fetch"])
    assert (sources.catalog_calls, sources.price_calls, sources.balance_calls, sources.zdr_calls) == (0, 0, [], 1)
    assert_no_secret(text=out)
    assert RUN.run(path, dry_run=True, sources=sources, base_env={}, history_jobs=None) == []
    out = capsys.readouterr().out
    assert "smoke" not in seen["wave"] and seen["history"] is None and len(seen["order"]) == 4
    assert seen["fetch"]() == {"data": []} and sources.catalog_calls == 1
    assert "OpenRouter key: not set (FLOTILLA_OPENROUTER_API_KEY or OPENROUTER_API_KEY)" in out


def test_main_reads_smoke_and_history_jobs(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "run", lambda _plan, **kw: seen.append(kw) or [{"kind": "stop", "reason": "done"}])
    assert RUN.main(["--smoke=3", "plan.json"]) == 0
    assert RUN.main(["plan.json", "--smoke"]) == 0
    assert RUN.main(["plan.json", "--history-jobs", "/tmp/history"]) == 0
    assert [(item["smoke"], item["history_jobs"]) for item in seen] == [
        (3, None), (1, None), (None, Path("/tmp/history"))]


# --- staged waves -------------------------------------------------------------------------------------------------

PROBE_ARMS = ["talk-quiet", "talk-false"]
M1, M2, M3 = "openrouter/a/one", "openrouter/b/two", "openrouter/c/three"
PROBE_HARBOR = r"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
config = json.loads(Path(args[args.index("-c") + 1]).read_text())
task = config["tasks"][0]["path"]
model = next(item["model_name"] for item in config["participants"] if item.get("model_name"))
spec = json.loads(Path(__CONTROL__).read_text())[task + "|" + model]
tries = Path(__TRIES__) / (Path(task).name + "--" + model.replace("/", "_"))
with tries.open("a") as handle:
    handle.write("x")
attempt = tries.stat().st_size
with Path(__LOG__).open("a") as handle:
    handle.write(json.dumps({"task": task, "model": model, "instance": spec["instance"], "arm": spec["arm"],
                             "attempt": attempt, "job": config["job_name"]}) + "\n")
trial = Path(config["jobs_dir"]) / config["job_name"] / (Path(task).name + "__t")
trial.mkdir(parents=True)
invalid = attempt <= spec["invalid"]
seats = [{"seat": row["seat"], "agent_info": {"name": row["name"], "model_info": None},
          "exception_info": {"exception_type": "AgentTimeoutError"} if invalid and row.get("model_name") else None,
          "agent_result": {"n_input_tokens": 100, "n_cache_tokens": 0, "n_output_tokens": 10, "cost_usd": 0.001}
          if row.get("model_name") else {}} for row in config["participants"]]
participants = [{key: row[key] for key in ("seat", "name", "model_name") if key in row}
                for row in config["participants"]]
result = {"config": {"task": {"path": task}, "participants": participants}, "exception_info": None,
          "started_at": f"2026-11-21T16:{attempt:02d}:00+00:00", "seat_results": seats}
(trial / "result.json").write_text(json.dumps(result))
if not invalid:
    (trial / "verifier").mkdir()
    (trial / "verifier" / "score.json").write_text(json.dumps({
        "error": None, "instance": spec["instance"], "condition": spec["arm"], "probe_kind": "verify",
        "seats": ["p1", "p2"], "correct": spec["correct"]}))
    (trial / "verifier" / "world-state.json").write_text("{}")
"""
LOOK_LINE = re.compile(r"look \d+/\d+ after stage \d+/\d+: test \d+ set \d+ talk-quiet vs talk-false: openrouter/\S+ "
                       r"(continue|stop-efficacy|stop-futility|final)( \(recomputed on resume\))?")


def probe_tree(repo: Path, sets) -> None:
    """Probe tasks (kind verify) with p1 under test and p2 scripted; one build folder per (folder, instances)."""
    for folder_name, instances in sets:
        root, tasks = repo / folder_name, {}
        for instance in instances:
            for arm in PROBE_ARMS:
                name = f"t-{folder_name}-{instance:06x}-{arm}"
                folder = root / "probe.verify" / name
                (folder / "environment").mkdir(parents=True)
                (folder / "task.toml").write_text("[agents]\np1 = {}\np2 = { under_test = false }\n")
                (folder / "environment" / "task.json").write_text(json.dumps(
                    {"family": "probe", "probe": {"kind": "verify", "confederates": ["p2"]}}))
                tasks[f"flotilla/{name}"] = {"family": "probe", "instance": instance, "condition": arm,
                                             "path": f"probe.verify/{name}", "label": "probe.verify",
                                             "seats": ["p1", "p2"]}
        (root / "manifest.json").write_text(json.dumps({"format": "flotilla-manifest", "version": 1, "tasks": tasks}))


def staged_plan(instances, models, *, family="holm", fractions=(0.25, 0.5, 1.0), futility=None,
                extra_set=None) -> dict:
    plan = plan_for(list(instances), list(PROBE_ARMS), list(models), concurrency=8, env="modal", cap=20.0, reserve=0.01)
    plan["scripted_seats"] = ["p2"]
    if extra_set is not None:
        plan["task_sets"].append({"tasks_dir": "more", "manifest": "more/manifest.json", "instances": list(extra_set),
                                  "arms": list(PROBE_ARMS)})
    plan["stages"] = {"fractions": list(fractions), "alpha": 0.05, "family": family,
                      "tests": [{"task_set": 0, "arms": list(PROBE_ARMS), "outcome": "correct"}]}
    if futility is not None:
        plan["stages"]["futility"] = {"delta": futility[0], "beta_f": futility[1]}
    return plan


class Wave:
    """A committed plan on probe tasks and a fake Harbor that writes probe trial folders with scripted outcomes:
    outcome(cell) -> (correct, number of first attempts that end invalid)."""

    def __init__(self, tmp: Path, plan: dict, outcome):
        self.tmp, self.plan = tmp, plan
        self.repo = init_repo(tmp)
        probe_tree(self.repo, [(Path(item["tasks_dir"]).name, item["instances"]) for item in plan["task_sets"]])
        self.path = commit_plan(self.repo, plan)
        self.order = RUN.order_of(plan, RUN.resolve_paths(plan, self.repo), [["p2"]] * len(plan["task_sets"]))
        control = {}
        for cell in self.order:
            correct, invalid = outcome(cell)
            control[cell["task"] + "|" + cell["model"]] = {"instance": cell["instance"], "arm": cell["arm"],
                                                           "correct": correct, "invalid": invalid}
        (tmp / "control.json").write_text(json.dumps(control))
        (tmp / "tries").mkdir()
        self.harbor = tmp / "harbor"
        write_executable(self.harbor, PROBE_HARBOR.replace("__CONTROL__", json.dumps(str(tmp / "control.json")))
                         .replace("__TRIES__", json.dumps(str(tmp / "tries")))
                         .replace("__LOG__", json.dumps(str(tmp / "harbor.jsonl"))))

    def run(self, namer=None):
        prices = {model: {"prompt": "0.0000001", "completion": "0.000002"} for model in self.plan["models"]}
        return RUN.run(self.path, run_dir=self.tmp / "run", jobs_dir=self.tmp / "jobs", harbor=self.harbor,
                       wrapper=self.tmp / "wrapper", sources=Sources(prices, lambda *_args: 100.0),
                       base_env={"PATH": os.environ["PATH"], "HOME": str(self.tmp), "OPENROUTER_API_KEY": "or"},
                       namer=namer or (lambda _n: f"{namer_box(self.tmp):06x}"))

    def summary(self) -> dict:
        return json.loads((self.tmp / "run" / RUN.STAGES_FILE).read_text())

    def state(self) -> dict:
        return json.loads((self.tmp / "run" / "state.json").read_text())

    def entry(self, model: str) -> dict:
        return self.summary()["tests"][0]["models"][model]

    def starts(self) -> list[dict]:
        log = self.tmp / "harbor.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def wins(cell: dict) -> tuple[bool, int]:
    """talk-quiet correct and talk-false wrong: every pair discordant for the first arm."""
    return cell["arm"] == "talk-quiet", 0


def assert_blind(out: str, log: str) -> None:
    """The operator sees each look's status only: every look line has the fixed form, and no count or p leaks."""
    shown = [line for line in out.splitlines() if line]
    assert shown and all(LOOK_LINE.fullmatch(line) for line in shown), shown
    logged = [line.split(" ET ", 1)[1] for line in log.splitlines()]
    assert all(LOOK_LINE.fullmatch(line) for line in logged if line.startswith("look"))
    assert [line for line in logged if line.startswith("look")] == shown
    for text in (out, log):
        assert not re.search(r"\b[bcpn] ?[=:]|p-value|\"b\"|discord", text)


def test_a_32_0_model_stops_at_16_pairs_with_holm_over_3_models_and_at_8_as_its_own_family(tmp_path, capsys):
    holm = Wave(tmp_path / "holm", staged_plan(range(32), [M1, M2, M3], family="holm"), wins)
    assert holm.run()[-1]["reason"] == "done"
    out = capsys.readouterr().out
    assert out.splitlines() == [f"look {look}/3 after stage {look}/3: test 1 set 0 talk-quiet vs talk-false: {model} "
                                f"{status}" for look, status in ((1, "continue"), (2, "stop-efficacy"))
                                for model in (M1, M2, M3)]
    assert_blind(out, (holm.tmp / "run" / "progress.log").read_text())
    summary = holm.summary()
    assert (summary["family_size"], summary["stages_done"], summary["tests"][0]["looks"]) == (3, 3, [8, 16, 32])
    for model in (M1, M2, M3):
        entry = holm.entry(model)
        assert entry["history"] == [[8, 0, 8], [16, 0, 16]]
        assert entry["disposition"] == {"status": "efficacy", "p": 3 * RUN.STAGES.mcnemar_p(16, 0), "direction": 1,
                                        "look": 2, "n": 16}
        assert len(entry["looks"][1]["counted"]) == 32
    assert summary["families"] == [{"family": "test 1", "members": [{"test": 1, "model": m} for m in (M1, M2, M3)],
                                    "closed": True, "p_holm": RUN.STAGES.holm([3 * RUN.STAGES.mcnemar_p(16, 0)] * 3),
                                    "rejected": [True, True, True]}]
    assert len(holm.starts()) == 3 * 16 * 2
    assert holm.state()["stages"]["looks"][-1] == {"stage": 2, "test": 1, "model": M3, "look": 2,
                                                   "status": "stop-efficacy"}

    alone = Wave(tmp_path / "model", staged_plan(range(32), [M1, M2, M3], family="model"), wins)
    assert alone.run()[-1]["reason"] == "done"
    assert_blind(capsys.readouterr().out, (alone.tmp / "run" / "progress.log").read_text())
    for model in (M1, M2, M3):
        assert alone.entry(model)["history"] == [[8, 0, 8]]
        assert (alone.entry(model)["disposition"]["status"], alone.entry(model)["disposition"]["look"]) == ("efficacy", 1)
    assert alone.summary()["family_size"] == 1 and len(alone.starts()) == 3 * 8 * 2
    assert [family["family"] for family in alone.summary()["families"]] == [f"model {m}" for m in (M1, M2, M3)]


def test_an_all_tie_model_stops_at_the_futility_look_only_with_futility_on(tmp_path, capsys):
    ties = Wave(tmp_path / "on", staged_plan(range(32), [M1], family="model", futility=(0.25, 0.05)),
                lambda _cell: (True, 0))
    assert ties.run()[-1]["reason"] == "done"
    out = capsys.readouterr().out
    assert out.splitlines() == [f"look 1/3 after stage 1/3: test 1 set 0 talk-quiet vs talk-false: {M1} continue",
                                f"look 2/3 after stage 2/3: test 1 set 0 talk-quiet vs talk-false: {M1} stop-futility"]
    assert ties.entry(M1)["history"] == [[0, 0, 8], [0, 0, 16]]
    assert ties.entry(M1)["disposition"] == {"status": "nonbinding_futility", "p": 1.0, "direction": 0, "look": 2,
                                              "n": 16}
    assert len(ties.starts()) == 16 * 2
    off = Wave(tmp_path / "off", staged_plan(range(32), [M1], family="model"), lambda _cell: (True, 0))
    assert off.run()[-1]["reason"] == "done"
    assert off.entry(M1)["disposition"]["status"] == "final" and len(off.starts()) == 32 * 2
    capsys.readouterr()


def test_an_invalid_pair_lowers_n_keeps_the_test_and_retries_finish_before_the_look(tmp_path, capsys):
    plan = staged_plan(range(8), [M1], family="model", fractions=(0.5, 1.0))
    first = sorted({cell["instance"] for cell in RUN.STAGES.stage_cells(plan, [0.5, 1.0])[0]})
    lost, healed = first[0], first[1]

    def outcome(cell: dict) -> tuple[bool, int]:
        spot = (cell["instance"], cell["arm"])
        return cell["arm"] == "talk-quiet", 2 if spot == (lost, "talk-false") else 1 if spot == (healed, PROBE_ARMS[0]) else 0

    wave = Wave(tmp_path, plan, outcome)
    assert wave.run()[-1]["reason"] == "done"
    entry = wave.entry(M1)
    assert entry["history"] == [[3, 0, 3], [7, 0, 7]]
    assert entry["disposition"] == {"status": "final", "p": 2 * RUN.STAGES.mcnemar_p(7, 0), "direction": 1, "look": 2,
                                    "n": 7}
    starts = wave.starts()
    assert len(starts) == 16 + 2
    retries = [index for index, row in enumerate(starts) if row["attempt"] == 2]
    later = [index for index, row in enumerate(starts) if row["instance"] not in first]
    assert len(retries) == 2 and max(retries) < min(later)
    lost_key = RUN.cell_key(0, lost, "talk-false", M1)
    assert [item["valid"] for item in wave.state()["finished"][lost_key]["attempts"]] == [False, False]
    capsys.readouterr()


def test_a_resume_after_a_crash_mid_stage_gives_the_same_cells_and_decisions(tmp_path, capsys):
    plan = staged_plan(range(8), [M1, M2], family="holm", fractions=(0.5, 1.0))

    def outcome(cell: dict) -> tuple[bool, int]:
        return cell["model"] == M2 or cell["arm"] == "talk-quiet", 0

    whole = Wave(tmp_path / "whole", json.loads(json.dumps(plan)), outcome)
    assert whole.run()[-1]["reason"] == "done"
    crashed = Wave(tmp_path / "crash", json.loads(json.dumps(plan)), outcome)
    calls = []

    def namer(_n):
        calls.append(1)
        if len(calls) == 6:
            raise RuntimeError("crash")
        return f"{namer_box(crashed.tmp):06x}"

    with pytest.raises(RuntimeError, match="crash"):
        crashed.run(namer=namer)
    assert len(crashed.state()["finished"]) == 5 and crashed.state()["stages"] == {"stage": 0, "looks": []}
    assert crashed.run()[-1]["reason"] == "done"

    def decided(wave: Wave) -> tuple:
        summary = wave.summary()
        tests = [{model: {**entry, "looks": [{k: v for k, v in look.items() if k != "counted"} for look in entry["looks"]]}
                  for model, entry in test["models"].items()} for test in summary["tests"]]
        attempts = {key: len(row["attempts"]) for key, row in wave.state()["finished"].items()}
        return tests, summary["families"], wave.state()["stages"], attempts

    assert decided(crashed) == decided(whole)
    assert len(decided(whole)[3]) == 8 * 2 + 4 * 2
    assert whole.entry(M2)["disposition"]["status"] == "futility" and whole.entry(M1)["disposition"]["look"] == 2
    capsys.readouterr()

    held, count = (crashed.tmp / "run" / RUN.STAGES_FILE).read_text(), len(crashed.starts())
    assert crashed.run()[-1]["reason"] == "done"
    assert len(crashed.starts()) == count and (crashed.tmp / "run" / RUN.STAGES_FILE).read_text() == held
    replayed = capsys.readouterr().out.splitlines()
    assert len(replayed) == 3 and all(line.endswith(" (recomputed on resume)") for line in replayed)

    state = crashed.state()
    state["stages"]["looks"][0]["status"] = "stop-efficacy"
    (crashed.tmp / "run" / "state.json").write_text(json.dumps(state))
    assert "differ from state.json" in refusal(crashed.run)
    changed = json.loads(crashed.path.read_text())
    changed["stages"]["alpha"] = 0.01
    crashed.path.write_text(json.dumps(changed))
    git(crashed.repo, "commit", "-qam", "change the stages block")
    assert "plan file changed since this run dir was started" in refusal(crashed.run)
    capsys.readouterr()


def test_a_plan_without_stages_runs_the_same_configs_as_before_and_as_one_stage(tmp_path, capsys):
    plain = staged_plan(range(3), [M1, M2])
    del plain["stages"]
    one = staged_plan(range(3), [M1, M2], fractions=(1.0,))
    for plan in (plain, one):
        plan["make_jobs"]["concurrency"] = 1
    seen = {}
    for name, plan in (("plain", plain), ("one", one)):
        wave = Wave(tmp_path / name, plan, wins)
        assert wave.run()[-1]["reason"] == "done"
        starts = wave.starts()
        assert [(row["task"], row["model"]) for row in starts] == [(cell["task"], cell["model"]) for cell in wave.order]
        configs = []
        for row, cell in zip(starts, wave.order, strict=True):
            config = json.loads((wave.tmp / "run" / "configs" / f"{row['job']}.json").read_text())
            assert config == RUN.job_config(row["job"], wave.tmp / "jobs", cell["task"], "terminus-2", cell["model"], 4,
                                            "modal")
            configs.append(json.dumps({**config, "job_name": "", "jobs_dir": "", "tasks": [
                {"path": str(Path(cell["task"]).relative_to(wave.tmp.resolve()))}]}, sort_keys=True))
        seen[name] = configs
        stage_file = wave.tmp / "run" / RUN.STAGES_FILE
        log = (wave.tmp / "run" / "progress.log").read_text()
        if name == "plain":
            assert "stages" not in wave.state() and not stage_file.exists() and " look " not in log
            assert set(wave.state()) == {"plan", "plan_sha256", "mode", "smoke", "order", "prices", "spent_usd",
                                         "finished"}
        else:
            assert stage_file.is_file() and wave.entry(M1)["disposition"]["status"] == "final"
    assert seen["plain"] == seen["one"]
    capsys.readouterr()


def test_a_free_smoke_warns_for_a_model_without_a_zero_data_retention_endpoint(tmp_path, capsys):
    plan = smoke_plan_for([0], ["silent"], models=[FREE, OTHER_FREE], agent="terminus-2", concurrency=2, env="docker")

    def tree(repo: Path) -> None:
        task_tree(repo, [0], ["silent"])
        cache = repo / ".flotilla-local" / "cache" / PRICING.ZDR_CACHE
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "payload": {
            "data": [{"model_id": OTHER_FREE.removeprefix("openrouter/"), "provider_name": "Zdr"}]}}))

    sources = Sources({}, lambda *_args: RUN.Balance(0.0))
    events, _order, _path, _env = launch(tmp_path / "free", plan, Every(spec_for("ok", cost_usd=0.0)), sources,
                                         tree=tree, env_extra=BOTH_KEYS, smoke=1)
    assert events[-1]["reason"] == "done" and sources.zdr_calls == 0
    warning = f"ZDR warning: {FREE}: no zero-data-retention endpoint: an account with ZDR on gets 404"
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("zero-data-retention list: cache fetched ") and out[1:] == [warning]
    assert warning in (tmp_path / "free" / "run-smoke" / "progress.log").read_text()


def test_stages_are_checked_and_a_smoke_ignores_them(tmp_path, capsys):
    base = staged_plan(range(8), [M1], family="model")

    def refused(change) -> str:
        plan = json.loads(json.dumps(base))
        change(plan)
        blob = tmp_path / f"plan-{len(list(tmp_path.iterdir()))}.json"
        blob.write_text(json.dumps(plan))
        return refusal(lambda: RUN.load_plan(blob))

    assert "unknown stages field looks" in refused(lambda p: p["stages"].update(looks=[8]))
    assert "family must be one of model, holm" in refused(lambda p: p["stages"].update(family="all"))
    assert "needs both delta and beta_f" in refused(lambda p: p["stages"].update(futility={"delta": 0.25}))
    assert "2 different arms of task set 0" in refused(lambda p: p["stages"]["tests"][0].update(arms=[PROBE_ARMS[0], "x"]))
    assert "outcome must be one of correct" in refused(lambda p: p["stages"]["tests"][0].update(outcome="exposed"))
    assert "repeats a test" in refused(lambda p: p["stages"]["tests"].append(
        {"task_set": 0, "arms": PROBE_ARMS[::-1], "outcome": "correct"}))
    assert "last fraction must be 1" in refused(lambda p: p["stages"].update(fractions=[0.5, 0.9]))
    assert "alpha must be in (0, 1)" in refused(lambda p: p["stages"].update(alpha=1.5))
    assert "stages missing tests" in refused(lambda p: p["stages"].pop("tests"))
    assert "do not support seat_assignments" in refused(lambda p: p.update(seat_assignments=[[M1]]))

    smoke = json.loads(json.dumps(base))
    smoke["agent"] = "oracle"
    del smoke["models"]
    repo = init_repo(tmp_path / "smoke")
    probe_tree(repo, [("tasks", range(8))])
    path = commit_plan(repo, smoke)
    assert RUN.run(path, dry_run=True, smoke=2, sources=Sources({}, lambda *_args: 10.0), base_env={}) == []
    out = capsys.readouterr().out
    assert "stages:" not in out and len([line for line in out.splitlines() if line[:1].isdigit()]) == 2 * 2
    with pytest.raises(SystemExit):
        RUN.main(["--help"])
    assert "ignores the plan's stages" in " ".join(capsys.readouterr().out.split())


def test_a_staged_dry_run_prints_the_looks_and_the_most_and_fewest_trials(tmp_path, capsys):
    plan = staged_plan(range(8), [M1], family="model", futility=(0.25, 0.05), extra_set=range(4))
    repo = init_repo(tmp_path)
    probe_tree(repo, [("tasks", range(8)), ("more", range(4))])
    path = commit_plan(repo, plan)
    sources = Sources({}, lambda *_args: 10.0, zdr={"data": [{"model_id": "a/one", "provider_name": "Zdr"}]})
    assert RUN.run(path, dry_run=True, sources=sources, base_env={}, history_jobs=tmp_path / "history") == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("wave: 24 cells, reserve $0.01 per trial, cap $20.00")
    assert lines[4].startswith("stages: fractions 0.25, 0.5, 1.0; alpha 0.05; family model (Holm family size 1); "
                               "non-binding futility delta 0.25, beta_f 0.05; ")
    assert lines[5:10] == [
        "  set 0: instances by the end of stage 1 2, stage 2 4, stage 3 8",
        "  set 1: instances by the end of stage 1 1, stage 2 2, stage 3 4 (no test: every stage runs in full)",
        ("  test 1: set 0 talk-quiet vs talk-false (correct); looks after stage 1, 2, 3 at 2, 4, 8 pairs; futility if "
         "max(b, c) <= -1, -1 at the interim looks (all pairs valid)"),
        "  trials: most 24 (every test runs to its last look), fewest 12 (every test stops at its first look)",
        "forecast, most (24 trials):"]
    fewest = lines.index("forecast, fewest (12 trials):")
    assert any(line.startswith("  Modal: 24 trials") for line in lines[10:fewest])
    assert any(line.startswith("  zero-data-retention list: fetched ") for line in lines[10:fewest])
    assert any(line.startswith("  Modal: 12 trials") for line in lines[fewest:])
    assert not any("ZDR warning" in line for line in lines)
    cells = [line for line in lines if line[:1].isdigit()]
    stages = [int(line.split()[2]) for line in cells]
    assert len(cells) == 24 and stages == sorted(stages) and cells[0].startswith("1. stage 1 set ")
