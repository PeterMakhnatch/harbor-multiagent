"""Exercise driver boundaries with synthetic receipts and no Docker process."""

import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_study import _result as _base_result

ADAPTER = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("interactive")


@pytest.fixture
def reviewed(driver):
    return {name: (ADAPTER / name).read_bytes() for name in driver.REVIEWED_FILES}


def _case(driver, case_id, *, null=False):
    return {
        "schema": "seatlab.calendar-case/v1",
        "dataset_commit": driver.DATASET_COMMIT,
        "case_id": case_id,
        "horizon": 720,
        "slots_per_day": 144,
        "duration": 2,
        "gold": [] if null else [2],
        "members": {
            **dict.fromkeys(driver.study.SEATS, []),
            "m1": [0, 1],
            "m2": list(range(720)) if null else [0],
        },
        "source_sha256": {"task_plans": "a" * 64},
    }


def _budget_case(driver):
    horizon = 16
    return {
        **_case(driver, "2/3"),
        "horizon": horizon,
        "slots_per_day": horizon,
        "duration": 1,
        "members": {
            **dict.fromkeys(driver.study.SEATS, []),
            "m1": sorted(set(range(horizon)) - {0, 2, 4, 6, 8, 10}),
            "m2": sorted(set(range(horizon)) - {1, 3, 5, 7, 9, 10}),
        },
        "gold": [10],
    }


def _register(driver, reviewed, out, cases):
    out.mkdir(mode=0o700)
    (out / "cells").mkdir(mode=0o700)
    source = {
        "out_path": str(out),
        "dataset_path": "/not/needed/to/collect",
        "dataset_commit": driver.DATASET_COMMIT,
        "policy": "cursor-budget-8",
        "round_limit": 8,
        "harbor_executable_path": "/not/needed/to/collect/harbor",
        "harbor_executable_sha256": "b" * 64,
        "reviewed_sha256": driver._digests(reviewed),
        "cases": cases,
    }
    hashes = {
        case_id: driver._digests(driver._task_payloads(case, reviewed))
        for case_id, case in cases.items()
    }
    plan = driver._plan(uuid4().hex, source, cases, hashes)
    driver.study._write_once(out / "source.json", source)
    driver.study._write_once(out / "plan.json", plan)
    return plan


def _world(driver, case, *, budget=False):
    """Encode a fixed transcript, without the actor, broker, or offline cursor."""
    canonical = driver.study._canonical

    def digest(value):
        return driver.study._sha(canonical(value))

    null = len(case["members"]["m2"]) == case["horizon"]
    views = {
        role: digest(
            {
                "role": role,
                "mode": "cursor",
                "horizon": case["horizon"],
                "slots_per_day": case["slots_per_day"],
                "duration": case["duration"],
                "busy": busy,
            }
        )
        for role, busy in case["members"].items()
    }
    if budget:
        payloads = [
            (
                bound,
                {
                    **dict.fromkeys(driver.study.SEATS, bound),
                    "m1": bound + bound % 2,
                    "m2": bound + (bound + 1) % 2,
                },
            )
            for bound in range(8)
        ]
    else:
        replies = {"m1": 2, "m2": None if null else 1, "m3": 0, "m4": 0, "m5": 0}
        payloads = [(0, replies)]
        if not null:
            payloads.append((2, dict.fromkeys(driver.study.SEATS, 2)))
    rounds = []
    for index, (bound, replies) in enumerate(payloads, 1):
        query = {"round": index, "bound": bound}
        bundle = {**query, "replies": replies}
        rounds.append(
            {**bundle, "query_sha256": digest(query), "bundle_sha256": digest(bundle)}
        )
    return {
        "schema": "seatlab.calendar-interactive-world/v1",
        "case_id": case["case_id"],
        "mode": "cursor",
        "initial_view_sha256": views,
        "rounds": rounds,
        "choice": None if null or budget else 2,
        "choice_view_sha256": rounds[-1]["bundle_sha256"],
        "acknowledged": True,
    }


def _record_attempt(
    driver,
    reviewed,
    out,
    plan,
    case,
    *,
    identifier=None,
    returncode=0,
    modify=None,
    world=None,
):
    case_id = case["case_id"]
    cell = next(row for row in plan["cells"] if row["case_id"] == case_id)
    root, native = driver.study._paths(out, cell)
    root.mkdir()
    task = root / "task"
    for name, contents in driver._task_payloads(case, reviewed).items():
        path = task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755 if name.endswith(".sh") else 0o644)
    driver.study._write_once(
        root / "trial-config.json", driver.study._native_config(task, native, cell)
    )
    files = {}
    if returncode == 0:
        if world is None:
            world = _world(driver, case)
        reward = int(world["choice"] in case["gold"])
        result = _base_result(
            driver.study, root, native, cell, identifier or str(uuid4()), reward
        )
        verify_start = result["verifier"]["finished_at"]
        result["verifier"] = {"started_at": verify_start, "finished_at": verify_start}
        if modify:
            modify(world, result)
        files = {
            "result": driver.study._canonical(result),
            "world": driver.study._canonical(world),
            "reward": f"{reward}\n".encode("ascii"),
        }
        for key, contents in files.items():
            path = native / driver.NATIVE_FILES[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
    driver.study._write_once(
        root / "attempt.json",
        {
            "schema": driver.ATTEMPT_SCHEMA,
            "trial_name": cell["trial_name"],
            "returncode": returncode,
            "task_sha256": driver.study._sha(
                driver.study._canonical(plan["task_sha256"][case_id])
            ),
            "native_sha256": driver._digests(files),
        },
    )
    return root, native


def _never_launch(*args, **kwargs):
    pytest.fail("collector tried to start an external process")


def test_collect_distinguishes_verified_null_failed_incomplete_and_unattempted(
    driver, reviewed, tmp_path, monkeypatch
):
    out = tmp_path / "private"
    cases = {
        case_id: _case(driver, case_id, null=case_id == "2/2")
        for case_id in ("3/2", "2/10", "2/2", "2/1", "5/1")
    }
    plan = _register(driver, reviewed, out, cases)
    for case_id in ("2/1", "2/2", "2/10"):
        _record_attempt(
            driver,
            reviewed,
            out,
            plan,
            cases[case_id],
            returncode=int(case_id == "2/10"),
        )
    (out / "cells" / "3-2-cursor").mkdir()
    moved = tmp_path / "existing-receipts"
    out.rename(moved)
    monkeypatch.setattr(driver.subprocess, "run", _never_launch)
    report = driver.collect(moved)
    assert set(report) == {
        "schema",
        "study_id",
        "mode",
        "policy",
        "round_limit",
        "summary",
        "by_stratum",
        "cells",
    }
    assert report["schema"] == "seatlab.calendar-interactive-result/v1"
    assert report["mode"] == "cursor"
    assert report["policy"] == "cursor-budget-8"
    assert report["round_limit"] == 8
    assert list(report["by_stratum"]) == ["2", "3", "5"]
    assert report["summary"] == {
        "planned": 5,
        "verified": 2,
        "unattempted": 1,
        "incomplete": 1,
        "failed": 1,
        "invalid": 0,
        "measured_nulls": 1,
        "terminations": {"exhausted": 1, "unanimous": 1, "budget": 0},
        "official_exact_matches": 1,
        "physically_feasible": 1,
        "rounds": 3,
        "scalar_disclosures": 14,
    }
    first, null, failed, incomplete, unattempted = report["cells"]
    assert [row["case_id"] for row in report["cells"]] == [
        "2/1",
        "2/2",
        "2/10",
        "3/2",
        "5/1",
    ]
    assert first["observed"]["choice"] == 2
    assert first["observed"]["termination"] == "unanimous"
    assert first["observed"]["resources"]["seat_exchanges"] == 10
    assert null["observed"]["choice"] is None
    assert null["observed"]["termination"] == "exhausted"
    assert null["observed"]["physically_feasible"] is None
    assert null["observed"]["reward"] == 0
    assert first["native_id"] != null["native_id"]
    assert failed["status"] == "failed" and set(failed) == {
        "case_id",
        "status",
        "reason",
    }
    assert incomplete == {"case_id": "3/2", "status": "incomplete"}
    assert unattempted == {"case_id": "5/1", "status": "unattempted"}
    assert "/not/needed" not in json.dumps(report)


@pytest.mark.parametrize("remove_choice", [False, True])
def test_collect_scores_only_an_explicit_budget_null(
    driver, reviewed, tmp_path, remove_choice
):
    out = tmp_path / "private"
    case = _budget_case(driver)
    plan = _register(driver, reviewed, out, {case["case_id"]: case})
    world = _world(driver, case, budget=True)

    def modify(world, _result):
        if remove_choice:
            del world["choice"]

    _record_attempt(driver, reviewed, out, plan, case, modify=modify, world=world)
    report = driver.collect(out)
    row = report["cells"][0]
    if remove_choice:
        assert row["status"] == "invalid"
        assert row["reason"] == "invalid interactive world evidence"
        assert "observed" not in row
    else:
        assert row["status"] == "verified"
        assert row["observed"]["choice"] is None
        assert row["observed"]["termination"] == "budget"
        assert row["observed"]["reward"] == 0
        assert row["observed"]["resources"]["round_trips"] == 8
    counts = {"exhausted": 0, "unanimous": 0, "budget": int(not remove_choice)}
    assert report["summary"]["terminations"] == counts
    assert report["by_stratum"]["2"]["terminations"] == counts


def test_collect_keeps_reference_mismatch_separate_from_feasibility(
    driver, reviewed, tmp_path
):
    out = tmp_path / "private"
    case = {**_case(driver, "2/1"), "gold": [3]}
    plan = _register(driver, reviewed, out, {"2/1": case})
    _record_attempt(driver, reviewed, out, plan, case)
    report = driver.collect(out)
    assert report["summary"]["verified"] == 1
    assert report["summary"]["official_exact_matches"] == 0
    assert report["summary"]["physically_feasible"] == 1
    assert report["cells"][0]["observed"]["choice"] == 2
    assert report["cells"][0]["observed"]["reward"] == 0


def test_collect_invalidates_duplicate_native_identifiers(driver, reviewed, tmp_path):
    out = tmp_path / "private"
    cases = {case_id: _case(driver, case_id) for case_id in ("2/1", "2/2")}
    plan = _register(driver, reviewed, out, cases)
    identifier = uuid4()
    for case, value in zip(
        cases.values(), (str(identifier), identifier.hex), strict=True
    ):
        _record_attempt(driver, reviewed, out, plan, case, identifier=value)
    report = driver.collect(out)
    assert report["summary"]["invalid"] == 2
    for row in report["cells"]:
        assert row["native_id"] == str(identifier)
        assert row["status"] == "invalid"
        assert row["reason"] == "duplicate native trial UUID"
        assert "observed" not in row


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("world", "invalid interactive world evidence"),
        ("seats", "invalid or missing native result"),
        ("stale", "invalid or missing native result"),
        ("reward", "native reward mismatch"),
    ],
)
def test_resealed_evidence_must_still_validate(
    driver, reviewed, tmp_path, target, reason
):
    out = tmp_path / "private"
    case = _case(driver, "2/1")
    plan = _register(driver, reviewed, out, {"2/1": case})

    def modify(world, result):
        if target == "world":
            world["choice"] = 3
        elif target == "seats":
            result["seat_results"].pop()
        elif target == "stale":
            result["seat_results"][0]["agent_execution"] = {
                "started_at": "2025-11-19T16:00:00Z",
                "finished_at": "2025-11-19T16:00:01Z",
            }
        else:
            result["verifier_result"]["rewards"]["reward"] = 0

    _record_attempt(driver, reviewed, out, plan, case, modify=modify)
    row = driver.collect(out)["cells"][0]
    assert row["status"] == "invalid" and row["reason"] == reason
    assert "observed" not in row


@pytest.mark.parametrize(
    "change",
    ["task", "filemode", "extra", "symlink", "wrong-artifact", "receipt", "fifo"],
)
def test_rejects_changed_task_or_raw_evidence(driver, reviewed, tmp_path, change):
    out = tmp_path / "private"
    case = _case(driver, "2/1")
    plan = _register(driver, reviewed, out, {"2/1": case})
    root, native = _record_attempt(driver, reviewed, out, plan, case)
    task = root / "task"
    world = native / driver.NATIVE_FILES["world"]
    if change == "task":
        (task / "solution/client.py").write_text("modified")
    elif change == "filemode":
        (task / "solution/solve.sh").chmod(0o644)
    elif change == "extra":
        (task / "solution/extra.json").write_text("{}")
    elif change == "symlink":
        target = world.rename(world.with_suffix(".original"))
        world.symlink_to(target)
    elif change == "wrong-artifact":
        old = native / driver.study.NATIVE_FILES["world"]
        old.parent.mkdir(parents=True)
        world.rename(old)
    elif change == "fifo":
        world.unlink()
        os.mkfifo(world)
    else:
        receipt = root / "attempt.json"
        receipt.write_text('{"returncode":0,"returncode":1}')
    row = driver.collect(out)["cells"][0]
    assert row["status"] == "invalid" and "observed" not in row


def test_rejects_source_snapshot_changes(driver, reviewed, tmp_path):
    out = tmp_path / "private"
    case = _case(driver, "2/1")
    _register(driver, reviewed, out, {"2/1": case})
    path = out / "source.json"
    source = json.loads(path.read_bytes())
    source["cases"]["2/1"]["gold"] = []
    path.write_bytes(driver.study._canonical(source))
    with pytest.raises(ValueError, match="source snapshot"):
        driver.collect(out)


def test_collect_requires_the_registered_verifier_bytes(
    driver, reviewed, tmp_path, monkeypatch
):
    out = tmp_path / "private"
    case_id = "2/1"
    _register(driver, reviewed, out, {case_id: _case(driver, case_id)})
    changed_source = tmp_path / "modified-source"
    changed_source.mkdir()
    (changed_source / "interactive_verify.py").write_bytes(
        reviewed["interactive_verify.py"] + b"\n"
    )
    monkeypatch.setattr(driver, "ADAPTER", changed_source)
    with pytest.raises(ValueError, match="local independent verifier differs"):
        driver.collect(out)


@pytest.mark.parametrize(
    ("file", "key", "value"),
    [
        ("source", "policy", "unbounded"),
        ("source", "round_limit", 9),
        ("source", "round_limit", 8.0),
        ("plan", "policy", "unbounded"),
        ("plan", "round_limit", 9),
        ("plan", "round_limit", 8.0),
    ],
)
def test_registration_refuses_resealed_policy_changes(
    driver, reviewed, tmp_path, file, key, value
):
    out = tmp_path / "private"
    case_id = "2/1"
    plan = _register(driver, reviewed, out, {case_id: _case(driver, case_id)})
    source_path = out / "source.json"
    source = json.loads(source_path.read_bytes())
    if file == "source":
        source[key] = value
        source_path.write_bytes(driver.study._canonical(source))
        plan = driver._plan(plan["study_id"], source, [case_id], plan["task_sha256"])
    else:
        plan[key] = value
    (out / "plan.json").write_bytes(driver.study._canonical(plan))
    with pytest.raises(ValueError, match="source snapshot"):
        driver.collect(out)


def test_run_uses_explicit_order_and_stops_before_next_trial(
    driver, tmp_path, monkeypatch
):
    harbor = tmp_path / "harbor"
    harbor.write_text("not executed in tests")
    harbor.chmod(0o700)
    out = tmp_path / "private"
    materialize = importlib.import_module("materialize")
    order = ["3/2", "2/10", "2/2"]
    created = []

    def load(_dataset, case_id):
        return _case(driver, case_id)

    def start(args, *, cwd, env, **_kwargs):
        created.append(args)
        plan = json.loads((out / "plan.json").read_bytes())
        source = json.loads((out / "source.json").read_bytes())
        for registration in (source, plan):
            assert registration["policy"] == "cursor-budget-8"
            assert type(registration["round_limit"]) is int
            assert registration["round_limit"] == 8
        assert plan["mode"] == "cursor"
        assert plan["source_sha256"] == driver.study._sha(
            driver.study._canonical(source)
        )
        assert args[:4] == [str(harbor.resolve()), "trial", "start", "--config"]
        config = json.loads(Path(args[4]).read_bytes())
        assert config["participants"] == [
            {"seat": seat, "name": "oracle"} for seat in driver.study.SEATS
        ]
        assert config["environment"] == {
            "type": "docker",
            "force_build": False,
            "delete": True,
        }
        assert "OPENAI_API_KEY" not in env
        task = cwd / "task"
        assert cwd.name == "3-2-cursor"
        assert (task / "environment/calendar_base.py").read_bytes() == (
            ADAPTER / "template/environment/calendar_world.py"
        ).read_bytes()
        assert (task / "environment/calendar_world.py").read_bytes() == (
            ADAPTER / "interactive_world.py"
        ).read_bytes()
        for staged, original in (
            ("solution/client.py", "interactive_client.py"),
            ("tests/verify.py", "interactive_verify.py"),
            ("solution/solve.sh", "template/solution/solve.sh"),
        ):
            payload = (task / staged).read_bytes()
            assert payload == (ADAPTER / original).read_bytes()
            assert (
                driver.study._sha(payload)
                == source["reviewed_sha256"][original]
                == plan["task_sha256"]["3/2"][staged]
            )
        assert (
            b"COPY calendar_world.py calendar_base.py case.json ./"
            in (task / "environment/Dockerfile").read_bytes()
        )
        assert "/logs/world/calendar.json" not in (task / "task.toml").read_text()
        for seat in driver.study.SEATS:
            instructions = (task / f"instructions/{seat}.md").read_text()
            assert f"You are `{seat}`" in instructions
        assert (task / "environment/case.json").read_bytes() == (
            driver.study._canonical({"mode": "cursor", "case": load(tmp_path, "3/2")})
        )
        assert not any(
            path.name == "case.json" for path in (task / "solution").iterdir()
        )
        (out / "STOP").touch()
        return SimpleNamespace(returncode=1)

    monkeypatch.setenv("OPENAI_API_KEY", "not-for-subprocess")
    monkeypatch.setattr(
        driver.study, "_source_case_ids", lambda _dataset: sorted(order)
    )
    monkeypatch.setattr(driver, "load_case", load)
    monkeypatch.setattr(materialize, "load_case", load)
    monkeypatch.setattr(driver, "_ready", lambda _env: None)
    monkeypatch.setattr(driver.subprocess, "run", start)
    report = driver.run(tmp_path, out, harbor, ",".join(order))
    assert len(created) == 1
    assert [row["case_id"] for row in report["cells"]] == ["2/2", "2/10", "3/2"]
    assert [row["status"] for row in report["cells"]] == [
        "unattempted",
        "unattempted",
        "failed",
    ]
    with pytest.raises(ValueError, match="new directory"):
        driver.run(tmp_path, out, harbor, ",".join(order))
    assert driver.collect(out) == report


@pytest.mark.parametrize("link", [False, True])
def test_missing_or_linked_reviewed_scripts_never_admit(
    driver, reviewed, tmp_path, monkeypatch, link
):
    source = tmp_path / "review"
    source.mkdir()
    for name, contents in reviewed.items():
        if name == "interactive_world.py":
            continue
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    if link:
        (source / "interactive_world.py").symlink_to(ADAPTER / "interactive_world.py")
    monkeypatch.setattr(driver, "ADAPTER", source)
    monkeypatch.setattr(driver.study, "_source_case_ids", lambda _dataset: ["2/1"])
    monkeypatch.setattr(driver.subprocess, "run", _never_launch)
    out = tmp_path / "private"
    harbor = tmp_path / "harbor"
    harbor.touch(mode=0o700)
    with pytest.raises(
        (FileNotFoundError, ValueError), match="interactive_world.py|symlink"
    ):
        driver.run(tmp_path, out, harbor, "2/1")
    assert not out.exists()
