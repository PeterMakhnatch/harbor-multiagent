from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from . import confirmation_summary as summary
from .visibility.spend import _HOLD, _UNIT, _offpeak

_PRIVATE = "seatlab.confirmation-private-join/v1"
_FIELDS = {"world", "arm", "trial_dir", "behavior_confirmed", "calls"}
_MODELS = {"deepseek-flash", "deepseek-v4.1-flash-1017"}
_TOKEN_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")


class _EvidenceError(ValueError):
    pass


class _Turn(NamedTuple):
    run: str
    role: str
    seat: str
    phase: str


class _Cell(NamedTuple):
    panel: str
    world: int
    arm: str
    name: str
    trial: Path | None
    behavior_confirmed: bool
    turns: tuple[_Turn, ...]


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise _EvidenceError(reason)


def _object(value: Any, reason: str) -> dict[str, Any]:
    _need(isinstance(value, dict), reason)
    return value


def _decode(data: bytes) -> dict[str, Any]:
    return _object(
        json.loads(
            data,
            object_pairs_hook=summary._unique_object,
            parse_constant=summary._invalid_constant,
        ),
        "expected a JSON object",
    )


def _physical(path: Path, *, directory: bool, missing_ok: bool = False) -> bool:
    _need(
        path.is_absolute() and path.resolve() == path,
        "expected a physical absolute path",
    )
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    _need((stat.S_ISDIR if directory else stat.S_ISREG)(mode), "unexpected path type")
    return True


def _layout(panel: str, world: int, arm: str) -> tuple[str, tuple[_Turn, ...]]:
    if panel == "calendar_answers":
        name = f"nanswer-w{world:02d}-{arm}"
        receiver, peer = ("p1", "p2") if world % 2 == 0 else ("p2", "p1")
        return name, (
            _Turn(name, "cal_tool_pick", receiver, "question"),
            _Turn(name, "cal_tool_reply", peer, "answer"),
            _Turn(name, "cal_decide", receiver, "choice"),
        )
    if panel == "notices":
        name = f"attnc-s{world}-r0-{arm}"
        phases = (
            ("inventory", "inventory"),
            ("select", "dispatch"),
            ("decide", "dispatch"),
        )
    else:
        name = f"danswer-s{world}-{arm}"
        phases = (
            ("inventory", "inventory"),
            ("question", "dispatch"),
            ("answer", "inventory"),
            ("decide", "dispatch"),
        )
    return name, tuple(
        _Turn(f"{name}-{phase}", seat, seat, phase) for phase, seat in phases
    )


def _trial_location(trial: Path, name: str, panel: str) -> None:
    _physical(trial, directory=True)
    cell_dir = trial.parent.parent
    _need(
        trial.parent.name == "trials"
        and cell_dir.name == name
        and cell_dir.parent.name == "cells",
        "trial directory does not match the registered cell",
    )
    digest = sha256(os.fsencode(cell_dir)).hexdigest()
    expected = (
        f"{name}-{digest[:12]}"
        if panel == "calendar_answers"
        else f"seatlab-team-{digest[:24]}"
    )
    _need(trial.name == expected, "trial name does not match the registered cell")


def _register(document: object) -> list[_Cell]:
    doc = _object(document, "expected private join object")
    _need(
        doc.keys() == {"schema", "panels"} and doc["schema"] == _PRIVATE,
        "invalid private join schema",
    )
    panels = _object(doc["panels"], "expected panels object")
    _need(panels.keys() == summary._STUDIES.keys(), "invalid panel register")
    cells = []
    seen_calls: set[tuple[str, str]] = set()
    for panel, (worlds, control, treatment, n_calls) in summary._STUDIES.items():
        rows = panels[panel]
        _need(
            isinstance(rows, list) and len(rows) == 2 * len(worlds),
            f"{panel}: invalid row count",
        )
        registered = {(world, arm) for world in worlds for arm in (control, treatment)}
        for i, row in enumerate(rows):
            where = f"{panel}[{i}]"
            _need(
                isinstance(row, dict) and row.keys() == _FIELDS,
                f"{where}: invalid row fields",
            )
            world, arm = row["world"], row["arm"]
            _need(
                type(world) is int
                and isinstance(arm, str)
                and (world, arm) in registered,
                f"{where}: invalid or duplicate world and arm",
            )
            registered.remove((world, arm))
            name, turns = _layout(panel, world, arm)
            calls = row["calls"]
            expected = {(t.run, t.role) for t in turns}
            _need(
                isinstance(calls, list) and len(calls) == n_calls,
                f"{where}: invalid call count",
            )
            for pair in calls:
                _need(
                    isinstance(pair, list)
                    and len(pair) == 2
                    and all(type(s) is str for s in pair),
                    f"{where}: invalid call key",
                )
                key = (pair[0], pair[1])
                _need(
                    key in expected and key not in seen_calls,
                    f"{where}: wrong or duplicate call key",
                )
                seen_calls.add(key)
            path, claim = row["trial_dir"], row["behavior_confirmed"]
            _need(path is None or isinstance(path, str), f"{where}: invalid trial path")
            _need(type(claim) is bool, f"{where}: expected a boolean behavior claim")
            trial = Path(path) if path is not None else None
            if trial is not None:
                _trial_location(trial, name, panel)
            cells.append(_Cell(panel, world, arm, name, trial, claim, turns))
    return sorted(cells, key=lambda cell: (cell.panel, cell.world, cell.arm))


def _natural(value: Any) -> bool:
    return type(value) is int and value >= 0


def _time(value: Any) -> datetime:
    _need(isinstance(value, str), "missing or invalid timestamp")
    instant = datetime.fromisoformat(value)
    _need(instant.utcoffset() is not None, "ledger timestamp is not timezone-aware")
    return instant.astimezone(UTC)


def _usd(nano: int) -> str:
    dollars, fraction = divmod(nano, _UNIT)
    return f"{dollars}.{fraction:09d}"


class _Charge(NamedTuple):
    model: str
    hit: int
    miss: int
    output: int
    low: int
    high: int

    @property
    def usage(self) -> tuple[int, int, int]:
        return self.hit + self.miss, self.hit, self.output


def _select_charge(db: sqlite3.Connection, turn: _Turn) -> tuple[str, _Charge | None]:
    rows = db.execute(
        "SELECT reserved_at, started_at, finished_at, served_model, hit, miss, output, cost_low, cost_high "
        "FROM calls WHERE run=? AND role=? LIMIT 2",
        (turn.run, turn.role),
    ).fetchall()
    if not rows:
        return "no_reservation", None
    _need(len(rows) == 1, "duplicate ledger call")
    reserved, started, finished, model, hit, miss, output, low, high = rows[0]
    reserved_at = _time(reserved)
    if all(value is None for value in rows[0][1:]):
        return "held", None
    _need(
        isinstance(model, str) and model.casefold() in _MODELS,
        "unrecognized served model",
    )
    _need(
        all(map(_natural, (hit, miss, output, low, high))),
        "invalid ledger tokens or amounts",
    )
    start, finish = _time(started), _time(finished)
    _need(
        reserved_at <= start and timedelta() <= finish - start <= timedelta(minutes=10),
        "invalid ledger time interval",
    )
    expected = 3 * hit + 150 * miss + 600 * output
    upper = expected if _offpeak(start) and _offpeak(finish) else 2 * expected
    _need((low, high) == (expected, upper) and upper <= _HOLD, "ledger price mismatch")
    return "settled", _Charge(model, hit, miss, output, low, high)


def _public_call(turn: _Turn, status: str, charge: _Charge | None) -> dict[str, Any]:
    return {
        "run": turn.run,
        "role": turn.role,
        "status": status,
        "served_model": charge.model if charge else None,
        "cache_hit": charge.hit if charge else None,
        "cache_miss": charge.miss if charge else None,
        "output_tokens": charge.output if charge else None,
        "usd": _usd(charge.low) if charge and charge.low == charge.high else None,
        "estimated_usd_low": _usd(charge.low) if charge else None,
        "estimated_usd_high": _usd(charge.high) if charge else None,
        "unsettled_hold_usd": _usd(_HOLD)
        if status == "held"
        else _usd(0)
        if charge
        else None,
    }


def _reward(
    raw: dict[str, Any], primary: Any, trial: Path, panel: str
) -> int | float | None:
    verifier = raw["verifier_result"]
    rewards = (
        None
        if verifier is None
        else _object(verifier, "invalid verifier result")["rewards"]
    )
    rewards = {} if rewards is None else _object(rewards, "invalid native rewards")
    scored = rewards.get("reward")
    valid = raw["reward_valid"]
    _need(
        type(valid) is bool and (not valid or scored is not None),
        "invalid native reward-valid flag",
    )
    if rewards:
        artifact = trial / "verifier" / "reward.json"
        _physical(artifact, directory=False)
        original = _decode(artifact.read_bytes())
        for values in (rewards.values(), original.values()):
            _need(
                all(
                    type(v) is int or (type(v) is float and isfinite(v)) for v in values
                ),
                "invalid value in native verifier rewards",
            )
        _need(original == rewards, "native verifier reward mismatch")
    _need(
        (scored is None and primary is None)
        or (type(primary) in (float, int) and scored == primary),
        "Eval Lab primary reward mismatch",
    )
    summary._score(scored, panel, panel)
    return scored if valid else None


def _read_native(
    cell: _Cell, load_trial: Callable[[Path], Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    trial = cell.trial
    evidence: dict[str, Any] = {
        "reward": None,
        "trial_ref": None,
        "result_sha256": None,
    }
    if trial is None or not _physical(
        trial / "result.json", directory=False, missing_ok=True
    ):
        return {}, evidence
    for filename in ("config.json", "lock.json", "artifacts/manifest.json"):
        _physical(
            trial / filename,
            directory=False,
            missing_ok=filename == "artifacts/manifest.json",
        )
    payload = (trial / "result.json").read_bytes()
    raw = _decode(payload)
    record = load_trial(trial)
    _need(
        record.path == trial and raw == record.result, "Eval Lab trial result mismatch"
    )
    _need(
        raw["trial_uri"] == trial.as_uri()
        and raw["trial_name"] == record.name == trial.name,
        "native trial URI or name mismatch",
    )
    native_id = UUID(raw["id"])
    _need(
        native_id.version == 4 and str(native_id) == raw["id"] == record.id,
        "invalid native trial ID",
    )
    config = _object(raw["config"], "invalid native trial config")
    _need(
        config == record.config
        and config.get("trial_name") == trial.name
        and config.get("trials_dir") == str(trial.parent),
        "native trial config mismatch",
    )
    return raw, {
        "reward": _reward(raw, record.primary_reward, trial, cell.panel),
        "trial_ref": sha256(
            b"seatlab-confirmation:v1:" + str(native_id).encode()
        ).hexdigest(),
        "result_sha256": sha256(payload).hexdigest(),
    }


def _seats(raw: dict[str, Any], cell: _Cell) -> dict[str, dict[str, Any]]:
    rows = raw.get("seat_results")
    if rows is None:
        return {}
    _need(isinstance(rows, list), "invalid native seat results")
    roster = {turn.seat for turn in cell.turns}
    seats = {}
    for row in rows:
        row = _object(row, "invalid native seat result")
        seat = row["seat"]
        _need(
            seat in roster and seat not in seats and row["under_test"] is True,
            "invalid native seat roster",
        )
        seats[seat] = row
    return seats


def _tokens(values: tuple[Any, Any, Any]) -> tuple[int, int, int] | None:
    if all(value is None for value in values):
        return None
    _need(
        all(value is None or _natural(value) for value in values),
        "invalid native token count",
    )
    if None in values:
        return None
    _need(values[1] <= values[0], "native cached tokens exceed input")
    return values


def _sum_usage(charges: list[_Charge]) -> tuple[int, int, int]:
    return (
        sum(charge.hit + charge.miss for charge in charges),
        sum(charge.hit for charge in charges),
        sum(charge.output for charge in charges),
    )


def _aggregate_matches(seats: dict[str, Any], by_turn: dict[_Turn, _Charge]) -> bool:
    for seat in {turn.seat for turn in by_turn}:
        context = seats.get(seat, {}).get("agent_result")
        if context is None:
            return False
        context = _object(context, "invalid native seat agent result")
        actual = _tokens(tuple(context.get(key) for key in _TOKEN_FIELDS))
        expected = _sum_usage(
            [charge for turn, charge in by_turn.items() if turn.seat == seat]
        )
        if actual != expected:
            return False
    return True


class _TurnReceipt(NamedTuple):
    usage: tuple[int, int, int] | None
    model: str | None
    status: str


def _calendar_turns(cell: _Cell, raw: dict[str, Any]) -> dict[_Turn, _TurnReceipt]:
    trial = cell.trial
    _need(trial is not None, "missing calendar trial directory")
    reports = {}
    for turn in cell.turns:
        path = trial / "seats" / turn.seat / "agent" / f"{turn.phase}.json"
        if not _physical(path, directory=False, missing_ok=True):
            continue
        data = _decode(path.read_bytes())
        _need(
            (
                data["schema"],
                data["trial_uuid"],
                data["run"],
                data["seat"],
                data["phase"],
                data["ledger_role"],
            )
            == (
                "seatlab.native-calendar-answer-turn/v1",
                raw["id"],
                turn.run,
                turn.seat,
                turn.phase,
                turn.role,
            ),
            "native calendar turn identity mismatch",
        )
        usage = data["usage"]
        if usage is not None:
            _need(
                isinstance(usage, list) and len(usage) == 3,
                "invalid native calendar turn usage",
            )
            usage = _tokens(tuple(usage))
        reports[turn] = _TurnReceipt(usage, data["served_model"], data["status"])
    return reports


def _team_turns(cell: _Cell, seats: dict[str, Any]) -> dict[_Turn, _TurnReceipt]:
    schema = (
        "seatlab.native-attention-seat/v1"
        if cell.panel == "notices"
        else "seatlab.native-dispatch-answers-seat/v1"
    )
    expected = {(t.seat, t.phase, t.run, t.role): t for t in cell.turns}
    reports = {}
    for seat, row in seats.items():
        context = row.get("agent_result")
        if context is None:
            continue
        metadata = _object(context, "invalid native seat agent result").get("metadata")
        if metadata is None:
            continue
        metadata = _object(metadata, "invalid native seat metadata")
        if "turns" not in metadata:
            continue
        _need(
            metadata.get("schema") == schema and isinstance(metadata["turns"], list),
            "invalid native team turn schema",
        )
        if cell.panel == "notices":
            _need(
                (metadata.get("cell_id"), metadata.get("role")) == (cell.name, seat),
                "native notice seat identity mismatch",
            )
        for data in metadata["turns"]:
            data = _object(data, "invalid native team turn")
            key = (seat, data["stage"], data["run"], data["role"])
            _need(
                key in expected and expected[key] not in reports,
                "native team turn identity mismatch",
            )
            usage = data["usage"]
            if usage is None:
                reports[expected[key]] = _TurnReceipt(None, None, data["status"])
            else:
                usage = _object(usage, "invalid native team turn usage")
                tokens = _tokens(
                    tuple(
                        usage[k]
                        for k in ("input_tokens", "cache_tokens", "output_tokens")
                    )
                )
                reports[expected[key]] = _TurnReceipt(
                    tokens, usage["served_model"], data["status"]
                )
    return reports


def _turn_matches(
    reports: dict[_Turn, _TurnReceipt], by_turn: dict[_Turn, _Charge]
) -> bool | None:
    if not reports:
        return None
    for turn, (tokens, model, _) in reports.items():
        charge = by_turn.get(turn)
        if charge is None:
            if tokens is not None or model is not None:
                return False
        elif tokens != charge.usage or (model is not None and model != charge.model):
            return False
    if reports.keys() != by_turn.keys():
        return None
    return True


def _row(
    cell: _Cell, db: sqlite3.Connection, load_trial: Callable[[Path], Any]
) -> dict[str, Any]:
    raw, evidence = _read_native(cell, load_trial)
    seats = _seats(raw, cell)
    by_turn, calls = {}, []
    held, absent = 0, 0
    for turn in cell.turns:
        status, charge = _select_charge(db, turn)
        calls.append(_public_call(turn, status, charge))
        if charge is not None:
            by_turn[turn] = charge
        held += status == "held"
        absent += status == "no_reservation"
    all_settled = len(by_turn) == len(cell.turns)
    reports = (
        _calendar_turns(cell, raw)
        if raw and cell.panel == "calendar_answers"
        else _team_turns(cell, seats)
    )
    turn_check = _turn_matches(reports, by_turn)
    aggregate_ok = all_settled and _aggregate_matches(seats, by_turn)
    if cell.behavior_confirmed:
        expected_status = "accepted" if cell.panel == "calendar_answers" else "received"
        _need(
            evidence["reward"] is not None
            and raw.get("finished_at") is not None
            and _time(raw["started_at"]) <= _time(raw["finished_at"])
            and raw["exception_info"] is None
            and all(s["exception_info"] is None for s in seats.values())
            and aggregate_ok
            and turn_check is True
            and all(
                t.model is not None and t.status == expected_status
                for t in reports.values()
            )
            and all(c.usage[0] > 0 for c in by_turn.values()),
            "complete behavior claim does not match native result and ledger evidence",
        )
    charges = list(by_turn.values())
    input_tokens, cache_hit, output_tokens = _sum_usage(charges)
    low, high = sum(c.low for c in charges), sum(c.high for c in charges)
    return {
        "world": cell.world,
        "arm": cell.arm,
        **evidence,
        "complete": cell.behavior_confirmed,
        "behavior_confirmed": cell.behavior_confirmed,
        "aggregate_usage_verified": aggregate_ok,
        "turn_usage_verified": turn_check,
        "settled_calls": len(charges),
        "held_calls": held,
        "calls_without_reservation": absent,
        "settled_tokens": {
            "input_tokens": input_tokens,
            "cache_hit": cache_hit,
            "cache_miss": input_tokens - cache_hit,
            "output_tokens": output_tokens,
        },
        "usd": _usd(low) if all_settled and low == high else None,
        "settled_usd_low": _usd(low),
        "settled_usd_high": _usd(high),
        "unsettled_hold_usd": _usd(held * _HOLD),
        "upper_usd_including_holds": _usd(high + held * _HOLD) if not absent else None,
        "calls": calls,
    }


def _stock_loader() -> Callable[[Path], Any]:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        from evallab.results import load_trial
    finally:
        sys.dont_write_bytecode = previous
    return load_trial


def collect(
    document: object, ledger: Path, *, load_trial: Callable[[Path], Any] | None = None
) -> dict[str, Any]:
    cells = _register(document)
    _physical(ledger, directory=True)
    db_path = ledger / "direct-flash.sqlite3"
    _physical(db_path, directory=False)
    with db_path.open("rb") as handle:
        header = handle.read(20)
    _need(
        header.startswith(b"SQLite format 3\x00") and header[18:20] == b"\x01\x01",
        "expected the original rollback-journal Flash ledger",
    )
    load_trial = _stock_loader() if load_trial is None else load_trial
    studies: dict[str, list[dict[str, Any]]] = {key: [] for key in summary._STUDIES}
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for cell in cells:
            try:
                studies[cell.panel].append(_row(cell, db, load_trial))
            except _EvidenceError as exc:
                raise _EvidenceError(
                    f"{cell.panel} {cell.world} {cell.arm}: {exc}"
                ) from exc
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                OverflowError,
                sqlite3.Error,
            ) as exc:
                raise _EvidenceError(
                    f"{cell.panel} {cell.world} {cell.arm}: invalid receipt ({type(exc).__name__})"
                ) from exc
    seen: set[str] = set()
    for panel, rows in studies.items():
        summary._registered(rows, panel, seen)
    return {
        "schema": summary._SCHEMA,
        "studies": studies,
        "provenance": {
            "reward": "Original Harbor result, verifier reward.json, and evallab.results.load_trial primary_reward; invalid native rewards are not observed.",
            "usage": "Direct Flash ledger counts; input equals cache_hit plus cache_miss. Harbor per-seat totals are checked separately from the behavior claim. Available original per-turn receipts are cross-checked.",
            "currency": "USD",
            "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
            "cost": "Integer nanodollars at list price. A weekday peak endpoint allows up to twice the base price; no holiday or account invoice is assumed.",
            "unsettled": "A held call has a reservation but no verified usage. No reservation does not establish zero cost; its upper bound is unknown.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Join native Harbor and read-only ledger receipts offline."
    )
    parser.add_argument("private_index", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = collect(_decode(args.private_index.read_bytes()), args.ledger)
    except ImportError:
        parser.error(
            "use the Eval Lab Python environment with the Harbor fork on PYTHONPATH"
        )
    except _EvidenceError as exc:
        parser.exit(2, f"confirmation_custody: {exc}\n")
    except (ValueError, OSError, RuntimeError, sqlite3.Error):
        parser.exit(
            2, "confirmation_custody: invalid private join or receipt evidence\n"
        )
    print(json.dumps(receipt, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
