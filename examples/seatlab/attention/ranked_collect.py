import argparse
import json
import sqlite3
import stat
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from .. import evidence, run_panel, seat_agent, team_driver, team_report
from ..visibility import clarify_run, native_pairs, spend
from . import audit, protocol, ranked_result, ranking
from .__main__ import _RUNS, _checked_study, _config, _load, _observed

_USAGE_FIELDS = ("served_model", "cache_hit", "cache_miss", "output")
_COST_FIELDS = ("estimated_usd_low", "estimated_usd_high")
_HOLD_FIELD = "unsettled_hold_usd"


def _registered(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    team_driver._check_path(root, 0o700, directory=True)
    record = clarify_run._object(team_driver._read_sealed(root / "register.json"))
    if (
        record.keys() != {"plan", "spend_directory"}
        or record["plan"]["schema"] != ranking.PLAN_SCHEMA
        or record["plan"]["transport"] != "direct"
        or record["plan"]["command_config_sha256"] is not None
        or _present(root / "commands.json")
    ):
        raise ValueError("expected a sealed ranked direct study")
    meter = Path(record["spend_directory"])
    if not meter.is_absolute() or meter != meter.resolve(strict=True):
        raise ValueError("expected the registered meter")
    team_driver._check_path(spend._location(meter), 0o600)
    plan, ledger = _load(root)
    if plan != record["plan"] or ledger is None:
        raise ValueError("registered plan or meter changed")
    return plan, ledger


def _bill_usage(bill: dict[str, Any]) -> tuple[dict[str, Any] | None, int, int]:
    if bill.keys() != {"run", "role", *_USAGE_FIELDS, *_COST_FIELDS, _HOLD_FIELD}:
        raise ValueError("invalid meter call schema")
    model, hit, miss, output = (bill[field] for field in _USAGE_FIELDS)
    minimum, maximum = (bill[field] for field in _COST_FIELDS)
    held = bill[_HOLD_FIELD]
    if maximum is None:
        if held != spend._usd(spend._HOLD) or any(
            value is not None for value in (minimum, model, hit, miss, output)
        ):
            raise ValueError("invalid open reservation")
        return None, 0, spend._HOLD
    if (
        held != spend._usd(0)
        or not isinstance(model, str)
        or model.casefold() not in native_pairs._SERVED_ENGINES
        or any(type(n) is not int or n < 0 for n in (hit, miss, output))
    ):
        raise ValueError("invalid settled reservation")
    low = hit * 3 + miss * 150 + output * 600
    high = next(
        (n for n in (low, 2 * low) if n <= spend._HOLD and spend._usd(n) == maximum),
        None,
    )
    if high is None or minimum != spend._usd(low):
        raise ValueError("invalid settled cost bounds")
    return (
        {
            "served_model": model,
            "input_tokens": hit + miss,
            "cache_tokens": hit,
            "output_tokens": output,
        },
        low,
        high,
    )


def _index_bills(
    ledger: dict[str, Any], cells: list[dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any] | None]]:
    if (
        ledger["schema"] != "seatlab.direct-flash-cost/v1"
        or ledger["currency"] != "USD"
        or not isinstance(ledger["calls"], list)
    ):
        raise ValueError("invalid registered meter report")
    by_cell: dict[str, dict[str, dict[str, Any] | None]] = {
        cell["cell_id"]: {} for cell in cells
    }
    expected = {
        (f"{cell_id}-{stage}", role): (cell_id, stage)
        for cell_id in by_cell
        for stage, role in _RUNS.items()
    }
    held = low = high = 0
    for bill in ledger["calls"]:
        cell_id, stage = expected.pop((bill["run"], bill["role"]))
        usage, minimum, maximum = _bill_usage(bill)
        by_cell[cell_id][stage] = usage
        held += usage is None
        low += minimum
        high += maximum
    if (
        type(ledger["unsettled_calls"]) is not int
        or ledger["unsettled_calls"] != held
        or ledger["known_usd_low"] != spend._usd(low)
        or ledger["upper_usd_including_holds"] != spend._usd(high)
    ):
        raise ValueError("meter totals differ from its reservations")
    return by_cell


def _present(path: Path, *, directory: bool = False) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(details.st_mode) or (not directory and details.st_nlink != 1):
        raise ValueError("unsafe native evidence path")
    return True


def _native_paths(
    trial: Path, cell: dict[str, Any], config: dict[str, Any]
) -> set[str]:
    directory = trial.parent.parent
    if not _present(directory, directory=True):
        return set()
    team_driver._check_path(directory, 0o700, directory=True)
    _present(directory / "outcome.json")
    for name, expected in (("attempt.json", cell), ("config.json", config)):
        marker = directory / name
        if _present(marker) and team_driver._read_sealed(marker) != team_driver._bytes(
            expected
        ):
            raise ValueError("native first-attempt registration changed")
    if not _present(trial.parent, directory=True):
        return set()
    team_driver._check_path(trial.parent, 0o700, directory=True)
    if not (directory / "attempt.json").is_file() or any(
        path != trial for path in trial.parent.iterdir()
    ):
        raise ValueError("unexpected native trial claim")
    if not _present(trial, directory=True):
        return set()

    present = {
        name for name in ("config.json", "result.json") if _present(trial / name)
    }
    if "config.json" in present:
        team_report._check_config(
            team_report._read_object(trial / "config.json"), config
        )
    if all(
        _present(trial / name, directory=True)
        for name in ("artifacts", "artifacts/logs", "artifacts/logs/world")
    ):
        dossier = trial / "artifacts/logs/world/handoff.json"
        if _present(dossier):
            present.add("handoff.json")
            _check_world(team_report._read_object(dossier), cell)
    return present


def _check_world(world: dict[str, Any], cell: dict[str, Any]) -> None:
    settings = {
        "schema": "seatlab.scenario/v1",
        "seed": cell["seed"],
        "condition": cell["condition"],
        "episodes": protocol.EPISODES,
    }
    if (
        world.get("schema") != "seatlab.world/v1"
        or world.get("complete") is not True
        or not run_panel._config_matches(world.get("settings"), settings)
    ):
        raise ValueError("wrong native world for ranked cell")
    evidence._score(world)


def _failed(record: dict[str, Any]) -> bool:
    exception = record["exception_info"]
    if exception is None:
        return False
    if (
        not isinstance(exception, dict)
        or not isinstance(exception.get("exception_type"), str)
        or not exception["exception_type"].isidentifier()
    ):
        raise ValueError("invalid native exception evidence")
    return True


def _read_result(
    body: bytes, trial: Path, config: dict[str, Any]
) -> tuple[dict[str, Any], str, bool]:
    native = clarify_run._object(body)
    trial_id = evidence._trial_id(native)
    if (
        UUID(trial_id).version != 4
        or type(native.get("schema_version", 1)) is not int
        or native.get("schema_version", 1) != 1
        or native.get("trial_name") != config["trial_name"]
        or native.get("trial_uri") != trial.as_uri()
        or native.get("task_id") != {"path": config["task"]["path"]}
        or native.get("agent_result") is not None
        or native.get("step_results") is not None
        or not isinstance(native.get("finished_at"), str)
        or not native["finished_at"]
        or type(native.get("reward_valid")) is not bool
    ):
        raise ValueError("wrong native result for ranked cell")
    team_report._check_config(native.get("config"), config)
    seats = evidence._by_role(native.get("seat_results"), "seat_results")
    failures = [_failed(record) for record in (native, *seats.values())]
    success = not any(failures)
    verifier = native.get("verifier_result")
    if verifier is not None and not isinstance(verifier, dict):
        raise ValueError("invalid native verifier evidence")
    rewards = verifier.get("rewards") if verifier is not None else None
    if success and native["reward_valid"] != bool(rewards):
        raise ValueError("inconsistent native reward validity")
    if rewards is not None and (
        not isinstance(rewards, dict)
        or rewards.keys() != {"reward"}
        or type(rewards["reward"]) not in (float, int)
        or not 0 <= rewards["reward"] <= 1
    ):
        raise ValueError("invalid stock verifier reward")
    return native, trial_id, success


def _usage_matches(
    turns: list[dict[str, Any]], bills: dict[str, dict[str, Any] | None], cell_id: str
) -> bool:
    expected = set(_RUNS)
    for turn in turns:
        stage = turn["stage"]
        if (
            stage not in expected
            or turn["run"] != f"{cell_id}-{stage}"
            or turn["role"] != _RUNS[stage]
            or turn["status"] != "received"
        ):
            raise ValueError("native audit has a wrong or duplicate turn")
        expected.remove(stage)
        bill = bills.get(stage)
        if bill is not None and turn["usage"] != bill:
            raise ValueError("native audit usage differs from its reservation")
    if expected:
        raise ValueError("native audit omitted a registered turn")
    return len(bills) == len(_RUNS) and all(
        usage is not None for usage in bills.values()
    )


def _partial_turns(
    metadata: dict[str, Any],
    cell_id: str,
    role: str,
    bills: dict[str, dict[str, Any] | None],
) -> tuple[str | None, str | None]:
    seat = None if metadata.get("stage") == "invalid_seat" else role
    if not run_panel._config_matches(
        metadata, {"schema": audit._SEAT_SCHEMA, "cell_id": cell_id, "role": seat}
    ):
        raise ValueError("unexpected partial native seat identity")
    turns = metadata.get("turns")
    stages = [stage for stage, actor in _RUNS.items() if actor == role]
    if not isinstance(turns, list) or len(turns) > len(stages):
        raise ValueError("unexpected partial native turn count")
    text = billed_stage = None
    for index, raw in enumerate(turns):
        stage = stages[index]
        if not isinstance(raw, dict):
            raise TypeError("invalid partial native turn")
        prompt = raw.get("prompt_sha256")
        if (
            not isinstance(prompt, str)
            or ranked_result._SHA256.fullmatch(prompt) is None
        ):
            raise ValueError("invalid partial native prompt digest")
        if raw.get("status") == "callback_error":
            if index != len(turns) - 1 or not run_panel._config_matches(
                raw,
                {
                    "stage": stage,
                    "role": role,
                    "run": f"{cell_id}-{stage}",
                    "text": None,
                    "response_sha256": None,
                    "usage": None,
                },
            ):
                raise ValueError("invalid partial native callback evidence")
            return None, None
        text, turn = audit._turn(raw, stage, role, cell_id, "direct")
        bill = bills.get(stage)
        if bill is not None and bill != turn["usage"]:
            raise ValueError("native audit usage differs from its reservation")
        billed_stage = stage if bill is not None else None
    return billed_stage, text


def _model_rejected(
    native: dict[str, Any],
    cell: dict[str, Any],
    bills: dict[str, dict[str, Any] | None],
) -> bool:
    rejected = False
    records = evidence._by_role(native["seat_results"], "seat_results")
    for role, record in records.items():
        context = record.get("agent_result")
        if context is not None and not isinstance(context, dict):
            raise ValueError("invalid partial native seat context")
        metadata = context.get("metadata") if context is not None else None
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            raise TypeError("invalid partial native seat metadata")
        stage, text = _partial_turns(metadata, cell["cell_id"], role, bills)
        exception = record["exception_info"]
        if (
            stage is None
            or text is None
            or exception is None
            or exception["exception_type"] != "_Unmeasured"
            or exception.get("exception_message")
            != "Native attention seat: invalid_model_decisions"
            or metadata.get("stage") != "invalid_model_decisions"
        ):
            continue
        try:
            if stage == "inventory":
                reply = seat_agent._json_object(text, "inventory model")
                seat_agent._checked_submission(reply, role, protocol.EPISODES)
            elif stage == "select":
                protocol.select(text)
            else:
                cases = [{"id": index} for index in range(protocol.EPISODES)]
                protocol.decide(text, cases, cell["seed"], cell["repetition"])
        except (ValueError, seat_agent._Unmeasured):
            rejected = True
    failure = native["exception_info"]
    return (
        rejected and failure is not None and failure["exception_type"] == "_Unmeasured"
    )


def _cell(
    root: Path,
    cell: dict[str, Any],
    ledger: dict[str, Any],
    bills: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    cell_id = cell["cell_id"]
    trial = team_driver._trial_dir(root, cell)
    config = _config(root, cell, "direct")
    present = _native_paths(trial, cell, config)
    result_path = trial / "result.json"
    body = result_path.read_bytes() if "result.json" in present else None
    observed = _observed(root, cell, "direct", ledger)
    state, outcome = observed["state"], observed["outcome"]
    if state not in {
        "complete",
        "unclaimed",
        "reserved_without_trial",
        "claimed",
        "native_failed",
        "unverified",
    } or (state == "complete") != (outcome is not None):
        raise ValueError("unexpected native row state")
    held = sum(usage is None for usage in bills.values())
    settled = len(bills) - held
    row = {
        "cell_id": cell_id,
        "seed": cell["seed"],
        "arm": cell["arm"],
        "status": "not_attempted" if state == "unclaimed" else "missing",
        "trial_uuid": None,
        "result_sha256": None,
        "optimal": None,
        "feasible": None,
        "settled_calls": settled,
        "held_calls": held,
    }
    if body is None:
        if outcome is not None:
            raise ValueError("native result disappeared during collection")
        return row

    native, trial_id, success = _read_result(body, trial, config)
    row.update(trial_uuid=trial_id, result_sha256=sha256(body).hexdigest())
    if (
        success
        and native["reward_valid"]
        and state != "claimed"
        and {"config.json", "handoff.json"} <= present
    ):
        if outcome is None:
            outcome = audit.inspect_trial(trial, config, cell, "direct")
        if outcome["trial_id"] != trial_id:
            raise ValueError("native audit belongs to a different result")
        feasible, optimal = audit._ranked_counts(outcome)
        matched = _usage_matches(outcome["turns"], bills, cell_id)
        row.update(feasible=feasible, optimal=optimal)
        if state == "complete" and matched:
            row["status"] = "complete"
    elif outcome is not None:
        raise ValueError("verified native row lacks complete stock evidence")
    elif _model_rejected(native, cell, bills) and state == "unverified":
        row["status"] = "invalid"
    if result_path.read_bytes() != body:
        raise ValueError("native result changed during collection")
    return row


def _collect(root: Path) -> dict[str, Any]:
    root = _checked_study(root)
    plan, ledger = _registered(root)
    cells = plan["cells"]
    bills = _index_bills(ledger, cells)
    document = {
        "schema": ranked_result.RECEIPT_SCHEMA,
        "cells": [_cell(root, cell, ledger, bills[cell["cell_id"]]) for cell in cells],
    }
    ranked_result._enroll(document)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = _collect(args.study)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error):
        parser.error("cannot collect a valid receipt from a sealed ranked direct study")
    print(json.dumps(document, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
