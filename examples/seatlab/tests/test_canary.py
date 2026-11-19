import fcntl
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    canary = importlib.import_module("canary")

START = datetime(2026, 11, 19, 9, 15, tzinfo=UTC)


@pytest.fixture
def schedule(tmp_path, monkeypatch):
    out = tmp_path / "study"
    runner = tmp_path / "run_panel.py"
    runner.write_text("# Frozen test runner\n", encoding="utf-8")
    args = [
        "run",
        "--out",
        str(out),
        "--harbor",
        sys.executable,
        "--runner",
        str(runner),
        "--start",
        canary.stamp(START),
        "--slots",
        "2",
        "--interval-seconds",
        "3600",
        "--window-seconds",
        "900",
        "--seed",
        "1009",
        "--condition",
        "reordered",
        "--episodes",
        "4",
        "--once",
    ]
    for key in ("SEATLAB_FREE_MODEL", "SEATLAB_FREE_PROVIDER", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    clock = SimpleNamespace(now=START)
    monkeypatch.setattr(canary, "now_utc", lambda: clock.now)

    def unexpected_sleep(seconds):
        pytest.fail(f"the test must not actually sleep ({seconds})")

    monkeypatch.setattr(canary, "time", SimpleNamespace(sleep=unexpected_sleep))
    monkeypatch.setattr(canary, "_group_active", lambda _: False)
    return out, runner, args, clock


def _raw(path):
    return path / "panel/cells/seed-1009--reordered/native/seatlab-test/result.json"


def _write_index(path, fingerprint="fixed-world"):
    (path / "panel/runs.json").write_text(
        json.dumps(
            {
                "schema": "seatlab.panel-runs/v1",
                "cells": [
                    {
                        "seed": 1009,
                        "condition": "reordered",
                        "status": "measured",
                        "evidence": {"fingerprint": fingerprint},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _fake_native(monkeypatch, after=None):
    launched = []

    def launch(out, plan, slot, arm, lock_fd, cutoff):
        path = canary._arm_dir(out, slot, arm)
        canary._record_decision(path, "attempted")
        launched.append((slot, arm))
        canary._create(
            path / "supervisor.json",
            {"group": 99999, "at": canary.stamp(canary.now_utc())},
        )
        raw = _raw(path)
        raw.parent.mkdir(parents=True)
        raw.write_text("{}", encoding="utf-8")
        _write_index(path)
        canary._create(path / "process.json", {"state": "exited", "returncode": 0})
        if after is not None:
            after(slot, arm)
        return True

    monkeypatch.setattr(canary, "_launch", launch)
    return launched


def _report(capsys):
    return json.loads(capsys.readouterr().out)


def test_window_boundary_and_permanent_decisions():
    assert canary.slot_phase(START - timedelta(microseconds=1), START, 900) == "future"
    assert canary.slot_phase(START, START, 900) == "due"
    assert canary.slot_phase(START + timedelta(seconds=899), START, 900) == "due"
    assert canary.slot_phase(START + timedelta(seconds=900), START, 900) == "missed"
    assert canary.pending_arms({"control": "attempted"}, False) == ("hosted",)
    assert canary.pending_arms({"hosted": "paused"}, False) == ("control",)
    assert canary.pending_arms({"control": "missed"}, True) == ()


def test_once_skips_old_slots_and_reuses_the_plan(schedule, monkeypatch, capsys):
    out, _, args, clock = schedule
    clock.now = START + timedelta(hours=1)
    launched = _fake_native(monkeypatch)
    assert canary.main(args) == 0
    report = _report(capsys)
    assert launched == [(2, "control"), (2, "hosted")]
    assert report["schema"] == "seatlab.canary-status/v1"
    assert report["slots"][0]["cutoff"] == "2026-11-19T09:30:00Z"
    assert report["slots"][1]["start"] == "2026-11-19T10:15:00Z"
    assert report["slots"][1]["arms"]["hosted"]["native_result"] == str(
        _raw(canary._arm_dir(out, 2, "hosted")).relative_to(out)
    )
    assert report["totals"]["hosted"] == {
        "planned": 2,
        "eligible": 2,
        "attempted": 1,
        "completed": 1,
        "unmeasured": 0,
        "missed": 1,
        "skipped": 0,
    }
    assert report["fingerprints_match"] is True
    frozen = (out / "plan.json").read_bytes()
    assert canary.main(args) == 0
    assert launched == [(2, "control"), (2, "hosted")]
    assert (out / "plan.json").read_bytes() == frozen


def test_once_leaves_future_slots_pending(schedule, monkeypatch, capsys):
    out, _, args, _ = schedule
    launched = _fake_native(monkeypatch)
    assert canary.main([*args, "--control-only"]) == 0
    assert launched == [(1, "control")]
    assert not (out / "slot-002").exists()
    report = _report(capsys)
    assert report["slots"][1]["arms"]["control"]["state"] == "pending"
    assert report["totals"]["hosted"]["planned"] == 2
    assert report["totals"]["hosted"]["eligible"] == 0
    assert report["totals"]["hosted"]["skipped"] == 2
    assert report["plan"]["hosted_route"]["model"].endswith(":free")


def test_live_slot_can_finish_after_its_admission_cutoff(schedule, monkeypatch):
    _, _, args, clock = schedule

    def after(slot, arm):
        if arm == "control":
            clock.now = START + timedelta(seconds=901)

    launched = _fake_native(monkeypatch, after)
    assert canary.main(args) == 0
    assert launched == [(1, "control"), (1, "hosted")]


def test_pause_persists_the_skipped_hosted_arm(schedule, monkeypatch, capsys):
    out, _, args, _ = schedule
    out.mkdir()
    marker = out / "PAUSE_HOSTED"
    marker.touch()
    launched = _fake_native(monkeypatch)
    assert canary.main(args) == 0
    assert marker.exists()
    report = _report(capsys)
    assert report["slots"][0]["arms"]["hosted"]["state"] == "paused"
    marker.unlink()
    assert canary.main(["status", "--out", str(out)]) == 0
    assert _report(capsys)["slots"][0]["arms"]["hosted"]["state"] == "paused"
    assert launched == [(1, "control")]


def test_stop_wakes_at_most_30_seconds_later(schedule, monkeypatch, capsys):
    out, _, args, clock = schedule
    clock.now = START - timedelta(minutes=2)
    launched = _fake_native(monkeypatch)
    sleeps = []

    def stop(seconds):
        sleeps.append(seconds)
        (out / "STOP").touch()

    monkeypatch.setattr(canary, "time", SimpleNamespace(sleep=stop))
    assert canary.main(args) == 0
    assert sleeps == [30]
    assert launched == []
    assert (out / "STOP").exists()
    assert _report(capsys)["totals"]["control"]["skipped"] == 2


def test_plan_rejects_paid_route_and_resume_mutations(schedule, monkeypatch, capsys):
    out, runner, args, _ = schedule
    launched = _fake_native(monkeypatch)
    monkeypatch.setenv("SEATLAB_FREE_MODEL", "owner/paid-model")
    assert canary.main(args) == 1
    assert ":free" in capsys.readouterr().err
    assert not (out / "plan.json").exists()
    monkeypatch.setenv("SEATLAB_FREE_MODEL", "owner/test-model:free")
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", "test-provider")
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-credential")
    assert canary.main(args) == 0
    report = _report(capsys)
    frozen = (out / "plan.json").read_text(encoding="utf-8")
    assert "not-a-real-credential" not in frozen
    assert "not-a-real-credential" not in json.dumps(report)
    assert report["plan"]["hosted_route"] == {
        "model": "owner/test-model:free",
        "provider": "test-provider",
    }
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", "other-provider")
    assert canary.main(args) == 1
    assert "immutable" in capsys.readouterr().err
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", "test-provider")
    runner.write_text("# Changed runner\n", encoding="utf-8")
    assert canary.main(args) == 1
    assert "immutable" in capsys.readouterr().err
    assert len(launched) == 2
    assert (out / "plan.json").read_text(encoding="utf-8") == frozen


def test_restart_collects_only_existing_raw_and_never_restarts_it(
    schedule, monkeypatch, capsys
):
    out, _, args, clock = schedule
    clock.now = START - timedelta(seconds=1)
    launched = _fake_native(monkeypatch)

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(canary, "time", SimpleNamespace(sleep=interrupt))
    assert canary.main([*args, "--control-only"]) == 130
    old = canary._arm_dir(out, 1, "control")
    canary._record_decision(old, "attempted")
    canary._create(old / "supervisor.json", {"group": 99999, "at": canary.stamp(START)})
    raw = _raw(old)
    raw.parent.mkdir(parents=True)
    raw.write_text('{"preserved":true}', encoding="utf-8")
    collection = []

    def collect(command, **kwargs):
        assert command[-3:] == ["--out", str(old / "panel"), "--collect"]
        assert kwargs["timeout"] == canary.COLLECT_TIMEOUT
        collection.append(command)
        _write_index(old)

    monkeypatch.setattr(canary.subprocess, "run", collect)
    clock.now = START + timedelta(hours=1)
    assert canary.main([*args, "--control-only"]) == 0
    assert launched == [(2, "control")]
    assert len(collection) == 1
    assert raw.read_text(encoding="utf-8") == '{"preserved":true}'
    assert _report(capsys)["totals"]["control"]["completed"] == 2
    assert canary.main([*args, "--control-only"]) == 0
    assert len(collection) == 1
    assert launched == [(2, "control")]


def test_live_orphan_blocks_later_attempts(schedule, monkeypatch, capsys):
    out, _, args, clock = schedule
    clock.now = START - timedelta(seconds=1)
    launched = _fake_native(monkeypatch)

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(canary, "time", SimpleNamespace(sleep=interrupt))
    assert canary.main([*args, "--control-only"]) == 130
    old = canary._arm_dir(out, 1, "control")
    canary._record_decision(old, "attempted")
    canary._create(old / "supervisor.json", {"group": 99999, "at": canary.stamp(START)})
    monkeypatch.setattr(canary, "_group_active", lambda _: True)
    clock.now = START + timedelta(hours=1)
    assert canary.main([*args, "--control-only"]) == 1
    assert "process group is still active" in capsys.readouterr().err
    assert not (old / "outcome.json").exists()
    assert launched == []


def test_commits_attempt_before_subprocess_and_never_overwrites_it(
    schedule, monkeypatch
):
    out, _, args, _ = schedule
    observed = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-control")

    def start(command, **kwargs):
        path = canary._arm_dir(out, 1, "control")
        assert canary._decision(path)["state"] == "attempted"
        assert (out / "plan.json").exists()
        assert command[0] == sys.executable
        assert kwargs["start_new_session"] is True
        assert len(kwargs["pass_fds"]) == 1
        assert "OPENROUTER_API_KEY" not in kwargs["env"]
        observed.append(path)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(canary.subprocess, "Popen", start)
    assert canary.main([*args, "--control-only"]) == 0
    attempt = observed[0] / "decision.json"
    before = attempt.read_bytes()
    assert canary._read(observed[0] / "outcome.json")["state"] == "unmeasured"
    with pytest.raises(FileExistsError):
        canary._record_decision(observed[0], "attempted")
    assert attempt.read_bytes() == before
    assert len(observed) == 1


def test_output_lock_blocks_writers_but_not_status(schedule, monkeypatch, capsys):
    out, _, args, _ = schedule
    launched = _fake_native(monkeypatch)
    assert canary.main([*args, "--control-only"]) == 0
    capsys.readouterr()
    with (out / canary.LOCK).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert canary.main([*args, "--control-only"]) == 1
        assert "owns this output" in capsys.readouterr().err
        assert canary.main(["status", "--out", str(out)]) == 0
        assert _report(capsys)["totals"]["control"]["completed"] == 1
    assert launched == [(1, "control")]


def test_refuses_existing_unowned_output(schedule, monkeypatch, capsys):
    out, _, args, _ = schedule
    out.mkdir()
    unrelated = out / "runs.json"
    unrelated.write_text("existing native panel", encoding="utf-8")
    launched = _fake_native(monkeypatch)
    assert canary.main(args) == 1
    assert "unrelated directory" in capsys.readouterr().err
    assert unrelated.read_text(encoding="utf-8") == "existing native panel"
    assert launched == []
