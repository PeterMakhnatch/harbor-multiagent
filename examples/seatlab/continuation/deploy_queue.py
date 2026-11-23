from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
from typing import Any

from . import resume_months as rm

SCHEMA = "seatlab.deploy-queue-panel/v1"
CELL_SCHEMA = "seatlab.deploy-queue-cell/v1"
PROG = "python3.12 -BS -m examples.seatlab.continuation.deploy_queue"
PAIRS = tuple(range(8))
SIDES = ("X", "Y")
LANDINGS = (13, 17)
PUBLIC_MONTHS = 13
SEED_LABEL = "seatlab/deploy-queue/v1/pair/{pair}/{side}/{purpose}"
ROLES = {"X": "planner", "Y": "analyst"}
WIN_MARGIN = 0.01
MIN_WINS = 2
ORACLE_MARGIN = 0.015
SCORE_TOLERANCE = 1e-12
SEED_MONEY = 2_000_000.0
TOP_MULTIPLE = 32.0
POLICIES = ("always_X", "always_Y", "higher_last_revenue", "lower_last_revenue")
CELL_LIMIT = 1 << 20
ENTRY_LIMIT = 256
NAME_LIMIT = 32
SHA = re.compile(r"[0-9a-f]{64}")
REASON = re.compile(r"[a-z0-9_.:-]{1,64}")
PAIR_ARG = re.compile(r"[0-7]")
CELL_KEYS = frozenset(
    {
        "schema",
        "pair",
        "side",
        "landing",
        "stock_commit",
        "valid",
        "public_books",
        "public_books_sha256",
        "score",
        "months",
        "reason",
        "world_commitments",
    }
)
OPTIONAL_KEYS = frozenset({"source", "limitations"})
BOOK_KEYS = frozenset({"month", "revenue", "cash_at_end"})
MONTH_KEYS = frozenset(
    {"month", "program", "plan_sha256", "ops_sha256", "frozen", "bankrupt"}
)
SCORE_KEYS = frozenset(
    {"value", "live_score", "bankrupt", "seed_money", "top_multiple"}
)
PROGRAM_SHA = {label: rm.PINNED[rel] for label, rel in rm.PROGRAMS.items()}
PROGRAM_LABELS = (*PROGRAM_SHA, "unknown")
PLAN_SHA = rm.PINNED[rm.REFERENCE_PLAN]
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
EXPECTED = {
    f"p{p}-{s}-{k}.json": (p, s, k) for p in PAIRS for s in SIDES for k in LANDINGS
}
LIMITATIONS = (
    "reads cell files only; no stock, generated or model code is executed",
    "live_score is rechecked from the cell's value, seed_money and top_multiple with research spend 0",
    "policies and thresholds are the registered fixed set; nothing is fitted to outcomes",
    "the deployment limit is an operator rule, not a Bespoke benchmark rule",
)


class Bad(Exception):
    pass


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise Bad(code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _num(x: Any) -> bool:
    return (type(x) is float and math.isfinite(x)) or (
        type(x) is int and abs(x) <= 1 << 53
    )


def _hex(x: Any) -> bool:
    return type(x) is str and SHA.fullmatch(x) is not None


def _commitments(pair: int, side: str) -> dict[str, str]:
    labels = {
        p: SEED_LABEL.format(pair=pair, side=side, purpose=p)
        for p in ("world", "truth")
    }
    return {p: _sha(_sha(label.encode()).encode()) for p, label in labels.items()}


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    found = dict(items)
    _need(len(found) == len(items), "duplicate_key")
    return found


def _finite(text: str) -> float:
    value = float(text)
    _need(math.isfinite(value), "non_finite")
    return value


def _constant(text: str) -> Any:
    raise Bad("non_finite")


def _parse(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_float=_finite,
            parse_constant=_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Bad("json") from None


def _read(dfd: int, name: str) -> bytes:
    try:
        fd = os.open(name, rm.OPEN_FLAGS, dir_fd=dfd)
    except OSError as e:
        raise Bad("symlink" if e.errno == errno.ELOOP else "unreadable") from None
    try:
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode), "not_regular_file")
        _need(info.st_size <= CELL_LIMIT, "too_large")
        chunks: list[bytes] = []
        total = 0
        while total <= CELL_LIMIT:
            chunk = os.read(fd, CELL_LIMIT + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        raise Bad("unreadable") from None
    finally:
        os.close(fd)
    _need(total <= CELL_LIMIT, "too_large")
    return b"".join(chunks)


def _identity(cell: Any, pair: int, side: str, landing: int) -> None:
    _need(type(cell) is dict, "not_object")
    keys = set(cell)
    _need(CELL_KEYS <= keys <= CELL_KEYS | OPTIONAL_KEYS, "keys")
    _need(cell["schema"] == CELL_SCHEMA, "schema")
    _need(type(cell["pair"]) is int and cell["pair"] == pair, "pair")
    _need(type(cell["side"]) is str and cell["side"] == side, "side")
    _need(type(cell["landing"]) is int and cell["landing"] == landing, "landing")
    _need(cell["stock_commit"] == rm.SOURCE_COMMIT, "stock_commit")
    _need(cell["world_commitments"] == _commitments(pair, side), "world_commitments")
    _need(type(cell["valid"]) is bool, "valid")
    reason = cell["reason"]
    _need(
        reason is None
        or (type(reason) is str and REASON.fullmatch(reason) is not None),
        "reason",
    )
    _need(not cell["valid"] or reason is None, "reason")


def _books(cell: dict[str, Any], full: bool) -> str:
    books = cell["public_books"]
    _need(type(books) is list and len(books) <= PUBLIC_MONTHS, "public_books")
    _need(not full or len(books) == PUBLIC_MONTHS, "public_books_short")
    for i, row in enumerate(books):
        _need(type(row) is dict and set(row) == BOOK_KEYS, "public_books")
        _need(type(row["month"]) is int and row["month"] == i, "public_books_month")
        _need(_num(row["revenue"]) and _num(row["cash_at_end"]), "public_books_number")
    measured = _sha(_canonical(books))
    _need(cell["public_books_sha256"] == measured, "public_books_sha256")
    return measured


def _month_rows(rows: Any) -> list[dict[str, Any]]:
    _need(type(rows) is list and len(rows) <= rm.MONTHS, "months")
    for i, row in enumerate(rows):
        _need(type(row) is dict and set(row) == MONTH_KEYS, "months")
        _need(type(row["month"]) is int and row["month"] == i, "months_month")
        _need(row["program"] in PROGRAM_LABELS, "months_program")
        _need(_hex(row["plan_sha256"]) and _hex(row["ops_sha256"]), "months_sha256")
        _need(type(row["frozen"]) is bool and type(row["bankrupt"]) is bool, "months")
    return rows


def _schedule(rows: list[dict[str, Any]], landing: int) -> bool:
    _need(PUBLIC_MONTHS <= len(rows), "months_short")
    for row in rows:
        planned = "starter" if row["month"] < landing else "reference"
        _need(row["program"] == planned, "program_schedule")
        _need(row["ops_sha256"] == PROGRAM_SHA[planned], "program_sha256")
        _need(row["plan_sha256"] == PLAN_SHA, "plan_sha256")
        _need(not row["frozen"], "software_frozen")
    _need(not any(row["bankrupt"] for row in rows[:-1]), "bankrupt_continued")
    bankrupt = rows[-1]["bankrupt"]
    _need(len(rows) == rm.MONTHS or bankrupt, "months_incomplete")
    return bankrupt


def _expected_score(score: dict[str, Any]) -> float:
    if score["bankrupt"]:
        return 0.0
    m = score["value"] / score["seed_money"]
    return (
        min(1.0, max(0.0, math.log(m) / math.log(score["top_multiple"])))
        if m > 0
        else 0.0
    )


def _score(score: Any, bankrupt: bool) -> float:
    _need(type(score) is dict and set(score) == SCORE_KEYS, "score")
    _need(
        all(_num(score[k]) for k in ("value", "live_score", "seed_money")),
        "score_number",
    )
    _need(
        type(score["bankrupt"]) is bool and score["bankrupt"] == bankrupt,
        "score_bankrupt",
    )
    _need(score["seed_money"] == SEED_MONEY, "score_seed_money")
    top = score["top_multiple"]
    _need(top == TOP_MULTIPLE if top is not None else bankrupt, "score_top_multiple")
    live = score["live_score"]
    _need(0.0 <= live <= 1.0, "score_range")
    _need(abs(live - _expected_score(score)) <= SCORE_TOLERANCE, "score_recompute")
    return float(live)


def _cell(dfd: int, name: str) -> dict[str, Any]:
    pair, side, landing = EXPECTED[name]
    out: dict[str, Any] = {"name": name, "pair": pair, "side": side, "landing": landing}
    out |= {"status": "rejected", "reason": None, "public_books_sha256": None}
    try:
        cell = _parse(_read(dfd, name))
        _identity(cell, pair, side, landing)
        rows = _month_rows(cell["months"])
        out["public_books_sha256"] = _books(cell, cell["valid"])
        if not cell["valid"]:
            _need(cell["score"] is None, "invalid_has_score")
            return out | {"status": "invalid", "reason": cell["reason"] or "invalid"}
        live = _score(cell["score"], _schedule(rows, landing))
    except Bad as e:
        return out | {"reason": str(e)}
    books = cell["public_books"]
    out |= {
        "status": "counted",
        "live_score": live,
        "last_revenue": books[-1]["revenue"],
    }
    return out | {"books": books}


def _entries(dfd: int) -> list[str]:
    names: list[str] = []
    with os.scandir(dfd) as it:
        for entry in it:
            if len(names) >= ENTRY_LIMIT:
                raise rm.Rejected("CELL_DIRECTORY holds too many entries")
            names.append(entry.name)
    return sorted(names)


def _demote(cell: dict[str, Any], code: str) -> None:
    for key in ("live_score", "last_revenue", "books"):
        cell.pop(key, None)
    cell |= {"status": "rejected", "reason": code}


def _cross_check(cells: dict[str, dict[str, Any]]) -> dict[str, int]:
    owners: dict[str, int] = {}
    for pair in PAIRS:
        for side in SIDES:
            both = [cells[f"p{pair}-{side}-{k}.json"] for k in LANDINGS]
            if not all(c["status"] == "counted" for c in both):
                continue
            if both[0]["public_books_sha256"] != both[1]["public_books_sha256"]:
                for c in both:
                    _demote(c, "public_prefix_mismatch")
                continue
            sha = both[0]["public_books_sha256"]
            owners[sha] = owners.get(sha, 0) + 1
    shared = [n for n in owners.values() if n > 1]
    return {
        "shared_prefix_groups": len(shared),
        "companies_in_shared_groups": sum(shared),
    }


def _load(
    directory: str,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, int]]:
    try:
        dfd = os.open(directory, DIR_FLAGS)
    except OSError:
        raise rm.Rejected("CELL_DIRECTORY is not a plain directory") from None
    try:
        names = _entries(dfd)
        cells: dict[str, dict[str, Any]] = {}
        for name, (pair, side, landing) in EXPECTED.items():
            if name in names:
                cells[name] = _cell(dfd, name)
            else:
                cells[name] = {
                    "name": name,
                    "pair": pair,
                    "side": side,
                    "landing": landing,
                    "status": "missing",
                    "reason": "missing",
                    "public_books_sha256": None,
                }
    finally:
        os.close(dfd)
    collisions = _cross_check(cells)
    return cells, [n for n in names if n not in EXPECTED], collisions


def _pair(cells: dict[str, dict[str, Any]], pair: int) -> dict[str, Any]:
    got = {(s, k): cells[f"p{pair}-{s}-{k}.json"] for s in SIDES for k in LANDINGS}
    grades = {s: {str(k): got[s, k].get("live_score") for k in LANDINGS} for s in SIDES}
    books = {s: got[s, LANDINGS[0]]["public_books_sha256"] for s in SIDES}
    row: dict[str, Any] = {"pair": pair, "grades": grades, "public_books_sha256": books}
    if not all(c["status"] == "counted" for c in got.values()):
        return row | {"complete": False}
    x_early = (got["X", 13]["live_score"] + got["Y", 17]["live_score"]) / 2
    y_early = (got["X", 17]["live_score"] + got["Y", 13]["live_score"]) / 2
    rx, ry = got["X", 13]["last_revenue"], got["Y", 13]["last_revenue"]
    diff = x_early - y_early
    favors = "X" if diff >= WIN_MARGIN else "Y" if -diff >= WIN_MARGIN else "tie"
    choices = {
        "always_X": "X",
        "always_Y": "Y",
        "higher_last_revenue": "X" if rx >= ry else "Y",
        "lower_last_revenue": "X" if rx <= ry else "Y",
    }
    row |= {"complete": True, "last_revenue": {"X": rx, "Y": ry}}
    row |= {
        "assignment": {"X": x_early, "Y": y_early},
        "difference": diff,
        "favors": favors,
    }
    return row | {
        "oracle": "X" if x_early >= y_early else "Y",
        "policy_choices": choices,
    }


def _decision(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(rows) == len(PAIRS) and all(r["complete"] for r in rows)
    means = {
        p: math.fsum(r["assignment"][r["policy_choices"][p]] for r in rows) / len(rows)
        for p in POLICIES
    }
    best = max(POLICIES, key=lambda p: (means[p], -POLICIES.index(p)))
    oracle = math.fsum(r["assignment"][r["oracle"]] for r in rows) / len(rows)
    wins = {v: sum(1 for r in rows if r["favors"] == v) for v in ("X", "Y", "tie")}
    checks = {
        "x_wins_at_least_2": wins["X"] >= MIN_WINS,
        "y_wins_at_least_2": wins["Y"] >= MIN_WINS,
        "oracle_advantage_at_least_0_015": oracle - means[best] >= ORACLE_MARGIN,
    }
    return {
        "policy_means": means,
        "best_fixed_policy": {"name": best, "mean": means[best]},
        "oracle_mean": oracle,
        "oracle_advantage": oracle - means[best],
        "wins": wins,
        "checks": checks,
    }


def panel(directory: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    cells, unknown, collisions = _load(directory)
    statuses = [c["status"] for c in cells.values()]
    count = {
        s: statuses.count(s) for s in ("counted", "invalid", "rejected", "missing")
    }
    complete = count["counted"] == len(EXPECTED)
    rows = [_pair(cells, p) for p in PAIRS]
    decision = _decision(rows) if complete else None
    passed = decision is not None and all(decision["checks"].values())
    coverage = {
        "expected": len(EXPECTED),
        "present": len(EXPECTED) - count["missing"],
        **count,
        "unknown_entries": len(unknown),
        "unknown_names": unknown[:NAME_LIMIT],
    }
    keep = (
        "name",
        "pair",
        "side",
        "landing",
        "status",
        "reason",
        "public_books_sha256",
    )
    report = {
        "schema": SCHEMA,
        "cell_schema": CELL_SCHEMA,
        "stock_commit": rm.SOURCE_COMMIT,
        "denominator": {
            "cells": len(EXPECTED),
            "pairs": len(PAIRS),
            "names_sha256": _sha(("\n".join(EXPECTED) + "\n").encode()),
        },
        "coverage": coverage,
        "complete": complete,
        "public_prefix_collisions": collisions,
        "cells": [{k: c[k] for k in keep} for c in cells.values()],
        "pairs": rows,
        "thresholds": {
            "win_margin": WIN_MARGIN,
            "min_wins": MIN_WINS,
            "oracle_margin": ORACLE_MARGIN,
        },
        "result": decision,
        "opportunity_passed": passed,
        "decision": "run_model_comparison"
        if passed
        else "do_not_run_model_teams"
        if complete
        else "incomplete_no_decision",
        "limitations": list(LIMITATIONS),
    }
    return report, cells


def _pair_arg(text: str) -> int:
    if PAIR_ARG.fullmatch(text) is None:
        raise argparse.ArgumentTypeError("pair must be one digit 0..7")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("panel", allow_abbrev=False).add_argument(
        "directory", metavar="CELL_DIRECTORY"
    )
    packet = sub.add_parser("packet", allow_abbrev=False)
    packet.add_argument("directory", metavar="CELL_DIRECTORY")
    packet.add_argument("--pair", type=_pair_arg, required=True)
    packet.add_argument("--side", choices=SIDES, required=True)
    return parser


def _emit(value: Any) -> None:
    sys.stdout.buffer.write(_canonical(value) + b"\n")
    sys.stdout.buffer.flush()


def _fail(message: str) -> int:
    sys.stderr.write(
        json.dumps({"error": message, "schema": SCHEMA}, sort_keys=True) + "\n"
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report, cells = panel(args.directory)
    except rm.Rejected as e:
        return _fail(str(e))
    if args.command == "panel":
        _emit(report)
        return 0 if report["opportunity_passed"] else 1
    if not report["opportunity_passed"]:
        return _fail(
            "registered opportunity filter has not passed; no packet is released"
        )
    cell = cells[f"p{args.pair}-{args.side}-{LANDINGS[0]}.json"]
    _emit({"role": ROLES[args.side], "books": cell["books"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
