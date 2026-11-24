import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import (
    ROUND_CEILING,
    Decimal,
    DecimalException,
    Inexact,
    Rounded,
    localcontext,
)
from typing import Any, NoReturn, override

from .account_contract import DIGEST, money
from .account_report import MAX_PACKETS, unique
from .seat_stream import (
    MIB,
    SEAT,
    UUID,
    Malformed,
    Record,
    _constant,
    _read,
    _require,
    _strict_json,
    _unique,
)

SCHEMA = "seatlab.generation-receipts/v1"
OWNER_SCHEMA = "seatlab.openrouter-seat-request/v1"
OWNER_PREFIX = b"openrouter:generation:v1\0"
OWNER_KEYS = {"schema", "trial", "seat", "generation_sha256"}
BASIS = "openrouter_generation.total_cost"
PROG = "python3.12 -BS -m examples.seatlab.workbench.generation_receipts"
MAX_LEDGER_BYTES = 32 * MIB
MAX_OWNER_BYTES = 32 * MIB
MAX_PACKET_BYTES = 32 * MIB
MAX_LINE_BYTES = 64 * 1024
MAX_ROWS = 262_144
MAX_NUMBER_CHARS = 40
MAX_COST_NANO = 10**18
PRECISION = 120
CALL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
MAX_ROUTE_CHARS = 128
ROUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*(?: [A-Za-z0-9_.:/-]+)*")
EVENTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "started": (
        frozenset(
            {
                "event",
                "at",
                "call_id",
                "identity",
                "model",
                "request_sha256",
                "reserved_nano",
                "input_token_bound",
                "output_token_bound",
            }
        ),
        frozenset({"adapter_version"}),
    ),
    "failed": (
        frozenset(
            {
                "event",
                "at",
                "call_id",
                "error_type",
                "http_status",
                "provider_detail",
                "reservation_retained",
            }
        ),
        frozenset(),
    ),
    "billing_unavailable": (
        frozenset({"event", "at", "call_id", "error_type"}),
        frozenset({"http_status"}),
    ),
    "hold": (frozenset({"event", "at", "call_id", "reason"}), frozenset()),
    "settled": (
        frozenset({"event", "at", "call_id", "cost_nano", "cost_basis", "receipt"}),
        frozenset(),
    ),
}
RECEIPT_REQUIRED = frozenset({"id", "total_cost"})
RECEIPT_ALLOWED = RECEIPT_REQUIRED | {
    "model",
    "provider_name",
    "native_tokens_prompt",
    "native_tokens_completion",
    "tokens_prompt",
    "tokens_completion",
    "latency",
}
KINDS = ("matched", "foreign_trial", "off_roster_seat", "unidentified")
CAVEAT = (
    "Known USD is the OpenRouter generation total_cost returned for calls settled in this local ledger, "
    "each generation counted once. It is not an account invoice for all time. Failed, billing-unavailable "
    "and unfinished calls carry no known charge here. Team assignment covers only the executions in "
    "the supplied packets. For an open job they are surviving executions, not confirmed finals. "
    "Generations without an owner record inside the supplied packets stay unassigned and are never "
    "attributed to a team."
)


@dataclass
class _Ledger:
    rows: int = 0
    started: dict[str, Record] = field(default_factory=dict)
    repeated_starts: int = 0
    failed: set[str] = field(default_factory=set)
    unavailable: set[str] = field(default_factory=set)
    held: set[str] = field(default_factory=set)
    settled: dict[str, tuple[str, Decimal]] = field(default_factory=dict)
    repeated_settlements: int = 0
    generation_calls: dict[str, str] = field(default_factory=dict)
    routes: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


def route_label(value: object) -> str | None:
    if type(value) is not str or not 0 < len(value) <= MAX_ROUTE_CHARS:
        return None
    return value if ROUTE.fullmatch(value) is not None else None


def _number(text: str) -> Decimal:
    _require(len(text) <= MAX_NUMBER_CHARS, "JSON number is too long")
    return Decimal(text)


def _integer(text: str) -> int:
    _require(len(text) <= MAX_NUMBER_CHARS, "JSON number is too long")
    return int(text)


def _json(raw: bytes, reason: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique,
            parse_constant=_constant,
            parse_float=_number,
            parse_int=_integer,
        )
    except (ValueError, RecursionError) as error:
        raise Malformed(reason) from error


def _lines(data: bytes, label: str) -> list[bytes]:
    _require(b"\r" not in data, f"{label} has carriage returns")
    body = data[:-1] if data.endswith(b"\n") else data
    if not body:
        return []
    lines = body.split(b"\n")
    _require(len(lines) <= MAX_ROWS, f"{label} has more than {MAX_ROWS} rows")
    for index, line in enumerate(lines):
        _require(
            0 < len(line) <= MAX_LINE_BYTES, f"{label} row {index} is empty or too long"
        )
    return lines


def _generation_hash(generation_id: str) -> str:
    return hashlib.sha256(OWNER_PREFIX + generation_id.encode("ascii")).hexdigest()


def owner(trial: str, seat: str, generation_id: str) -> Record:
    _require(
        type(trial) is str and UUID.fullmatch(trial) is not None,
        "invalid owner trial identity",
    )
    _require(
        type(seat) is str and SEAT.fullmatch(seat) is not None,
        "invalid owner seat identifier",
    )
    _require(
        type(generation_id) is str and GENERATION.fullmatch(generation_id) is not None,
        "invalid generation identifier",
    )
    return {
        "schema": OWNER_SCHEMA,
        "trial": trial,
        "seat": seat,
        "generation_sha256": _generation_hash(generation_id),
    }


def _ledger_row(raw: bytes, index: int) -> Record:
    row = _json(raw, f"ledger row {index} is not strict UTF-8 JSON")
    _require(type(row) is dict, f"ledger row {index} is not a JSON object")
    event = row.get("event")
    _require(
        type(event) is str and event in EVENTS,
        f"ledger row {index} has an unsupported event",
    )
    required, optional = EVENTS[event]
    _require(
        required <= row.keys() <= required | optional,
        f"ledger row {index} does not have the {event} fields",
    )
    call = row["call_id"]
    _require(
        type(call) is str and CALL.fullmatch(call) is not None,
        f"ledger row {index} has an invalid call identifier",
    )
    return row


def _settlement(row: Record, index: int) -> tuple[str, Decimal]:
    _require(
        row["cost_basis"] == BASIS, f"ledger row {index} has an unsupported cost basis"
    )
    nano = row["cost_nano"]
    _require(
        type(nano) is int and 0 <= nano <= MAX_COST_NANO,
        f"ledger row {index} has an invalid cost_nano",
    )
    receipt = row["receipt"]
    _require(
        type(receipt) is dict and RECEIPT_REQUIRED <= receipt.keys() <= RECEIPT_ALLOWED,
        f"ledger row {index} has unsupported receipt fields",
    )
    generation = receipt["id"]
    _require(
        type(generation) is str and GENERATION.fullmatch(generation) is not None,
        f"ledger row {index} has an invalid generation identifier",
    )
    total = receipt["total_cost"]
    _require(
        type(total) in (int, Decimal),
        f"ledger row {index} total cost is not a JSON number",
    )
    usd = Decimal(money(total))
    ceiling = (usd * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING)
    _require(
        ceiling == nano, f"ledger row {index} cost_nano contradicts the returned total"
    )
    return generation, usd


def _route(ledger: _Ledger, call: str, row: Record, index: int) -> None:
    receipt = row["receipt"]
    route = (
        route_label(receipt.get("model")),
        route_label(receipt.get("provider_name")),
    )
    prior = ledger.routes.setdefault(call, route)
    _require(
        prior == route,
        f"ledger row {index} settles a call with a contradictory route",
    )


def _apply(ledger: _Ledger, row: Record, index: int, *, include_routes: bool) -> None:
    call = row["call_id"]
    match row["event"]:
        case "started":
            prior = ledger.started.setdefault(call, row)
            _require(
                prior == row,
                f"ledger row {index} restarts a call with different fields",
            )
            ledger.repeated_starts += prior is not row
        case "failed":
            ledger.failed.add(call)
        case "billing_unavailable":
            ledger.unavailable.add(call)
        case "hold":
            ledger.held.add(call)
        case "settled":
            settlement = _settlement(row, index)
            prior = ledger.settled.get(call)
            if prior is not None:
                _require(
                    prior == settlement,
                    f"ledger row {index} settles a call with a contradictory generation or cost",
                )
                ledger.repeated_settlements += 1
                if include_routes:
                    _route(ledger, call, row, index)
                return
            owner_call = ledger.generation_calls.setdefault(settlement[0], call)
            _require(
                owner_call == call,
                f"ledger row {index} reuses a generation for another local call",
            )
            ledger.settled[call] = settlement
            if include_routes:
                _route(ledger, call, row, index)


def _ledger(path: str, *, include_routes: bool = False) -> _Ledger:
    _require(type(include_routes) is bool, "invalid route option")
    ledger = _Ledger()
    lines = _lines(_read(path, MAX_LEDGER_BYTES, "ledger"), "ledger")
    for index, raw in enumerate(lines):
        _apply(ledger, _ledger_row(raw, index), index, include_routes=include_routes)
    ledger.rows = len(lines)
    outcomes = ledger.failed | ledger.unavailable | ledger.held | ledger.settled.keys()
    _require(
        outcomes <= ledger.started.keys(),
        "ledger has outcomes for calls that never started",
    )
    _require(
        not ledger.failed & ledger.settled.keys(),
        "ledger has a failed call that also settled",
    )
    _require(
        include_routes or not ledger.routes,
        "ledger collected routes without the option",
    )
    _require(
        not include_routes or ledger.routes.keys() == ledger.settled.keys(),
        "ledger routes do not match its settlements",
    )
    return ledger


def _owner_row(raw: bytes, index: int) -> tuple[str, tuple[str, str]]:
    row = _json(raw, f"owner row {index} is not strict UTF-8 JSON")
    _require(
        type(row) is dict
        and row.keys() == OWNER_KEYS
        and row["schema"] == OWNER_SCHEMA,
        f"owner row {index} is not a {OWNER_SCHEMA} record",
    )
    trial, seat, digest = row["trial"], row["seat"], row["generation_sha256"]
    _require(
        type(trial) is str and UUID.fullmatch(trial) is not None,
        f"owner row {index} has an invalid trial",
    )
    _require(
        type(seat) is str and SEAT.fullmatch(seat) is not None,
        f"owner row {index} has an invalid seat",
    )
    _require(
        type(digest) is str and DIGEST.fullmatch(digest) is not None,
        f"owner row {index} has an invalid digest",
    )
    return digest, (trial, seat)


def _owners(path: str | None) -> tuple[dict[str, tuple[str, str]], int, int]:
    if path is None:
        return {}, 0, 0
    lines = _lines(_read(path, MAX_OWNER_BYTES, "owner file"), "owner file")
    owners: dict[str, tuple[str, str]] = {}
    repeats = 0
    for index, raw in enumerate(lines):
        digest, claim = _owner_row(raw, index)
        prior = owners.setdefault(digest, claim)
        _require(
            prior == claim, f"owner row {index} assigns one generation to a second seat"
        )
        repeats += prior is not claim
    return owners, len(lines), repeats


def _roster(packets: list[Record]) -> tuple[dict[str, frozenset[str]], int]:
    _require(type(packets) is list, "invalid packet inventory")
    if not packets:
        return {}, 0
    records, repeated = unique(packets)
    roster = {
        attempt["trial"]: frozenset(seat["name"] for seat in attempt["seats"])
        for record in records
        for attempt in record["attempts"]
    }
    return roster, repeated


def _kind(claim: tuple[str, str] | None, roster: dict[str, frozenset[str]]) -> str:
    if claim is None:
        return "unidentified"
    if claim[0] not in roster:
        return "foreign_trial"
    return "matched" if claim[1] in roster[claim[0]] else "off_roster_seat"


def _assign(
    ledger: _Ledger,
    owners: dict[str, tuple[str, str]],
    roster: dict[str, frozenset[str]],
) -> Record:
    totals = {kind: [0, Decimal(0)] for kind in KINDS}
    team: dict[tuple[str, str], list[Any]] = {}
    settled_digests: set[str] = set()
    for _, (generation, usd) in sorted(ledger.settled.items()):
        digest = _generation_hash(generation)
        settled_digests.add(digest)
        claim = owners.get(digest)
        kind = _kind(claim, roster)
        totals[kind][0] += 1
        totals[kind][1] += usd
        if kind == "matched" and claim is not None:
            row = team.setdefault(claim, [0, Decimal(0)])
            row[0] += 1
            row[1] += usd
    waiting = {
        digest: claim
        for digest, claim in owners.items()
        if digest not in settled_digests
    }
    inside = {
        digest for digest, claim in waiting.items() if _kind(claim, roster) == "matched"
    }
    waiting_seats = {waiting[digest] for digest in inside}
    seats = {(trial, seat) for trial, names in roster.items() for seat in names}
    pending = waiting_seats - team.keys()
    return {
        "generations": {
            kind: {"count": count, "usd": money(usd)}
            for kind, (count, usd) in totals.items()
        },
        "known_usd": {
            "settled": money(sum((usd for _, usd in totals.values()), Decimal(0))),
            "assigned_to_team": money(totals["matched"][1]),
            "not_assigned": money(
                sum((totals[kind][1] for kind in KINDS[1:]), Decimal(0))
            ),
        },
        "owner_records_without_settled_generation": {
            "packet_seat": len(inside),
            "outside_packets": len(waiting) - len(inside),
        },
        "seats": {
            "with_settled_generation": len(team),
            "with_settled_and_waiting_generations": len(waiting_seats & team.keys()),
            "owner_record_without_settled_generation": len(pending),
            "without_owner_record": len(seats) - len(team) - len(pending),
        },
        "team": [
            {"trial": trial, "seat": seat, "generations": count, "usd": money(usd)}
            for (trial, seat), (count, usd) in sorted(team.items())
        ],
    }


def _ledger_summary(ledger: _Ledger) -> Record:
    open_calls = ledger.started.keys() - ledger.settled.keys()
    failed = open_calls & ledger.failed
    unavailable = (open_calls & ledger.unavailable) - failed
    return {
        "rows": ledger.rows,
        "calls": len(ledger.started),
        "repeated_start_rows": ledger.repeated_starts,
        "repeated_settlement_rows": ledger.repeated_settlements,
        "reservation_holds": len(ledger.held),
        "settled_calls": len(ledger.settled),
        "unsettled_calls": {
            "failed": len(failed),
            "billing_unavailable": len(unavailable),
            "no_outcome": len(open_calls) - len(failed) - len(unavailable),
        },
    }


def reconcile(
    packets: list[Record], ledger_path: str, owners_path: str | None = None
) -> Record:
    _require(type(ledger_path) is str, "invalid ledger path")
    _require(owners_path is None or type(owners_path) is str, "invalid owner file path")
    try:
        with localcontext() as context:
            context.prec = PRECISION
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            roster, repeated_packets = _roster(packets)
            ledger = _ledger(ledger_path)
            owners, owner_rows, repeated_owners = _owners(owners_path)
            assigned = _assign(ledger, owners, roster)
    except DecimalException as error:
        raise Malformed("USD arithmetic exceeds exact supported range") from error
    return {
        "schema": SCHEMA,
        "basis": BASIS,
        "currency": "USD",
        "packets": {
            "supplied": len(packets),
            "repeated": repeated_packets,
            "trials": len(roster),
            "seats": sum(len(names) for names in roster.values()),
        },
        "ledger": _ledger_summary(ledger),
        "owners": {
            "rows": owner_rows,
            "records": len(owners),
            "repeated_rows": repeated_owners,
        },
        **assigned,
        "caveat": CAVEAT,
    }


def _packet_file(path: str) -> Record:
    return _strict_json(
        _read(path, MAX_PACKET_BYTES, "packet"), "packet is not strict UTF-8 JSON"
    )


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(
            "generation_receipts: usage error; expected LEDGER [--owners OWNERS] [--packet PACKET]...\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Assign settled OpenRouter generation totals to packet seats by owner digest.",
    )
    cli.add_argument("ledger")
    cli.add_argument("--owners")
    cli.add_argument("--packet", action="append", default=[])
    args = cli.parse_args(argv)
    try:
        _require(len(args.packet) <= MAX_PACKETS, "too many packet files")
        report = reconcile(
            [_packet_file(path) for path in args.packet], args.ledger, args.owners
        )
    except Malformed as error:
        print(f"generation_receipts: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
