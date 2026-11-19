#!/usr/bin/env python3
"""Compare offline calendar negotiation protocols without running Harbor trials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cases import DATASET_COMMIT, list_cases, load_case, member_starts

SCHEMA = "seatlab.calendar-hybrid-cost/v1"
ROLES = ("m1", "m2", "m3", "m4", "m5")
STRATA = ("2", "3", "4", "5")
SCALAR_CAP = 8
CORPUS_SIZE = 200
ARMS = ("cursor", "budget8", "hybrid8", "bitset")
NATIVE_COMPARISON_CASES = tuple(
    f"{stratum}/{number}" for stratum in STRATA for number in range(7, 13)
)


@dataclass(frozen=True)
class ArmResult:
    """Protocol outcome and canonical payload accounting for one arm."""

    choice: int | None
    round_trips: int
    seat_exchanges: int
    scalar_disclosures: int
    distinct_disclosed_starts: int
    suffix_exchanges: int
    suffix_masks: int
    suffix_bit_positions: int
    request_bytes: int
    response_bytes: int
    payload_bytes: int
    physically_feasible: bool | None
    official_exact_match: bool


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _json_bytes(value: object) -> int:
    return len(_compact_json(value).encode("utf-8"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_compact_json(value).encode("utf-8")).hexdigest()


def _check_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _check_starts(starts: Sequence[int], horizon: int) -> None:
    previous = -1
    for tick in starts:
        if type(tick) is not int or not previous < tick < horizon:
            raise ValueError(
                "starts must be strictly increasing integer ticks in the horizon"
            )
        previous = tick


def suffix_hex_width(bound: int, horizon: int) -> int:
    """Return the canonical hex digit width ceil((horizon - bound) / 4)."""
    _check_positive_int(horizon, "horizon")
    if type(bound) is not int or not 0 <= bound < horizon:
        raise ValueError("bound must be an integer tick inside the horizon")
    return (horizon - bound + 3) // 4


def encode_suffix_hex(starts: Sequence[int], bound: int, horizon: int) -> str:
    """Encode feasible starts >= bound as canonical fixed-width lowercase hex.

    Bit 0 represents ``bound`` and bit ``k`` represents ``bound + k`` for
    ``0 <= k < horizon - bound``. High padding bits above the horizon are zero,
    and leading hex zeros are retained to reach ``ceil((horizon - bound) / 4)``.
    """
    width = suffix_hex_width(bound, horizon)
    _check_starts(starts, horizon)
    mask = 0
    index = bisect_left(starts, bound)
    for tick in starts[index:]:
        mask |= 1 << (tick - bound)
    return f"{mask:0{width}x}"


def decode_suffix_hex(mask_hex: str, bound: int, horizon: int) -> int:
    """Validate canonical fixed-width lowercase suffix hex and return its bitmask."""
    width = suffix_hex_width(bound, horizon)
    if (
        not isinstance(mask_hex, str)
        or len(mask_hex) != width
        or any(ch not in "0123456789abcdef" for ch in mask_hex)
    ):
        raise ValueError(
            f"suffix mask must be {width} lowercase hex digits at bound {bound}"
        )
    value = int(mask_hex, 16)
    bit_count = horizon - bound
    if value >> bit_count:
        raise ValueError("suffix mask has nonzero high padding bits")
    return value


def earliest_common_from_masks(
    masks: Mapping[str, str], bound: int, horizon: int
) -> int | None:
    """Intersect canonical suffix masks and return the smallest common start."""
    if not masks:
        raise ValueError("at least one seat mask is required")
    common: int | None = None
    for mask_hex in masks.values():
        bits = decode_suffix_hex(mask_hex, bound, horizon)
        common = bits if common is None else (common & bits)
    if not common:
        return None
    lowest_bit = (common & -common).bit_length() - 1
    return bound + lowest_bit


def _physically_feasible(case: Mapping[str, Any], choice: int | None) -> bool | None:
    if choice is None:
        return None
    duration = case["duration"]
    horizon = case["horizon"]
    slots_per_day = case["slots_per_day"]
    end = choice + duration
    if (
        type(choice) is not int
        or not 0 <= choice < end <= horizon
        or choice // slots_per_day != (end - 1) // slots_per_day
    ):
        return False
    return all(
        not any(choice <= tick < end for tick in busy)
        for busy in case["members"].values()
    )


def _score_arm(
    case: Mapping[str, Any],
    choice: int | None,
    *,
    round_trips: int,
    seat_exchanges: int,
    scalar_disclosures: int,
    distinct_disclosed_starts: int,
    suffix_exchanges: int,
    suffix_masks: int,
    suffix_bit_positions: int,
    request_bytes: int,
    response_bytes: int,
) -> ArmResult:
    """Attach physical feasibility and gold scoring only after a choice is made."""
    gold = case["gold"]
    official_exact_match = choice is not None and choice in gold
    return ArmResult(
        choice=choice,
        round_trips=round_trips,
        seat_exchanges=seat_exchanges,
        scalar_disclosures=scalar_disclosures,
        distinct_disclosed_starts=distinct_disclosed_starts,
        suffix_exchanges=suffix_exchanges,
        suffix_masks=suffix_masks,
        suffix_bit_positions=suffix_bit_positions,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        payload_bytes=request_bytes + response_bytes,
        physically_feasible=_physically_feasible(case, choice),
        official_exact_match=official_exact_match,
    )


def _run_scalar_rounds(
    starts_by_role: Mapping[str, Sequence[int]],
    *,
    horizon: int,
    max_rounds: int | None,
) -> dict[str, Any]:
    """Run monotone scalar cursor rounds until terminal or max_rounds is reached."""
    _check_positive_int(horizon, "horizon")
    if max_rounds is not None:
        _check_positive_int(max_rounds, "max_rounds")
    roles = tuple(starts_by_role)
    if not roles:
        raise ValueError("at least one seat is required")
    for ticks in starts_by_role.values():
        _check_starts(ticks, horizon)

    seat_count = len(roles)
    round_trips = 0
    scalar_disclosures = 0
    distinct: set[tuple[str, int]] = set()
    request_bytes = 0
    response_bytes = 0
    bound = 0

    while True:
        round_trips += 1
        request_bytes += seat_count * _json_bytes(bound)
        replies: list[int | None] = []
        next_bound = bound
        exhausted = False
        for role in roles:
            ticks = starts_by_role[role]
            idx = bisect_left(ticks, bound)
            reply = ticks[idx] if idx < len(ticks) else None
            replies.append(reply)
            response_bytes += _json_bytes(reply)
            if reply is None:
                exhausted = True
            else:
                scalar_disclosures += 1
                distinct.add((role, reply))
                if reply > next_bound:
                    next_bound = reply

        if exhausted:
            return {
                "terminal": True,
                "choice": None,
                "next_bound": next_bound,
                "round_trips": round_trips,
                "seat_exchanges": round_trips * seat_count,
                "scalar_disclosures": scalar_disclosures,
                "distinct_disclosed_starts": len(distinct),
                "request_bytes": request_bytes,
                "response_bytes": response_bytes,
            }
        if all(reply == next_bound for reply in replies):
            return {
                "terminal": True,
                "choice": next_bound,
                "next_bound": next_bound,
                "round_trips": round_trips,
                "seat_exchanges": round_trips * seat_count,
                "scalar_disclosures": scalar_disclosures,
                "distinct_disclosed_starts": len(distinct),
                "request_bytes": request_bytes,
                "response_bytes": response_bytes,
            }
        if max_rounds is not None and round_trips == max_rounds:
            return {
                "terminal": False,
                "choice": None,
                "next_bound": next_bound,
                "round_trips": round_trips,
                "seat_exchanges": round_trips * seat_count,
                "scalar_disclosures": scalar_disclosures,
                "distinct_disclosed_starts": len(distinct),
                "request_bytes": request_bytes,
                "response_bytes": response_bytes,
            }
        bound = next_bound


def _run_suffix_round(
    starts_by_role: Mapping[str, Sequence[int]],
    *,
    bound: int,
    horizon: int,
) -> dict[str, Any]:
    """Run one canonical suffix-hex round at bound and return choice plus costs."""
    suffix_hex_width(bound, horizon)
    roles = tuple(starts_by_role)
    if not roles:
        raise ValueError("at least one seat is required")
    seat_count = len(roles)
    request_bytes = seat_count * _json_bytes(["suffix-hex", bound])
    masks: dict[str, str] = {}
    response_bytes = 0
    for role in roles:
        mask_hex = encode_suffix_hex(starts_by_role[role], bound, horizon)
        masks[role] = mask_hex
        response_bytes += _json_bytes(mask_hex)
    choice = earliest_common_from_masks(masks, bound, horizon)
    return {
        "choice": choice,
        "masks": masks,
        "round_trips": 1,
        "seat_exchanges": seat_count,
        "suffix_exchanges": 1,
        "suffix_masks": seat_count,
        "suffix_bit_positions": seat_count * (horizon - bound),
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
    }


def simulate_cursor(
    starts_by_role: Mapping[str, Sequence[int]], *, horizon: int
) -> dict[str, Any]:
    """Unbounded exact scalar cursor negotiation."""
    outcome = _run_scalar_rounds(starts_by_role, horizon=horizon, max_rounds=None)
    return {
        "choice": outcome["choice"],
        "round_trips": outcome["round_trips"],
        "seat_exchanges": outcome["seat_exchanges"],
        "scalar_disclosures": outcome["scalar_disclosures"],
        "distinct_disclosed_starts": outcome["distinct_disclosed_starts"],
        "suffix_exchanges": 0,
        "suffix_masks": 0,
        "suffix_bit_positions": 0,
        "request_bytes": outcome["request_bytes"],
        "response_bytes": outcome["response_bytes"],
    }


def simulate_cursor8(
    starts_by_role: Mapping[str, Sequence[int]],
    *,
    horizon: int,
    cap: int = SCALAR_CAP,
) -> dict[str, Any]:
    """Scalar cursor stopped after at most ``cap`` rounds (nonterminal -> None)."""
    outcome = _run_scalar_rounds(starts_by_role, horizon=horizon, max_rounds=cap)
    return {
        "choice": outcome["choice"],
        "round_trips": outcome["round_trips"],
        "seat_exchanges": outcome["seat_exchanges"],
        "scalar_disclosures": outcome["scalar_disclosures"],
        "distinct_disclosed_starts": outcome["distinct_disclosed_starts"],
        "suffix_exchanges": 0,
        "suffix_masks": 0,
        "suffix_bit_positions": 0,
        "request_bytes": outcome["request_bytes"],
        "response_bytes": outcome["response_bytes"],
    }


def simulate_hybrid8(
    starts_by_role: Mapping[str, Sequence[int]],
    *,
    horizon: int,
    cap: int = SCALAR_CAP,
) -> dict[str, Any]:
    """Up to ``cap`` scalar cursor rounds, then one suffix-hex fallback round."""
    scalar = _run_scalar_rounds(starts_by_role, horizon=horizon, max_rounds=cap)
    if scalar["terminal"]:
        return {
            "choice": scalar["choice"],
            "round_trips": scalar["round_trips"],
            "seat_exchanges": scalar["seat_exchanges"],
            "scalar_disclosures": scalar["scalar_disclosures"],
            "distinct_disclosed_starts": scalar["distinct_disclosed_starts"],
            "suffix_exchanges": 0,
            "suffix_masks": 0,
            "suffix_bit_positions": 0,
            "request_bytes": scalar["request_bytes"],
            "response_bytes": scalar["response_bytes"],
        }
    suffix = _run_suffix_round(
        starts_by_role, bound=scalar["next_bound"], horizon=horizon
    )
    return {
        "choice": suffix["choice"],
        "round_trips": scalar["round_trips"] + suffix["round_trips"],
        "seat_exchanges": scalar["seat_exchanges"] + suffix["seat_exchanges"],
        "scalar_disclosures": scalar["scalar_disclosures"],
        "distinct_disclosed_starts": scalar["distinct_disclosed_starts"],
        "suffix_exchanges": suffix["suffix_exchanges"],
        "suffix_masks": suffix["suffix_masks"],
        "suffix_bit_positions": suffix["suffix_bit_positions"],
        "request_bytes": scalar["request_bytes"] + suffix["request_bytes"],
        "response_bytes": scalar["response_bytes"] + suffix["response_bytes"],
    }


def simulate_bitset(
    starts_by_role: Mapping[str, Sequence[int]], *, horizon: int
) -> dict[str, Any]:
    """One-round pure full bitset using the same canonical hex encoding at bound 0."""
    suffix = _run_suffix_round(starts_by_role, bound=0, horizon=horizon)
    return {
        "choice": suffix["choice"],
        "round_trips": suffix["round_trips"],
        "seat_exchanges": suffix["seat_exchanges"],
        "scalar_disclosures": 0,
        "distinct_disclosed_starts": 0,
        "suffix_exchanges": suffix["suffix_exchanges"],
        "suffix_masks": suffix["suffix_masks"],
        "suffix_bit_positions": suffix["suffix_bit_positions"],
        "request_bytes": suffix["request_bytes"],
        "response_bytes": suffix["response_bytes"],
    }


def analyze_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Measure all four protocol arms on one canonical cases.py document."""
    members = case["members"]
    if set(members) != set(ROLES):
        raise ValueError("calendar case must contain exactly seats m1 through m5")
    horizon = _check_positive_int(case["horizon"], "horizon")
    duration = _check_positive_int(case["duration"], "duration")
    slots_per_day = _check_positive_int(case["slots_per_day"], "slots_per_day")

    # Compute local feasible starts solely from private member busy ticks.
    # Gold is never passed to any protocol simulator; it is only used in _score_arm.
    starts_by_role = {
        role: member_starts(
            members[role],
            duration=duration,
            horizon=horizon,
            slots_per_day=slots_per_day,
        )
        for role in ROLES
    }

    raw_arms = {
        "cursor": simulate_cursor(starts_by_role, horizon=horizon),
        "budget8": simulate_cursor8(starts_by_role, horizon=horizon),
        "hybrid8": simulate_hybrid8(starts_by_role, horizon=horizon),
        "bitset": simulate_bitset(starts_by_role, horizon=horizon),
    }

    arms = {
        name: asdict(_score_arm(case, **metrics)) for name, metrics in raw_arms.items()
    }
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "stratum": case["case_id"].split("/", 1)[0],
        "dataset_commit": case["dataset_commit"],
        "case_sha256": _sha256(case),
        "exact_arms_agree": (
            arms["cursor"]["choice"]
            == arms["hybrid8"]["choice"]
            == arms["bitset"]["choice"]
        ),
        "arms": arms,
    }
    if "source_sha256" in case:
        row["source_sha256"] = dict(case["source_sha256"])
    return row


def summarize_cases(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate outcome counts, denominators, and resource sums for a case slice."""
    case_count = len(rows)
    arms_summary: dict[str, Any] = {}
    sum_fields = (
        "round_trips",
        "seat_exchanges",
        "scalar_disclosures",
        "distinct_disclosed_starts",
        "suffix_exchanges",
        "suffix_masks",
        "suffix_bit_positions",
        "request_bytes",
        "response_bytes",
        "payload_bytes",
    )
    for arm in ARMS:
        arm_rows = [row["arms"][arm] for row in rows]
        non_null_count = sum(item["choice"] is not None for item in arm_rows)
        null_count = case_count - non_null_count
        exact_passes = sum(item["official_exact_match"] is True for item in arm_rows)
        feasible_passes = sum(item["physically_feasible"] is True for item in arm_rows)
        infeasible_count = sum(
            item["physically_feasible"] is False for item in arm_rows
        )
        sums = {field: sum(item[field] for item in arm_rows) for field in sum_fields}
        maxes = {
            field: max((item[field] for item in arm_rows), default=0)
            for field in sum_fields
        }
        arms_summary[arm] = {
            "cases": case_count,
            "non_null_choices": non_null_count,
            "null_choices": null_count,
            "official_exact_match": {
                "passes": exact_passes,
                "denominator": case_count,
            },
            "physically_feasible": {
                "passes": feasible_passes,
                "infeasible": infeasible_count,
                "denominator": non_null_count,
            },
            "sums": sums,
            "max": maxes,
        }
    return {
        "case_count": case_count,
        "exact_arms_agree": {
            "passes": sum(row["exact_arms_agree"] is True for row in rows),
            "denominator": case_count,
        },
        "arms": arms_summary,
    }


def _summarize_by_stratum(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {stratum: [] for stratum in STRATA}
    for row in rows:
        stratum = row["stratum"]
        grouped.setdefault(stratum, []).append(row)
    return {
        stratum: summarize_cases(group)
        for stratum, group in grouped.items()
        if group
    }


def analyze_dataset(dataset: Path) -> dict[str, Any]:
    """Analyze the official 200 numerically sorted MASBench Scheduling cases."""
    case_ids = list_cases(dataset)
    expected_ids = [
        f"{stratum}/{number}"
        for stratum, size in ((2, 40), (3, 60), (4, 60), (5, 40))
        for number in range(1, size + 1)
    ]
    if case_ids != expected_ids:
        raise ValueError(
            f"expected exactly {CORPUS_SIZE} official cases in strata of 40, 60, 60 and 40; "
            "a registered case ID is absent or an extra case ID is present"
        )

    rows = [analyze_case(load_case(dataset, case_id)) for case_id in case_ids]
    native_set = set(NATIVE_COMPARISON_CASES)
    native_rows = [row for row in rows if row["case_id"] in native_set]
    other_rows = [row for row in rows if row["case_id"] not in native_set]

    return {
        "schema": SCHEMA,
        "execution": "offline_deterministic_simulation",
        "dataset_commit": DATASET_COMMIT,
        "seat_count": len(ROLES),
        "scalar_cap": SCALAR_CAP,
        "case_count": len(rows),
        "request_accounting": {
            "scalar_round": "5 compact JSON integers (bound)",
            "hybrid8_suffix_round": '5 compact JSON arrays ["suffix-hex",bound]',
            "bitset_startup_round": '5 compact JSON arrays ["suffix-hex",0]',
            "response_scalar": "compact JSON integer or null",
            "response_suffix": "compact JSON quoted fixed-width lowercase hex mask",
            "excludes": [
                "hashes",
                "envelopes",
                "role_labels",
                "transport_framing",
                "latency_or_privacy_claims",
            ],
        },
        "native_comparison_case_ids": list(NATIVE_COMPARISON_CASES),
        "summary": {
            "all_cases": summarize_cases(rows),
            "by_stratum": _summarize_by_stratum(rows),
            "disjoint_native_7_to_12": {
                "case_ids": [row["case_id"] for row in native_rows],
                **summarize_cases(native_rows),
                "by_stratum": _summarize_by_stratum(native_rows),
            },
            "remaining_cases": {
                "case_count": len(other_rows),
                **summarize_cases(other_rows),
            },
        },
        "cases": rows,
    }


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write JSON to a new path without overwriting existing files."""
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"output path already exists: {path}")
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"output directory does not exist: {parent}")
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        os.link(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="MASBench repository root")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="path to a new JSON output file (must not already exist)",
    )
    args = parser.parse_args(argv)
    try:
        report = analyze_dataset(args.dataset)
        _write_new_json(args.out, report)
    except (OSError, ValueError) as error:
        parser.exit(2, f"hybrid_cost: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
