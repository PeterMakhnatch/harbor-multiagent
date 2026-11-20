import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from examples.seatlab.visibility import native_usage, pair_report

_CELL = pair_report._Cell(3101, 0, "fresh")
_PRIVATE_ID = str(UUID(int=1))


def _seal(path: Path, row: dict) -> None:
    path.write_text(json.dumps(row), encoding="utf-8")
    path.chmod(0o400)


def _trial(
    study: Path, cell: pair_report._Cell = _CELL, trial_id: str = _PRIVATE_ID
) -> SimpleNamespace:
    study = study.resolve()
    config = native_usage._config(study, cell)
    directory = study / "cells" / cell.cell_id
    trial = directory / "trials" / config["trial_name"]
    trial.mkdir(parents=True)
    marker = {
        **pair_report._expected(cell),
        "trial_name": config["trial_name"],
        "task_name": None,
        "trial_id": None,
        "native_returncode": None,
        "native_score": None,
        "outcome_valid": False,
        "error": "preparation_failed",
        "fingerprint": None,
        "actions": None,
        "seats": dict.fromkeys(pair_report._ROLES),
    }
    _seal(directory / "attempt.json", marker)
    _seal(directory / "receipt.json", {**marker, "trial_id": trial_id})
    _seal(directory / "config.json", config)
    (trial / "result.json").touch()
    return SimpleNamespace(
        path=trial,
        id=trial_id,
        config=config,
        primary_reward=0.0,
        result={
            "id": trial_id,
            "trial_uri": trial.as_uri(),
            "trial_name": config["trial_name"],
            "task_id": {"path": config["task"]["path"]},
            "config": deepcopy(config),
            "reward_valid": False,
            "verifier_result": {"rewards": {"reward": 0.0}},
            "seat_results": [
                {
                    "seat": role,
                    "agent_info": pair_report._AGENT,
                    "agent_result": {
                        "n_input_tokens": 9,
                        "metadata": {
                            "schema": pair_report._SEAT_SCHEMA,
                            "role": role,
                            "run_id": cell.cell_id,
                            "requested_model": pair_report._MODEL,
                            "model_turns": 1,
                            "stage": "invalid_model_decisions"
                            if role == "inventory"
                            else "complete",
                            "raw_message": "private raw message",
                        },
                    },
                }
                for role in pair_report._ROLES
            ],
        },
    )


def _call(role: str, *, settled: bool = True) -> dict:
    return {
        "run": _CELL.cell_id,
        "role": role,
        "cache_hit": 75 if settled else None,
        "cache_miss": 25 if settled else None,
        "output": 0 if settled else None,
        "estimated_usd_low": "0.000003975" if settled else None,
        "estimated_usd_high": "0.000003975" if settled else None,
        "unsettled_hold_usd": "0.000000000" if settled else "0.010000000",
        "served_model": "private provider request string",
    }


def _by_path(*records: SimpleNamespace):
    return {record.path: record for record in records}.__getitem__


def test_keeps_usage_submission_reward_and_missing_facts_separate(
    tmp_path: Path,
) -> None:
    record = _trial(tmp_path)
    record.result["seat_results"][1]["agent_result"]["metadata"]["stage"] = (
        "provider_timeout"
    )
    report = native_usage._report(
        tmp_path,
        [_call("inventory"), _call("dispatch", settled=False)],
        _by_path(record),
    )

    run = report["runs"][0]
    inventory, dispatch = (run["seats"][role] for role in pair_report._ROLES)
    assert run["native_trial"] == {"reward": 0.0, "reward_valid": False}
    assert inventory["status"] == "known"
    assert inventory["native_submitted_action"] is False
    assert inventory["native_usage"] == {
        "n_input_tokens": 9,
        "n_cache_tokens": None,
        "n_output_tokens": None,
    }
    assert inventory["direct_ledger"] == {
        "paid_provider_response": True,
        "cache_hit": 75,
        "cache_miss": 25,
        "output": 0,
        "estimated_usd_low": "0.000003975",
        "estimated_usd_high": "0.000003975",
        "unsettled_hold_usd": "0.000000000",
    }
    assert dispatch["status"] == "ambiguous"
    assert dispatch["native_submitted_action"] is None
    assert dispatch["direct_ledger"]["paid_provider_response"] is None
    assert dispatch["direct_ledger"]["unsettled_hold_usd"] == "0.010000000"
    assert report["counts"] == {
        "registered_runs": 24,
        "attempted_runs": 1,
        "verified_native_trials": 1,
        "seats": {"known": 1, "ambiguous": 1, "unattempted": 46},
    }
    assert report["unjoined_ledger"] == []
    payload = json.dumps(report)
    for private in (
        str(tmp_path.resolve()),
        _PRIVATE_ID,
        record.result["trial_name"],
        "private raw message",
        "private provider request string",
    ):
        assert private not in payload


def test_refuses_a_native_run_label_mismatch(tmp_path: Path) -> None:
    record = _trial(tmp_path)
    metadata = record.result["seat_results"][0]["agent_result"]["metadata"]
    metadata["run_id"] = "s3101-r0-silent"
    report = native_usage._report(
        tmp_path, [_call("inventory"), _call("dispatch")], _by_path(record)
    )
    run = report["runs"][0]
    rejected = run["seats"]["inventory"]
    assert rejected["status"] == "ambiguous"
    assert rejected["reason"] == "seat_identity_unverified"
    assert rejected["native_submitted_action"] is None
    assert rejected["native_usage"]["n_input_tokens"] is None
    assert rejected["direct_ledger"] is None
    assert run["seats"]["dispatch"]["status"] == "known"
    assert run["seats"]["dispatch"]["native_submitted_action"] is True
    assert report["unjoined_ledger"][0]["ledger"]["paid_provider_response"] is True


def test_refuses_mismatched_and_reused_uuids(tmp_path: Path) -> None:
    record = _trial(tmp_path)
    record.result["id"] = str(UUID(int=2))
    report = native_usage._report(tmp_path, [_call("inventory")], _by_path(record))
    assert report["runs"][0]["native_trial_issue"] == "stock_trial_uuid_mismatch"
    assert report["runs"][0]["native_trial"] is None
    assert report["runs"][0]["seats"]["inventory"]["direct_ledger"] is None

    record.result["id"] = _PRIVATE_ID
    copied = _trial(tmp_path, pair_report._Cell(3101, 0, "silent"))
    report = native_usage._report(
        tmp_path, [_call("inventory")], _by_path(record, copied)
    )
    assert report["counts"]["verified_native_trials"] == 0
    assert all(
        run["native_trial_issue"] == "duplicate_native_trial_uuid"
        for run in report["runs"][:2]
    )


def test_rejects_a_result_uri_with_only_the_same_basename(tmp_path: Path) -> None:
    record = _trial(tmp_path)
    record.result["trial_uri"] = (tmp_path / "elsewhere" / record.path.name).as_uri()
    report = native_usage._report(tmp_path, [_call("inventory")], _by_path(record))

    run = report["runs"][0]
    assert run["native_trial_issue"] == "stock_trial_identity_unverified"
    assert run["native_trial"] is None
    assert run["seats"]["inventory"]["direct_ledger"] is None


def test_native_seat_can_be_unattempted_in_an_attempted_run(tmp_path: Path) -> None:
    record = _trial(tmp_path)
    record.result["seat_results"][1].update(agent_result=None, agent_execution=None)
    record.result.pop("reward_valid")
    record.result.pop("verifier_result")
    record.primary_reward = None
    report = native_usage._report(tmp_path, [_call("inventory")], _by_path(record))

    run = report["runs"][0]
    assert run["attempted"] is True
    assert run["native_trial"] == {"reward": None, "reward_valid": None}
    assert run["seats"]["dispatch"]["status"] == "unattempted"
    assert run["seats"]["dispatch"]["native_submitted_action"] is False
