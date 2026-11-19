#!/usr/bin/env python3
"""Compare deterministic calendar disclosures without running Harbor trials."""

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cases import list_cases, load_case, member_starts

Seat = Callable[[int], int | None]
ROLES = ("m1", "m2", "m3", "m4", "m5")
MODES = ("full", "first8", "cursor")
SCHEMA = "seatlab.calendar-cursor/v1"


@dataclass(frozen=True)
class Resources:
    round_trips: int
    seat_exchanges: int
    scalar_disclosures: int
    distinct_disclosed_starts: int
    request_payload_bytes: int
    response_payload_bytes: int

    @property
    def payload_bytes(self) -> int:
        return self.request_payload_bytes + self.response_payload_bytes


@dataclass(frozen=True)
class Decision:
    start: int | None
    resources: Resources
    round_bounds: tuple[int, ...] = ()


def _check_horizon(horizon: int) -> None:
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("horizon must be a positive integer")


def _check_starts(starts: Sequence[int], horizon: int) -> None:
    previous = -1
    for tick in starts:
        if type(tick) is not int or not previous < tick < horizon:
            raise ValueError(
                "starts must be strictly increasing integer ticks in the horizon"
            )
        previous = tick


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _wire_bytes(value: int | None | list[int]) -> int:
    return len(_compact_json(value).encode("utf-8"))


def make_cursor(starts: Sequence[int], *, horizon: int) -> Seat:
    """Open a single-use seat over a private, unchanging local start sequence."""
    _check_horizon(horizon)
    _check_starts(starts, horizon)
    iterator = iter(starts)
    candidate = next(iterator, None)
    previous_bound = -1

    def next_at_or_after(bound: int) -> int | None:
        nonlocal candidate, previous_bound
        if type(bound) is not int or not previous_bound < bound < horizon:
            raise ValueError(
                "bounds must be strictly increasing integer ticks in the horizon"
            )
        previous_bound = bound
        while candidate is not None and candidate < bound:
            candidate = next(iterator, None)
        return candidate

    return next_at_or_after


def coordinate_cursor(participants: Mapping[str, Seat], *, horizon: int) -> Decision:
    """Find the earliest common start using only public bounds and seat replies."""
    _check_horizon(horizon)
    if not participants:
        raise ValueError("at least one seat is required")

    previous: dict[str, int | None] = dict.fromkeys(participants)
    bounds: list[int] = []
    disclosures = distinct = requests = responses = 0
    bound = 0
    while True:
        bounds.append(bound)
        requests += len(participants) * _wire_bytes(bound)
        next_bound = bound
        exhausted = False
        for seat, ask in participants.items():
            tick = ask(bound)
            if tick is not None and (
                type(tick) is not int or not bound <= tick < horizon
            ):
                raise ValueError(f"{seat}: invalid reply {tick!r} at bound {bound}")
            known = previous[seat]
            if known is not None and known >= bound and tick != known:
                raise ValueError(
                    f"{seat}: reply {tick!r} contradicts known start {known}"
                )
            previous[seat] = tick
            responses += _wire_bytes(tick)
            if tick is None:
                exhausted = True
            else:
                disclosures += 1
                # A monotone seat can repeat only its most recent disclosed tick.
                distinct += tick != known
                next_bound = max(next_bound, tick)
        if exhausted:
            start = None
            break
        if all(tick == next_bound for tick in previous.values()):
            start = next_bound
            break
        # All replies are >= bound. If they differ, their maximum is > bound.
        bound = next_bound

    rounds = len(bounds)
    return Decision(
        start=start,
        resources=Resources(
            round_trips=rounds,
            seat_exchanges=rounds * len(participants),
            scalar_disclosures=disclosures,
            distinct_disclosed_starts=distinct,
            request_payload_bytes=requests,
            response_payload_bytes=responses,
        ),
        round_bounds=tuple(bounds),
    )


def choose_one_shot(reports: Mapping[str, list[int]], *, horizon: int) -> Decision:
    """Choose from the received lists, without access to unreported starts."""
    _check_horizon(horizon)
    common: set[int] | None = None
    disclosures = responses = 0
    for starts in reports.values():
        _check_starts(starts, horizon)
        if common is None:
            common = set(starts)
        else:
            common.intersection_update(starts)
        disclosures += len(starts)
        responses += _wire_bytes(starts)
    if common is None:
        raise ValueError("at least one seat is required")
    return Decision(
        start=min(common, default=None),
        resources=Resources(
            round_trips=1,
            seat_exchanges=len(reports),
            scalar_disclosures=disclosures,
            distinct_disclosed_starts=disclosures,
            request_payload_bytes=0,
            response_payload_bytes=responses,
        ),
    )


def _physically_feasible(case: dict[str, Any], start: int | None) -> bool | None:
    if start is None:
        return None
    end = start + case["duration"]
    day = case["slots_per_day"]
    if not 0 <= start < end <= case["horizon"] or start // day != (end - 1) // day:
        return False
    return all(
        not any(start <= busy < end for busy in ticks)
        for ticks in case["members"].values()
    )


def analyze_case(case: dict[str, Any]) -> dict[str, Any]:
    """Compare all three protocols for one canonical cases.py document."""
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
    decisions = {
        "full": choose_one_shot(starts, horizon=horizon),
        "first8": choose_one_shot(
            {seat: ticks[:8] for seat, ticks in starts.items()}, horizon=horizon
        ),
        "cursor": coordinate_cursor(
            {
                seat: make_cursor(ticks, horizon=horizon)
                for seat, ticks in starts.items()
            },
            horizon=horizon,
        ),
    }
    return {
        "record_type": "case",
        "schema": SCHEMA,
        "case_id": case["case_id"],
        "dataset_commit": case["dataset_commit"],
        "seat_count": len(ROLES),
        "reference_start_count": len(case["gold"]),
        "full_cursor_same_start": decisions["full"].start == decisions["cursor"].start,
        "modes": {
            mode: {
                "start": decision.start,
                "reference_score": int(decision.start in case["gold"]),
                "physically_feasible": _physically_feasible(case, decision.start),
                "resources": {
                    **asdict(decision.resources),
                    "payload_bytes": decision.resources.payload_bytes,
                },
            }
            for mode, decision in decisions.items()
        },
    }


def analyze(dataset: Path, *, jsonl: Path | None = None) -> dict[str, Any]:
    """Return all-case calibration and optionally save case rows plus the summary."""
    records = [
        analyze_case(load_case(dataset, case_id)) for case_id in list_cases(dataset)
    ]
    if not records:
        raise ValueError("dataset contains no calendar cases")

    modes = {}
    for mode in MODES:
        rows = [record["modes"][mode] for record in records]
        resources = {}
        for field in rows[0]["resources"]:
            values = [row["resources"][field] for row in rows]
            total = sum(values)
            resources[field] = {
                "total": total,
                "mean": total / len(rows),
                "max": max(values),
            }
        modes[mode] = {
            "reference_passes": sum(row["reference_score"] for row in rows),
            "physically_feasible_choices": sum(
                row["physically_feasible"] is True for row in rows
            ),
            "physically_infeasible_choices": sum(
                row["physically_feasible"] is False for row in rows
            ),
            "null_choices": sum(row["start"] is None for row in rows),
            "resources": resources,
        }
    summary = {
        "record_type": "summary",
        "schema": SCHEMA,
        "execution": "local_simulation",
        "dataset_commit": records[0]["dataset_commit"],
        "case_count": len(records),
        "seat_count": len(ROLES),
        "full_cursor_agreements": sum(
            record["full_cursor_same_start"] for record in records
        ),
        "modes": modes,
    }
    if jsonl is not None:
        with jsonl.open("x", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(_compact_json(record) + "\n")
            output.write(_compact_json(summary) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    analysis = commands.add_parser("analyze", help="compare full, first8, and cursor")
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
        parser.exit(2, f"cursor: {error}\n")
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
