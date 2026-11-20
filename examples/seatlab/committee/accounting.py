"""Check committee trial consumption with the stock Eval Lab raw reader."""

import argparse
import json
import math
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from . import analysis
from .schedule import ARMS, ROLES, TASK_CHECKSUM, Cell, grid

_PHASES = ("share", "vote")
_TOKENS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _sum_usage(rows: Iterable[dict]) -> dict:
    usage = list(rows)
    return {
        key: sum(values)
        if (values := [row[key] for row in usage if row[key] is not None])
        else None
        for key in _TOKENS
    }


def _raw_trial(
    study: Path, cell: Cell, load_trial: Callable[[Path], Any], used_ids: set[UUID]
) -> tuple[str, Any, str | None]:
    name = f"committee-{cell['cell_id']}"
    relative = f"cells/{cell['cell_id']}/trials/{name}"
    try:
        result_path = analysis._path(study, f"{relative}/result.json")
    except FileNotFoundError:
        return "absent", None, None
    except (OSError, analysis._Excluded):
        return "unreadable", None, "unsafe_or_unreadable_native_result"

    trial = result_path.parent
    try:
        native = analysis._object(
            analysis._parse(result_path.read_text(encoding="utf-8")),
            "native_result_invalid",
        )
        # The stock reader opens these optional files itself. Reuse the same path guard.
        for path in ("config.json", "lock.json", "artifacts/manifest.json"):
            try:
                analysis._path(trial, path)
            except FileNotFoundError:
                pass
        raw = load_trial(trial)
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        analysis._Excluded,
    ):
        return "present", None, "raw_trial_unreadable"

    try:
        trial_id = UUID(native["id"])
        uri = trial.as_uri()
        analysis._require(
            trial_id.int
            and UUID(raw.id) == trial_id
            and raw.path == trial
            and raw.name == native["trial_name"] == name
            and raw.result.get("trial_uri") == native["trial_uri"] == uri
            and raw.result.get("task_checksum") == TASK_CHECKSUM
            and raw.result.get("task_name") == analysis._TASK,
            "raw_trial_identity_mismatch",
        )
        # Bind even an incomplete result to its scheduled cell and configured seats.
        analysis._roster_config(raw.result.get("config"), {**cell, "trial_name": name})
    except (AttributeError, KeyError, TypeError, ValueError, analysis._Excluded):
        return "present", None, "raw_trial_identity_mismatch"
    if trial_id in used_ids:
        return "present", None, "duplicate_native_trial"
    used_ids.add(trial_id)
    return "present", raw, None


def _native_seat(records: list, cell: Cell, role: str) -> tuple[dict, str | None]:
    seats = [seat for seat in records if _object(seat).get("seat") == role]
    context = {}
    error = None
    if seats:
        seat = seats[0]
        info = _object(seat.get("agent_info"))
        context = _object(seat.get("agent_result"))
        metadata = _object(context.get("metadata"))
        expected_info = {"provider": "deepseek", "name": "deepseek-flash"}
        wrong_agent = (bool(context) or bool(info)) and (
            info.get("name") != analysis._AGENT
            or info.get("model_info") != expected_info
        )
        wrong_scope = context and (
            metadata.get("role") != role
            or metadata.get("run_id") != cell["cell_id"]
            or metadata.get("arm") != cell["arm"]
            or metadata.get("map_index") != cell["map_index"]
        )
        if (
            len(seats) != 1
            or seat.get("under_test") is not True
            or wrong_agent
            or wrong_scope
        ):
            context, error = {}, "raw_seat_identity_mismatch"
    usage = {key: _count(context.get(key)) for key in _TOKENS}
    if any(context.get(key) is not None and usage[key] is None for key in _TOKENS):
        error = "raw_seat_usage_invalid"
    return usage, error


def _direct_usage(call: dict) -> dict:
    hit, miss, output = (
        _count(call.get(key)) for key in ("cache_hit", "cache_miss", "output")
    )
    total = hit + miss if hit is not None and miss is not None else None
    return dict(zip(_TOKENS, (total, hit, output), strict=True))


def _spend_totals(calls: list[dict]) -> dict:
    settled = [call for call in calls if call["estimated_usd_high"] is not None]
    low = sum((Decimal(call["estimated_usd_low"]) for call in settled), Decimal(0))
    high = sum((Decimal(call["estimated_usd_high"]) for call in settled), Decimal(0))
    held = sum((Decimal(call["unsettled_hold_usd"]) for call in calls), Decimal(0))
    unresolved = len(calls) - len(settled)
    return {
        "recorded_calls": len(calls),
        "settled_calls": len(settled),
        "unresolved_holds": unresolved,
        "unresolved_hold_usd": f"{held:.9f}",
        "settled_usd_low": f"{low:.9f}",
        "settled_usd_high": f"{high:.9f}",
        "upper_usd_including_holds": f"{high + held:.9f}",
        "exact_list_price_usd": f"{low:.9f}"
        if low == high and not unresolved
        else None,
        "actual_usd": None,  # The ledger has prices and usage, not billed debits.
        "direct_usage": _sum_usage(_direct_usage(call) for call in settled),
    }


def account(study: Path, *, spend_dir: Path) -> dict:
    """Report scoped usage, with independent action scores only for accepted cells."""
    from evallab.results import load_trial

    study = study.resolve(strict=True)
    checked = analysis.analyze(study, spend_dir=spend_dir)
    ledger_status, ledger = analysis._ledger(spend_dir)
    accepted = {row["cell_id"]: row for row in checked["cells"]}
    excluded = {row["cell_id"]: row for row in checked["excluded_cells"]}
    cells = []
    known_calls = []
    used_ids: set[UUID] = set()
    for cell in grid():
        cell_id = cell["cell_id"]
        scored = accepted.get(cell_id)
        evidence = scored if scored is not None else excluded[cell_id]
        status, raw, error = _raw_trial(study, cell, load_trial, used_ids)
        mismatches = [error] if error else []
        reason = evidence.get("reason")
        if reason == "native_reward_mismatch":
            mismatches.append(reason)
        native_reward = raw.primary_reward if raw is not None else None
        if native_reward is not None and not math.isfinite(native_reward):
            native_reward = None
        action_reward = (
            float(scored["strict_group_majority"]) if scored is not None else None
        )
        reward_status = "unavailable"
        if action_reward is not None:
            reward_status = "matched" if native_reward == action_reward else "mismatch"
            if reward_status == "mismatch":
                mismatches.append("raw_reward_mismatch")
        elif reason == "native_reward_mismatch":
            reward_status = "mismatch"
        records = raw.result.get("seat_results") if raw is not None else None
        records = records if isinstance(records, list) else []
        if any(
            not isinstance(_object(seat).get("seat"), str) or seat["seat"] not in ROLES
            for seat in records
        ):
            mismatches.append("unexpected_raw_seat")
        seats = []
        for role, public in zip(ROLES, evidence["seats"], strict=True):
            native, seat_error = _native_seat(records, cell, role)
            turns, settled = {}, []
            held = False
            for phase in _PHASES:
                call = ledger.get((f"{cell_id}-{phase}", role))
                turn = dict(public["turns"][phase])
                if call is not None:
                    known_calls.append(call)
                    usage = _direct_usage(call)
                    if call["estimated_usd_high"] is not None:
                        settled.append(usage)
                    else:
                        held = True
                    turn["direct"] = {
                        "usage": usage,
                        **{
                            key: call[key]
                            for key in (
                                "estimated_usd_low",
                                "estimated_usd_high",
                                "unsettled_hold_usd",
                            )
                        },
                    }
                turns[phase] = turn
            direct = _sum_usage(settled)
            # A failed cell may have one paid stage and no reservation for the other.
            # Compare its measured usage, but never mark it as a two-stage match.
            comparable = (
                bool(settled)
                and not held
                and all(row[key] is not None for row in settled for key in _TOKENS)
            )
            complete = len(settled) == len(_PHASES)
            usage_differs = (
                comparable and (raw is not None or complete) and native != direct
            )
            if seat_error is not None or usage_differs:
                usage_status = "mismatch"
                mismatches.append(
                    f"{public['seat']}:{seat_error or 'native_usage_mismatch'}"
                )
            elif comparable and raw is not None:
                usage_status = "matched" if complete else "partial_matched"
            else:
                usage_status = "unavailable"
            seats.append(
                public
                | {
                    "turns": turns,
                    "native_usage": native,
                    "direct_usage": direct,
                    "usage_status": usage_status,
                }
            )
        cells.append(
            cell
            | {
                "raw_result": status,
                "raw_bound": raw is not None,
                "analysis_status": evidence.get("status", "accepted"),
                "analysis_reason": reason,
                "native_reward": native_reward,
                "accepted_action_reward": action_reward,
                "reward_status": reward_status,
                "mismatches": mismatches,
                "seats": seats,
            }
        )
    all_seats = [seat for cell in cells for seat in cell["seats"]]
    return {
        "schema": "seatlab.committee-accounting/v1",
        "scope": {"arms": list(ARMS), "latent_worlds": 1, "task": checked["task"]},
        "summary": {
            "scheduled_cells": len(cells),
            **{
                f"{status}_cells": sum(row["raw_result"] == status for row in cells)
                for status in ("present", "absent", "unreadable")
            },
            "bound_cells": sum(row["raw_bound"] for row in cells),
            "accepted_cells": len(accepted),
            "mismatch_cells": sum(bool(row["mismatches"]) for row in cells),
            "matched_seats": sum(
                seat["usage_status"] == "matched" for seat in all_seats
            ),
            "partial_matched_seats": sum(
                seat["usage_status"] == "partial_matched" for seat in all_seats
            ),
            "matched_rewards": sum(row["reward_status"] == "matched" for row in cells),
        },
        "cells": cells,
        "totals": {
            "native_usage": _sum_usage(seat["native_usage"] for seat in all_seats),
            "native_observed_seats": {
                key: sum(seat["native_usage"][key] is not None for seat in all_seats)
                for key in _TOKENS
            },
            "spend_evidence": ledger_status,
            "spend": _spend_totals(known_calls)
            if ledger_status == "available"
            else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = account(args.study, spend_dir=args.spend_dir)
    except ImportError:
        parser.error("use the stock Eval Lab Python interpreter")
    except OSError:
        parser.error("the study root is unavailable")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return int(
        report["summary"]["mismatch_cells"] > 0 or report["totals"]["spend"] is None
    )


if __name__ == "__main__":
    raise SystemExit(main())
