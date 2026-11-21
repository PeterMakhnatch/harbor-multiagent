from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any, cast

_WORLDS = (1, 2, 3, 5, 6, 7, 9, 10, 11)
_DIRECTIONS = (0, 1)
_COORDINATES = tuple(product(_WORLDS, _DIRECTIONS, (0,)))
_ARMS = ("blind", "peer")
_SEATS = ("p1", "p2")
_ROLES = ("sender", "receiver")
_STATUSES = ("complete", "partial", "invalid_seed", "not_started", "invalid_evidence")
_SCORE_STATUSES = ("complete", "partial")
_REQUESTED = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SCHEMA = "seatlab.calendar-query-archive/v1"
_DEFAULT = (
    Path(__file__).resolve().parent.parent / "results" / "calendar-query-native.json"
)

_Row = Mapping[str, Any]


def _mapping(value: object, fields: tuple[str, ...]) -> _Row:
    if (
        not isinstance(value, Mapping)
        or not all(isinstance(key, str) for key in value)
        or not all(key in value for key in fields)
    ):
        raise ValueError("missing required calendar query fields")
    return cast(_Row, value)


def _slot(value: object) -> bool:
    return type(value) is int and 0 <= value < 35


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _check_lists(value: object, keys: tuple[str, ...]) -> None:
    lists = _mapping(value, keys)
    for key in keys:
        slots = lists[key]
        if slots is not None and (
            not isinstance(slots, list)
            or len(slots) > 35
            or not all(_slot(slot) for slot in slots)
            or slots != sorted(set(slots))
        ):
            raise ValueError("expected ascending, unique calendar starts or null")


def _check_arm(value: object, trial_ids: set[str]) -> None:
    arm = _mapping(value, ("reward", "slot", "trial_id_sha256"))
    reward, slot, trial_id = (arm[key] for key in ("reward", "slot", "trial_id_sha256"))
    if reward is not None and (type(reward) is not int or reward not in (0, 1)):
        raise ValueError("expected binary native reward or null")
    if slot is not None and not _slot(slot):
        raise ValueError("expected a native slot from 0 to 34 or null")
    if reward is not None and trial_id is None:
        raise ValueError("scored native arm lacks a trial digest")
    if (slot is not None and reward is None) or (slot is None and reward == 1):
        raise ValueError("contradictory native reward and slot")
    if trial_id is not None:
        if not _digest(trial_id):
            raise ValueError("invalid native trial digest")
        if trial_id in trial_ids:
            raise ValueError("reused native trial identity")
        trial_ids.add(trial_id)


def _check_turns(value: object, ids: set[str]) -> None:
    if not isinstance(value, list) or len(value) > 6:
        raise ValueError("expected at most six native turn receipts per block")
    for item in value:
        turn = _mapping(item, ("id", "role", "prompt_sha256", "response_sha256"))
        identifier = turn["id"]
        if (
            not isinstance(identifier, str)
            or not identifier
            or turn["role"] not in _ROLES
            or not all(
                _digest(turn[key]) for key in ("prompt_sha256", "response_sha256")
            )
        ):
            raise ValueError("invalid native turn receipt")
        if identifier in ids:
            raise ValueError("duplicate native turn receipt")
        ids.add(identifier)


def _registered(rows: Iterable[_Row]) -> list[_Row]:
    registered: dict[tuple[int, int, int], _Row] = {}
    trial_ids: set[str] = set()
    turn_ids: set[str] = set()
    for value in rows:
        row = _mapping(
            value,
            (
                "world",
                "direction",
                "repetition",
                "status",
                "receiver",
                "starts",
                "revised",
                "arms",
                "turns",
            ),
        )
        key = (row["world"], row["direction"], row["repetition"])
        if any(type(part) is not int for part in key) or key not in _COORDINATES:
            raise ValueError("unregistered native block coordinates")
        if key in registered:
            raise ValueError("duplicate registered native block")
        status = row["status"]
        if status not in _STATUSES or row["receiver"] != _SEATS[1 - key[1]]:
            raise ValueError("invalid block status or receiver assignment")
        _check_lists(row["starts"], _SEATS)
        _check_lists(row["revised"], _ARMS)
        arms = _mapping(row["arms"], _ARMS)
        for arm in _ARMS:
            _check_arm(arms[arm], trial_ids)
        blind, peer = (arms[name] for name in _ARMS)
        if status in _SCORE_STATUSES:
            paired = blind["reward"] is not None and peer["reward"] is not None
            if (status == "complete") != paired:
                raise ValueError("contradictory block status and native rewards")
            if (
                paired
                and blind["slot"] is not None
                and blind["slot"] == peer["slot"]
                and blind["reward"] != peer["reward"]
            ):
                raise ValueError("contradictory native rewards for the same slot")
        _check_turns(row["turns"], turn_ids)
        registered[key] = row
    if len(registered) != len(_COORDINATES):
        raise ValueError(
            "missing registered native blocks; include explicit unknown rows"
        )
    return [registered[key] for key in _COORDINATES]


def _comparison(rows: list[_Row]) -> dict[str, Any]:
    arms = {}
    for name in _ARMS:
        outcomes = [
            row["arms"][name] for row in rows if row["status"] in _SCORE_STATUSES
        ]
        scores = [arm for arm in outcomes if arm["reward"] is not None]
        arms[name] = {
            "scored": len(scores),
            "success": sum(arm["reward"] == 1 for arm in scores),
            "model_invalid_scored_zero": sum(arm["slot"] is None for arm in scores),
            "unknown": len(rows) - len(scores),
        }
    deltas: dict[int, list[int]] = {row["world"]: [] for row in rows}
    counts: Counter[int] = Counter()
    lower = upper = slot_pairs = equal_slots = 0
    for row in rows:
        blind, peer = (row["arms"][name] for name in _ARMS)
        b, p = None, None
        if row["status"] in _SCORE_STATUSES:
            b, p = blind["reward"], peer["reward"]
        lower += (0 if p is None else p) - (1 if b is None else b)
        upper += (1 if p is None else p) - (0 if b is None else b)
        if b is None or p is None:
            continue
        delta = p - b
        deltas[row["world"]].append(delta)
        counts[delta] += 1
        if blind["slot"] is not None and peer["slot"] is not None:
            slot_pairs += 1
            equal_slots += blind["slot"] == peer["slot"]
    supported = [mean(values) for values in deltas.values() if values]
    observed = mean(supported) if supported else None
    return {
        "registered_blocks": len(rows),
        "arms": arms,
        "paired": {
            "blocks": counts.total(),
            "improved": counts[1],
            "harmed": counts[-1],
            "unchanged": counts[0],
        },
        "native_slot_equality": {"comparable": slot_pairs, "equal": equal_slots},
        "equal_world": {
            "worlds_with_pair": len(supported),
            "registered_worlds": len(deltas),
            "peer_minus_blind": observed if len(supported) == len(deltas) else None,
            "observed_worlds_only_peer_minus_blind": observed,
        },
        "registered_peer_minus_blind_bounds": [lower / len(rows), upper / len(rows)],
    }


def _final_prompt_equality(rows: list[_Row]) -> dict[str, int]:
    comparable = equal = 0
    for row in rows:
        if row["status"] not in _SCORE_STATUSES:
            continue
        run = f"rx-w{row['world']:02d}-d{row['direction']}-r0"
        prompts = {
            turn["id"]: turn["prompt_sha256"]
            for turn in row["turns"]
            if turn["role"] == "receiver"
        }
        blind, peer = (prompts.get(f"{run}-{arm}:cal_decide") for arm in _ARMS)
        if blind is not None and peer is not None:
            comparable += 1
            equal += blind == peer
    return {"comparable": comparable, "equal": equal}


def _list_counts(lists: list[list[int] | None]) -> dict[str, int]:
    lengths = Counter(len(slots) for slots in lists if slots is not None)
    return {
        "known": lengths.total(),
        "unknown": len(lists) - lengths.total(),
        "exactly_requested": lengths[_REQUESTED],
        "exceeded_requested": sum(
            count for length, count in lengths.items() if length > _REQUESTED
        ),
    }


def _recommendations(rows: list[_Row]) -> dict[str, Any]:
    starts = [
        row["starts"]
        if row["status"] in (*_SCORE_STATUSES, "invalid_seed")
        else dict.fromkeys(_SEATS)
        for row in rows
    ]
    revised = [
        row["revised"] if row["status"] in _SCORE_STATUSES else dict.fromkeys(_ARMS)
        for row in rows
    ]
    overlap = {arm: [] for arm in _ARMS}
    paired: Counter[str] = Counter()
    exceeded = 0
    for row, seeds, recommendations in zip(rows, starts, revised, strict=True):
        lists = (
            seeds["p1"],
            seeds["p2"],
            recommendations["blind"],
            recommendations["peer"],
        )
        exceeded += any(
            slots is not None and len(slots) > _REQUESTED for slots in lists
        )
        receiver = seeds[row["receiver"]]
        if receiver is None:
            continue
        candidates = set(receiver)
        shared = {
            arm: len(candidates.intersection(slots))
            for arm in _ARMS
            if (slots := recommendations[arm]) is not None
        }
        for arm, count in shared.items():
            overlap[arm].append(count)
        if len(shared) == 2:
            blind, peer = (shared[arm] for arm in _ARMS)
            paired[
                "increased"
                if peer > blind
                else "decreased"
                if peer < blind
                else "unchanged"
            ] += 1
    return {
        "requested_maximum_starts": _REQUESTED,
        "blocks_either_seat_exceeded": exceeded,
        "starts_by_seat": {
            seat: _list_counts([seeds[seat] for seeds in starts]) for seat in _SEATS
        },
        "sender_recommendations_by_arm": {
            arm: _list_counts([lists[arm] for lists in revised]) for arm in _ARMS
        },
        "overlap_with_original_receiver_candidates": {
            "arms": {
                arm: {
                    "comparable": len(counts),
                    "with_overlap": sum(count > 0 for count in counts),
                    "shared_starts": sum(counts),
                }
                for arm, counts in overlap.items()
            },
            "paired": {
                "blocks": paired.total(),
                **{key: paired[key] for key in ("increased", "decreased", "unchanged")},
            },
        },
    }


def analyze(rows: Iterable[_Row]) -> dict[str, Any]:
    registered = _registered(rows)
    status_counts = Counter(row["status"] for row in registered)
    receipts = Counter(
        turn["role"]
        for row in registered
        if row["status"] not in ("not_started", "invalid_evidence")
        for turn in row["turns"]
    )
    return {
        "coverage": {
            "worlds": len(_WORLDS),
            "blocks": len(registered),
            "teams": len(registered) * len(_ARMS),
            "block_status": {status: status_counts[status] for status in _STATUSES},
        },
        **_comparison(registered),
        "native_final_prompt_equality": _final_prompt_equality(registered),
        "by_world": {
            str(w): _comparison([row for row in registered if row["world"] == w])
            for w in _WORLDS
        },
        "by_direction": {
            str(d): _comparison([row for row in registered if row["direction"] == d])
            for d in _DIRECTIONS
        },
        "recommendations": _recommendations(registered),
        "response_receipts_by_role": {role: receipts[role] for role in _ROLES},
        "interpretation": (
            "Directions reuse worlds. The 2 teams in each block share opening lists. "
            "The bounds cover unknown rewards in this register, not sampling confidence. "
            "Receipts count bound responses, not provider calls."
        ),
    }


def _read_study(study: Path) -> list[_Row]:
    from .calendar_query_report import read_block
    from .calendar_query_run import blocks

    return [read_block(study, block) for block in blocks()]


def _read_archive(path: Path) -> list[_Row]:
    archive = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(archive, dict)
        or archive.get("schema") != _SCHEMA
        or not isinstance(archive.get("rows"), list)
    ):
        raise ValueError(
            "expected a seatlab.calendar-query-archive/v1 object with rows"
        )
    return archive["rows"]


def _print_summary(result: dict[str, Any]) -> None:
    coverage, paired = result["coverage"], result["paired"]
    print(
        f"Registered: {coverage['worlds']} worlds, {coverage['blocks']} blocks, "
        f"{coverage['teams']} teams."
    )
    statuses = ", ".join(
        f"{status} {count}" for status, count in coverage["block_status"].items()
    )
    print(f"Block status: {statuses}.")
    for arm, counts in result["arms"].items():
        if counts["scored"]:
            print(
                f"{arm}: {counts['success']}/{counts['scored']} tasks succeeded; {counts['unknown']} unknown."
            )
        else:
            print(f"{arm}: no scored tasks; {counts['unknown']} unknown.")
    print(
        f"Paired rewards ({paired['blocks']} blocks): {paired['improved']} improved, "
        f"{paired['harmed']} harmed, {paired['unchanged']} unchanged."
    )
    world = result["equal_world"]
    estimate = world["peer_minus_blind"]
    formatted = f"{100 * estimate:+.1f} pp" if estimate is not None else "not available"
    print(
        f"Equal-world change (peer minus blind): {formatted} "
        f"({world['worlds_with_pair']}/{world['registered_worlds']} worlds with a pair)."
    )
    lo, hi = result["registered_peer_minus_blind_bounds"]
    print(f"Bounds for registered blocks: [{100 * lo:+.1f}, {100 * hi:+.1f}] pp.")
    equality = result["native_slot_equality"]
    if equality["comparable"]:
        print(
            f"Same native slot: {equality['equal']}/{equality['comparable']} blocks with 2 slots."
        )
    else:
        print("No blocks have 2 submitted slots.")
    recommendations = result["recommendations"]
    overlap = recommendations["overlap_with_original_receiver_candidates"]["arms"]
    for arm, counts in overlap.items():
        if counts["comparable"]:
            print(
                f"{arm} recommendations overlap the receiver's first list in {counts['with_overlap']}/{counts['comparable']} comparable blocks."
            )
        else:
            print(f"{arm}: no comparable recommendations.")
    exceed = recommendations["blocks_either_seat_exceeded"]
    receipts = result["response_receipts_by_role"]
    print(f"Either seat exceeds 2 starts in {exceed} observed blocks.")
    print(
        f"Response receipts: {receipts['sender']} sender, {receipts['receiver']} receiver."
    )
    print(result["interpretation"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare registered native calendar query blocks."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--study", type=Path)
    source.add_argument("--input", type=Path, default=_DEFAULT)
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json", action="store_true", help="Emit the complete analysis."
    )
    output.add_argument(
        "--export",
        action="store_true",
        help="From --study, export verified native scores, decoded slot choices and turn commitments, not private task files.",
    )
    args = parser.parse_args(argv)
    if args.export and args.study is None:
        parser.error("--export requires a private native --study")
    try:
        rows = (
            _read_study(args.study)
            if args.study is not None
            else _read_archive(args.input)
        )
        result = analyze(rows)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.export:
        print(
            json.dumps(
                {"schema": _SCHEMA, "rows": rows},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    elif args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
