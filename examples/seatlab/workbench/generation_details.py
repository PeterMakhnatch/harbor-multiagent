import re
from decimal import (
    ROUND_CEILING,
    Decimal,
    DecimalException,
    Inexact,
    Rounded,
    localcontext,
)
from typing import TypeGuard

from .account_contract import money
from .generation_receipts import (
    BASIS,
    MAX_COST_NANO,
    PRECISION,
    _assign,
    _json,
    _lines,
    _owners,
    _roster,
)
from .seat_stream import MIB, Malformed, Record, _read, _require

SCHEMA = "seatlab.openrouter-generation-details/v1"
MAX_DETAILS_BYTES = 32 * MIB
MAX_GENERATION_ID_CHARS = 128
GENERATION_ID = re.compile(r"gen-[0-9A-Za-z-]+")
DOCUMENT_KEYS = frozenset({"data"})
DATA_REQUIRED = frozenset({"id", "total_cost"})
NOT_DETAILS = frozenset(
    {"choices", "object", "cost_nano", "cost_basis", "receipt", "call_id", "event"}
)
CAVEAT = (
    "Known USD is the OpenRouter generation total_cost in the supplied local file of saved "
    "generation details, each generation counted once. The file is a local claim by whoever "
    "saved it. It is not checked against OpenRouter and it is not an account invoice. It holds "
    "no call starts, failures or reservations, so it makes no claim of completeness: the cost "
    "of any call without a document here is unknown, outside these returns. Team assignment "
    "covers only the executions in the supplied packets. For an open job they are surviving "
    "executions, not confirmed finals. Generations without an owner record inside the supplied "
    "packets stay unassigned and are never attributed to a team."
)


def generation_id(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and len(value) <= MAX_GENERATION_ID_CHARS
        and GENERATION_ID.fullmatch(value) is not None
    )


def _cost(total: object, index: int) -> Decimal:
    if not isinstance(total, (int, Decimal)) or isinstance(total, bool):
        raise Malformed(f"details row {index} total_cost is not a JSON number")
    _require(
        not Decimal(total).is_signed(),
        f"details row {index} total_cost is negative",
    )
    usd = Decimal(money(total))
    nano = (usd * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING)
    _require(
        nano <= MAX_COST_NANO,
        f"details row {index} total_cost exceeds the supported range",
    )
    return usd


def _document(raw: bytes, index: int) -> tuple[str, Decimal]:
    row = _json(raw, f"details row {index} is not strict UTF-8 JSON")
    _require(
        type(row) is dict and row.keys() == DOCUMENT_KEYS,
        f"details row {index} is not a saved generation details response",
    )
    data = row["data"]
    _require(
        type(data) is dict and DATA_REQUIRED <= data.keys(),
        f"details row {index} data lacks id and total_cost",
    )
    _require(
        not NOT_DETAILS & data.keys() and type(data.get("usage")) is not dict,
        f"details row {index} is a chat completion or client cost record",
    )
    _require(
        generation_id(data["id"]),
        f"details row {index} has an invalid generation identifier",
    )
    return data["id"], _cost(data["total_cost"], index)


def _details(path: str) -> tuple[dict[str, Decimal], int, int]:
    lines = _lines(
        _read(path, MAX_DETAILS_BYTES, "generation details file"),
        "generation details file",
    )
    totals: dict[str, Decimal] = {}
    repeats = 0
    for index, raw in enumerate(lines):
        generation, usd = _document(raw, index)
        if generation in totals:
            _require(
                totals[generation] == usd,
                f"details row {index} contradicts an earlier total_cost for its generation",
            )
            repeats += 1
        else:
            totals[generation] = usd
    _require(len(totals) + repeats == len(lines), "details row count mismatch")
    return totals, len(lines), repeats


def reconcile(
    packets: list[Record], details_path: str, owners_path: str | None = None
) -> Record:
    _require(type(details_path) is str, "invalid generation details path")
    _require(owners_path is None or type(owners_path) is str, "invalid owner file path")
    try:
        with localcontext() as context:
            context.prec = PRECISION
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            roster, repeated_packets = _roster(packets)
            totals, rows, repeated_rows = _details(details_path)
            owners, owner_rows, repeated_owners = _owners(owners_path)
            assigned = _assign(totals.items(), owners, roster)
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
        "documents": {
            "rows": rows,
            "distinct_generations": len(totals),
            "repeated_rows": repeated_rows,
        },
        "owners": {
            "rows": owner_rows,
            "records": len(owners),
            "repeated_rows": repeated_owners,
        },
        **assigned,
        "caveat": CAVEAT,
    }
