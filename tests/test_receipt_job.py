"""Unit tests for examples/seatlab/receipt_job.py."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig
from harbor.models.task.task import Task
from harbor.trial.seats import check_launch

_EXAMPLES_SEATLAB = Path(__file__).resolve().parents[1] / "examples" / "seatlab"
if str(_EXAMPLES_SEATLAB) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_SEATLAB))

receipt_job = importlib.import_module("receipt_job")


def _write_prepared(root: Path) -> Path:
    """Create a minimal prepared receipt-board directory matching prepare.py layout."""
    root.mkdir(parents=True, exist_ok=True)
    case = {"options": {"p1": ["amber", "cobalt"], "p2": ["mint", "coral"]}}
    task_toml = (
        'schema_version = "1.4"\n'
        'artifacts = ["/logs/world/receipts.jsonl"]\n\n'
        "[task]\n"
        'name = "seatlab/receipt-board"\n\n'
        "[agent]\n"
        "timeout_sec = 180.0\n\n"
        "[agents.p1]\n"
        "under_test = true\n\n"
        "[agents.p2]\n"
        "under_test = true\n"
    )
    compose = "services:\n  main: {}\n  world: {}\n  p1: {}\n  p2: {}\n"
    for arm in ("full", "first"):
        task = root / arm / "task"
        (task / "environment").mkdir(parents=True)
        (task / "instructions").mkdir(parents=True)
        (task / "solution").mkdir(parents=True)
        (task / "tests").mkdir(parents=True)
        (task / "task.toml").write_text(task_toml, encoding="utf-8")
        (task / "environment" / "case.json").write_text(
            json.dumps(case) + "\n", encoding="utf-8"
        )
        (task / "environment" / "mode.json").write_text(
            json.dumps(arm) + "\n", encoding="utf-8"
        )
        (task / "environment" / "world.py").write_text("# world\n", encoding="utf-8")
        (task / "environment" / "docker-compose.yaml").write_text(
            compose, encoding="utf-8"
        )
        (task / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        for seat in ("p1", "p2"):
            (task / "instructions" / f"{seat}.md").write_text(
                f"You are {seat}.\n", encoding="utf-8"
            )
    return root


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_prepare_job_builds_valid_stock_job_config_and_plan(tmp_path: Path) -> None:
    prepared = _write_prepared(tmp_path / "prepared")
    out = tmp_path / "job-out"

    config_path, plan_path, plan = receipt_job.prepare_job(prepared, out)

    assert config_path == out / "job-config.json"
    assert plan_path == out / "job-plan.json"
    assert _load_json(plan_path) == plan

    raw_config = _load_json(config_path)
    assert raw_config["schema_version"] == 1
    assert raw_config["n_attempts"] == 1
    assert raw_config["n_concurrent_trials"] == 1
    assert raw_config["retry"] == {"max_retries": 0}
    assert raw_config["environment"] == {
        "type": "docker",
        "force_build": True,
        "delete": True,
    }
    assert raw_config["participants"] == [
        {"seat": "p1", "name": "oracle"},
        {"seat": "p2", "name": "oracle"},
    ]
    assert Path(raw_config["jobs_dir"]).is_absolute()
    assert Path(raw_config["jobs_dir"]) == (out / "jobs").resolve()
    assert len(raw_config["tasks"]) == 2
    for entry, arm in zip(raw_config["tasks"], ("full", "first"), strict=True):
        task_path = Path(entry["path"])
        assert task_path.is_absolute()
        assert task_path == (out / "tasks" / f"receipt-board-{arm}").resolve()
        assert entry["source"] == f"receipt-board-{arm}"
        assert Task.is_valid_dir(task_path)

    # Stock Harbor JobConfig parses and builds two real seat-compatible trials.
    job_config = JobConfig.model_validate(raw_config, extra="forbid")
    assert job_config.n_attempts == 1
    assert job_config.n_concurrent_trials == 1
    assert job_config.retry.max_retries == 0
    assert [(p.seat, p.name) for p in job_config.participants] == [
        ("p1", "oracle"),
        ("p2", "oracle"),
    ]

    trial_configs = JobPlan.build_trial_configs(
        job_config,
        job_config.tasks,
        job_id=uuid4(),
    )
    assert len(trial_configs) == 2
    assert len({tc.trial_name for tc in trial_configs}) == 2
    for tc, arm in zip(trial_configs, ("full", "first"), strict=True):
        assert tc.task.source == f"receipt-board-{arm}"
        assert tc.trial_name.startswith(f"receipt-board-{arm}__")
        task = Task(tc.task.get_local_path())
        check_launch(tc, task)

    # Plan is path-free and binds integer slots, arms, slugs, digests, and views.
    serialized_plan = json.dumps(plan)
    assert str(prepared) not in serialized_plan
    assert str(out) not in serialized_plan
    assert plan["schema"] == receipt_job.PLAN_SCHEMA
    assert plan["expected_trials"] == 2
    assert plan["seats"] == ["p1", "p2"]
    slots = plan["slots"]
    assert [s["expected_slot"] for s in slots] == [1, 2]
    assert all(type(s["expected_slot"]) is int for s in slots)
    assert [s["arm"] for s in slots] == ["full", "first"]
    assert [s["variant_slug"] for s in slots] == [
        "receipt-board-full",
        "receipt-board-first",
    ]
    assert len({s["task_sha256"] for s in slots}) == 2
    assert slots[0]["expected_actions"] == {"p1": "cobalt", "p2": "coral"}
    assert slots[1]["expected_actions"] == {"p1": "amber", "p2": "mint"}
    assert slots[0]["expected_views"] == {
        "p1": {"role": "p1", "options": ["amber", "cobalt"]},
        "p2": {"role": "p2", "options": ["mint", "coral"]},
    }
    assert slots[1]["expected_views"] == {
        "p1": {"role": "p1", "options": ["amber"]},
        "p2": {"role": "p2", "options": ["mint"]},
    }
    assert slots[0]["expected_view_sha256"] != slots[1]["expected_view_sha256"]


@pytest.mark.unit
def test_prepare_job_refuses_existing_output_and_nested_output(tmp_path: Path) -> None:
    prepared = _write_prepared(tmp_path / "prepared")
    existing = tmp_path / "exists"
    existing.mkdir()

    with pytest.raises(FileExistsError):
        receipt_job.prepare_job(prepared, existing)

    with pytest.raises(ValueError, match="outside the prepared directory"):
        receipt_job.prepare_job(prepared, prepared / "nested")


@pytest.mark.unit
def test_prepare_job_rejects_mismatched_mode_and_case(tmp_path: Path) -> None:
    prepared = _write_prepared(tmp_path / "prepared")
    (prepared / "first" / "task" / "environment" / "mode.json").write_text(
        '"full"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="mode.json must be 'first'"):
        receipt_job.prepare_job(prepared, tmp_path / "out-bad-mode")
    assert not (tmp_path / "out-bad-mode").exists()

    (prepared / "first" / "task" / "environment" / "mode.json").write_text(
        '"first"\n', encoding="utf-8"
    )
    (prepared / "first" / "task" / "environment" / "case.json").write_text(
        json.dumps({"options": {"p1": ["x"], "p2": ["y"]}}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identical case.json"):
        receipt_job.prepare_job(prepared, tmp_path / "out-bad-case")
    assert not (tmp_path / "out-bad-case").exists()


@pytest.mark.unit
def test_prepare_job_rejects_symlink_inside_prepared_task(tmp_path: Path) -> None:
    prepared = _write_prepared(tmp_path / "prepared")
    secret = tmp_path / "secret.txt"
    secret.write_text("do-not-follow\n", encoding="utf-8")
    (prepared / "full" / "task" / "extra.txt").symlink_to(secret)

    with pytest.raises(ValueError, match="symlink not allowed"):
        receipt_job.prepare_job(prepared, tmp_path / "out-symlink")
    assert not (tmp_path / "out-symlink").exists()


@pytest.mark.unit
def test_main_prints_harbor_run_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prepared = _write_prepared(tmp_path / "prepared")
    out = tmp_path / "job-out"

    exit_code = receipt_job.main(["--prepared", str(prepared), "--out", str(out)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    lines = stdout.strip().splitlines()
    assert lines[0] == f"harbor run -c {out / 'job-config.json'}"
    plan = _load_json(out / "job-plan.json")
    assert lines[1] == f"Expected job directory: {out / 'jobs' / plan['job_name']}"
    assert lines[2] == f"Registered job plan: {out / 'job-plan.json'}"
