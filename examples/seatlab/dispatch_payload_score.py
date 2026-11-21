import argparse
import json
import sqlite3
import sys
from contextlib import closing
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

_SCHEMA = "seatlab.dispatch-payload/v1"
_SOURCE_SHA256 = "86eaf01d4a5306ed9961bd1d39c5a4e8feff113fdec6be795e23a6c28f217c40"
_ARMS = ("shown", "masked")
_WORLDS = tuple(range(6801, 6809))
_GRID = tuple(
    {
        "run": f"dpp-s{world}-r{repeat}-{arm}",
        "world": world,
        "repeat": repeat,
        "arm": arm,
    }
    for index, world in enumerate(_WORLDS)
    for repeat in range(2)
    for arm in (_ARMS if (index + repeat) % 2 == 0 else _ARMS[::-1])
)
_SOURCE_FIELDS = ("source_uuid", "source_result_sha256", "prompt_sha256")
_PUBLIC_SOURCE_FIELDS = ("source_ref", "source_result_sha256", "prompt_sha256")
_REPLY_FIELDS = {
    "status": "unobserved",
    "served_model": None,
    "response_sha256": None,
    "choices": None,
}
_OUTCOMES = {"opened": 2, "unopened": 4}
_TOKENS = ("cache_hit", "cache_miss", "output")
_PRICES = ("estimated_usd_low", "estimated_usd_high", "unsettled_hold_usd")
_LIMIT = 1024 * 1024
Row = dict[str, Any]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _same(a: object, b: object) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _model(value: object) -> bool:
    from examples.seatlab.confirmation_custody import _MODELS

    return isinstance(value, str) and value.casefold() in _MODELS


def _fingerprints(row: Row) -> None:
    from examples.seatlab.visibility.calendar_packet import _check_digest

    uuid, result, prompt = (row[k] for k in _SOURCE_FIELDS)
    _require(isinstance(uuid, str) and str(UUID(uuid)) == uuid, "source_uuid")
    _check_digest(result)
    _check_digest(prompt)


def _first_reply(record: Row, expected: Row, max_bytes: int) -> Row:
    from .dispatch_payload import decode_choices

    text = record["response_text"]
    _require(isinstance(text, str), "first_reply_text")
    encoded = text.encode()
    _require(
        len(encoded) <= max_bytes and _model(record["served_model"]),
        "first_reply_metadata",
    )
    try:
        choices = decode_choices(text)
    except (ValueError, RecursionError):
        choices = None
    source = {key: record[key] for key in _SOURCE_FIELDS}
    _fingerprints(source)
    row = {
        **source,
        **expected,
        "served_model": record["served_model"],
        "status": "accepted" if choices is not None else "invalid",
        "choices": choices,
        "response_sha256": sha256(encoded).hexdigest(),
    }
    _require(_same(record, {**row, "response_text": text}), "first_reply_binding")
    return row


def _journal(
    study: Path, expected: list[Row], *, closed: bool
) -> tuple[list[Row], set[str]]:
    from .calendar_notices.peer_payload_score import _optional_read
    from .visibility import calendar_packet, deepseek

    max_bytes = deepseek._MAX_RESPONSE_BYTES
    limit = len(_GRID) * (6 * max_bytes + 2048)
    data = _optional_read(study / "attempts.jsonl", limit) or b""
    lines = data.split(b"\n")
    tail = lines.pop()
    _require(not closed or not tail, "truncated_closed_journal")
    records = iter(lines)
    rows = expected.copy()
    started = set()
    for index, row in enumerate(expected):
        head = next(records, None)
        if head is None:
            break
        run = row["run"]
        _require(calendar_packet._json(head) == {"started": run}, "journal_order")
        started.add(run)
        reply = next(records, None)
        if reply is None:
            break
        record = calendar_packet._object(calendar_packet._json(reply))
        rows[index] = _first_reply(record, row, max_bytes)
    _require(next(records, None) is None, "extra_attempt_record")
    return rows, started


def _ledger(root: Path, started: set[str]) -> Row | None:
    from .confirmation_custody import _physical, _select_charge, _Turn
    from .visibility import spend

    try:
        path = spend._location(root)
        _physical(path, directory=False)
    except FileNotFoundError:
        return None
    meter = spend.report(root, run_prefix="dpp-")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for call in meter["calls"]:
            run = call["run"]
            _require(run in started and call["role"] == "dispatch", "ledger_identity")
            state, charge = _select_charge(
                db, _Turn(run, "dispatch", "dispatch", "decide")
            )
            if charge is None:
                _require(
                    state == "held" and call["estimated_usd_high"] is None,
                    "ledger_hold",
                )
            else:
                measured = (
                    charge.model,
                    charge.hit,
                    charge.miss,
                    charge.output,
                    spend._usd(charge.low),
                    spend._usd(charge.high),
                )
                exported = tuple(
                    call[k] for k in ("served_model", *_TOKENS, *_PRICES[:2])
                )
                _require(measured == exported, "ledger_charge")
    return meter


def _meter(meter: Row | None, started: set[str]) -> tuple[dict[str, Row], Row]:
    from examples.seatlab.visibility import spend

    calls = {}
    low_sum = high_sum = held = 0
    totals = dict.fromkeys(_TOKENS, 0)
    required = {"run", "role", "served_model", *_TOKENS, *_PRICES}
    for call in [] if meter is None else meter["calls"]:
        run = call["run"]
        _require(
            call.keys() == required
            and run in started
            and run not in calls
            and call["role"] == "dispatch",
            "meter_identity",
        )
        calls[run] = call
        hit, miss, output = (call[k] for k in _TOKENS)
        low, high, hold = (call[k] for k in _PRICES)
        if high is None:
            _require(
                hit is miss is output is low is call["served_model"] is None
                and hold == spend._usd(spend._HOLD),
                "meter_hold",
            )
            held += 1
            continue
        _require(
            _model(call["served_model"])
            and all(type(n) is int and n >= 0 for n in (hit, miss, output)),
            "meter_usage",
        )
        price = 3 * hit + 150 * miss + 600 * output
        upper = price if low == high else 2 * price
        _require(
            (low, high, hold) == (spend._usd(price), spend._usd(upper), spend._usd(0))
            and upper <= spend._HOLD,
            "meter_price",
        )
        for key in _TOKENS:
            totals[key] += call[key]
        low_sum += price
        high_sum += upper
    held_amount = held * spend._HOLD
    cost = {
        "settled_calls": len(calls) - held,
        "held_calls": held,
        "settled_tokens": totals,
        "settled_usd_low": spend._usd(low_sum),
        "settled_usd_high": spend._usd(high_sum),
        "held_usd": spend._usd(held_amount),
        "upper_usd_including_holds": spend._usd(high_sum + held_amount),
    }
    if meter is not None:
        expected = {
            "schema": "seatlab.direct-flash-cost/v1",
            "currency": "USD",
            "calls": list(calls.values()),
            "unsettled_calls": held,
            "known_usd_low": cost["settled_usd_low"],
            "upper_usd_including_holds": cost["upper_usd_including_holds"],
            "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
        }
        _require(_same(meter, expected), "meter_totals")
    return calls, cost


def _public_row(row: Row, started: set[str], source: Any, call: Row | None) -> Row:
    scores = None
    if (
        source is not None
        and row["status"] != "unobserved"
        and call is not None
        and call["estimated_usd_high"] is not None
    ):
        choices = row["choices"] or {}
        scores = {key: {"optimal": 0, "feasible": 0} for key in _OUTCOMES}
        for shipment, bay in choices.items():
            part = scores["opened" if shipment in source.opened else "unopened"]
            part["optimal"] += bay == source.optimal[shipment]
            part["feasible"] += bay in source.eligible[shipment]
    identity = {key: None for key in _PUBLIC_SOURCE_FIELDS}
    if source is not None:
        identity.update(
            source_ref=sha256(
                b"seatlab-confirmation:v1:" + row["source_uuid"].encode()
            ).hexdigest(),
            source_result_sha256=row["source_result_sha256"],
            prompt_sha256=row["prompt_sha256"],
        )
    return {
        **{key: row[key] for key in (*_GRID[0], "status", "served_model")},
        **identity,
        "started": row["run"] in started,
        "scores": scores,
    }


def _public_start_order(cells: list[Row]) -> set[str]:
    _require(isinstance(cells, list) and len(cells) == len(_GRID), "public_grid")
    fields = {
        *_GRID[0],
        *_PUBLIC_SOURCE_FIELDS,
        "status",
        "served_model",
        "started",
        "scores",
    }
    identities: dict[tuple[int] | tuple[int, str], Any] = {}
    for cell, coord in zip(cells, _GRID, strict=True):
        _require(
            cell.keys() == fields and _same({k: cell[k] for k in coord}, coord),
            "public_row",
        )
        _require(type(cell["started"]) is bool, "public_start")
        world = cell["world"]
        source = (cell["source_ref"], cell["source_result_sha256"])
        prompt = cell["prompt_sha256"]
        arm = (world, cell["arm"])
        _require(
            identities.setdefault((world,), source) == source
            and identities.setdefault(arm, prompt) == prompt,
            "public_repeat_binding",
        )
    started = {cell["run"] for cell in cells if cell["started"]}
    _require(
        started == {row["run"] for row in _GRID[: len(started)]},
        "public_start_order",
    )
    return started


def summarize(cells: list[Row], meter: Row | None) -> Row:
    from examples.seatlab.calendar_notices.presentation_summary import _exact_test

    calls, cost = _meter(meter, _public_start_order(cells))
    arms = {
        arm: _arm_summary([r for r in cells if r["arm"] == arm], calls) for arm in _ARMS
    }
    missing = sum(arm["missing"] for arm in arms.values())
    inference = {
        "world_differences": None,
        "net": None,
        "p": None,
        "tail": None,
        "assignments": None,
        "descriptive_net": None,
        "verdict": "inconclusive",
    }
    if not missing:
        differences = dict.fromkeys((str(world) for world in _WORLDS), 0)
        for cell in cells:
            optimal = sum(v["optimal"] for v in cell["scores"].values())
            difference = optimal if cell["arm"] == "shown" else -optimal
            differences[str(cell["world"])] += difference
        test = _exact_test(list(differences.values()))
        net = test["sum_world_list_minus_raw"]
        p = test["one_sided_sign_flip_p"]
        shown, masked = (arms[arm] for arm in _ARMS)
        inference = {
            "world_differences": differences,
            "net": net,
            "p": p,
            "tail": test["sign_flip_tail"],
            "assignments": test["sign_flip_assignments"],
            "descriptive_net": {
                part: {
                    key: shown[part][key] - masked[part][key]
                    for key in ("optimal", "feasible")
                }
                for part in ("shipments", *_OUTCOMES)
            },
            "verdict": (
                "pass"
                if net >= 12 and Fraction(p) <= Fraction(1, 20)
                else "not_established"
            ),
        }
    return {"arms": arms, "missing": missing, "cost": cost, **inference}


def _arm_summary(rows: list[Row], calls: dict[str, Row]) -> Row:
    from examples.seatlab.visibility.calendar_packet import _check_digest

    counts = dict.fromkeys(
        (
            "scored",
            "invalid",
            "unobserved",
            "meter_missing",
            "meter_held",
            "source_missing",
        ),
        0,
    )
    parts = {
        key: {"enrolled": len(rows) * n, "scored": 0, "optimal": 0, "feasible": 0}
        for key, n in _OUTCOMES.items()
    }
    for row in rows:
        status, model, scores = (row[k] for k in ("status", "served_model", "scores"))
        call = calls.get(row["run"])
        observed = status in ("accepted", "invalid")
        _require(
            (observed and row["started"] and _model(model))
            or (status == "unobserved" and model is None),
            "public_reply_status",
        )
        source = row["source_ref"] is not None
        for key in _PUBLIC_SOURCE_FIELDS:
            if source:
                _check_digest(row[key])
            else:
                _require(row[key] is None, "public_missing_source")
        held = call is not None and call["estimated_usd_high"] is None
        if observed and call is not None and not held:
            _require(model == call["served_model"], "reply_meter_model")
        for name, condition in (
            ("invalid", status == "invalid"),
            ("unobserved", not observed),
            ("meter_missing", call is None),
            ("meter_held", held),
            ("source_missing", not source),
        ):
            counts[name] += condition
        scorable = observed and source and call is not None and not held
        _require((scores is not None) == scorable, "public_score_presence")
        if not scorable:
            continue
        _require(scores.keys() == _OUTCOMES.keys(), "public_score_parts")
        counts["scored"] += 1
        for name, n in _OUTCOMES.items():
            part = scores[name]
            optimal, feasible = part["optimal"], part["feasible"]
            _require(
                part.keys() == {"optimal", "feasible"}
                and type(optimal) is int
                and type(feasible) is int
                and 0 <= optimal <= feasible <= n
                and (status != "invalid" or feasible == 0),
                "public_score_counts",
            )
            parts[name]["scored"] += n
            parts[name]["optimal"] += optimal
            parts[name]["feasible"] += feasible
    totals = {
        key: sum(part[key] for part in parts.values())
        for key in ("enrolled", "scored", "optimal", "feasible")
    }
    return {
        **counts,
        "missing": len(rows) - counts["scored"],
        "shipments": totals,
        **parts,
    }


def score(native: Path, study: Path, ledger: Path) -> Row:
    from . import dispatch_payload, dispatch_payload_run
    from .calendar_notices.peer_payload_score import _optional_read
    from .visibility import calendar_packet, deepseek

    native, study, ledger = (p.expanduser().absolute() for p in (native, study, ledger))
    _require(dispatch_payload.SOURCE_SHA256 == _SOURCE_SHA256, "source_pin")
    expected: list[Row] = [{**coord, **_REPLY_FIELDS} for coord in _GRID]
    try:
        sources = dispatch_payload.sources(native)
    except FileNotFoundError:
        sources = {}
    else:
        _require(sources.keys() == set(_WORLDS), "source_grid")
        plan = dispatch_payload_run.plan(sources)
        for row, (actual, prompt) in zip(expected, plan, strict=True):
            source = sources[row["world"]]
            row.update(
                source_uuid=source.source_uuid,
                source_result_sha256=source.source_result_sha256,
                prompt_sha256=sha256(prompt.encode()).hexdigest(),
            )
            _require(
                prompt == getattr(source, row["arm"]) and _same(row, actual),
                "plan_binding",
            )
    receipt_bytes = _optional_read(study / "receipt.json", _LIMIT)
    rows, started = _journal(study, expected, closed=receipt_bytes is not None)
    meter = _ledger(ledger, started)
    if receipt_bytes is not None:
        receipt = calendar_packet._object(
            calendar_packet._json(receipt_bytes),
            "schema",
            "original_receipt_sha256",
            "model",
            "stopped",
            "cells",
            "meter",
        )
        complete = all(row["status"] != "unobserved" for row in rows)
        _require(
            receipt["schema"] == dispatch_payload_run.SCHEMA
            and receipt["original_receipt_sha256"] == _SOURCE_SHA256
            and receipt["model"] == deepseek.MODEL
            and complete == (receipt["stopped"] is None)
            and (complete or isinstance(receipt["stopped"], str)),
            "private_receipt_identity",
        )
        for row, saved in zip(rows, receipt["cells"], strict=True):
            _require(
                _same(row, {k: saved[k] for k in row})
                and saved.keys() == {*row, *_SOURCE_FIELDS},
                "receipt_journal_binding",
            )
    calls = {call["run"]: call for call in meter["calls"]} if meter is not None else {}
    cells = [
        _public_row(row, started, sources.get(row["world"]), calls.get(row["run"]))
        for row in rows
    ]
    return {
        "schema": _SCHEMA,
        "original_receipt_sha256": _SOURCE_SHA256,
        "cells": cells,
        "meter": meter,
        "summary": summarize(cells, meter),
    }


def _read_public_summary(path: Path) -> Row:
    from examples.seatlab.dispatch_payload import _roster
    from examples.seatlab.dispatch_peer_case import _sources
    from examples.seatlab.visibility import calendar_packet

    receipt = calendar_packet._object(
        calendar_packet._json(calendar_packet._read(path, _LIMIT)),
        "schema",
        "original_receipt_sha256",
        "cells",
        "meter",
        "summary",
    )
    _require(
        receipt["schema"] == _SCHEMA
        and receipt["original_receipt_sha256"] == _SOURCE_SHA256,
        "public_receipt_identity",
    )
    summary = summarize(receipt["cells"], receipt["meter"])
    roster, prompts = _roster(), _sources()
    for row in receipt["cells"]:
        if row["source_ref"] is not None:
            native = roster[row["world"]]
            _require(
                (row["source_ref"], row["source_result_sha256"])
                == (native["trial_ref"], native["result_sha256"]),
                "public_source_binding",
            )
            _require(
                row["prompt_sha256"]
                == prompts[row["world"]][f"{row['arm']}_prompt_sha256"],
                "public_prompt_binding",
            )
    _require(_same(summary, receipt["summary"]), "public_summary_mismatch")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Score the registered Dispatch peer-payload replay.",
    )
    parser.add_argument("native", nargs="?", type=Path)
    parser.add_argument("study", nargs="?", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    native, study, ledger = args.native, args.study, args.spend_dir
    try:
        if args.receipt is not None and native is study is ledger is None:
            if not __package__:
                sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            result = _read_public_summary(args.receipt)
        elif (
            args.receipt is None
            and __package__
            and native is not None
            and study is not None
            and ledger is not None
        ):
            result = score(native, study, ledger)
        else:
            parser.error(
                "supply NATIVE STUDY --spend-dir LEDGER with python -m, or --receipt RECEIPT"
            )
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        AttributeError,
        RuntimeError,
        sqlite3.Error,
    ):
        parser.error("cannot bind the registered source, attempts, and charges")
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
