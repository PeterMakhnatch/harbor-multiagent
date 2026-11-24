"""Reliability ledger (docs/results/scripts/reliability.py) on synthetic job folders: one trial per cause class,
precedence between causes, stalls, abandoned and running trials, cost on invalid trials, run_wave cells and retries,
grouping per wave, model and environment, error bursts, and the CLI's markdown and JSON."""

from __future__ import annotations

import json
import os
import time
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FLOTILLA, load

RL = load(FLOTILLA / "docs" / "results" / "scripts" / "reliability.py", "flotilla_test_reliability")
T0 = datetime(2026, 11, 23, 19, 0, 0, tzinfo=UTC)
A, B = "openrouter/a/model", "openrouter/b/model"
TRIAL = "t-0123456789ab__Abc1234"
NOT_FOUND = 'litellm.NotFoundError: NotFoundError: OpenrouterException - {"error":{"message":"0 endpoints","code":404}}'
RATE_LINE = "Unknown Error in LLM interaction: litellm.RateLimitError: RateLimitError: too many requests\n"


def iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def put(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))


def job(jobs, name, env="modal", finished=True):
    put(jobs / name / "config.json", {"job_name": name, "environment": {"type": env}})
    put(jobs / name / "result.json", {"finished_at": iso(9000) if finished else None})
    return jobs / name / TRIAL


def steps(at, offsets, cost):
    return [{"step_id": index, "source": "agent", "timestamp": iso(at + offset),
             "metrics": {"cost_usd": cost / len(offsets)}} for index, offset in enumerate(offsets, 1)]


def seat(folder, name, model, at, cost, offsets, exception, started):
    provider, _, short = model.partition("/")
    if started and offsets:
        put(folder / "seats" / name / "agent" / "trajectory.json",
            {"agent": {"model_name": model}, "steps": steps(at, offsets, cost)})
    return {
        "seat": name, "under_test": True,
        "agent_info": {"name": "terminus-2", "model_info": {"provider": provider, "name": short}},
        "agent_result": {"cost_usd": cost if started else None},
        "agent_execution": {"started_at": iso(at + 30), "finished_at": iso(at + 300)} if started else None,
        "exception_info": {"exception_type": exception[0], "exception_message": exception[1]} if exception else None,
    }


def trial(jobs, name, *, at=0.0, env="modal", models=(A, A), cost=0.1, reward=1.0, exception=None, seat_errors=None,
          unstarted=(), verdict=None, starts=1, log="", offsets=(10, 40, 70)):
    folder = job(jobs, name, env)
    errors = seat_errors or {}
    seats = [seat(folder, f"p{index}", model, at, cost, offsets, errors.get(f"p{index}"), f"p{index}" not in unstarted)
             for index, model in enumerate(models, 1)]
    put(folder / "result.json", {
        "trial_name": TRIAL, "task_name": "flotilla/t-0123456789ab", "started_at": iso(at), "finished_at": iso(at + 400),
        "environment_setup": {"started_at": iso(at), "finished_at": iso(at + 20)},
        "agent_setup": {"started_at": iso(at + 20), "finished_at": iso(at + 30)},
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": {"exception_type": exception[0], "exception_message": exception[1]} if exception else None,
        "seat_results": seats,
        "config": {"participants": [{"seat": f"p{index}", "name": "terminus-2", "model_name": model}
                                    for index, model in enumerate(models, 1)]},
    })
    if verdict is not False:
        put(folder / "verifier" / "score.json", verdict or {"instance": 0, "condition": "talk", "error": None})
        put(folder / "verifier" / "world-state.json", {"messages": []})
    put(folder / "artifacts" / "logs" / "world" / "events.jsonl",
        "".join(json.dumps({"seq": index, "kind": "start", "t": 0}) + "\n" for index in range(starts)))
    put(folder / "trial.log", log)
    return folder


def unfinished(jobs, name, *, finished, cost=0.05):
    folder = job(jobs, name, finished=finished)
    put(folder / "config.json", {"participants": [{"seat": "p1", "name": "terminus-2", "model_name": A}]})
    put(folder / "seats" / "p1" / "agent" / "trajectory.json",
        {"agent": {"model_name": A}, "steps": steps(0, (10, 40), cost)})
    if finished:
        old = time.time() - 4 * 3600
        for path in folder.rglob("*"):
            os.utime(path, (old, old))
    return folder


def fixture(tmp_path):
    jobs, runs = tmp_path / "jobs", tmp_path / "runs"
    trial(jobs, "wa-000001", at=30, env="docker", log=RATE_LINE)
    trial(jobs, "wa-000002", at=60, cost=0.3, reward=0.5,
          exception=("AgentTimeoutError", "Seat 'p1': Agent execution timed out after 600.0 seconds"),
          seat_errors={"p1": ("AgentTimeoutError", "Agent execution timed out after 600.0 seconds")})
    trial(jobs, "wa-000003", at=120, cost=0.0, reward=None, verdict=False,
          exception=("NotFoundError", NOT_FOUND), seat_errors={"p2": ("NotFoundError", NOT_FOUND)},
          log=f"Trial {TRIAL} failed: {NOT_FOUND}\n")
    trial(jobs, "wa-000004", at=180, reward=None, verdict=False, unstarted=("p1", "p2"), offsets=(),
          exception=("RuntimeError", "docker compose build failed: #1 [internal] load local bake definitions"))
    trial(jobs, "wa-000005", at=240, reward=None, verdict=False, exception=("VerifierTimeoutError", "verifier timed out"))
    trial(jobs, "wa-000006", at=300, starts=2)
    trial(jobs, "wa-000007", at=360, unstarted=("p2",))
    trial(jobs, "wa-000008", at=420, reward=None)
    trial(jobs, "wa-000009", at=480, reward=None, verdict=False, exception=("CancelledError", ""))
    trial(jobs, "wa-000010", at=540, reward=None, verdict=False)
    trial(jobs, "wa-000011", at=5 * 3600, exception=("ValueError", "boom"))
    trial(jobs, "wa-000012", at=7200, offsets=(10, 40, 700))
    trial(jobs, "wa-000013", at=600, exception=("AgentTimeoutError", "Agent execution timed out after 900.0 seconds"),
          log=RATE_LINE * 3)
    unfinished(jobs, "wa-000014", finished=True)
    unfinished(jobs, "wa-000015", finished=False)
    trial(jobs, "wb-000001", at=8000, models=(B, B), cost=0.2)
    trial(jobs, "wb-000002", at=8100, env="docker")
    trial(jobs, "xx-ab-1seat", at=8200, models=(A, B))
    cell = {"key": "k1", "task_set": 0, "instance": 0, "arm": "talk", "model": A, "task": "unused"}
    put(runs / "wb" / "state.json", {"plan": "wb", "order": [cell], "finished": {"k1": {"valid": True, "attempts": [
        {"job": "wb-000001", "valid": False, "cost_usd": 0.4}, {"job": "wb-000002", "valid": True, "cost_usd": 0.2}]}}})
    return jobs, runs


def scanned(tmp_path):
    jobs, runs = fixture(tmp_path)
    trials, activity = RL.scan(jobs, ["*"], runs, 500.0, time.time())
    args = Namespace(jobs=jobs, globs=["*"], runs=runs, threshold=500.0, window=120.0, burst_gap=600.0)
    return {t["job"]: t for t in trials}, RL.build(trials, activity, args)


def test_every_cause_class_and_precedence(tmp_path):
    rows, _payload = scanned(tmp_path)
    causes = {name: (row["status"], row["cause"], row["detail"]) for name, row in rows.items()}
    assert causes == {
        "wa-000001": ("valid", None, None),
        "wa-000002": ("invalid", "agent-timeout", "AgentTimeoutError 600s"),
        "wa-000003": ("invalid", "provider", "NotFoundError 404"),
        "wa-000004": ("invalid", "environment",
                      "RuntimeError: docker compose build failed: #1 [internal] load local bake definitions"),
        "wa-000005": ("invalid", "verifier", "VerifierTimeoutError"),
        "wa-000006": ("invalid", "world-restart", "more than one world start"),
        "wa-000007": ("invalid", "seat-never-started", "seat p2"),
        "wa-000008": ("invalid", "missing-reward", "no usable reward"),
        "wa-000009": ("invalid", "cancelled", "CancelledError"),
        "wa-000010": ("invalid", "verifier", "no verifier score"),
        "wa-000011": ("invalid", "other", "ValueError"),
        "wa-000012": ("valid", None, None),
        "wa-000013": ("invalid", "provider", "RateLimitError 429"),
        "wa-000014": ("abandoned", "abandoned", "no result.json"),
        "wa-000015": ("running", None, None),
        "wb-000001": ("invalid", "other", "run_wave cell check (seat models or instance)"),
        "wb-000002": ("valid", None, None),
        "xx-ab-1seat": ("valid", None, None),
    }
    assert [cause for cause, _detail in rows["wa-000013"]["causes"]] == ["provider", "agent-timeout"]
    assert ["seat-never-started", "seat p1"] in rows["wa-000004"]["causes"]
    assert rows["wa-000001"]["provider_errors"] == {"rate-limit": 1}
    assert rows["wa-000002"]["reward"] == 0.5 and rows["wa-000003"]["reward"] is None
    assert rows["wa-000002"]["exceptions"] == [{"owner": None, "model": None, "type": "AgentTimeoutError"},
                                               {"owner": "p1", "model": A, "type": "AgentTimeoutError"}]
    assert rows["wa-000001"]["duration_s"] == 400 and rows["wa-000001"]["setup_s"] == 30
    assert RL.exception_cause("RateLimitError", "litellm.RateLimitError: status_code: 503") == (
        "provider", "RateLimitError 503")
    assert RL.exception_cause("EnvironmentStartTimeoutError", "")[0] == "environment"


def test_stalls_costs_and_groups(tmp_path):
    rows, payload = scanned(tmp_path)
    assert [name for name, row in rows.items() if row["stalled"]] == ["wa-000012"]
    assert rows["wa-000012"]["stall_gap_s"] == 660 and rows["wa-000012"]["valid"]
    assert rows["wa-000014"]["cost_usd"] == pytest.approx(0.05)
    assert rows["wa-000007"]["cost_usd"] == pytest.approx(0.1)
    totals = payload["totals"]
    invalid = ["wa-000002", "wa-000003", "wa-000004", "wa-000005", "wa-000006", "wa-000007", "wa-000008", "wa-000009",
               "wa-000010", "wa-000011", "wa-000013", "wa-000014", "wb-000001"]
    assert totals["trials"] == 17 and totals["valid"] == 4 and totals["running"] == 1 and totals["abandoned"] == 1
    assert totals["invalid_cost_usd"] == pytest.approx(sum(rows[name]["cost_usd"] for name in invalid))
    assert totals["invalid_cost_usd"] == pytest.approx(0.6 + 0.0 + 0.0 + 0.2 * 7 + 0.1 + 0.05 + 0.4)
    assert totals["invalid_with_reward"] == 6 and totals["provider_errors_recovered"] == 1
    assert totals["run_wave_trials"] == 2 and totals["run_wave_charged_invalid_usd"] == pytest.approx(0.4)
    assert totals["state_disagreements"] == []
    waves = payload["groups"]["wave"]
    assert sorted(waves) == ["wa", "wb", "xx"]
    assert (waves["wa"]["trials"], waves["wa"]["valid"], waves["wa"]["stalled"]) == (14, 2, 1)
    assert waves["wa"]["invalid"]["provider"] == 2 and waves["wa"]["invalid"]["abandoned"] == 1
    assert (waves["wb"]["trials"], waves["wb"]["retried_cells"], waves["wb"]["invalid"]) == (2, 1, {"other": 1})
    assert waves["wa"]["duration_median_s"] == 400 and waves["wa"]["setup_p90_s"] == 30
    env = payload["groups"]["env"]
    assert (env["docker"]["trials"], env["docker"]["valid"]) == (2, 2) and env["modal"]["invalid"]["environment"] == 1
    models = payload["groups"]["model"]
    assert models[B]["trials"] == 2 and models[B]["cost_usd"] == pytest.approx(0.5)
    assert payload["groups"]["provider"]["openrouter/a"]["trials"] == 16
    assert payload["seat_exceptions"][A] == {"AgentTimeoutError": 1, "NotFoundError": 1}
    assert payload["causes"]["agent-timeout"] == {"trials": 1, "cost_usd": pytest.approx(0.6),
                                                  "details": {"AgentTimeoutError 600s": 1}}


def test_bursts_messages_and_cli(tmp_path, capsys):
    _rows, payload = scanned(tmp_path)
    assert [(b["invalid"], b["started"]) for b in payload["bursts"]] == [(11, 12)]
    burst = payload["bursts"][0]
    assert burst["start"] == T0.timestamp() + 10 and burst["waves"] == {"wa": 11}
    assert payload["wave_starts"]["wa"]["first_hour_invalid"] == 11
    messages = {m["message"]: m for m in payload["messages"]}
    timeout = messages["Agent execution timed out after N seconds"]
    assert timeout["trials"] == 2 and timeout["example"].endswith(f"wa-000002/{TRIAL}")
    assert messages["CancelledError"]["invalid_trials"] == 1
    jobs, runs = tmp_path / "jobs", tmp_path / "runs"
    out = tmp_path / "ledger.json"
    assert RL.main([str(jobs), "wa-*", "wb-*", "--runs", str(runs), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "valid 3 (18.8%); invalid 13, of them abandoned 1" in text
    assert "| `wb` | 2 | 50.0% | other 1 |" in text
    assert "## Invalid bursts" in text and "2026-11-23 14:00 ET" in text
    saved = json.loads(out.read_text())
    assert saved["totals"]["trials"] == 16 and len(saved["trials"]) == 17
    assert RL.main([str(jobs), "nothing-*"]) == 0
    assert "Trials: 0 counted" in capsys.readouterr().out
