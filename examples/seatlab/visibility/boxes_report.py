"""Verify private box receiver receipts and report matched choices offline."""

import argparse
import json
import sqlite3
import stat
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any, TypedDict, cast

from . import boxes_choice, boxes_packet, boxes_run, deepseek, spend

_FIELDS = {
    "schema",
    "run",
    "role",
    "packet_sha256",
    "prompt_sha256",
    "status",
    "reason",
    "requested_model",
    "served_model",
    "usage",
    "response_text",
    "response_sha256",
    "original_box",
}
_OUTCOMES = ("peer_only_repeat", "own_repeat", "globally_new")
_STATUSES = (
    "accepted",
    "invalid",
    "failed",
    "held",
    "unattempted",
    "receipt_unavailable",
    "spend_unavailable",
    "source_unavailable",
)
_MAX_RECEIPT_BYTES = 6 * boxes_run.MAX_RESPONSE_BYTES + 4096


@dataclass(frozen=True, slots=True)
class _Result:
    status: str
    spend: str
    receipt_status: str | None = None
    original_box: int | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class _Pair:
    instance: int
    numbering: int
    repetition: int
    own: _Result
    shared: _Result

    def delta(self) -> dict[str, int] | None:
        if self.own.outcome is None or self.shared.outcome is None:
            return None
        return {
            name: int(self.shared.outcome == name) - int(self.own.outcome == name)
            for name in _OUTCOMES
        }

    def public(self) -> dict[str, int | str | dict[str, int] | None]:
        return {
            "instance": self.instance,
            "numbering": self.numbering,
            "repetition": self.repetition,
            "own_status": self.own.status,
            "shared_status": self.shared.status,
            "own_outcome": self.own.outcome,
            "shared_outcome": self.shared.outcome,
            "shared_minus_own": self.delta(),
        }


def _entries(path: Path) -> dict[str, Path]:
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("study directory invalid")
    return {entry.name: entry for entry in path.iterdir()}


def _claims(study: Path, registered: set[str]) -> dict[str, Path]:
    if not stat.S_ISDIR(study.lstat().st_mode):
        raise ValueError("study directory invalid")
    try:
        claims = _entries(study / "cells")
    except FileNotFoundError:
        return {}
    if not claims.keys() <= registered:
        raise ValueError("unregistered or duplicate cell sample")
    return claims


def _ledger(
    spend_dir: Path | None, registered: set[str]
) -> dict[str, sqlite3.Row] | None:
    if spend_dir is None:
        return None
    try:
        file = spend._location(spend_dir.expanduser().absolute())
        info = file.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("spend ledger file invalid")
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows: dict[str, sqlite3.Row] = {}
        for record in db.execute(
            """SELECT run, role, reserved_at, started_at, finished_at, served_model,
                      hit, miss, output, cost_low, cost_high
               FROM calls WHERE substr(run, 1, 3) = 'bx-' OR role = ?""",
            (boxes_run.ROLE,),
        ):
            row = cast("sqlite3.Row", record)  # The connection installs this factory.
            run: object = row["run"]
            if (
                not isinstance(run, str)
                or run not in registered
                or row["role"] != boxes_run.ROLE
                or run in rows
            ):
                raise ValueError("unexpected or duplicate box ledger call")
            rows[run] = row
        return rows


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def _model(value: object) -> bool:
    return isinstance(value, str) and value.casefold() in {
        deepseek.MODEL,
        deepseek.REVISION.casefold(),
    }


def _timestamp(value: object) -> datetime:
    try:
        time = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        time = None
    if time is None or time.utcoffset() != timedelta(0):
        raise ValueError("box ledger timestamp invalid")
    return time


def _spend_state(row: sqlite3.Row | None, available: bool) -> str:
    if not available:
        return "unavailable"
    if row is None:
        return "not_reserved"
    reserved = _timestamp(row["reserved_at"])
    if row["cost_high"] is None:
        if any(
            row[key] is not None
            for key in (
                "started_at",
                "finished_at",
                "served_model",
                "hit",
                "miss",
                "output",
                "cost_low",
            )
        ):
            raise ValueError("partial settlement on held box call")
        return "held"
    if not _model(row["served_model"]) or not all(
        _count(row[key]) for key in ("hit", "miss", "output", "cost_low", "cost_high")
    ):
        raise ValueError("box ledger model or token counts invalid")
    started = _timestamp(row["started_at"])
    finished = _timestamp(row["finished_at"])
    if reserved > started or not (
        timedelta(0) <= finished - started <= timedelta(minutes=10)
    ):
        raise ValueError("box ledger call order invalid")
    low = row["hit"] * 3 + row["miss"] * 150 + row["output"] * 600
    high = low if spend._offpeak(started) and spend._offpeak(finished) else 2 * low
    if (row["cost_low"], row["cost_high"]) != (low, high) or high > spend._HOLD:
        raise ValueError("box ledger costs invalid")
    return "settled"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate receipt fields")
    return result


def _receipt(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > _MAX_RECEIPT_BYTES
    ):
        raise ValueError("box receipt file invalid")
    with path.open("rb") as file:
        raw = file.read(_MAX_RECEIPT_BYTES + 1)
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise ValueError("box receipt file invalid")
    try:
        value: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (ValueError, RecursionError):
        raise ValueError("box receipt JSON invalid") from None
    if not isinstance(value, dict) or value.keys() != _FIELDS:
        raise ValueError("box receipt fields invalid")
    return cast("dict[str, Any]", value)


def _match_usage(receipt: dict[str, Any], row: sqlite3.Row | None, state: str) -> None:
    usage = receipt["usage"]
    if (
        not _model(receipt["served_model"])
        or not isinstance(usage, dict)
        or usage.keys() != {"input", "cached", "output"}
        or not all(_count(value) for value in usage.values())
        or usage["cached"] > usage["input"]
    ):
        raise ValueError("box receipt model or token counts invalid")
    if state == "unavailable":
        return
    if row is None or state != "settled":
        raise ValueError("box receipt has no matching settlement")
    if receipt["served_model"] != row["served_model"] or (
        usage["input"],
        usage["cached"],
        usage["output"],
    ) != (row["hit"] + row["miss"], row["hit"], row["output"]):
        raise ValueError("box receipt model or token counts differ from ledger")


def _replay(
    receipt: dict[str, Any],
    case: boxes_packet.Case,
    cell: boxes_choice.Cell,
    packet_digest: str,
    row: sqlite3.Row | None,
    state: str,
) -> _Result:
    if (
        receipt["schema"] != boxes_run.RECEIPT_SCHEMA
        or receipt["run"] != cell.id
        or receipt["role"] != boxes_run.ROLE
        or receipt["requested_model"] != deepseek.MODEL
    ):
        raise ValueError("box receipt identity mismatch")
    if receipt["packet_sha256"] != packet_digest:
        raise ValueError("box receipt packet commitment mismatch")
    prompt_digest = sha256(boxes_choice.prompt(case, cell).encode("utf-8")).hexdigest()
    if receipt["prompt_sha256"] != prompt_digest:
        raise ValueError("box receipt prompt commitment mismatch")

    status = receipt["status"]
    reason = receipt["reason"]
    text = receipt["response_text"]
    original = receipt["original_box"]
    if status == "failed":
        if (
            not isinstance(reason, str)
            or reason not in boxes_run.FAILURE_REASONS
            or text is not None
            or receipt["response_sha256"] is not None
            or original is not None
        ):
            raise ValueError("failed box receipt fields invalid")
        if receipt["served_model"] is not None or receipt["usage"] is not None:
            if reason != "provider_response_invalid":
                raise ValueError("unexpected usage on failed box receipt")
            _match_usage(receipt, row, state)
        elif state == "settled" and reason != "settlement_unverified":
            raise ValueError("settled failure lacks matching box usage")
        observed = {"unavailable": "spend_unavailable", "held": "held"}.get(
            state, "failed"
        )
        return _Result(observed, state, status)

    if status not in ("accepted", "invalid") or not isinstance(text, str):
        raise ValueError("box receipt status or response invalid")
    try:
        raw_text = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("box receipt response encoding invalid") from None
    if len(raw_text) > boxes_run.MAX_RESPONSE_BYTES:
        raise ValueError("box receipt response exceeds the retained limit")
    if sha256(raw_text).hexdigest() != receipt["response_sha256"]:
        raise ValueError("box receipt response commitment mismatch")
    _match_usage(receipt, row, state)
    try:
        decoded = boxes_choice.decode(text, cell.numbering)
    except (ValueError, RecursionError):
        decoded = None
    if status == "invalid":
        if reason != "invalid_choice" or decoded is not None or original is not None:
            raise ValueError("invalid box receipt does not match replay")
    elif reason is not None or type(original) is not int or original != decoded:
        raise ValueError("accepted box receipt does not match replay")
    if state == "unavailable":
        return _Result("spend_unavailable", state, status)
    if original is None:
        return _Result("invalid", state, status)
    return _Result(
        "accepted", state, status, original, boxes_choice.category(case, original)
    )


def _inspect(
    claim: Path | None,
    case: boxes_packet.Case | None,
    cell: boxes_choice.Cell,
    digest: str,
    row: sqlite3.Row | None,
    ledger_available: bool,
) -> _Result:
    state = _spend_state(row, ledger_available)
    if case is None:
        if claim is not None or row is not None:
            raise ValueError("evidence exists for an unavailable box source")
        return _Result("source_unavailable", state)
    if claim is None:
        if row is not None:
            raise ValueError("box ledger call lacks a registered claim")
        return _Result("unattempted", state)
    files = _entries(claim)
    if files.keys() - {"receipt.json"}:
        raise ValueError("unexpected or duplicate box receipt")
    if "receipt.json" not in files:
        status = {"unavailable": "spend_unavailable", "held": "held"}.get(
            state, "receipt_unavailable"
        )
        return _Result(status, state)
    return _replay(_receipt(files["receipt.json"]), case, cell, digest, row, state)


class _ArmSummary(TypedDict):
    scheduled: int
    outcome_denominator: int
    missing_choices: int
    status_counts: dict[str, int]
    outcomes: dict[str, int]


def _arms(results: dict[boxes_choice.Cell, _Result]) -> dict[str, _ArmSummary]:
    arms: dict[str, _ArmSummary] = {}
    for arm in ("own", "shared"):
        group = [result for cell, result in results.items() if cell.arm == arm]
        statuses = Counter(result.status for result in group)
        outcomes = Counter(result.outcome for result in group)
        arms[arm] = {
            "scheduled": len(group),
            "outcome_denominator": statuses["accepted"],
            "missing_choices": len(group) - statuses["accepted"] - statuses["invalid"],
            "status_counts": {name: statuses[name] for name in _STATUSES},
            "outcomes": {name: outcomes[name] for name in _OUTCOMES},
        }
    return arms


class _InstanceSummary(TypedDict):
    instance: int
    possible_pairs: int
    complete_pairs: int
    shared_minus_own_numerator: dict[str, int]


class _PairedSummary(TypedDict):
    possible_pairs: int
    complete_pairs: int
    registered_instances: int
    supported_instances: int
    per_instance: list[_InstanceSummary]
    shared_minus_own: dict[str, float] | None


def _paired(pairs: list[_Pair], instances: tuple[int, ...]) -> _PairedSummary:
    per_instance: list[_InstanceSummary] = []
    weighted = dict.fromkeys(_OUTCOMES, Fraction(0))
    supported = 0
    for instance in instances:
        group = [pair for pair in pairs if pair.instance == instance]
        deltas = [delta for pair in group if (delta := pair.delta()) is not None]
        numerator = {name: sum(delta[name] for delta in deltas) for name in _OUTCOMES}
        per_instance.append(
            {
                "instance": instance,
                "possible_pairs": len(group),
                "complete_pairs": len(deltas),
                "shared_minus_own_numerator": numerator,
            }
        )
        if deltas:
            supported += 1
            for name, total in numerator.items():
                weighted[name] += Fraction(total, len(deltas))
    return {
        "possible_pairs": sum(row["possible_pairs"] for row in per_instance),
        "complete_pairs": sum(row["complete_pairs"] for row in per_instance),
        "registered_instances": len(instances),
        "supported_instances": supported,
        "per_instance": per_instance,
        "shared_minus_own": (
            {name: float(value / supported) for name, value in weighted.items()}
            if supported and supported == len(instances)
            else None
        ),
    }


def _coverage_challenge(
    pairs: list[_Pair], deltas: dict[str, float] | None
) -> dict[str, int | bool | None]:
    shifts = Counter(
        pair.shared.outcome
        for pair in pairs
        if pair.own.outcome == "peer_only_repeat" and pair.shared.outcome is not None
    )
    return {
        "peer_only_to_own_repeat_pairs": shifts["own_repeat"],
        "peer_only_to_globally_new_pairs": shifts["globally_new"],
        "peer_only_rate_lower": None
        if deltas is None
        else deltas["peer_only_repeat"] < 0,
        "own_repeat_rate_higher": None if deltas is None else deltas["own_repeat"] > 0,
        "globally_new_rate_higher": None
        if deltas is None
        else deltas["globally_new"] > 0,
        "lower_peer_only_without_more_new": (
            None
            if deltas is None
            else deltas["peer_only_repeat"] < 0 and deltas["globally_new"] <= 0
        ),
    }


def analyze(
    study: Path,
    *,
    packet_path: Path,
    expected_sha256: str,
    spend_dir: Path | None = None,
) -> dict[str, Any]:
    """Read a stopped study; publish only verified choices and public grid coordinates."""
    cases = boxes_packet.load(packet_path, expected_sha256)
    cells = boxes_choice.grid()
    registered = {cell.id for cell in cells}
    claims = _claims(study, registered)
    ledger = _ledger(spend_dir, registered)
    results = {
        cell: _inspect(
            claims.get(cell.id),
            cases[cell.instance],
            cell,
            expected_sha256,
            ledger.get(cell.id) if ledger is not None else None,
            ledger is not None,
        )
        for cell in cells
    }
    indexed = {
        (cell.instance, cell.numbering, cell.repetition, cell.arm): result
        for cell, result in results.items()
    }
    pairs = [
        _Pair(
            cell.instance,
            cell.numbering,
            cell.repetition,
            results[cell],
            indexed[(cell.instance, cell.numbering, cell.repetition, "shared")],
        )
        for cell in cells
        if cell.arm == "own"
    ]
    instances = tuple(cases)
    earlier = tuple(
        instance
        for instance, case in cases.items()
        if case is not None and case.earlier_observation
    )
    primary = _paired(pairs, instances)
    return {
        "schema": "seatlab.box-receiver-report/v1",
        "scope": "Next box choices only. This does not measure hidden flags or team reward.",
        "basis": (
            "Only replayed choices with matching direct spend records count as outcomes. "
            "An unattempted cell has no private claim and no observed reservation. "
            "Receipts bind the requested model. The ledger attests the served model and "
            "token counts. It does not authenticate a provider."
        ),
        "counting": (
            "Only pairs with two accepted choices count in contrasts. "
            "Average complete contrasts within each instance, then weight instances equally. "
            "The earlier-observation cohort does not prove the original model saw those results."
        ),
        "packet_sha256": expected_sha256,
        "spend_ledger_available": ledger is not None,
        "arms": _arms(results),
        "primary": primary,
        "earlier_observation_sensitivity": _paired(
            [pair for pair in pairs if pair.instance in earlier],
            earlier,
        ),
        "coverage_challenge": _coverage_challenge(pairs, primary["shared_minus_own"]),
        **{
            f"by_{axis}": [
                {
                    axis: index,
                    "arms": _arms(
                        {
                            cell: result
                            for cell, result in results.items()
                            if getattr(cell, axis) == index
                        }
                    ),
                    "paired": _paired(
                        [pair for pair in pairs if getattr(pair, axis) == index],
                        instances,
                    ),
                }
                for index in (0, 1)
            ]
            for axis in ("numbering", "repetition")
        },
        "pairs": [pair.public() for pair in pairs],
        "cells": [
            {
                "instance": cell.instance,
                "numbering": cell.numbering,
                "repetition": cell.repetition,
                "arm": cell.arm,
                "status": result.status,
                "spend": result.spend,
                "receipt_status": result.receipt_status,
                "original_box": result.original_box,
                "outcome": result.outcome,
            }
            for cell, result in results.items()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--expect-sha256")
    args = parser.parse_args(argv)
    if (args.packet is None) != (args.expect_sha256 is None):
        parser.error("--packet and --expect-sha256 must be supplied together")
    try:
        result = analyze(
            args.study,
            packet_path=boxes_choice.PACKET if args.packet is None else args.packet,
            expected_sha256=(
                boxes_choice.PACKET_SHA256
                if args.expect_sha256 is None
                else args.expect_sha256
            ),
            spend_dir=args.spend_dir,
        )
    except (OSError, ValueError, sqlite3.Error):
        print(
            json.dumps({"error": "cannot_verify_box_receiver_study"}), file=sys.stderr
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
