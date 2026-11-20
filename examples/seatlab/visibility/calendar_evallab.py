"""Cross-check the calendar report with Eval Lab's raw reader and a cf- spend export."""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from . import calendar_packet as packet
from .calendar_protocol import Block, blocks

_USAGE = ("input_tokens", "cache_tokens", "output_tokens")
_SEATS = {"p1", "p2"}
_SEEDS = {"cal_initial_p1", "cal_initial_p2"}
_TURNS = {"cal_recheck", "cal_decide"}
_SKIPPED = {"unattempted", "missing_evidence", "native_failure", "invalid_evidence"}
_INVALID = "model_invalid_scored_zero"
_BAD_INPUT = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    RuntimeError,
    OverflowError,
)

_Json = dict[str, Any]
_Call = tuple[tuple[int, int, int] | None, int, int]


class _Trial(Protocol):
    path: Path
    result: _Json
    config: _Json
    id: str
    name: str
    primary_reward: float | None


class _Mismatch(ValueError):
    """A diagnostic that contains no raw evidence."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise _Mismatch(reason)


def _unique(pairs: list[tuple[str, Any]]) -> _Json:
    result = dict(pairs)
    _require(len(result) == len(pairs), "duplicate JSON key")
    return result


def _read(path: Path) -> _Json:
    return json.loads(path.read_bytes(), object_pairs_hook=_unique)


def _money(value: str) -> int:
    _require(
        isinstance(value, str) and bool(re.fullmatch(r"[0-9]+\.[0-9]{9}", value)),
        "invalid spend amount",
    )
    return int(value.replace(".", ""))


def _ledger(cost: _Json) -> dict[str, dict[str, _Call]]:
    _require(cost["schema"] == "seatlab.direct-flash-cost/v1", "wrong spend schema")
    _require(cost["currency"] == "USD", "wrong spend currency")
    runs: defaultdict[str, dict[str, _Call]] = defaultdict(dict)
    for row in cost["calls"]:
        run, role = row["run"], row["role"]
        _require(
            isinstance(run, str)
            and run.startswith("cf-")
            and isinstance(role, str)
            and role not in runs[run],
            "unscoped or duplicate spend row",
        )
        low, high = row["estimated_usd_low"], row["estimated_usd_high"]
        hold = _money(row["unsettled_hold_usd"])
        hit, miss, out = (row[k] for k in ("cache_hit", "cache_miss", "output"))
        if low is None and high is None:
            _require(
                hold > 0 and hit is miss is out is None, "invalid unsettled spend row"
            )
            runs[run][role] = None, 0, hold
        else:
            lo, hi = _money(low), _money(high)
            measured = all(type(n) is int and n >= 0 for n in (hit, miss, out))
            _require(hold == 0 and lo <= hi and measured, "invalid settled spend row")
            runs[run][role] = (hit + miss, hit, out), lo, hi
    return runs


def _totals(calls: list[_Call]) -> _Json:
    counts: Counter[str] = Counter()
    for usage, low, high in calls:
        if usage is not None:
            counts.update(dict(zip(_USAGE, usage, strict=True)))
        counts.update(low=low, high=high, unsettled=usage is None)

    def usd(nano: int) -> str:
        whole, fractional = divmod(nano, 1_000_000_000)
        return f"{whole}.{fractional:09d}"

    return {
        **{k: counts[k] for k in _USAGE},
        "calls": len(calls),
        "unsettled_calls": counts["unsettled"],
        "known_usd_low": usd(counts["low"]),
        "upper_usd_including_holds": usd(counts["high"]),
    }


def _verify(
    native: _Trial,
    path: Path,
    raw_id: str,
    checksum: str,
    block: Block,
    row: _Json,
    calls: dict[str, _Call],
) -> None:
    result, run = native.result, path.parent.parent.name
    identity = (native.path, native.name, native.id, result["trial_uri"])
    expected = (path, path.name, raw_id, path.as_uri())
    _require(identity == expected, "native trial identity differs")
    fixture = Path(result["task_id"]["path"])
    name = f"schedule-{block.world:03d}-talk"
    _require(
        fixture.is_absolute() and fixture.resolve() == fixture, "invalid task path"
    )
    configs = (native.config, result["config"])
    _require(
        fixture.name == name
        and result["task_name"] == f"flotilla/{name}"
        and result["task_checksum"] == checksum
        and all(c["task"]["path"] == str(fixture) for c in configs),
        "native fixture or checksum differs",
    )
    start = datetime.fromisoformat(result["started_at"])
    finish = datetime.fromisoformat(result["finished_at"])
    ordered = start.utcoffset() is not None and finish.utcoffset() is not None
    valid = result["exception_info"] is None and result["reward_valid"] is True
    _require(ordered and start <= finish and valid, "native result is not valid")
    reward, invalid = row["reward"], row["invalid_seat"]
    raw_rewards = result["verifier_result"]["rewards"]
    _require(raw_rewards.keys() == {"reward"}, "native reward differs")
    scores = (reward, raw_rewards["reward"], native.primary_reward)
    numeric = all(type(n) in (int, float) and n == reward for n in scores)
    _require(
        type(reward) is int and reward in (0, 1) and numeric, "native reward differs"
    )
    invalid_shape = invalid in _SEATS and reward == 0
    _require(
        invalid is None if row["status"] == "complete" else invalid_shape,
        "report model-invalid status differs",
    )
    roster = result["seat_results"]
    seats = {seat["seat"]: seat for seat in roster}
    _require(len(roster) == 2 and seats.keys() == _SEATS, "native roster differs")
    _require(row["tokens"].keys() == _SEATS, "report roster differs")
    stop = invalid == block.sender
    roles = {"cal_recheck"} if stop else _TURNS
    _require(calls.keys() == roles, "native cell ledger coverage differs")
    for receiver, seat in enumerate((block.sender, block.receiver)):
        seat_result = seats[seat]
        context = seat_result["agent_result"]
        meta = context["metadata"]["calendar"]
        no_call = stop and receiver == 1
        stage = "model_invalid" if seat == invalid else "complete"
        messages = (meta["incoming_id"], meta["outgoing_id"])
        _require(
            seat_result["under_test"] is True
            and seat_result["exception_info"] is None
            and meta["control"] is False
            and (meta["run"], meta["seat"]) == (run, seat)
            and meta["stage"] == ("peer_model_invalid" if no_call else stage)
            and messages == (f"m{1 + receiver}", f"m{2 - receiver}"),
            "native seat role differs",
        )
        counts = tuple(context.get(f"n_{k}") for k in _USAGE)
        reported = row["tokens"][seat]
        if no_call:
            empty = all(n is None or type(n) is int and n == 0 for n in counts)
            _require(reported is None and empty, "no-call seat has usage")
            continue
        usage = calls["cal_decide" if receiver else "cal_recheck"][0]
        _require(
            reported is not None and reported.keys() == set(_USAGE),
            "report seat usage is absent",
        )
        claimed = tuple(reported[k] for k in _USAGE)
        _require(
            usage is not None
            and counts == claimed == usage
            and all(type(n) is int for n in (*counts, *claimed)),
            "native, report and settled seat usage differ",
        )


def reconcile(study: Path, report: _Json, cost: _Json) -> _Json:
    """Read existing evidence; never score a trial or count a skipped arm."""
    # Do not create bytecode in the optional Eval Lab checkout.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        load_trial: Callable[[Path], _Trial] = import_module(
            "evallab.results"
        ).load_trial
    finally:
        sys.dont_write_bytecode = previous

    study = study.resolve(strict=True)
    _require(study.is_dir(), "study root is not a directory")
    registered = {block.id: block for block in blocks()}
    worlds = packet.load(packet.PACKET, packet.PACKET_SHA256)
    ledger, skipped = _ledger(cost), Counter()
    _require(
        report["schema"] == "seatlab.calendar-feedback-report/v1",
        "wrong calendar report schema",
    )
    rows = {row["block"]: row for row in report["blocks"]}
    _require(
        len(report["blocks"]) == len(rows) and rows.keys() == registered.keys(),
        "report block coverage differs",
    )
    ids: Counter[UUID] = Counter()
    matched: dict[str, UUID] = {}
    skipped_runs: set[str] = set()
    for key, block in registered.items():
        row = rows[key]
        coordinates = (
            row["world"],
            row["direction"],
            row["repetition"],
            row["arm_order"],
        )
        expected = (block.world, block.direction, block.repetition, list(block.arms))
        _require(
            row["arms"].keys() == set(block.arms) and coordinates == expected,
            "report block coordinates differ",
        )
        for arm in block.arms:
            branch = row["arms"][arm]
            status, run = branch["status"], f"{key}-{arm}"
            path = study / "cells" / run / "trials" / f"calendar-{run}"
            raw = path / "result.json"
            scored = status in {"complete", _INVALID}
            # Unscored native records also take part in the UUID census.
            try:
                _require(
                    raw.resolve() == raw and raw.is_file(), "native result path differs"
                )
                raw_id = _read(raw)["id"]
                uid = UUID(raw_id)
            except _BAD_INPUT:
                if scored:
                    raise _Mismatch("native result path or UUID is invalid") from None
            else:
                ids[uid] += 1
                if scored:
                    _require(uid.int != 0, "zero native trial UUID")
                    _require(
                        {p.name for p in path.parent.iterdir()} == {path.name},
                        "extra native trial path",
                    )
                    _verify(
                        load_trial(path),
                        path,
                        raw_id,
                        worlds[block.world].task_checksum,
                        block,
                        branch,
                        ledger.get(run, {}),
                    )
                    matched[run] = uid
                    continue
            _require(
                status in _SKIPPED and "reward" not in branch,
                "unknown or scored skipped status",
            )
            skipped[status] += 1
            skipped_runs.add(run)
    _require(
        all(ids[uid] == 1 for uid in matched.values()), "duplicate native trial UUID"
    )

    kinds = ("matched_native", "seed_calls", "unscored_calls", "unmatched_ledger")
    groups: dict[str, list[_Call]] = {k: [] for k in kinds}
    cohort: list[_Call] = []
    for run, calls in ledger.items():
        for role, call in calls.items():
            if run in matched:
                group = "matched_native"
            elif run in registered and role in _SEEDS:
                group = "seed_calls"
            elif run in skipped_runs and role in _TURNS:
                group = "unscored_calls"
            else:
                group = "unmatched_ledger"
            groups[group].append(call)
            cohort.append(call)
    totals = _totals(cohort)
    fields = ("unsettled_calls", "known_usd_low", "upper_usd_including_holds")
    _require(all(cost[k] == totals[k] for k in fields), "cohort spend totals differ")
    return {
        "schema": "seatlab.calendar-evallab/v1",
        "matched_trials": len(matched),
        "matched_paid_seats": len(groups["matched_native"]),
        "skipped_statuses": dict(skipped),
        "cohort": totals,
        **{key: _totals(values) for key, values in groups.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calendar_evallab", description=__doc__)
    for flag in ("--study", "--report", "--cost"):
        parser.add_argument(flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        aggregate = reconcile(args.study, _read(args.report), _read(args.cost))
    except ImportError:
        parser.error("Eval Lab must be importable (evallab.results.load_trial)")
    except _Mismatch as exc:
        parser.error(str(exc))
    except _BAD_INPUT:
        parser.error("cannot read or reconcile the supplied evidence")
    print(json.dumps(aggregate, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
