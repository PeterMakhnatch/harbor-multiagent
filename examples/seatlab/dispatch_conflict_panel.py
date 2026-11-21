from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import TypedDict, cast

from .calendar_notices.visible_replay_summary import _sign_flip_p
from .dispatch_conflicts import Request, registry
from .receiver_panel import (
    _open_nonblocking,
    _print_json,
    _read_replies,
    _reject_constant,
    _shard,
    _unique_keys,
    _write_request,
)

_SCHEMA = "seatlab.dispatch-claim-conflicts/v1"
_WORLDS = range(6801, 6809)
_ARMS = ("shown", "flagged")
_REPEATS = 4
_CELL_COUNT = len(_WORLDS) * len(_ARMS) * _REPEATS
_SHIPMENTS = {"opened": 2, "unopened": 4}
_OPPORTUNITIES_PER_ARM = 96
_SHIPMENTS_PER_ARM = len(_WORLDS) * _REPEATS * sum(_SHIPMENTS.values())
_STATUSES = ("complete", "invalid", "missing")
_FIELDS = ("shipments", "opportunities", "unsupported", "feasible", "optimal")
_CELL_FIELDS = {
    "id",
    "world",
    "repeat",
    "arm",
    "prompt_sha256",
    "source_result_sha256",
    "status",
    "score",
}
_MAX_RECEIPT_BYTES = 128 * 1024

_Score = dict[str, dict[str, int]]
_Observations = dict[str, tuple[str, _Score | None]]


class _ArmCounts(TypedDict):
    first_replies: dict[str, int]
    opened: dict[str, int]
    unopened: dict[str, int]
    total: dict[str, int]


def _emit(cases: dict[str, Request], shard: tuple[int, int]) -> None:
    index, count = shard
    if count > len(_WORLDS):
        raise ValueError("use at most one worker per enrolled world")
    for case in cases.values():
        if (case.world - _WORLDS.start) % count == index:
            _write_request(
                case.id,
                "dispatch",
                case.world,
                case.source_result_sha256,
                case.prompt_sha256,
                case.system,
                case.prompt,
            )


def _identity(case: Request) -> dict[str, int | str]:
    return {
        "id": case.id,
        "world": case.world,
        "repeat": case.repeat,
        "arm": case.arm,
        "prompt_sha256": case.prompt_sha256,
        "source_result_sha256": case.source_result_sha256,
    }


def _check_score(value: object, case: Request) -> _Score:
    if not isinstance(value, dict) or value.keys() != _SHIPMENTS.keys():
        raise ValueError(f"{case.id}: invalid score groups")
    expected_opportunities = case.opportunities
    for group, shipments in _SHIPMENTS.items():
        row = value[group]
        if not isinstance(row, dict) or row.keys() != set(_FIELDS):
            raise ValueError(f"{case.id}: invalid {group} score fields")
        if not all(
            type(count) is int and 0 <= count <= shipments for count in row.values()
        ):
            raise ValueError(f"{case.id}: invalid {group} score counts")
        if (
            row["shipments"] != shipments
            or row["opportunities"] != expected_opportunities[group]
            or row["unsupported"] > row["opportunities"]
            or row["optimal"] > row["feasible"]
        ):
            raise ValueError(f"{case.id}: contradictory {group} score counts")
    return cast(_Score, value)


def _score_cell(case: Request, reply: str | None) -> dict[str, object]:
    status = "missing"
    score = None
    if reply is not None:
        try:
            scored = case.score(reply)
        except (ValueError, RecursionError):
            status = "invalid"
        else:
            status = "complete"
            score = _check_score(scored, case)
    return {**_identity(case), "status": status, "score": score}


def _score(files: list[Path], cases: dict[str, Request]) -> None:
    replies = _read_replies(files, cases)
    cells = [
        _score_cell(case, replies[case.id][0] if case.id in replies else None)
        for case in cases.values()
    ]
    _print_json({"schema": _SCHEMA, "cells": cells})


def _read_receipt(path: Path, cases: dict[str, Request]) -> _Observations:
    with open(path, "rb", opener=_open_nonblocking) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("expected a regular receipt file")
        data = source.read(_MAX_RECEIPT_BYTES + 1)
    if len(data) > _MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds the byte limit")
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )
    if (
        not isinstance(value, dict)
        or value.keys() != {"schema", "cells"}
        or value["schema"] != _SCHEMA
        or not isinstance(value["cells"], list)
        or len(value["cells"]) != len(cases)
    ):
        raise ValueError("expected a complete Dispatch conflict receipt grid")
    observations: _Observations = {}
    for cell in value["cells"]:
        if not isinstance(cell, dict) or cell.keys() != _CELL_FIELDS:
            raise ValueError("invalid receipt cell fields")
        cell_id = cell["id"]
        if (
            not isinstance(cell_id, str)
            or cell_id not in cases
            or cell_id in observations
        ):
            raise ValueError("unknown or duplicate receipt cell ID")
        case = cases[cell_id]
        if (
            type(cell["world"]) is not int
            or type(cell["repeat"]) is not int
            or any(cell[key] != expected for key, expected in _identity(case).items())
        ):
            raise ValueError(f"{cell_id}: receipt cell does not match enrollment")
        status = cell["status"]
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError(f"{cell_id}: invalid receipt cell status")
        score = cell["score"]
        if status == "complete":
            score = _check_score(score, case)
        elif score is not None:
            raise ValueError(f"{cell_id}: incomplete receipt cell has a score")
        observations[cell_id] = (status, score)
    return observations


def _arm_counts(cases: Iterable[Request], observations: _Observations) -> _ArmCounts:
    statuses = dict.fromkeys(_STATUSES, 0)
    opened = dict.fromkeys(_FIELDS, 0)
    unopened = dict.fromkeys(_FIELDS, 0)
    for case in cases:
        status, score = observations[case.id]
        statuses[status] += 1
        if score is not None:
            for field in _FIELDS:
                opened[field] += score["opened"][field]
                unopened[field] += score["unopened"][field]
    return {
        "first_replies": statuses,
        "opened": opened,
        "unopened": unopened,
        "total": {field: opened[field] + unopened[field] for field in _FIELDS},
    }


def _paired_effects(
    cases: dict[str, Request], observations: _Observations
) -> list[dict[str, int | None]]:
    effects = []
    for world in _WORLDS:
        shown, flagged = (
            _arm_counts(
                (
                    case
                    for case in cases.values()
                    if case.world == world and case.arm == arm
                ),
                observations,
            )
            for arm in _ARMS
        )
        matched = all(
            arm["first_replies"]["complete"] == _REPEATS for arm in (shown, flagged)
        )
        effects.append(
            {
                "world": world,
                "unsupported_shown_minus_flagged": (
                    shown["total"]["unsupported"] - flagged["total"]["unsupported"]
                    if matched
                    else None
                ),
                "stock_optimal_flagged_minus_shown": (
                    flagged["total"]["optimal"] - shown["total"]["optimal"]
                    if matched
                    else None
                ),
            }
        )
    return effects


def _behavioral_rule(
    arms: dict[str, _ArmCounts], effects: list[dict[str, int | None]]
) -> dict[str, object]:
    differences = [
        difference
        for effect in effects
        if (difference := effect["unsupported_shown_minus_flagged"]) is not None
    ]
    result: dict[str, object] = {
        "scope": "Conditional on the supplied replies. This does not verify model identity, "
        "first-attempt provenance, or charges. This is not a new native team result.",
        "verdict": "inconclusive",
        "matched_worlds": len(differences),
        "registered_opportunities_per_arm": _OPPORTUNITIES_PER_ARM,
        "registered_shipments_per_arm": _SHIPMENTS_PER_ARM,
        "unsupported_shown_minus_flagged": None,
        "unsupported_reduction_pp": None,
        "stock_optimal_flagged_minus_shown": None,
        "two_sided_sign_flip_p": None,
    }
    if len(differences) != len(_WORLDS):
        return result
    if any(
        arm["total"]["opportunities"] != _OPPORTUNITIES_PER_ARM
        or arm["total"]["shipments"] != _SHIPMENTS_PER_ARM
        for arm in arms.values()
    ):
        raise ValueError("complete grid has incorrect registered denominators")
    reduction = Fraction(sum(differences), _OPPORTUNITIES_PER_ARM)
    optimal_delta = (
        arms["flagged"]["total"]["optimal"] - arms["shown"]["total"]["optimal"]
    )
    p = _sign_flip_p(differences)
    passed = (
        reduction >= Fraction(1, 10) and p <= Fraction(1, 20) and optimal_delta >= 0
    )
    result.update(
        verdict="pass" if passed else "not_established",
        unsupported_shown_minus_flagged=sum(differences),
        unsupported_reduction_pp=float(100 * reduction),
        stock_optimal_flagged_minus_shown=optimal_delta,
        two_sided_sign_flip_p={"numerator": p.numerator, "denominator": p.denominator},
    )
    return result


def _summarize(path: Path, cases: dict[str, Request]) -> None:
    observations = _read_receipt(path, cases)
    arms = {
        arm: _arm_counts(
            (case for case in cases.values() if case.arm == arm), observations
        )
        for arm in _ARMS
    }
    effects = _paired_effects(cases, observations)
    _print_json(
        {
            "arms": arms,
            "by_world": effects,
            "behavioral_rule": _behavioral_rule(arms, effects),
        }
    )


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit, score, or summarize the fixed Dispatch report-conflict panel.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    emit = commands.add_parser("emit", allow_abbrev=False)
    emit.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    score = commands.add_parser("score", allow_abbrev=False)
    score.add_argument("files", nargs="+", type=Path, metavar="FILE")
    summarize = commands.add_parser("summarize", allow_abbrev=False)
    summarize.add_argument("receipt", type=Path, metavar="RECEIPT")
    args = parser.parse_args()
    if args.command == "score" and len(args.files) > _CELL_COUNT:
        score.error(f"expected at most {_CELL_COUNT} worker files")
    try:
        cases = registry()
        if len(cases) != _CELL_COUNT:
            raise ValueError("unexpected Dispatch conflict registry size")
        if args.command == "emit":
            _emit(cases, args.shard)
        elif args.command == "score":
            _score(args.files, cases)
        else:
            _summarize(args.receipt, cases)
    except (OSError, ValueError, RecursionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
