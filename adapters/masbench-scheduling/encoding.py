#!/usr/bin/env python3
"""Compare exact calendar encodings on the registered 200-case offline cohort.

The winner metric is total compact JSON body bytes: seat reply bytes, plus
cursor request bytes. All arms with the minimum share a tie. A sole winner is
counted separately. No framing, hashes, or runtime overhead enter this rank.
The rank does not measure privacy or latency.
"""

import argparse
import base64
import json
from collections.abc import Iterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from cases import DATASET_COMMIT, list_cases, load_case, member_starts
from cursor import ROLES, coordinate_cursor, make_cursor

SCHEMA = "seatlab.calendar-encoding/v1"
COHORT_SIZE = 200
STATIC_MODES = ("full", "runs", "bitmap")
MODES = (*STATIC_MODES, "cursor")


def _check_horizon(horizon: int) -> None:
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("horizon must be a positive integer")


def _ordered_ticks(starts: Sequence[object], horizon: int) -> Iterator[int]:
    _check_horizon(horizon)
    previous = -1
    for tick in starts:
        if type(tick) is not int or not previous < tick < horizon:
            raise ValueError(
                "starts must be strictly increasing integer ticks in the horizon"
            )
        yield tick
        previous = tick


def decode_full(payload: object, horizon: int) -> list[int]:
    """Read a JSON array of unique, increasing start ticks."""
    if type(payload) is not list:
        raise ValueError("full payload must be a list")
    for _ in _ordered_ticks(payload, horizon):
        pass
    return cast(list[int], payload)


def encode_runs(starts: Sequence[int], horizon: int) -> list[list[int]]:
    """Encode maximal ascending, inclusive runs of adjacent starts."""
    runs: list[list[int]] = []
    for tick in _ordered_ticks(starts, horizon):
        if runs and runs[-1][1] + 1 == tick:
            runs[-1][1] = tick
        else:
            runs.append([tick, tick])
    return runs


def decode_runs(payload: object, horizon: int) -> list[int]:
    _check_horizon(horizon)
    if type(payload) is not list:
        raise ValueError("run payload must be a list")
    starts: list[int] = []
    previous_end = -2
    for run in payload:
        if type(run) is not list or len(run) != 2:
            raise ValueError("each run must be a two-element list")
        lo, hi = run
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi < horizon:
            raise ValueError(
                "run endpoints must be ordered integer ticks in the horizon"
            )
        if lo <= previous_end + 1:
            raise ValueError("runs must be ascending, disjoint and nonadjacent")
        starts.extend(range(lo, hi + 1))
        previous_end = hi
    return starts


def encode_bitmap(starts: Sequence[int], horizon: int) -> str:
    """Encode tick i at bit 7 - i % 8 of byte i // 8, then use standard base64."""
    _check_horizon(horizon)
    raw = bytearray((horizon + 7) // 8)
    for tick in _ordered_ticks(starts, horizon):
        raw[tick // 8] |= 1 << (7 - tick % 8)
    return base64.b64encode(raw).decode("ascii")


def decode_bitmap(payload: object, horizon: int) -> list[int]:
    _check_horizon(horizon)
    size = (horizon + 7) // 8
    if type(payload) is not str or len(payload) != 4 * ((size + 2) // 3):
        raise ValueError("bitmap must be a base64 string of the expected length")
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as error:
        raise ValueError("bitmap must use standard base64") from error
    if len(raw) != size or base64.b64encode(raw).decode("ascii") != payload:
        raise ValueError("bitmap must use canonical base64 for the expected byte count")
    padding_bits = size * 8 - horizon
    if raw[-1] & ((1 << padding_bits) - 1):
        raise ValueError("bitmap has nonzero bits beyond the horizon")
    return [tick for tick in range(horizon) if raw[tick // 8] & (1 << (7 - tick % 8))]


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class _Outcome:
    start: int | None
    exact_members: int | None
    round_trips: int
    request_payload_bytes: int
    response_payload_bytes: int


class ModeRecord(TypedDict):
    start: int | None
    reference_score: int
    exact_members: int | None
    round_trips: int
    request_payload_bytes: int
    response_payload_bytes: int
    payload_bytes: int


class CaseRecord(TypedDict):
    schema: str
    record_type: str
    dataset_commit: str
    case_id: str
    stratum: int
    seat_count: int
    all_static_decodes_exact: bool
    earliest_agreement: bool
    reference_score_agreement: bool
    smallest_payload_modes: list[str]
    modes: dict[str, ModeRecord]


def _one_shot(mode: str, members: dict[str, list[int]], horizon: int) -> _Outcome:
    response_bytes = exact_members = 0
    common: set[int] | None = None
    for starts in members.values():
        reply: object
        if mode == "runs":
            reply = encode_runs(starts, horizon)
            decode = decode_runs
        elif mode == "bitmap":
            reply = encode_bitmap(starts, horizon)
            decode = decode_bitmap
        else:
            reply = starts
            decode = decode_full
        body = _compact_json(reply).encode("utf-8")
        response_bytes += len(body)
        decoded = decode(json.loads(body), horizon)
        exact_members += decoded == starts
        if common is None:
            common = set(decoded)
        else:
            common.intersection_update(decoded)
    if common is None:
        raise ValueError("at least one seat is required")
    return _Outcome(min(common, default=None), exact_members, 1, 0, response_bytes)


def analyze_case(case: dict[str, Any]) -> CaseRecord:
    """Run actual JSON codec round trips and independently score each choice."""
    members = case["members"]
    if set(members) != set(ROLES):
        raise ValueError("calendar case must contain exactly seats m1 through m5")
    horizon = case["horizon"]
    starts = {
        seat: member_starts(
            members[seat],
            duration=case["duration"],
            horizon=horizon,
            slots_per_day=case["slots_per_day"],
        )
        for seat in ROLES
    }
    outcomes = {mode: _one_shot(mode, starts, horizon) for mode in STATIC_MODES}
    cursor = coordinate_cursor(
        {seat: make_cursor(ticks, horizon=horizon) for seat, ticks in starts.items()},
        horizon=horizon,
    )
    outcomes["cursor"] = _Outcome(
        cursor.start,
        None,
        cursor.resources.round_trips,
        cursor.resources.request_payload_bytes,
        cursor.resources.response_payload_bytes,
    )
    gold = case["gold"]
    modes: dict[str, ModeRecord] = {
        mode: {
            "start": result.start,
            "reference_score": int(result.start is not None and result.start in gold),
            "exact_members": result.exact_members,
            "round_trips": result.round_trips,
            "request_payload_bytes": result.request_payload_bytes,
            "response_payload_bytes": result.response_payload_bytes,
            "payload_bytes": result.request_payload_bytes
            + result.response_payload_bytes,
        }
        for mode, result in outcomes.items()
    }
    minimum = min(mode["payload_bytes"] for mode in modes.values())
    return {
        "schema": SCHEMA,
        "record_type": "case",
        "dataset_commit": case["dataset_commit"],
        "case_id": case["case_id"],
        "stratum": int(case["case_id"].split("/", 1)[0]),
        "seat_count": len(ROLES),
        "all_static_decodes_exact": all(
            modes[mode]["exact_members"] == len(ROLES) for mode in STATIC_MODES
        ),
        "earliest_agreement": all(
            row["start"] == modes["full"]["start"] for row in modes.values()
        ),
        "reference_score_agreement": all(
            row["reference_score"] == modes["full"]["reference_score"]
            for row in modes.values()
        ),
        "smallest_payload_modes": [
            mode for mode in MODES if modes[mode]["payload_bytes"] == minimum
        ],
        "modes": modes,
    }


def _new_tally() -> dict[str, Any]:
    return {
        "case_count": 0,
        "all_static_decodes_exact_cases": 0,
        "earliest_agreement_cases": 0,
        "reference_score_agreement_cases": 0,
        "tie_cases": 0,
        "modes": {
            mode: {
                "exact_member_decodes": None if mode == "cursor" else 0,
                "reference_passes": 0,
                "sole_wins": 0,
                "tied_wins": 0,
                "total_round_trips": 0,
                "total_request_payload_bytes": 0,
                "total_response_payload_bytes": 0,
                "total_payload_bytes": 0,
                "max_payload_bytes": 0,
            }
            for mode in MODES
        },
    }


def _tally(group: dict[str, Any], record: CaseRecord) -> None:
    winners = record["smallest_payload_modes"]
    tied = len(winners) > 1
    group["case_count"] += 1
    group["all_static_decodes_exact_cases"] += record["all_static_decodes_exact"]
    group["earliest_agreement_cases"] += record["earliest_agreement"]
    group["reference_score_agreement_cases"] += record["reference_score_agreement"]
    group["tie_cases"] += tied
    for mode, row in record["modes"].items():
        totals = group["modes"][mode]
        if row["exact_members"] is not None:
            totals["exact_member_decodes"] += row["exact_members"]
        totals["reference_passes"] += row["reference_score"]
        if mode in winners:
            totals["tied_wins" if tied else "sole_wins"] += 1
        totals["total_round_trips"] += row["round_trips"]
        totals["total_request_payload_bytes"] += row["request_payload_bytes"]
        totals["total_response_payload_bytes"] += row["response_payload_bytes"]
        totals["total_payload_bytes"] += row["payload_bytes"]
        totals["max_payload_bytes"] = max(
            totals["max_payload_bytes"], row["payload_bytes"]
        )


def analyze(dataset: Path, *, jsonl: Path | None = None) -> dict[str, Any]:
    """Keep only cohort and stratum counters; stream the optional case evidence."""
    case_ids = list_cases(dataset)
    if len(case_ids) != COHORT_SIZE:
        raise ValueError(f"expected {COHORT_SIZE} cases, found {len(case_ids)}")
    cohort = _new_tally()
    strata: dict[int, dict[str, Any]] = {}
    stream = jsonl.open("x", encoding="utf-8", newline="\n") if jsonl else nullcontext()
    with stream as output:
        for case_id in case_ids:
            record = analyze_case(load_case(dataset, case_id))
            _tally(cohort, record)
            stratum = record["stratum"]
            if stratum not in strata:
                strata[stratum] = _new_tally()
            _tally(strata[stratum], record)
            if output is not None:
                output.write(_compact_json(record) + "\n")
        summary = {
            "schema": SCHEMA,
            "record_type": "summary",
            "execution": "local_simulation",
            "dataset_commit": DATASET_COMMIT,
            "seat_count": len(ROLES),
            "winner_metric": "payload_bytes",
            "winner_rule": "minimum across all four arms; all minima count as tied wins if shared",
            **cohort,
            "strata": [
                {"stratum": stratum, **strata[stratum]} for stratum in sorted(strata)
            ],
        }
        if output is not None:
            output.write(_compact_json(summary) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    analysis = commands.add_parser(
        "analyze", help="compare full, runs, bitmap, and cursor"
    )
    analysis.add_argument(
        "--dataset", type=Path, required=True, help="MASBench repository root"
    )
    analysis.add_argument(
        "--jsonl", type=Path, help="write case records and summary to a new file"
    )
    args = parser.parse_args()
    try:
        summary = analyze(args.dataset, jsonl=args.jsonl)
    except (OSError, ValueError) as error:
        parser.exit(2, f"encoding: {error}\n")
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    for check in (
        "all_static_decodes_exact_cases",
        "earliest_agreement_cases",
        "reference_score_agreement_cases",
    ):
        if summary[check] != summary["case_count"]:
            parser.exit(1, f"encoding: {check} failed for at least one case\n")


if __name__ == "__main__":
    main()
