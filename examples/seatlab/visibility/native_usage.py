"""Compare native Harbor seat usage with the direct Flash ledger, offline."""

import argparse
import json
import sqlite3
import stat
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import team_census, team_driver, team_report
from . import pair_report, spend

_SCHEMA = "seatlab.visibility-native-usage/v1"
_ROLES = pair_report._ROLES
_READ_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    RuntimeError,
    OverflowError,
)


@dataclass
class _Trial:
    attempted: bool = False
    reason: str | None = None
    receipt_id: str | None = None
    stock_id: str | None = None
    raw: team_census.RawTrialSnapshot | None = None
    seats: list[Any] | None = None


def _config(root: Path, cell: pair_report._Cell) -> dict[str, Any]:
    # Match the native launcher without importing its Harbor and provider code.
    config = team_driver._trial_config(
        root,
        {
            "cell_id": cell.cell_id,
            "seed": cell.seed,
            "condition": cell.condition,
            "pairing": "MM",
        },
    )
    config["agent_timeout_multiplier"] = 2.0
    for participant in config["participants"]:
        participant.update(
            import_path="examples.seatlab.visibility.native_pairs:DirectFlashSeat",
            model_name="deepseek/deepseek-flash",
            kwargs={"run_id": cell.cell_id},
        )
    return config


def _read_trial(
    study: Path, cell: pair_report._Cell, load_trial: Callable[[Path], Any]
) -> _Trial:
    directory = study / "cells" / cell.cell_id
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return _Trial()
    found = _Trial(attempted=True, reason="registration_unverified")
    try:
        if not stat.S_ISDIR(info.st_mode):
            return found
        config = _config(study, cell)
        expected = pair_report._expected(cell)
        marker = pair_report._read_sealed(directory / "attempt.json")
        if not pair_report._marker_matches(marker, expected):
            return found
        expected["trial_name"] = config["trial_name"]
        receipt = pair_report._read_sealed(directory / "receipt.json")
        if marker["trial_name"] != config["trial_name"] or not pair_report._matches(
            receipt, expected
        ):
            return found
        found.receipt_id = pair_report._canonical_uuid(receipt.get("trial_id"))
        if found.receipt_id is None:
            found.reason = "receipt_trial_uuid_missing_or_invalid"
            return found
        team_report._check_config(
            pair_report._read_sealed(directory / "config.json"), config
        )

        trial = directory / "trials" / config["trial_name"]
        found.reason = "stock_result_path_unverified"
        native_files = (
            "result.json",
            "config.json",
            "lock.json",
            "artifacts/manifest.json",
        )
        if not (trial / "result.json").is_file() or not all(
            team_report._within_trial(trial / name, trial) for name in native_files
        ):
            return found
        found.reason = "stock_trial_load_failed"
        record = load_trial(trial)
        found.reason = "stock_trial_identity_unverified"
        raw = record.result
        if (
            record.path != trial
            or raw.get("trial_uri") != trial.as_uri()
            or raw.get("trial_name") != config["trial_name"]
            or raw.get("task_id") != {"path": config["task"]["path"]}
        ):
            return found
        team_report._check_config(record.config, config)
        team_report._check_config(raw.get("config"), config)
        found.stock_id = pair_report._canonical_uuid(raw.get("id"))
        found.raw = team_census.normalize_trial_record(record, trial)
        if found.stock_id != found.receipt_id or found.raw.trial_id != found.receipt_id:
            found.reason = "stock_trial_uuid_mismatch"
            return found
        if isinstance(seats := raw.get("seat_results"), list):
            found.seats = seats
        found.reason = None
    except _READ_ERRORS:
        pass
    return found


def _native_seat(trial: _Trial, cell_id: str, role: str) -> tuple[str, bool | None]:
    seats = [
        seat
        for seat in trial.seats or []
        if isinstance(seat, dict) and seat.get("seat") == role
    ]
    if len(seats) != 1 or seats[0].get("agent_info") != pair_report._AGENT:
        return "seat_identity_unverified", None
    seat = seats[0]
    if all(
        key in seat and seat[key] is None for key in ("agent_result", "agent_execution")
    ):
        return "unattempted", False
    context = seat.get("agent_result")
    metadata = context.get("metadata") if isinstance(context, dict) else None
    if not isinstance(metadata, dict) or not pair_report._matches(
        metadata,
        {
            "schema": pair_report._SEAT_SCHEMA,
            "role": role,
            "run_id": cell_id,
            "requested_model": pair_report._MODEL,
        },
    ):
        return "seat_identity_unverified", None
    turns, stage = metadata.get("model_turns"), metadata.get("stage")
    if type(turns) is not int or turns not in (0, 1):
        return "seat_attempt_unverified", None
    if turns == 0:
        if stage not in ("waiting_view", "invalid_view"):
            return "seat_attempt_unverified", None
        return "unattempted", False
    if stage == "complete":
        return (
            "attempted",
            True,
        )  # The native agent records completion after acceptance.
    return "attempted", False if stage == "invalid_model_decisions" else None


def _ledger_facts(row: dict[str, Any]) -> dict[str, Any]:
    tokens = {
        key: team_census._nonnegative_int(row.get(key))
        for key in ("cache_hit", "cache_miss", "output")
    }
    low, high = row["estimated_usd_low"], row["estimated_usd_high"]
    hold = row["unsettled_hold_usd"]
    # spend.report renders the cost fields; the returned model string is not exported.
    settled = (
        low is not None
        and high is not None
        and hold == "0.000000000"
        and all(value is not None for value in tokens.values())
    )
    return {
        "paid_provider_response": True if settled else None,
        **(tokens if settled else dict.fromkeys(tokens)),
        "estimated_usd_low": low if settled else None,
        "estimated_usd_high": high if settled else None,
        "unsettled_hold_usd": hold,
    }


def _report(
    study: Path, calls: list[dict[str, Any]], load_trial: Callable[[Path], Any]
) -> dict:
    study = study.resolve(strict=True)
    if not study.is_dir():
        raise ValueError("study must be a directory")
    grid = pair_report._grid()
    registered = {cell.cell_id for cell in grid}
    cells_dir = study / "cells"
    if cells_dir.is_symlink() or (
        cells_dir.exists()
        and (
            not cells_dir.is_dir()
            or any(entry.name not in registered for entry in cells_dir.iterdir())
        )
    ):
        raise ValueError("unexpected cell set")
    ledger = defaultdict(list)
    for row in calls:
        label, role = row["run"], row["role"]
        if label in registered and role in _ROLES:
            ledger[label, role].append(row)
    trials = {cell.cell_id: _read_trial(study, cell, load_trial) for cell in grid}
    uuid_cells = defaultdict(set)
    for cell_id, trial in trials.items():
        for uid in (trial.receipt_id, trial.stock_id):
            if uid is not None:
                uuid_cells[uid].add(cell_id)

    counts: Counter[str] = Counter()
    runs, unjoined = [], []
    for cell in grid:
        cell_id = cell.cell_id
        trial = trials[cell_id]
        snapshot = trial.raw
        issue = trial.reason
        if len(uuid_cells.get(trial.receipt_id, ())) > 1:
            issue = "duplicate_native_trial_uuid"
        verified = snapshot if issue is None else None
        seats = {}
        for role in _ROLES:
            rows = ledger.get((cell_id, role), [])
            facts = _ledger_facts(rows[0]) if len(rows) == 1 else None
            if verified:
                state, submitted = _native_seat(trial, cell_id, role)
            elif trial.attempted:
                state, submitted = issue, None
            else:
                state, submitted = "unattempted", None if rows else False
            joined = facts if state == "attempted" else None
            if len(rows) > 1:
                status, reason = "ambiguous", "duplicate_ledger_identity"
            elif state == "unattempted" and not rows:
                status, reason = "unattempted", None
            elif state != "attempted":
                status = "ambiguous"
                reason = (
                    "ledger_without_native_attempt" if state == "unattempted" else state
                )
            elif joined is None or joined["paid_provider_response"] is None:
                status = "ambiguous"
                reason = (
                    "ledger_reservation_unsettled" if joined else "ledger_row_missing"
                )
            else:
                status, reason = "known", None
            if rows and joined is None:
                unjoined.append(
                    {
                        "cell_id": cell_id,
                        "role": role,
                        "reason": reason,
                        "ledger": facts,
                    }
                )
            counts[status] += 1
            seats[role] = {
                "status": status,
                "reason": reason,
                "native_submitted_action": submitted,
                "native_usage": (
                    verified.seat_usage[role]
                    if verified and state in {"attempted", "unattempted"}
                    else team_census._empty_role_usage()
                ),
                "direct_ledger": joined,
            }
        runs.append(
            {
                "cell_id": cell_id,
                "attempted": trial.attempted,
                "native_trial_issue": issue,
                "native_trial": (
                    {
                        "reward": verified.primary_reward,
                        "reward_valid": verified.reward_valid,
                    }
                    if verified
                    else None
                ),
                "seats": seats,
            }
        )
    return {
        "schema": _SCHEMA,
        "join_basis": (
            "The stock result must match the sealed native path and receipt UUID. "
            "The direct ledger has only cell and role labels. Use the ledger for this "
            "study; the stock reader cannot verify that the ledger belongs to it."
        ),
        "counts": {
            "registered_runs": len(grid),
            "attempted_runs": sum(trial.attempted for trial in trials.values()),
            "verified_native_trials": sum(
                run["native_trial"] is not None for run in runs
            ),
            "seats": {
                status: counts[status]
                for status in ("known", "ambiguous", "unattempted")
            },
        },
        "runs": runs,
        "unjoined_ledger": unjoined,
    }


def _stock_loader(root: Path) -> Callable[[Path], Any]:
    # Importing the stock reader must not create bytecode in the supplied checkout.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        from evallab import results
    finally:
        sys.dont_write_bytecode = previous
    if not results.__file__ or not Path(results.__file__).resolve().is_relative_to(
        root
    ):
        raise ImportError(
            "Use the Python environment of the requested Eval Lab checkout"
        )
    return results.load_trial


def collect(study: Path, spend_dir: Path, evallab_root: Path) -> dict:
    """Read the study and its dedicated ledger; do not run any native or paid work."""
    load_trial = _stock_loader(evallab_root.expanduser().resolve(strict=True))
    return _report(
        study.expanduser(),
        spend.report(spend_dir.expanduser().absolute())["calls"],
        load_trial,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "spend-dir", "evallab-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = collect(args.study, args.spend_dir, args.evallab_root)
    except (ImportError, AttributeError):
        print(
            json.dumps(
                {"schema": _SCHEMA, "error": "stock_evallab_results_unavailable"}
            )
        )
        return 1
    except (*_READ_ERRORS, sqlite3.Error):
        print(
            json.dumps({"schema": _SCHEMA, "error": "local_usage_inputs_unavailable"})
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
