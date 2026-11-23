from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn, cast

Record = dict[str, Any]
Counts = dict[str, int]
Seat = tuple[str, Record]
Audience = str | frozenset[str]
Message = tuple[int, str, str, bool, Audience | None, float | None]
Output = tuple[int | None, list[Any]]
ReadResult = tuple[int | None, list[Any] | None]
Evidence = tuple[int, int, int | None]
Reader = tuple[
    Callable[[Record], bool], Callable[[list[Record], int, Counts], ReadResult]
]

BOARD_SCHEMA = "message board of the team"
BOARD_KEYS = frozenset(
    {"step_id", "source", "kind", "message", "to", "minute", "timestamp"}
)
BOARD_KIND = "board"
EVERYONE = "everyone"
NONE = "NONE"
UNKNOWN = "UNKNOWN"
CLAUDE = "claude-code"
ANTIGRAVITY = "antigravity-cli"
CLAUDE_READ_TOOLS = frozenset({"mcp__board__inbox", "mcp__board__history"})
ANTIGRAVITY_FUNCTION = "call_mcp_tool"
ANTIGRAVITY_SERVER = "board"
ANTIGRAVITY_READ_TOOLS = frozenset({"inbox", "history"})
ANTIGRAVITY_HEADER = re.compile(
    r"Created At: ([0-9TZ:\-]{20})\nCompleted At: ([0-9TZ:\-]{20})\n"
)
ANTIGRAVITY_EMPTY = "tool call completed"
DIVERTED_PREFIX = "The output was large and was saved to: "
SHORTENED = re.compile(r"\n… \[[0-9][0-9,]* more characters not shown\]\Z")
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
MESSAGE_REQUIRED = frozenset({"seq", "from", "text"})
MESSAGE_OPTIONAL = frozenset({"to", "ts"})
WRAPPER_KEY = "result"
MAX_STEPS = 100_000
MAX_CALLS_PER_STEP = 64
MAX_OBJECTS_PER_OUTPUT = 10_000
MAX_OBSERVATION_CHARS = 16 << 20
COUNTERS = (
    "board_rows",
    "rows_foreign",
    "rows_off_roster",
    "rows_ambiguous",
    "calls_read",
    "calls_pooled",
    "calls_unseen",
    "calls_unrecognized_harness",
    "outputs_ok",
    "outputs_shortened",
    "outputs_diverted",
    "outputs_unrecognized",
    "objects_seen",
    "objects_malformed",
    "objects_own",
    "objects_foreign",
    "objects_unmatched",
    "objects_audience_mismatch",
    "objects_ambiguous_row",
    "seq_conflicts",
    "row_seq_conflicts",
    "bounds_open",
    "bounds_next_step",
    "bounds_completion",
    "bounds_regressed",
    "bounds_before_sent",
    "receipts",
)


def _seconds(value: Any, where: str) -> int:
    if not isinstance(value, str) or STAMP.fullmatch(value) is None:
        raise ValueError(f"{where}: expected UTC second timestamp")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
    )


def _audience(value: Any) -> Audience | None:
    if value is None or value == EVERYONE:
        return EVERYONE
    if isinstance(value, str) and value:
        return frozenset({value})
    if isinstance(value, list) and 0 < len(value) <= MAX_OBJECTS_PER_OUTPUT:
        names: list[str] = [
            name
            for name in value
            if isinstance(name, str) and name and name != EVERYONE
        ]
        if len(names) == len(value):
            return frozenset(names)
    return None


def _check_seats(seats: list[Seat]) -> tuple[str, str]:
    if not isinstance(seats, list) or len(seats) != 2:
        raise ValueError("seats: expected exactly two seats")
    names: list[str] = []
    for slot, seat in enumerate(seats):
        if not isinstance(seat, tuple) or len(seat) != 2:
            raise ValueError(f"seats[{slot}]: expected (name, transcript)")
        name, transcript = seat
        if not isinstance(name, str) or not name or name == EVERYONE:
            raise ValueError(f"seats[{slot}]: invalid seat name")
        if not isinstance(transcript, dict) or not isinstance(
            transcript.get("steps"), list
        ):
            raise TypeError(f"seats[{slot}]: expected ATIF object with steps")
        if len(transcript["steps"]) > MAX_STEPS:
            raise ValueError(f"seats[{slot}]: too many steps")
        if not all(isinstance(step, dict) for step in transcript["steps"]):
            raise ValueError(f"seats[{slot}]: expected step objects")
        names.append(name)
    if names[0] == names[1]:
        raise ValueError("seats: names must differ")
    return names[0], names[1]


def _board_rows(
    board: Record, names: tuple[str, str], counts: Counts
) -> dict[int, Record]:
    if not isinstance(board, dict) or board.get("schema") != BOARD_SCHEMA:
        raise ValueError("board: unexpected schema")
    steps = board.get("steps")
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError("board: expected bounded steps list")
    rows: dict[int, Record] = {}
    for index, value in enumerate(steps):
        where = f"board.steps[{index}]"
        if not isinstance(value, dict) or set(value) != BOARD_KEYS:
            raise ValueError(f"{where}: unexpected keys")
        step = cast(Record, value)
        row = step["step_id"]
        if isinstance(row, bool) or not isinstance(row, int) or row in rows:
            raise ValueError(f"{where}: step_id must be a unique integer")
        for key in ("source", "kind", "message"):
            if not isinstance(step[key], str):
                raise TypeError(f"{where}.{key}: expected string")
        if not _finite(step["minute"]):
            raise ValueError(f"{where}.minute: expected finite number")
        audience = _audience(step["to"])
        if audience is None:
            raise ValueError(f"{where}.to: invalid audience")
        rows[row] = {
            "row": row,
            "source": step["source"],
            "kind": step["kind"],
            "message": step["message"],
            "audience": audience,
            "minute": step["minute"],
            "sent_s": _seconds(step["timestamp"], f"{where}.timestamp"),
        }
    counts["board_rows"] = len(rows)
    return _roster_rows(rows, names, counts)


def _roster_rows(
    rows: dict[int, Record], names: tuple[str, str], counts: Counts
) -> dict[int, Record]:
    kept: dict[int, Record] = {}
    for row, item in rows.items():
        if item["kind"] != BOARD_KIND or item["source"] not in names:
            counts["rows_foreign"] += 1
            continue
        direction = names.index(item["source"])
        partner = names[1 - direction]
        if item["audience"] != EVERYONE and item["audience"] != frozenset({partner}):
            counts["rows_off_roster"] += 1
            continue
        item["direction"] = direction
        kept[row] = item
    keys: dict[tuple[str, str], list[int]] = {}
    for row, item in kept.items():
        keys.setdefault((item["source"], item["message"]), []).append(row)
    for group in keys.values():
        if len(group) > 1:
            counts["rows_ambiguous"] += len(group)
    return kept


def _decode_body(body: str, counts: Counts) -> list[Any] | None:
    if SHORTENED.search(body) is not None:
        counts["outputs_shortened"] += 1
        return None
    if body.startswith(DIVERTED_PREFIX):
        counts["outputs_diverted"] += 1
        return None
    try:
        parsed = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        counts["outputs_unrecognized"] += 1
        return None
    items: list[Any]
    if (
        isinstance(parsed, dict)
        and set(parsed) == {WRAPPER_KEY}
        and isinstance(parsed[WRAPPER_KEY], list)
    ):
        items = parsed[WRAPPER_KEY]
    elif isinstance(parsed, dict) and MESSAGE_REQUIRED <= set(parsed):
        items = [parsed]
    else:
        counts["outputs_unrecognized"] += 1
        return None
    if len(items) > MAX_OBJECTS_PER_OUTPUT:
        counts["outputs_unrecognized"] += 1
        return None
    counts["outputs_ok"] += 1
    return items


def _reject_constant(name: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _is_claude_read(call: Record) -> bool:
    return call.get("function_name") in CLAUDE_READ_TOOLS


def _is_antigravity_read(call: Record) -> bool:
    arguments = call.get("arguments")
    return (
        call.get("function_name") == ANTIGRAVITY_FUNCTION
        and isinstance(arguments, dict)
        and arguments.get("ServerName") == ANTIGRAVITY_SERVER
        and arguments.get("ToolName") in ANTIGRAVITY_READ_TOOLS
    )


def _claude_output(steps: list[Record], index: int, counts: Counts) -> ReadResult:
    items = _decode_body(steps[index]["observation"], counts)
    if items is None:
        return None, None
    if not items or index + 1 >= len(steps):
        return None, items
    start = _seconds(steps[index].get("timestamp"), f"step[{index}].timestamp")
    following = _seconds(
        steps[index + 1].get("timestamp"), f"step[{index + 1}].timestamp"
    )
    if following < start:
        counts["bounds_regressed"] += 1
        return None, items
    counts["bounds_next_step"] += 1
    return following, items


def _antigravity_output(steps: list[Record], index: int, counts: Counts) -> ReadResult:
    observation = steps[index]["observation"]
    header = ANTIGRAVITY_HEADER.match(observation)
    if header is None:
        counts["outputs_unrecognized"] += 1
        return None, None
    body = observation[header.end() :]
    if body == ANTIGRAVITY_EMPTY:
        counts["outputs_ok"] += 1
        return None, []
    items = _decode_body(body, counts)
    if items is None:
        return None, None
    created = _seconds(header.group(1), f"step[{index}].created")
    completed = _seconds(header.group(2), f"step[{index}].completed")
    if completed < created:
        counts["bounds_regressed"] += 1
        return None, items
    counts["bounds_completion"] += 1
    return completed, items


READERS: dict[str, Reader] = {
    CLAUDE: (_is_claude_read, _claude_output),
    ANTIGRAVITY: (_is_antigravity_read, _antigravity_output),
}


def _seat_outputs(transcript: Record, counts: Counts) -> tuple[str, list[Output]]:
    agent = transcript.get("agent")
    harness = agent.get("harness") if isinstance(agent, dict) else None
    steps: list[Record] = transcript["steps"]
    if harness not in READERS:
        counts["calls_unrecognized_harness"] += sum(
            1 for step in steps if isinstance(step.get("tool_calls"), list)
        )
        return UNKNOWN, []
    is_read, read_output = READERS[harness]
    outputs: list[Output] = []
    reads = 0
    for index, step in enumerate(steps):
        calls = step.get("tool_calls")
        if calls is None:
            continue
        if not isinstance(calls, list) or not calls or len(calls) > MAX_CALLS_PER_STEP:
            raise ValueError(
                f"step[{index}].tool_calls: expected bounded non-empty list"
            )
        records: list[Record] = [call for call in calls if isinstance(call, dict)]
        if len(records) != len(calls):
            raise ValueError(f"step[{index}].tool_calls: expected objects")
        board_reads = sum(1 for call in records if is_read(call))
        if board_reads == 0:
            continue
        reads += board_reads
        counts["calls_read"] += board_reads
        if len(calls) != 1:
            counts["calls_pooled"] += board_reads
            continue
        observation = step.get("observation")
        if not isinstance(observation, str) or len(observation) > MAX_OBSERVATION_CHARS:
            counts["calls_unseen"] += 1
            continue
        bound, items = read_output(steps, index, counts)
        if items is not None:
            outputs.append((bound, items))
    if reads == 0:
        return NONE, outputs
    if not outputs:
        return UNKNOWN, outputs
    return harness, outputs


def _message(item: Any) -> Message | None:
    if not isinstance(item, dict):
        return None
    keys = set(item)
    if not MESSAGE_REQUIRED <= keys or not keys <= MESSAGE_REQUIRED | MESSAGE_OPTIONAL:
        return None
    seq, sender, text = item["seq"], item["from"], item["text"]
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    if not isinstance(sender, str) or not isinstance(text, str):
        return None
    audience = _audience(item["to"]) if "to" in item else None
    if "to" in item and audience is None:
        return None
    if "ts" in item and not _finite(item["ts"]):
        return None
    return seq, sender, text, "to" in item, audience, item.get("ts")


def _match(
    message: Message,
    index: dict[tuple[str, str], list[int]],
    rows: dict[int, Record],
    counts: Counts,
) -> int | None:
    _, sender, text, has_audience, audience, _ = message
    candidates = index.get((sender, text), [])
    if not candidates:
        counts["objects_unmatched"] += 1
        return None
    if has_audience:
        candidates = [row for row in candidates if rows[row]["audience"] == audience]
        if not candidates:
            counts["objects_audience_mismatch"] += 1
            return None
    if len(candidates) != 1:
        counts["objects_ambiguous_row"] += 1
        return None
    return candidates[0]


def _evidence(
    names: tuple[str, str],
    seat_outputs: list[list[Output]],
    rows: dict[int, Record],
    counts: Counts,
) -> list[Evidence]:
    by_seq: dict[int, set[Message]] = {}
    pending: list[Evidence] = []
    index: dict[tuple[str, str], list[int]] = {}
    for row, item in rows.items():
        index.setdefault((item["source"], item["message"]), []).append(row)
    for slot, outputs in enumerate(seat_outputs):
        own, partner = names[slot], names[1 - slot]
        for bound, items in outputs:
            for item in items:
                counts["objects_seen"] += 1
                message = _message(item)
                if message is None:
                    counts["objects_malformed"] += 1
                    continue
                by_seq.setdefault(message[0], set()).add(message)
                if message[1] == own:
                    counts["objects_own"] += 1
                    continue
                if message[1] != partner:
                    counts["objects_foreign"] += 1
                    continue
                row = _match(message, index, rows, counts)
                if row is not None:
                    pending.append((message[0], row, bound))
    conflicted = {seq for seq, variants in by_seq.items() if len(variants) > 1}
    counts["seq_conflicts"] = len(conflicted)
    seqs_by_row: dict[int, set[int]] = {}
    for seq, row, _ in pending:
        seqs_by_row.setdefault(row, set()).add(seq)
    split_rows = {row for row, seqs in seqs_by_row.items() if len(seqs) > 1}
    counts["row_seq_conflicts"] = len(split_rows)
    return [
        entry
        for entry in pending
        if entry[0] not in conflicted and entry[1] not in split_rows
    ]


def project(board: Record, seats: list[Seat]) -> Record:
    names = _check_seats(seats)
    counts: Counts = dict.fromkeys(COUNTERS, 0)
    rows = _board_rows(board, names, counts)
    framing: list[str] = []
    seat_outputs: list[list[Output]] = []
    for _, transcript in seats:
        seat_framing, outputs = _seat_outputs(transcript, counts)
        framing.append(seat_framing)
        seat_outputs.append(outputs)
    reads: dict[int, set[int]] = {row: set() for row in rows}
    for _, row, bound in _evidence(names, seat_outputs, rows, counts):
        if bound is None:
            counts["bounds_open"] += 1
            continue
        if bound < rows[row]["sent_s"]:
            counts["bounds_before_sent"] += 1
        counts["receipts"] += 1
        reads[row].add(bound)
    messages: list[Record] = [
        {
            "row": row,
            "direction": rows[row]["direction"],
            "sent_s": rows[row]["sent_s"],
            "minute": rows[row]["minute"],
            "read_s": sorted(reads[row]),
        }
        for row in sorted(rows)
    ]
    assert all(
        set(item) == {"row", "direction", "sent_s", "minute", "read_s"}
        for item in messages
    )
    return {"messages": messages, "diagnostics": {**counts, "framing": framing}}
