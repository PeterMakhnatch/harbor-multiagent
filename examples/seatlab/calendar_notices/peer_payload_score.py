import argparse
import json
import sqlite3
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import Any

_SCHEMA = "seatlab.calendar-peer-payload/v1"
_ARMS = ("shown", "masked")
_WORLDS = tuple(f"w{i:02d}" for i in range(12, 24))
_GRID = tuple(product(_WORLDS, range(2), _ARMS))
_MAX_RECEIPT = 1024 * 1024
Row = dict[str, Any]


def _optional_read(path: Path, limit: int) -> bytes | None:
    from ..visibility import calendar_packet

    try:
        path.lstat()
    except FileNotFoundError:
        return None
    if path.resolve(strict=True) != path:
        raise ValueError("unsafe_study_artifact")
    return calendar_packet._read(path, limit)


def _first_reply(expected: Row, recorded: Row) -> Row:
    from ..visibility import deepseek
    from . import audit
    from . import protocol as p

    text = recorded.get("response_text")
    model = recorded.get("served_model")
    if not isinstance(text, str):
        raise TypeError("missing_reply_text")
    audit.require(
        isinstance(model, str) and model.casefold() in deepseek._ACCEPTED_MODELS,
        "unexpected_reply_model",
    )
    try:
        slot = p.decode(text, "choice")
    except (ValueError, RecursionError):
        slot, status = None, "invalid"
    else:
        status = "accepted"
    bound = {
        **expected,
        "status": status,
        "slot": slot,
        "response_sha256": p.digest(text),
        "served_model": model,
    }
    audit.require(
        p.wire(recorded) == p.wire({**bound, "response_text": text}), "reply_binding"
    )
    return bound


def _journal(
    study: Path, expected: list[Row], *, closed: bool
) -> tuple[list[Row], set[str]]:
    from ..visibility import deepseek
    from . import audit

    limit = len(_GRID) * (3 * deepseek._MAX_RESPONSE_BYTES + 2048)
    data = _optional_read(study / "attempts.jsonl", limit) or b""
    lines = data.split(b"\n")
    tail = lines.pop()
    audit.require(not closed or not tail, "truncated_closed_journal")
    records = iter(lines)
    rows = expected.copy()
    started: set[str] = set()
    for index, row in enumerate(expected):
        start = next(records, None)
        if start is None:
            break
        run = row["run"]
        audit.require(audit._object(start) == {"started": run}, "start_order_binding")
        started.add(run)
        reply = next(records, None)
        if reply is None:
            break
        rows[index] = _first_reply(row, audit._object(reply))
    audit.require(next(records, None) is None, "extra_attempt_record")
    return rows, started


def _meter(report: Row, rows: list[Row], started: set[str]) -> dict[str, str]:
    from ..visibility import deepseek
    from . import audit

    replies = {row["run"]: row for row in rows}
    charges = {}
    for call in report["calls"]:
        run, model = call["run"], call["served_model"]
        audit.require(
            run in started and run not in charges and call["role"] == "cal_decide",
            "charge_identity_binding",
        )
        usage = (call["cache_hit"], call["cache_miss"], call["output"])
        if call["estimated_usd_high"] is None:
            audit.require(model is None and usage == (None, None, None), "hold_binding")
            charges[run] = "held"
            continue
        replied_model = replies[run]["served_model"]
        audit.require(
            isinstance(model, str)
            and model.casefold() in deepseek._ACCEPTED_MODELS
            and all(type(n) is int and n >= 0 for n in usage)
            and (replied_model is None or model == replied_model),
            "settled_charge_binding",
        )
        charges[run] = "settled"
    return charges


def _check_receipt(receipt: Row, rows: list[Row], meter: Row) -> None:
    from ..visibility import deepseek
    from . import audit, peer_payload, peer_payload_run
    from . import protocol as p

    audit.require(
        receipt.get("schema") == peer_payload_run.SCHEMA
        and receipt.get("original_receipt_sha256") == peer_payload.SOURCE_SHA256
        and receipt.get("model") == deepseek.MODEL,
        "private_receipt_identity",
    )
    audit.require(
        p.wire(receipt.get("cells")) == p.wire(rows), "receipt_journal_binding"
    )
    audit.require(
        p.wire(receipt.get("meter")) == p.wire(meter), "receipt_ledger_binding"
    )


def _native_starts(native: Path, original: Row) -> tuple[set[int], set[int]]:
    from ..visibility import calendar_packet
    from . import audit, team_views
    from . import protocol as p

    cell = team_views.Cell(original["world"], "exact")
    directory = native / "cells" / cell.run
    binding: Row = {"trial_uuid": original["trial_uuid"]}
    config, tables = audit._registration(directory, cell, binding, None)
    result = audit.raw(directory / "trials" / cell.run / "result.json")
    audit.require(
        binding["_source"] == original["source"]
        and p.digest(result) == original["result_sha256"],
        "native_source_binding",
    )
    audit._result_binding(directory, cell, audit._object(result), config, binding)
    receiver = cell.index % 2
    own, peer = tables[receiver], tables[1 - receiver]
    return set(calendar_packet.starts(own)), set(calendar_packet.starts(peer))


def _public_cells(
    native: Path, originals: dict[str, Row], rows: list[Row], charges: dict[str, str]
) -> list[Row]:
    starts = {}
    cells = []
    for row in rows:
        world, status = row["world"], row["status"]
        settlement = charges.get(row["run"], "missing")
        local = joint = None
        if settlement == "settled" and status != "unobserved":
            local = joint = False
            if status == "accepted":
                if world not in starts:
                    starts[world] = _native_starts(native, originals[world])
                own, peer = starts[world]
                local = row["slot"] in own
                joint = local and row["slot"] in peer
        cells.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "run",
                        "world",
                        "repeat",
                        "presentation",
                        "source_uuid",
                        "source_result_sha256",
                        "prompt_sha256",
                        "served_model",
                    )
                },
                "status": status,
                "meter_status": settlement,
                "locally_valid": local,
                "joint_success": joint,
            }
        )
    return cells


def _arm_counts(rows: list[Row]) -> dict[str, int]:
    counts = {
        "enrolled": len(rows),
        "scored": 0,
        "joint_successes": 0,
        "locally_valid": 0,
        "unobserved": 0,
        "meter_missing": 0,
        "meter_unsettled": 0,
    }
    for row in rows:
        status = row.get("status", "unobserved")
        settlement = row.get("meter_status")
        if status == "unobserved":
            counts["unobserved"] += 1
        elif status not in ("accepted", "invalid"):
            raise ValueError("summary_status")
        elif settlement == "missing":
            counts["meter_missing"] += 1
        elif settlement == "held":
            counts["meter_unsettled"] += 1
        else:
            local, joint = row["locally_valid"], row["joint_success"]
            if (
                settlement != "settled"
                or type(local) is not bool
                or type(joint) is not bool
                or (joint and not local)
                or (status == "invalid" and local)
            ):
                raise ValueError("summary_outcome")
            counts["scored"] += 1
            counts["joint_successes"] += int(joint)
            counts["locally_valid"] += int(local)
    counts["missing"] = counts["enrolled"] - counts["scored"]
    return counts


def summarize(cells: list[Row]) -> Row:
    grid: dict[tuple[str, int, str], Row] = {}
    for row in cells:
        world, repeat, arm = row["world"], row["repeat"], row["presentation"]
        key = (world, repeat, arm)
        if type(repeat) is not int or key not in _GRID or key in grid:
            raise ValueError("summary_grid")
        grid[key] = row
    arms = {
        arm: _arm_counts([grid.get(key, {}) for key in _GRID if key[2] == arm])
        for arm in _ARMS
    }
    missing = sum(counts["missing"] for counts in arms.values())
    result = {
        "arms": arms,
        "missing": missing,
        "world_differences": None,
        "net": None,
        "p": None,
        "tail": None,
        "assignments": None,
        "verdict": "inconclusive",
    }
    if missing:
        return result
    differences = {
        world: sum(
            int(grid[world, repeat, "shown"]["joint_success"])
            - int(grid[world, repeat, "masked"]["joint_success"])
            for repeat in range(2)
        )
        for world in _WORLDS
    }
    net = sum(differences.values())
    assignments = 2 ** len(differences)
    tail = sum(
        sum(sign * d for sign, d in zip(signs, differences.values(), strict=True))
        >= net
        for signs in product((-1, 1), repeat=len(differences))
    )
    probability = Fraction(tail, assignments)
    return {
        **result,
        "world_differences": differences,
        "net": net,
        "p": str(probability),
        "tail": tail,
        "assignments": assignments,
        "verdict": (
            "pass" if net >= 4 and probability <= Fraction(1, 20) else "not_established"
        ),
    }


def score(native: Path, study: Path, ledger: Path) -> Row:
    from ..visibility import spend
    from . import audit, peer_payload, peer_payload_run
    from . import protocol as p

    native, study, ledger = (
        root.expanduser().absolute() for root in (native, study, ledger)
    )
    originals = peer_payload.originals()
    plan = peer_payload_run.plan(native, ledger)
    expected = []
    for row, prompt in plan:
        original = originals[row["world"]]
        audit.require(
            row["source_uuid"] == original["trial_uuid"]
            and row["source_result_sha256"] == original["result_sha256"]
            and row["prompt_sha256"] == p.digest(prompt),
            "plan_source_or_prompt_binding",
        )
        expected.append(row)
    audit.require(len(expected) == len(_GRID), "incomplete_plan")
    receipt = _optional_read(study / "receipt.json", _MAX_RECEIPT)
    rows, started = _journal(study, expected, closed=receipt is not None)
    meter = spend.report(ledger, run_prefix=peer_payload_run.PREFIX)
    if receipt is not None:
        _check_receipt(audit._object(receipt), rows, meter)
    charges = _meter(meter, rows, started)
    cells = _public_cells(native, originals, rows, charges)
    return {
        "schema": _SCHEMA,
        "cells": cells,
        "meter": meter,
        "summary": summarize(cells),
    }


def _read_public_summary(path: Path) -> Row:
    def unique_object(pairs: list[tuple[str, Any]]) -> Row:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate_public_field")
        return value

    with path.open("rb") as stream:
        data = stream.read(_MAX_RECEIPT + 1)
    if len(data) > _MAX_RECEIPT:
        raise ValueError("oversize_public_receipt")
    receipt = json.loads(data, object_pairs_hook=unique_object)
    cells = receipt["cells"]
    if (
        receipt["schema"] != _SCHEMA
        or not isinstance(cells, list)
        or len(cells) != len(_GRID)
    ):
        raise ValueError("public_receipt_grid")
    summary = summarize(cells)
    if json.dumps(summary, sort_keys=True) != json.dumps(
        receipt["summary"], sort_keys=True
    ):
        raise ValueError("public_summary_mismatch")
    return summary


def main() -> None:
    cli = argparse.ArgumentParser(
        description="Score the registered peer-payload choices.", allow_abbrev=False
    )
    cli.add_argument("native", nargs="?", type=Path)
    cli.add_argument("study", nargs="?", type=Path)
    cli.add_argument("--spend-dir", type=Path)
    cli.add_argument("--receipt", type=Path)
    args = cli.parse_args()
    if args.receipt is not None:
        if any(
            value is not None for value in (args.native, args.study, args.spend_dir)
        ):
            cli.error("--receipt does not take native, study, or --spend-dir")
        try:
            result = _read_public_summary(args.receipt)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            cli.error("cannot reproduce the summary from the public receipt")
    else:
        if args.native is None or args.study is None or args.spend_dir is None:
            cli.error(
                "supply native and study paths plus --spend-dir, or use --receipt"
            )
        if not __package__:
            cli.error("use python -m for scoring private evidence")
        try:
            result = score(args.native, args.study, args.spend_dir)
        except (
            OSError,
            ValueError,
            LookupError,
            TypeError,
            RuntimeError,
            sqlite3.Error,
        ):
            cli.error(
                "cannot bind the study to its native, journal, and ledger evidence"
            )
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
