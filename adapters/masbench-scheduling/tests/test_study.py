"""Study selection, registration, and receipt boundaries without a runtime."""

import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
CASE_IDS = [f"{stratum}/{i}" for stratum in range(2, 6) for i in range(1, 51)]
TIMING = {"started_at": "2026-11-19T16:00:00Z", "finished_at": "2026-11-19T16:00:01Z"}


@pytest.fixture
def study(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("study")


def _case(study, case_id, *, gold=(10, 23)):
    return {
        "schema": "seatlab.calendar-case/v1",
        "dataset_commit": study.DATASET_COMMIT,
        "case_id": case_id,
        "horizon": 48,
        "slots_per_day": 24,
        "duration": 2,
        "gold": list(gold),
        "members": {
            seat: list(range(10)) if seat == "m2" else [] for seat in study.SEATS
        },
        "source_sha256": {"task_plans": "a" * 64},
    }


def _assert_no_launch(*args, **kwargs):
    pytest.fail("the study executed an external command")


def _register(study, out, case_ids, modes=("full", "first8")):
    cases = {case_id: _case(study, case_id) for case_id in case_ids}
    plan = study._plan(uuid4().hex, cases, list(modes), "1/1", len(cases))
    (out / "cells").mkdir(parents=True)
    study._write_once(
        out / "source.json",
        {
            "dataset_path": "/private/nonexistent/original",
            "dataset_commit": study.DATASET_COMMIT,
            "cases": cases,
        },
    )
    study._write_once(out / "plan.json", plan)
    return plan, cases


def _world(study, case, mode):
    reports, choice = study._expected(case, mode)
    initial_views = {
        seat: {
            "role": seat,
            "mode": mode,
            "busy": busy,
            **{key: case[key] for key in ("horizon", "slots_per_day", "duration")},
        }
        for seat, busy in case["members"].items()
    }
    return {
        "schema": "seatlab.calendar-world/v1",
        "case_id": case["case_id"],
        "mode": mode,
        "initial_view_sha256": {
            seat: study._sha(study._canonical(view))
            for seat, view in initial_views.items()
        },
        "reports": reports,
        "proposal_sha256": study._sha(
            study._canonical(
                {
                    "role": "m1",
                    "mode": mode,
                    "reports": reports,
                }
            )
        ),
        "choice": choice,
        "acknowledged": True,
    }


def _result(study, root, native, cell, identifier, reward):
    config = study._native_config(root / "task", native, cell)
    config.update(
        install_only=False,
        source_trial=None,
        user_agent=None,
        verifier={"disable": False},
    )
    config["environment"]["import_path"] = None
    for participant in config["participants"]:
        participant.update(
            dict.fromkeys(
                (
                    "import_path",
                    "model_name",
                    "base_url",
                    "model_api_key_name",
                    "model_api_format",
                )
            )
        )
    agent = {"name": "oracle", "model_info": None}
    return {
        **TIMING,
        "schema_version": 1,
        "id": identifier,
        "trial_name": cell["trial_name"],
        "trial_uri": native.as_uri(),
        "agent_info": agent,
        "config": config,
        "exception_info": None,
        "verifier": TIMING,
        "verifier_environment_mode": "shared",
        "verifier_result": {"rewards": {"reward": reward}},
        "seat_results": [
            {
                "seat": seat,
                "under_test": True,
                "agent_info": agent,
                "agent_execution": TIMING,
                "exception_info": None,
            }
            for seat in study.SEATS
        ],
    }


def _attempt(study, out, cell, case, *, identifier=None, returncode=0, modify=None):
    root, native = study._paths(out, cell)
    root.mkdir()
    if returncode == 0:
        world = _world(study, case, cell["mode"])
        reward = int(world["choice"] in case["gold"])
        result = _result(study, root, native, cell, identifier or str(uuid4()), reward)
        if modify:
            modify(world, result)
        data = {
            "world": study._canonical(world),
            "result": study._canonical(result),
            "reward": f"{reward}\n".encode(),
        }
        for key, contents in data.items():
            path = native / study.NATIVE_FILES[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
    else:
        data = {}
    study._write_once(
        root / "attempt.json",
        {
            "schema": study.ATTEMPT_SCHEMA,
            "trial_name": cell["trial_name"],
            "case_sha256": study._sha(study._canonical(case)),
            "returncode": returncode,
            "sha256": {
                key: hashlib.sha256(contents).hexdigest()
                for key, contents in data.items()
            },
        },
    )
    return native


def test_shards_cover_all_ids_once_with_remainder_and_numeric_order(study):
    shards = [
        study._select("all", "full,first8", f"{i}/3", CASE_IDS[::-1])
        for i in range(1, 4)
    ]
    assert [len(ids) for ids, _, _ in shards] == [67, 67, 66]
    assert [case_id for ids, _, _ in shards for case_id in ids] == CASE_IDS
    assert all(modes == ["full", "first8"] and n == 200 for _, modes, n in shards)


@pytest.mark.parametrize(
    ("cases", "modes", "shard"),
    [
        ("2/1,2/1", "full", "1/1"),
        ("2/01", "full", "1/1"),
        ("../2/1", "full", "1/1"),
        ("", "full", "1/1"),
        ("2/1", "other", "1/1"),
        ("2/1", "full,full", "1/1"),
        ("2/1", "full", "0/1"),
        ("2/1", "full", "2/1"),
        ("2/1", "full", "1/2"),
    ],
)
def test_invalid_selection_is_rejected(study, cases, modes, shard):
    with pytest.raises(ValueError):
        study._select(cases, modes, shard, CASE_IDS)


def test_source_rejects_wrong_pin_dirty_inputs_and_incomplete_corpus(
    study, monkeypatch, tmp_path
):
    dataset = tmp_path.resolve()
    checkout = {"revision": "wrong revision", "changed": "Scheduling/modified.json"}

    def command(args):
        if args[-1] == "--show-toplevel":
            return str(dataset)
        if args[-1] == "HEAD":
            return checkout["revision"]
        return checkout["changed"]

    monkeypatch.setattr(study, "_command", command)
    monkeypatch.setattr(study, "list_cases", lambda _dataset: CASE_IDS[:-1])
    with pytest.raises(ValueError, match=study.DATASET_COMMIT):
        study._source_case_ids(dataset)
    checkout["revision"] = study.DATASET_COMMIT
    with pytest.raises(ValueError, match="differ from the pinned revision"):
        study._source_case_ids(dataset)
    checkout["changed"] = ""
    with pytest.raises(ValueError, match="200 unique original cases"):
        study._source_case_ids(dataset)


def test_audit_counts_source_disagreement_feasibility_and_null_separately(
    study, monkeypatch, tmp_path
):
    monkeypatch.setattr(study, "_source_case_ids", lambda _dataset: CASE_IDS)
    monkeypatch.setattr(
        study,
        "load_case",
        lambda _dataset, case_id: _case(study, case_id, gold=(11, 23)),
    )
    monkeypatch.setattr(study.subprocess, "run", _assert_no_launch)
    report = study.audit(tmp_path)
    assert report["reference_agreement"] == {"matches": 0, "denominator": 200}
    assert report["reference_cases"][0]["only_in_reference"] == [23]
    assert len(report["cells"]) == 400
    assert "observed" not in report["cells"][0]
    assert report["summary"]["full"]["official_exact_match"] == {
        "passes": 0,
        "denominator": 200,
    }
    assert report["summary"]["full"]["physically_feasible"] == {
        "passes": 200,
        "denominator": 200,
    }
    assert report["summary"]["first8"]["physically_feasible"] == {
        "passes": 0,
        "denominator": 0,
    }
    assert report["summary"]["first8"]["no_choice"] == 200


def test_collect_is_portable_read_only_and_counts_each_planned_cell(
    study, monkeypatch, tmp_path
):
    out = tmp_path / "study"
    plan, cases = _register(study, out, ["2/1", "2/2", "2/3"])
    full, first8, failed, incomplete, tampered, unattempted = plan["cells"]
    for cell in (full, first8, tampered):
        native = _attempt(study, out, cell, cases[cell["case_id"]])
    _attempt(study, out, failed, cases[failed["case_id"]], returncode=1)
    study._paths(out, incomplete)[0].mkdir()
    (native / study.NATIVE_FILES["reward"]).write_text("0\n")
    assert not study._paths(out, unattempted)[0].exists()

    moved = out.rename(tmp_path / "moved")
    monkeypatch.setattr(study.subprocess, "run", _assert_no_launch)
    before = {
        p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()
    }
    report = study.collect(moved)
    assert study.collect(moved) == report
    assert before == {
        p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()
    }
    assert str(tmp_path) not in json.dumps(report)
    assert "/private/nonexistent" not in json.dumps(report)
    assert [row["status"] for row in report["cells"]] == [
        "verified",
        "verified",
        "failed",
        "incomplete",
        "invalid",
        "unattempted",
    ]
    observed = report["cells"][0]["observed"]
    assert observed["choice"] == 10 and observed["reward"] == 1
    assert observed["protocol_match"] is True
    assert set(observed["initial_view_sha256"]) == set(study.SEATS)
    assert (
        observed["reports_sha256"]
        == report["cells"][0]["source_expected"]["reports_sha256"]
    )
    assert report["cells"][1]["observed"]["choice"] is None
    assert report["summary"]["full"]["planned"] == 3
    assert report["summary"]["full"]["official_exact_match"] == {
        "passes": 1,
        "denominator": 1,
    }
    assert report["summary"]["first8"]["no_choice"] == 1


def test_native_protocol_mismatch_does_not_change_independent_scores(study, tmp_path):
    out = tmp_path / "study"
    plan, cases = _register(study, out, ["2/1"], ("full",))
    _attempt(
        study,
        out,
        plan["cells"][0],
        cases["2/1"],
        modify=lambda world, _result: world.update(choice=23),
    )
    row = study.collect(out)["cells"][0]
    assert row["status"] == "verified"
    assert row["source_expected"]["choice"] == 10
    assert row["observed"]["choice"] == 23
    assert row["observed"]["reward"] == 1
    assert row["observed"]["official_exact_match"] is True
    assert row["observed"]["physically_feasible"] is False
    assert row["observed"]["protocol_match"] is False


def test_duplicate_native_id_invalidates_every_claim(study, tmp_path):
    out = tmp_path / "study"
    plan, cases = _register(study, out, ["2/1"])
    identifier = str(uuid4())
    for cell in plan["cells"]:
        _attempt(study, out, cell, cases[cell["case_id"]], identifier=identifier)
    for row in study.collect(out)["cells"]:
        assert row["status"] == "invalid"
        assert row["reason"] == "duplicate native trial UUID"
        assert row["native_id"] == identifier
        assert "observed" not in row


@pytest.mark.parametrize(
    ("modify", "reason"),
    [
        (
            lambda world, _result: world.update(proposal_sha256="0" * 64),
            "invalid world evidence",
        ),
        (
            lambda _world, result: result["seat_results"].pop(),
            "invalid or missing native result",
        ),
        (
            lambda _world, result: result["verifier_result"].update(
                rewards={"reward": 0}
            ),
            "native reward mismatch",
        ),
    ],
)
def test_receipts_with_consistent_file_hashes_still_need_valid_evidence(
    study, tmp_path, modify, reason
):
    out = tmp_path / "study"
    plan, cases = _register(study, out, ["2/1"], ("full",))
    cell = plan["cells"][0]
    _attempt(study, out, cell, cases[cell["case_id"]], modify=modify)
    row = study.collect(out)["cells"][0]
    assert row["status"] == "invalid" and row["reason"] == reason


def test_collect_rejects_source_snapshot_changes(study, tmp_path):
    out = tmp_path / "study"
    _plan, _cases = _register(study, out, ["2/1"])
    path = out / "source.json"
    source = json.loads(path.read_text())
    source["cases"]["2/1"]["gold"] = [23]
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="plan and source snapshot do not match"):
        study.collect(out)


def test_stop_preserves_registration_and_does_not_restart(study, monkeypatch, tmp_path):
    harbor = tmp_path / "harbor"
    harbor.write_text("unused executable")
    harbor.chmod(0o700)
    out = tmp_path / "study"
    started = []

    def stop_after_cell(_dataset, _harbor, out, cell, _case_hash):
        assert (out / "plan.json").is_file()
        started.append((cell["case_id"], cell["mode"]))
        study._paths(out, cell)[0].mkdir()
        (out / "STOP").touch()

    monkeypatch.setattr(study, "_source_case_ids", lambda _dataset: CASE_IDS)
    monkeypatch.setattr(
        study, "load_case", lambda _dataset, case_id: _case(study, case_id)
    )
    monkeypatch.setattr(study, "_ready", lambda: None)
    monkeypatch.setattr(study, "_run_cell", stop_after_cell)
    monkeypatch.setattr(study.subprocess, "run", _assert_no_launch)
    result = study.run(tmp_path, out, harbor, "2/2,2/1", "full,first8")
    assert started == [("2/1", "full")]
    assert [row["status"] for row in result["cells"]] == [
        "incomplete",
        "unattempted",
        "unattempted",
        "unattempted",
    ]
    assert out.stat().st_mode & 0o077 == 0
    for path in (tmp_path, harbor):
        assert str(path) not in (out / "plan.json").read_text()
    with pytest.raises(ValueError, match="new directory"):
        study.run(tmp_path, out, harbor, "all", "full,first8")
    assert study.collect(out) == result
    assert started == [("2/1", "full")]


@pytest.mark.parametrize("changed", [False, True])
def test_runner_checks_materialized_input_before_any_native_start(
    study, monkeypatch, tmp_path, changed
):
    out = tmp_path / "study"
    plan, cases = _register(study, out, ["2/1"], ("full",))
    cell = plan["cells"][0]
    case_hash = plan["case_hashes"][cell["case_id"]]
    root, native = study._paths(out, cell)
    commands = []

    def materialize(_dataset, case_id, mode, task):
        (task / "environment").mkdir(parents=True)
        case = {**cases[case_id], "gold": []} if changed else cases[case_id]
        study._write_once(
            task / "environment" / "case.json", {"mode": mode, "case": case}
        )
        return task

    def invoke(args, **_kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=1)

    monkeypatch.setitem(
        sys.modules, "materialize", SimpleNamespace(materialize=materialize)
    )
    monkeypatch.setattr(study.subprocess, "run", invoke)
    harbor = tmp_path / "harbor"
    if changed:
        with pytest.raises(ValueError, match="differs from registered input"):
            study._run_cell(tmp_path, harbor, out, cell, case_hash)
        assert not commands
        assert not (root / "attempt.json").exists()
    else:
        study._run_cell(tmp_path, harbor, out, cell, case_hash)
        config_path = root / "trial-config.json"
        assert commands == [
            [str(harbor), "trial", "start", "--config", str(config_path)]
        ]
        config = json.loads(config_path.read_text())
        assert config["trials_dir"] == str(native.parent)
        assert config["participants"] == [
            {"seat": seat, "name": "oracle"} for seat in study.SEATS
        ]
        assert config["environment"] == {
            "type": "docker",
            "delete": True,
            "force_build": False,
        }
        assert (
            json.loads((root / "attempt.json").read_text())["case_sha256"] == case_hash
        )
