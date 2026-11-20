"""Reserve before a direct Flash call. Account for each returned token or keep the full hold."""

import argparse
import json
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

_UNIT = (
    1_000_000_000  # integer nanodollars; the smallest list-price rate is 3 per token
)
_HOLD = 10_000_000  # $0.01 for <=8192 request bytes, <=512 output tokens, no tools
_FLOOR = 1_500_000_000
_SHARED_CAP = 10 * _UNIT
_ROLES = {
    "inventory",
    "dispatch",
    "decision_maker",
    "analyst_1",
    "analyst_2",
    "analyst_3",
    "analyst_4",
    "cal_initial_p1",
    "cal_initial_p2",
    "cal_recheck",
    "cal_decide",
    "cal_tool_pick",
    "cal_tool_reply",
}
_HOST_LEDGER: Path | None = None


class SpendRefused(ValueError):
    pass


def _money(value: Decimal) -> int:
    if not value.is_finite() or value < 0 or value * _UNIT != int(value * _UNIT):
        raise SpendRefused("invalid_usd")
    return int(value * _UNIT)


def _usd(nano: int) -> str:
    return f"{Decimal(nano) / _UNIT:.9f}"


def _location(root: Path) -> Path:
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise SpendRefused("private_spend_directory_required")
    return root / "direct-flash.sqlite3"


def bind_host_spend_dir() -> None:
    """Keep the ledger location in the host; do not export it to native children."""
    global _HOST_LEDGER
    value = os.environ.pop("SEATLAB_SPEND_DIR", None)
    if (
        _HOST_LEDGER is not None
        or value is None
        or value == ""
        or not Path(value).is_absolute()
    ):
        raise SpendRefused("host_spend_binding_invalid")
    root = Path(value)
    _location(root)
    _HOST_LEDGER = root


def initialize(root: Path, opening_balance_usd: Decimal, lane_cap_usd: Decimal) -> None:
    opening, cap = _money(opening_balance_usd), _money(lane_cap_usd)
    if opening < _FLOOR or not _HOLD <= cap <= _SHARED_CAP:
        raise SpendRefused("invalid_initial_budget")
    path = _location(root)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE budget (opening INTEGER, last_balance INTEGER, lane_cap INTEGER);
            CREATE TABLE calls (
                run TEXT NOT NULL, role TEXT NOT NULL, reserved_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, served_model TEXT, hit INTEGER,
                miss INTEGER, output INTEGER, cost_low INTEGER, cost_high INTEGER,
                PRIMARY KEY (run, role)
            );
        """)
        db.execute("INSERT INTO budget VALUES (?,?,?)", (opening, opening, cap))


@contextmanager
def _connect(root: Path | None = None):
    if root is None:
        root = _HOST_LEDGER
    if root is None:
        value = os.environ.get("SEATLAB_SPEND_DIR")
        if value is None or value == "":
            raise SpendRefused("no_spend_directory")
        root = Path(value)
    path = _location(root)
    with (
        closing(
            sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=15)
        ) as db,
        db,
    ):
        db.row_factory = sqlite3.Row
        yield db


def reserve(run_id: str, role: str, *, balance_usd: Decimal) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", run_id) or role not in _ROLES:
        raise SpendRefused("invalid_call_identity")
    balance = _money(balance_usd)
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        opening, last, cap = db.execute("SELECT * FROM budget").fetchone()
        charged, pending = db.execute(
            "SELECT COALESCE(SUM(cost_high),0), COUNT(*) - COUNT(cost_high) FROM calls"
        ).fetchone()
        holds = (pending + 1) * _HOLD
        if balance > last:
            raise SpendRefused("account_balance_increased_reconcile_first")
        if balance - holds < _FLOOR:
            raise SpendRefused("balance_floor")
        if (
            charged + holds > cap
            or max(opening - balance, charged) + holds > _SHARED_CAP
        ):
            raise SpendRefused("budget_cap")
        try:
            db.execute(
                "INSERT INTO calls(run,role,reserved_at) VALUES (?,?,?)",
                (run_id, role, datetime.now(UTC).isoformat()),
            )
        except sqlite3.IntegrityError:
            raise SpendRefused("call_already_reserved") from None
        db.execute("UPDATE budget SET last_balance=?", (balance,))


def _offpeak(instant: datetime) -> bool:
    utc = instant.astimezone(UTC)
    return utc.weekday() >= 5 or not (1 <= utc.hour < 4 or 6 <= utc.hour < 10)


def settle(
    run_id: str,
    role: str,
    *,
    cache_hit: int,
    cache_miss: int,
    output: int,
    served_model: str,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    if (
        any(type(n) is not int or n < 0 for n in (cache_hit, cache_miss, output))
        or not served_model
        or started_at.tzinfo is None
        or finished_at.tzinfo is None
        or not timedelta(0) <= finished_at - started_at <= timedelta(minutes=10)
    ):
        raise SpendRefused("unknown_usage_kept_full_hold")
    low = cache_hit * 3 + cache_miss * 150 + output * 600
    # At weekday peak the account may still get the holiday price. Do not
    # invent a billing calendar. Off-peak in both endpoints is unambiguous
    # for a <=10 min request: the shortest peak interval lasts 3 hours.
    high = low if _offpeak(started_at) and _offpeak(finished_at) else 2 * low
    if high > _HOLD:
        raise SpendRefused("usage_exceeds_registered_hold")
    with _connect() as db:
        result = db.execute(
            "UPDATE calls SET hit=?,miss=?,output=?,served_model=?,started_at=?,finished_at=?,"
            "cost_low=?,cost_high=? WHERE run=? AND role=? AND cost_high IS NULL",
            (
                cache_hit,
                cache_miss,
                output,
                served_model,
                started_at.astimezone(UTC).isoformat(),
                finished_at.astimezone(UTC).isoformat(),
                low,
                high,
                run_id,
                role,
            ),
        )
        if result.rowcount != 1:
            raise SpendRefused("no_open_reservation")


def report(root: Path, *, run_prefix: str | None = None) -> dict[str, Any]:
    """Export public usage and cost, without account or request identifiers."""
    if run_prefix is not None and (not run_prefix or not run_prefix.isascii()):
        raise SpendRefused("invalid_run_prefix")

    runs: list[dict[str, str | int | None]] = []
    total_low = total_high = unresolved = 0
    with _connect(root) as db:
        for row in db.execute("SELECT * FROM calls ORDER BY run,role"):
            if run_prefix is not None and not row["run"].startswith(run_prefix):
                continue
            measured = row["cost_high"] is not None
            unresolved += not measured
            total_low += row["cost_low"] if measured else 0
            total_high += row["cost_high"] if measured else _HOLD
            runs.append(
                {
                    "run": row["run"],
                    "role": row["role"],
                    "served_model": row["served_model"],
                    "cache_hit": row["hit"],
                    "cache_miss": row["miss"],
                    "output": row["output"],
                    "estimated_usd_low": _usd(row["cost_low"]) if measured else None,
                    "estimated_usd_high": _usd(row["cost_high"]) if measured else None,
                    "unsettled_hold_usd": _usd(0 if measured else _HOLD),
                }
            )
    return {
        "schema": "seatlab.direct-flash-cost/v1",
        "currency": "USD",
        "calls": runs,
        "unsettled_calls": unresolved,
        "known_usd_low": _usd(total_low),
        "upper_usd_including_holds": _usd(total_high),
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
    }


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("private_dir", type=Path)
    init.add_argument("--opening-balance-usd", type=Decimal, required=True)
    init.add_argument("--lane-cap-usd", type=Decimal, required=True)
    report_cli = sub.add_parser("report")
    report_cli.add_argument("private_dir", type=Path)
    report_cli.add_argument(
        "--run-prefix", help="select runs with this nonempty literal ASCII prefix"
    )
    args = cli.parse_args()
    if args.command == "init":
        initialize(
            args.private_dir.resolve(), args.opening_balance_usd, args.lane_cap_usd
        )
    else:
        receipt = report(args.private_dir.resolve(), run_prefix=args.run_prefix)
        print(json.dumps(receipt, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
