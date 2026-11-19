"""Cohort admission, at-most-once claims, and native evidence aggregation."""

import importlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

ADAPTER = Path(__file__).resolve().parents[1]


@pytest.fixture
def cohort(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("cohort")


@pytest.fixture
def state(tmp_path):
    state = tmp_path.resolve()
    frozen = state / "frozen"
    frozen.mkdir()
    runner = frozen / "study.py"
    runner.touch()
    root = state / "cohort"
    root.mkdir()
    dataset = state / "dataset"
    dataset.mkdir()
    harbor = state / "harbor"
    harbor.touch(mode=0o700)
    return SimpleNamespace(
        runner=runner,
        root=root,
        dataset=dataset,
        harbor=harbor,
        lock=state / "native-experiment.lock",
        start_index=3,
        end_index=4,
        reserve_hourly_minute=15,
        reserve_before_seconds=120,
        reserve_after_seconds=150,
        max_batch_seconds=150,
        continue_on_failure=False,
    )


def _never(*args, **kwargs):
    pytest.fail("unexpected I/O or wait")


def _clock(cohort, monkeypatch, *, now=lambda: 0, sleep=_never, monotonic=lambda: 0):
    monkeypatch.setattr(
        cohort, "time", SimpleNamespace(time=now, sleep=sleep, monotonic=monotonic)
    )


def _registration(path):
    path.mkdir()
    for name in ("plan.json", "source.json"):
        (path / name).touch()


def _rows(cohort, index):
    return [
        {
            "case_id": case_id,
            "mode": mode,
            "status": "verified",
            "native_id": str(UUID(int=index * 100 + n, version=4)),
            "observed": {
                "choice": 20 if mode == "full" else None,
                "official_exact_match": mode == "full",
                "physically_feasible": True if mode == "full" else None,
                "protocol_match": True,
            },
            "source_expected": {"choice": 20 if mode == "full" else None},
        }
        for n, (case_id, mode) in enumerate(cohort._identities(index))
    ]


def _fake_study_collect(cohort, monkeypatch, runner, inputs):
    called = []

    def collect(command, *, cwd, env, capture_output, check):
        assert command[:5] == [sys.executable, "-B", str(runner), "collect", "--out"]
        assert cwd == runner.parent and capture_output and not check
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        path = Path(command[5])
        called.append(path)
        document = {
            "schema": "seatlab.calendar-study-result/v1",
            "cells": inputs[path],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(document))

    monkeypatch.setattr(cohort.subprocess, "run", collect)
    return called


def test_fixed_plan_has_400_distinct_cells(cohort):
    cases = [cohort.batch_cases(index) for index in range(1, 61)]
    assert cases[0] == ("2/1", "3/1", "4/1", "5/1")
    assert cases[39] == ("2/40", "3/40", "4/40", "5/40")
    assert cases[40] == ("3/41", "4/41")
    assert cases[-1] == ("3/60", "4/60")
    flattened = {
        (case, mode) for batch in cases for case in batch for mode in cohort.MODES
    }
    assert len(flattened) == sum(map(len, cases)) * len(cohort.MODES) == 400


def test_admission_reserves_estimated_batch_time_in_each_hour(cohort):
    reservation = cohort.Reservation()
    for hour in (0, 1, 432):
        start = hour * 3600
        assert reservation.wait(start + 629.9) == 0
        assert reservation.wait(start + 630) == 420
        assert reservation.wait(start + 900) == 150
        assert reservation.wait(start + 1049) == 1
        assert reservation.wait(start + 1050) == 0
    crossing = cohort.Reservation(minute=1, before=120, after=150, budget=150)
    assert crossing.wait(3389) == 0
    assert crossing.wait(3390) == 420
    assert crossing.wait(3600) == 210
    assert crossing.wait(3810) == 0
    with pytest.raises(ValueError, match="reservation"):
        cohort.Reservation(before=3450, after=0, budget=150)


def test_lock_held_by_inherited_description_after_parent_closes(cohort, state):
    with cohort._try_lock(state.lock) as fd:
        assert fd is not None
        retained = os.dup(fd)
    try:
        with cohort._try_lock(state.lock) as competing:
            assert competing is None
    finally:
        os.close(retained)
    with cohort._try_lock(state.lock) as available:
        assert available is not None


def test_pause_priority_and_clock_wait_without_holding_lock(cohort, state, monkeypatch):
    pause = state.root / "PAUSE"
    priority = state.lock.parent / "PRIORITY"
    local_priority = state.root / "PRIORITY"
    for marker in (pause, priority, local_priority):
        marker.touch()
    now, delays = 630, []

    def sleep(delay):
        nonlocal now
        delays.append(delay)
        with cohort._try_lock(state.lock) as borrowed:
            assert borrowed is not None
        if len(delays) == 1:
            pause.unlink()
        elif len(delays) == 2:
            priority.unlink()
        elif len(delays) == 3:
            local_priority.unlink()
        else:
            now = 1050

    _clock(cohort, monkeypatch, now=lambda: now, sleep=sleep)
    with cohort._admission(
        state.root, state.lock, cohort.Reservation(), "batch-003"
    ) as fd:
        assert fd is not None
    assert delays == [cohort.POLL_SECONDS] * 4


def test_stop_interrupts_slot_wait_without_claiming(cohort, state, monkeypatch):
    def sleep(delay):
        assert 0 < delay <= cohort.POLL_SECONDS
        (state.root / "STOP").touch()

    _clock(cohort, monkeypatch, now=lambda: 900, sleep=sleep)
    monkeypatch.setattr(cohort, "_launch", _never)
    assert cohort.run(state) == 0
    assert not (state.root / ".claims").exists()


@pytest.mark.parametrize("index", [1, 2, 61])
def test_reserved_batches_cannot_be_launched(cohort, state, index):
    state.start_index = state.end_index = index
    with pytest.raises(ValueError, match="run range"):
        cohort.run(state)
    assert list(state.root.iterdir()) == []


def test_launch_and_safe_resume_use_exact_cases_and_preserve_inputs(
    cohort, state, monkeypatch
):
    _clock(cohort, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setenv("DOCKER_HOST", "test-only-override")
    old = state.root / "batch-002"
    old.mkdir()
    (old / "untouched").write_text("peer result")
    prefilled = state.root / "batch-003"
    _registration(prefilled)
    fresh = state.root / "batch-004"
    checks = _fake_study_collect(
        cohort,
        monkeypatch,
        state.runner,
        {prefilled: _rows(cohort, 3), fresh: _rows(cohort, 4)},
    )
    launched = []

    @contextmanager
    def launch(command, *, pass_fds, env, cwd, stdout, stderr, start_new_session):
        assert command[:6] == [
            sys.executable,
            "-B",
            str(state.runner),
            "run",
            "--out",
            str(fresh),
        ]
        assert dict(zip(command[6::2], command[7::2], strict=True)) == {
            "--dataset": str(state.dataset),
            "--harbor": str(state.harbor),
            "--cases": "2/4,3/4,4/4,5/4",
            "--modes": "full,first8",
        }
        assert len(pass_fds) == 1 and start_new_session and cwd == state.runner.parent
        assert "OPENAI_API_KEY" not in env and "DOCKER_HOST" not in env
        with cohort._try_lock(state.lock) as competing:
            assert competing is None
        assert not fresh.exists()
        assert (state.root / ".claims/batch-004").is_dir()
        launched.append(fresh)
        _registration(fresh)
        stdout.write(b"native batch log\n")
        yield SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(cohort.subprocess, "Popen", launch)
    assert cohort.run(state) == 0
    assert cohort.run(state) == 0
    assert launched == [fresh]
    assert checks == [prefilled, prefilled, fresh]
    assert (old / "untouched").read_text() == "peer result"
    assert (
        state.root / ".claims/batch-004/run.log"
    ).read_text() == "native batch log\n"


def test_failed_claim_stops_and_explicit_continue_never_retries(
    cohort, state, monkeypatch
):
    _clock(cohort, monkeypatch)
    launches = []

    def launch(runner, dataset, harbor, out, claim, index, fd):
        launches.append(index)
        claim.mkdir()
        return int(index == 3)

    monkeypatch.setattr(cohort, "_launch", launch)
    assert cohort.run(state) == 1
    assert cohort.run(state) == 1
    assert launches == [3]
    state.continue_on_failure = True
    assert cohort.run(state) == 1
    assert launches == [3, 4]
    assert (state.root / ".claims/batch-003").is_dir()


def test_batch_over_budget_stops_even_when_continue_enabled(cohort, state, monkeypatch):
    elapsed = iter([0, 151])
    _clock(cohort, monkeypatch, monotonic=lambda: next(elapsed))
    launches = []

    def launch(runner, dataset, harbor, out, claim, index, fd):
        launches.append(index)
        claim.mkdir()
        return 0

    monkeypatch.setattr(cohort, "_launch", launch)
    state.continue_on_failure = True
    assert cohort.run(state) == 1
    assert launches == [3]


def test_collect_checks_exact_identity_global_uuid_and_missing_cells(
    cohort, state, monkeypatch
):
    smoke = state.root.parent / "smoke"
    peer = state.root.parent / "peer-batch-002"
    wrong = state.root / "batch-004"
    claim = state.root / ".claims/batch-003"
    claim.mkdir(parents=True)
    for path in (smoke, peer, wrong):
        _registration(path)
    smoke_rows, peer_rows, wrong_rows = (_rows(cohort, index) for index in (1, 2, 4))
    peer_rows[0].update(status="failed", native_id=UUID(smoke_rows[0]["native_id"]).hex)
    peer_rows[0].pop("observed")
    peer_rows[1]["observed"].update(
        choice=22, physically_feasible=True, protocol_match=False
    )
    peer_rows[2].update(status="failed")
    peer_rows[2].pop("observed")
    wrong_rows[0]["mode"] = (
        "first8"  # Same count, duplicate identity, missing full cell.
    )
    called = _fake_study_collect(
        cohort,
        monkeypatch,
        state.runner,
        {smoke: smoke_rows, peer: peer_rows[::-1], wrong: wrong_rows},
    )
    report = cohort.collect(state.runner, state.root, smoke, peer)
    assert called == [smoke, peer, wrong]
    assert report["planned"] == 400
    assert report["missing"] == {
        "total": 387,
        "unattempted": 368,
        "incomplete": 8,
        "failed": 1,
        "invalid": 10,
    }
    assert report["summary"]["full"]["choice_agreement"] == {
        "passes": 5,
        "denominator": 5,
    }
    first8 = report["summary"]["first8"]
    assert first8["choice_agreement"] == {"passes": 7, "denominator": 8}
    assert first8["official_exact_match"] == {"passes": 0, "denominator": 8}
    assert first8["scheduled_official_exact_match"] == {"passes": 0, "denominator": 1}
    assert first8["physically_feasible"] == {"passes": 1, "denominator": 1}
    assert first8["no_choice"] == 7
    duplicate = (report["cells"][0], report["cells"][8])
    assert all(
        row["status"] == "invalid" and "observed" not in row for row in duplicate
    )
    assert {warning["reason"] for warning in report["warnings"]} == {
        "duplicate native trial UUID",
        "native decision or reports differ from source",
    }
    assert report["cells"][-1]["case_id"] == "4/60"


def test_official_match_counts_verified_null_but_not_missing(cohort):
    rows = [row for row in _rows(cohort, 1) if row["mode"] == "first8"]
    rows[0]["observed"].update(
        choice=20, official_exact_match=True, physically_feasible=True
    )
    rows[-1] = {"status": "unattempted"}
    summary = cohort._mode_summary(rows)
    assert summary["official_exact_match"] == {"passes": 1, "denominator": 3}
    assert summary["scheduled_official_exact_match"] == {"passes": 1, "denominator": 1}
    assert summary["physically_feasible"] == {"passes": 1, "denominator": 1}
    assert summary["no_choice"] == 2
    assert summary["missing"] == 1


def test_collect_does_not_shadow_an_existing_batch_two(cohort, state):
    smoke = state.root.parent / "smoke"
    peer = state.root.parent / "peer"
    for path in (smoke, peer, state.root / "batch-002"):
        _registration(path)
    with pytest.raises(ValueError, match="both local and external inputs.*batch-002"):
        cohort.collect(state.runner, state.root, smoke, peer)
