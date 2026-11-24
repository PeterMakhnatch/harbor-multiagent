from typing import Any, cast

from ..wait_peer.receipt import HEX64
from . import certified_panel as cp
from .panel import require

SCHEMA = "seatlab.reporter-owner-counters/v1"
OUTPUT = "seatlab.reporter-owner-usage/v1"
FIELDS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "total_tokens",
)
ROW_KEYS = frozenset(("world", "arm", "seat", "session_sha256", "usage"))
MAX_ROWS = cp.MAX_TEAMS * cp.REPORTERS
CONTRACT = "archival client: total_tokens == input_tokens + output_tokens, every row"
Row = dict[str, Any]
Key = tuple[int, str, str]


def _bound(teams: list[Row]) -> dict[Key, tuple[str, int]]:
    require(type(teams) is list and len(teams) == cp.MAX_TEAMS, "teams_shape")
    bound: dict[Key, tuple[str, int]] = {}
    cells: set[tuple[int, str]] = set()
    for team in teams:
        prov, wire = team["provenance"], team["external"]
        cell = (prov["world"], prov["arm"])
        require(cell == (wire["world"], wire["arm"]), "teams_cell")
        require(cell[0] in cp.WORLDS and cell[1] in cp.ARMS, "teams_cell")
        require(cell not in cells, "teams_duplicate")
        cells.add(cell)
        for seat in prov["seats"]:
            if seat["status"] != "bound":
                continue
            session, tokens = seat["session_sha256"], seat["client_tokens"]
            hexed = type(session) is str and HEX64.fullmatch(session) is not None
            require(hexed, "teams_session")
            counted = type(tokens) is int and 0 < tokens <= cp.MAX_TOKENS
            require(counted, "teams_tokens")
            bound[(cell[0], cell[1], seat["seat"])] = (session, tokens)
    require(len(cells) == cp.MAX_TEAMS, "teams_shape")
    return bound


def _count(value: object) -> int:
    require(type(value) is int, "usage_count")
    assert isinstance(value, int)
    require(0 <= value <= cp.MAX_TOKENS, "usage_count")
    return value


def _mapping(value: object, code: str) -> Row:
    require(type(value) is dict, code)
    return cast(Row, value)


def _usage(value: object) -> dict[str, int]:
    data = _mapping(value, "usage_shape")
    require(data.keys() == set(FIELDS), "usage_shape")
    usage = {field: _count(data[field]) for field in FIELDS}
    require(
        usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"],
        "client_contract",
    )
    return usage


def _record(value: object) -> tuple[Key, str, dict[str, int]]:
    data = _mapping(value, "row_shape")
    require(data.keys() == ROW_KEYS, "row_shape")
    world, arm, seat = data["world"], data["arm"], data["seat"]
    require(type(world) is int and world in cp.WORLDS, "row_world")
    require(type(arm) is str and arm in cp.ARMS, "row_arm")
    require(type(seat) is str and seat in cp.SEATS, "row_seat")
    session = data["session_sha256"]
    hexed = type(session) is str and HEX64.fullmatch(session) is not None
    require(hexed, "row_session")
    return (world, arm, seat), session, _usage(data["usage"])


def _matched(supplement: object, bound: dict[Key, tuple[str, int]]) -> dict[Key, Row]:
    data = _mapping(supplement, "supplement_shape")
    require(data.keys() == {"schema", "rows"}, "supplement_shape")
    require(data["schema"] == SCHEMA, "supplement_schema")
    rows = data["rows"]
    require(type(rows) is list and len(rows) <= MAX_ROWS, "supplement_rows")
    usage: dict[Key, Row] = {}
    for value in rows:
        key, session, counts = _record(value)
        require(key not in usage, "duplicate_row")
        require(key in bound, "unbound_row")
        require(session == bound[key][0], "session_mismatch")
        require(counts["total_tokens"] == bound[key][1], "total_mismatch")
        usage[key] = counts
    require(usage.keys() == bound.keys(), "missing_row")
    return usage


def _group(rows: list[dict[str, int]]) -> Row:
    return {
        "count": len(rows),
        "sums": {field: sum(row[field] for row in rows) for field in FIELDS},
    }


def _arm(usage: dict[Key, Row], arm: str, shared: set[tuple[int, str]]) -> Row:
    own = {(w, s): row for (w, a, s), row in usage.items() if a == arm}
    groups = {
        "full": _group(list(own.values())),
        "matched": _group([row for key, row in own.items() if key in shared]),
        "only": _group([row for key, row in own.items() if key not in shared]),
    }
    counts = groups["matched"]["count"] + groups["only"]["count"]
    assert groups["full"]["count"] == counts
    for field in FIELDS:
        parts = groups["matched"]["sums"][field] + groups["only"]["sums"][field]
        assert groups["full"]["sums"][field] == parts
    return groups


def partition(teams: list[Row], supplement: object) -> Row:
    bound = _bound(teams)
    usage = _matched(supplement, bound)
    seats = {arm: {(w, s) for (w, a, s) in usage if a == arm} for arm in cp.ARMS}
    shared = seats["all"] & seats["stop"]
    arms = {arm: _arm(usage, arm, shared) for arm in cp.ARMS}
    stop, every = arms["stop"]["matched"]["sums"], arms["all"]["matched"]["sums"]
    delta = {field: stop[field] - every[field] for field in FIELDS}
    assert arms["stop"]["matched"]["count"] == arms["all"]["matched"]["count"]
    assert delta["total_tokens"] == delta["input_tokens"] + delta["output_tokens"]
    return {
        "schema": OUTPUT,
        "client_contract": CONTRACT,
        "join": "world + physical seat; matched = bound owner in both arms",
        "fields": list(FIELDS),
        "arms": arms,
        "shared_stop_minus_all": delta,
        "shared_input_plus_cache_read_delta": (
            delta["input_tokens"] + delta["cache_read_tokens"]
        ),
        "claims": {"causal": False, "price": False, "provider": False},
    }
