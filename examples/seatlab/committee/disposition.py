"""Decide whether a new team may follow a failed first attempt. Never retry it."""

import argparse
import json
from pathlib import Path

from . import analysis, schedule


def _model_rejection(root: Path, cell: schedule.Cell, spend_dir: Path) -> bool:
    directory = analysis._path(root, f"cells/{cell['cell_id']}", directory=True)
    outcome = analysis._json(directory, "outcome.json", "launch_missing")
    if (
        outcome
        != {
            "schema": "seatlab.committee-launch/v1",
            "error": "native_trial_incomplete",
            "returncode": 0,
        }
        or type(outcome["returncode"]) is not int
    ):
        return False
    trial_name = f"committee-{cell['cell_id']}"
    attempt = analysis._json(directory, "attempt.json", "attempt_missing")
    if attempt != {
        "schema": "seatlab.committee-attempt/v1",
        **cell,
        "trial_name": trial_name,
        "task_git_tree": schedule.TASK_TREE,
    }:
        return False
    config = analysis._json(directory, "config.json", "config_missing")
    task_path = analysis._roster_config(config, attempt)
    trial = analysis._path(directory, f"trials/{trial_name}", directory=True)
    result = analysis._json(trial, "result.json", "result_missing")
    if not (
        result["trial_name"] == trial_name
        and result["trial_uri"] == trial.as_uri()
        and result["task_checksum"] == schedule.TASK_CHECKSUM
        and result["task_id"] == {"path": task_path}
        and analysis._roster_config(result["config"], attempt) == task_path
        and result["exception_info"]["exception_type"] == "_SeatFailure"
    ):
        return False
    availability, ledger = analysis._ledger(spend_dir)
    if availability != "available":
        return False
    rejected = 0
    stalled = 0
    share_rejected = False
    for role, seat in analysis._roles(result["seat_results"], "seat_invalid").items():
        meta = seat["agent_result"]["metadata"]
        if seat["under_test"] is not True or not all(
            meta.get(k) == v
            for k, v in {
                "role": role,
                "run_id": cell["cell_id"],
                "map_index": cell["map_index"],
                "arm": cell["arm"],
                "schema": analysis._SEAT_SCHEMA,
            }.items()
        ):
            return False
        identity = seat["agent_info"]
        if identity.get("name") != analysis._AGENT or identity.get("model_info") != {
            "provider": "deepseek",
            "name": "deepseek-flash",
        }:
            return False
        sidecar = analysis._json(
            trial, f"seats/{role}/agent/committee-turns.json", "receipt_missing"
        )
        if meta != (
            sidecar
            | {
                "turns": {
                    stage: {k: v for k, v in turn.items() if k != "action"}
                    for stage, turn in sidecar["turns"].items()
                }
            }
        ):
            return False
        turns = meta["turns"]
        if set(turns) != {"share", "vote"}:
            return False
        statuses = tuple(turns[stage]["status"] for stage in ("share", "vote"))
        exc = seat["exception_info"]
        if meta["status"] == "complete" and exc is None:
            if statuses != ("submitted", "submitted"):
                return False
        elif (
            not isinstance(exc, dict)
            or exc.get("exception_type") != "_SeatFailure"
            or meta["status"] != "failed"
        ):
            return False
        elif meta["failure"] == "invalid_model_action":
            if (meta["phase"], statuses) not in (
                ("share", ("rejected", "not_started")),
                ("vote", ("submitted", "rejected")),
            ):
                return False
            rejected += 1
            share_rejected |= meta["phase"] == "share"
        elif (
            meta["failure"] == "chat_barrier_timeout"
            and meta["phase"] == "barrier"
            and statuses == ("submitted", "not_started")
        ):
            stalled += 1
        else:
            return False
        for stage, turn in turns.items():
            call = ledger.get((f"{cell['cell_id']}-{stage}", role))
            if turn["status"] == "not_started":
                if call is not None:
                    return False
            elif (
                call is None
                or call["estimated_usd_high"] is None
                or call["served_model"] != turn["served_model"]
                or turn["served_model"] not in analysis._MODELS.values()
            ):
                return False
    return rejected > 0 and (stalled == 0 or share_rejected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    cells = {c["cell_id"]: c for c in (*schedule.grid(), *schedule.tally_grid())}
    parser.add_argument("cell", choices=cells)
    parser.add_argument("--spend-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        allowed = _model_rejection(
            args.study.resolve(strict=True), cells[args.cell], args.spend_dir
        )
    except (
        analysis._Excluded,
        OSError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
    ):
        allowed = False
    print(
        json.dumps(
            {
                "cell": args.cell,
                "disposition": "exclude_model_rejection_continue"
                if allowed
                else "stop",
            }
        )
    )
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
