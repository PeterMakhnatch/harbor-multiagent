from __future__ import annotations

import argparse
import errno
import json
import os
import re
import stat
import sys
from typing import Any, NoReturn

SCHEMA = "seatlab.continuation-owner-inbox/v1"
PROG = "python3.12 -BS -m examples.seatlab.continuation.owner_inbox"
SOURCE_COMMIT = "dbec43ff757c86c17077e61b37c7f584c880bdee"
SOURCE = {
    "commit": SOURCE_COMMIT,
    "log_writer": "harness/src/multi_agent_harness/board/core.py L105-L110",
    "register": "harness/src/multi_agent_harness/board/core.py L141-L154",
    "send_and_notify": "harness/src/multi_agent_harness/board/core.py L186-L216",
    "history_count_only": "harness/src/multi_agent_harness/board/core.py L231-L238",
    "inbox_seqs": "harness/src/multi_agent_harness/board/core.py L262-L287",
    "board_start": "harness/src/multi_agent_harness/board/server.py L238-L245",
    "subagent_wake_and_relay": "harness/src/multi_agent_harness/board/subagents.py L108-L130, L218-L271",
}
FILENAME = "board-events.jsonl"
MAX_BYTES = 256 << 20
MAX_LINE = 128 << 10
MAX_LINES = 2_000_000
MAX_LIMIT = 200
MAX_WAIT = 60
MAX_NAMES = 4096
CHUNK = 1 << 20
AGENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
HARNESS = "@harness"
SYSTEM_ROLE = "system"
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
BASE = frozenset({"ts", "event", "agent"})
MESSAGE_FIELDS = BASE | {"seq", "to", "text"}
INBOX_FIELDS = BASE | {"wait_seconds", "returned", "seqs"}
HISTORY_FIELDS = BASE | {"after", "returned"}
RATE_FIELDS = BASE | {"action", "count"}
REFUSED_FIELDS = {
    "send": BASE | {"action", "reason", "to", "recipients"},
    "history": BASE | {"action", "reason", "after"},
    "inbox": BASE | {"action", "reason"},
}
HISTORICAL = frozenset({"post", "read"})
MESSAGE_SHAPED = frozenset({"seq", "seqs", "to", "recipients", "text", "returned"})
CORE_EVENTS = (
    "config",
    "plugins",
    "register",
    "send",
    "notify",
    "inbox",
    "history",
    "refused",
    "rate_limited",
    "subagent",
)
HANDLERS = {event: f"_on_{event}" for event in CORE_EVENTS}
EXIT = {"complete": 0, "unattested": 3, "incomplete": 3, "unsupported": 3, "missing": 4}
MEANING = {
    "confirmed": "the log has an inbox event by that recipient whose seqs list holds this exact message seq; "
    "the board returned it to an inbox call. Not proof that a model read, used or acted on it.",
    "not_confirmed": "no such inbox event. Not proof of unread: history, wake prompts and other routes log no seq.",
    "history": "history events log a row count only, so those rows are never attributed to any message.",
    "broadcast": "broadcast edges use the registered roster only with --closed-roster and --complete-log, "
    "and only when every registration line comes before the first message line.",
    "order": "sends and inbox reads are matched by seq, never by line order.",
    "privacy": "counts and line numbers only: no agent names, roles, message text, times or paths.",
}
EPILOG = (
    "Exit 0 complete, 3 report not complete (unattested, incomplete or unsupported), "
    "4 no file at the path, 2 input rejected.\n"
    f"Stock source {SOURCE_COMMIT}: harness/src/multi_agent_harness/board/core.py."
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def _read(path: str) -> tuple[bytes, bool] | None:
    _require(
        os.path.basename(path) == FILENAME, f"path must name a file called {FILENAME}"
    )
    try:
        fd = os.open(path, OPEN_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(
            "log is a symbolic link"
            if exc.errno == errno.ELOOP
            else "log cannot be opened"
        ) from None
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode), "log is not a regular file")
        _require(before.st_size <= MAX_BYTES, f"log exceeds {MAX_BYTES} bytes")
        chunks: list[bytes] = []
        size = 0
        while size <= MAX_BYTES:
            chunk = os.read(fd, min(CHUNK, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
    except OSError:
        raise ValueError("log cannot be read") from None
    finally:
        os.close(fd)
    _require(size <= MAX_BYTES, f"log exceeds {MAX_BYTES} bytes")
    changed = size != before.st_size or (after.st_size, after.st_mtime_ns) != (
        before.st_size,
        before.st_mtime_ns,
    )
    return b"".join(chunks), changed


def _split(data: bytes) -> tuple[list[bytes], bool]:
    lines = data.split(b"\n")
    tail = lines.pop()
    _require(len(lines) <= MAX_LINES, f"log has more than {MAX_LINES} lines")
    return lines, tail != b""


def _decode(raw: bytes, number: int) -> dict[str, Any]:
    _require(
        0 < len(raw) <= MAX_LINE,
        f"line {number}: empty or longer than {MAX_LINE} bytes",
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"line {number}: not strict JSON") from None
    _require(type(value) is dict, f"line {number}: not a JSON object")
    return value


def _fields(record: dict[str, Any], allowed: frozenset[str], number: int) -> None:
    _require(
        record.keys() == allowed,
        f"line {number}: {record['event']} fields differ from the stock schema",
    )


def _name(value: Any, number: int) -> str:
    _require(
        type(value) is str and AGENT.fullmatch(value) is not None,
        f"line {number}: not a stock agent name",
    )
    return value


def _count(value: Any, upper: int, number: int) -> int:
    _require(
        type(value) is int and 0 <= value <= upper,
        f"line {number}: count out of the stock range",
    )
    return value


def _recipients(
    value: Any, author: str, shortest_list: int, number: int
) -> tuple[str, ...] | None:
    if value is None:
        return None
    names = [value] if type(value) is str else value
    _require(
        type(names) is list and len(names) <= MAX_NAMES,
        f"line {number}: to is not a name or a list",
    )
    _require(
        type(value) is str or len(names) >= shortest_list,
        f"line {number}: to list is shorter than stock",
    )
    checked = tuple(_name(name, number) for name in names)
    _require(
        len(set(checked)) == len(checked), f"line {number}: to repeats a recipient"
    )
    _require(author not in checked, f"line {number}: author is a recipient")
    return checked


class _Log:
    def __init__(self) -> None:
        self.roles: dict[str, str | None] = {}
        self.register_lines: list[int] = []
        self.messages: dict[int, tuple[str, str, tuple[str, ...] | None]] = {}
        self.message_lines: list[int] = []
        self.returns: set[tuple[str, int]] = set()
        self.actors: set[str] = set()
        self.configs: list[int] = []
        self.wakes: set[tuple[str, int]] = set()
        self.relayed: set[int] = set()
        self.tally = dict.fromkeys(
            (
                "inbox_calls",
                "inbox_rows",
                "history_calls",
                "history_rows",
                "refused_sends",
                "refused_other",
                "rate_limited_ops",
                "spawns",
                "extension_events",
            ),
            0,
        )

    def add(self, record: dict[str, Any], number: int) -> str | None:
        event = record.get("event")
        if type(event) is not str or not BASE.issubset(record):
            return "not_a_stock_board_event"
        if event in HISTORICAL:
            return "historical_post_read_schema"
        _require(
            type(record["ts"]) in (int, float), f"line {number}: ts is not a number"
        )
        method = HANDLERS.get(event)
        handler = None if method is None else getattr(self, method)
        if handler is None:
            if not MESSAGE_SHAPED.isdisjoint(record):
                return "unknown_message_event"
            self.tally["extension_events"] += 1
            return None
        handler(record, number)
        return None

    def _on_config(self, record: dict[str, Any], number: int) -> None:
        _require(record["agent"] is None, f"line {number}: config has an agent")
        _require(
            not self.configs,
            f"line {number}: second board start: appended or restarted job, not one export",
        )
        self.configs.append(number)

    def _on_plugins(self, record: dict[str, Any], number: int) -> None:
        _require(record["agent"] is None, f"line {number}: plugins has an agent")

    def _on_register(self, record: dict[str, Any], number: int) -> None:
        _require(
            record.keys() in (BASE, BASE | {"role"}),
            f"line {number}: register fields differ from stock",
        )
        name = _name(record["agent"], number)
        role = record.get("role")
        _require(
            role is None or (type(role) is str and role != ""),
            f"line {number}: register role is not text",
        )
        _require(
            name not in self.roles,
            f"line {number}: agent registered twice: appended or restarted job",
        )
        self.roles[name] = role
        self.register_lines.append(number)

    def _message(self, record: dict[str, Any], number: int, notify: bool) -> None:
        _fields(record, MESSAGE_FIELDS, number)
        author = record["agent"]
        if notify:
            _require(
                author == HARNESS, f"line {number}: notify author is not the harness"
            )
        else:
            self.actors.add(_name(author, number))
        seq = record["seq"]
        _require(
            type(seq) is int and seq >= 1,
            f"line {number}: seq is not a positive integer",
        )
        _require(
            seq not in self.messages,
            f"line {number}: duplicate message seq: conflicting or mixed jobs",
        )
        names = _recipients(record["to"], author, 1 if notify else 2, number)
        _require(
            not notify or names is not None, f"line {number}: notify has no recipient"
        )
        self.actors.update(names or ())
        self.messages[seq] = ("notify" if notify else "send", author, names)
        self.message_lines.append(number)

    def _on_send(self, record: dict[str, Any], number: int) -> None:
        self._message(record, number, notify=False)

    def _on_notify(self, record: dict[str, Any], number: int) -> None:
        self._message(record, number, notify=True)

    def _on_inbox(self, record: dict[str, Any], number: int) -> None:
        _fields(record, INBOX_FIELDS, number)
        agent = _name(record["agent"], number)
        wait = record["wait_seconds"]
        _require(
            type(wait) in (int, float) and 0 <= wait <= MAX_WAIT,
            f"line {number}: wait_seconds out of range",
        )
        returned = _count(record["returned"], MAX_LIMIT, number)
        seqs = record["seqs"]
        _require(
            type(seqs) is list and len(seqs) == returned,
            f"line {number}: seqs do not match returned",
        )
        previous = 0
        for seq in seqs:
            _require(
                type(seq) is int and seq > previous,
                f"line {number}: seqs are not increasing positive integers",
            )
            previous = seq
            self.returns.add((agent, seq))
        self.actors.add(agent)
        self.tally["inbox_calls"] += 1
        self.tally["inbox_rows"] += returned

    def _on_history(self, record: dict[str, Any], number: int) -> None:
        _fields(record, HISTORY_FIELDS, number)
        self.actors.add(_name(record["agent"], number))
        _require(
            type(record["after"]) is int, f"line {number}: after is not an integer"
        )
        self.tally["history_calls"] += 1
        self.tally["history_rows"] += _count(record["returned"], MAX_LIMIT, number)

    def _on_refused(self, record: dict[str, Any], number: int) -> None:
        self.actors.add(_name(record["agent"], number))
        action = record.get("action")
        _require(type(action) is str, f"line {number}: refused action is not text")
        if action in REFUSED_FIELDS:
            _fields(record, REFUSED_FIELDS[action], number)
        self.tally["refused_sends" if action == "send" else "refused_other"] += 1

    def _on_rate_limited(self, record: dict[str, Any], number: int) -> None:
        _fields(record, RATE_FIELDS, number)
        self.actors.add(_name(record["agent"], number))
        action = record["action"]
        _require(
            action is None or type(action) is str,
            f"line {number}: rate_limited action is not text",
        )
        count = _count(record["count"], MAX_LINES * MAX_LIMIT, number)
        _require(count >= 1, f"line {number}: rate_limited count is zero")
        self.tally["rate_limited_ops"] += count

    def _on_subagent(self, record: dict[str, Any], number: int) -> None:
        op = record.get("op")
        _require(type(op) is str, f"line {number}: subagent op is not text")
        if op == "spawn":
            self.tally["spawns"] += 1
        if op not in ("wake", "finish") or record.get("seq") is None:
            return
        seq = record["seq"]
        _require(
            type(seq) is int and seq >= 1,
            f"line {number}: subagent seq is not a positive integer",
        )
        name = _name(record.get("name"), number)
        if op == "wake":
            self.wakes.add((name, seq))
        else:
            _require(
                record["agent"] == name,
                f"line {number}: finish relay names another agent",
            )
            self.relayed.add(seq)

    def markers(self, changed: bool, truncated: bool) -> list[str]:
        seqs = sorted(self.messages)
        found = {
            "changed_during_read": changed,
            "truncated_tail": truncated,
            "no_board_start_first_line": self.configs != [1],
            "seq_gap": seqs != list(range(1, len(seqs) + 1)),
            "unregistered_agent": not self.actors.issubset(self.roles),
            "unknown_seq_returns": any(
                seq not in self.messages for _, seq in self.returns
            ),
        }
        return sorted(name for name, present in found.items() if present)

    def harness_relayed(self, seq: int, author: str) -> bool:
        return seq in self.relayed or self.roles.get(author) == SYSTEM_ROLE


def _audience(log: _Log, markers: list[str], complete: bool, closed: bool) -> str:
    if not any(
        event == "send" and names is None for event, _, names in log.messages.values()
    ):
        return "no_broadcasts"
    if not closed:
        return "withheld_roster_not_attested"
    if markers or not complete:
        return "withheld_log_not_complete"
    if log.tally["spawns"]:
        return "withheld_roster_grew_by_spawn"
    if log.register_lines and max(log.register_lines) > min(log.message_lines):
        return "withheld_registration_after_first_message"
    return "expanded"


def _row() -> dict[str, Any]:
    return {
        "sends": 0,
        "edges": 0,
        "confirmed": 0,
        "not_confirmed": 0,
        "not_confirmed_wake_prompt_logged": 0,
    }


def _channels(
    log: _Log, expanded: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    channels = {"direct_single": _row(), "direct_group": _row(), "broadcast": _row()}
    excluded = {"notifications": 0, "notification_edges": 0, "harness_relayed_sends": 0}
    roster = tuple(sorted(log.roles))
    for seq, (event, author, names) in sorted(log.messages.items()):
        if event == "notify":
            excluded["notifications"] += 1
            excluded["notification_edges"] += len(names or ())
            continue
        if log.harness_relayed(seq, author):
            excluded["harness_relayed_sends"] += 1
            continue
        row = channels[
            "broadcast"
            if names is None
            else "direct_single"
            if len(names) == 1
            else "direct_group"
        ]
        row["sends"] += 1
        if names is None and not expanded:
            continue
        for receiver in roster if names is None else names:
            if receiver == author:
                continue
            row["edges"] += 1
            if (receiver, seq) in log.returns:
                row["confirmed"] += 1
            else:
                row["not_confirmed"] += 1
                row["not_confirmed_wake_prompt_logged"] += int(
                    (receiver, seq) in log.wakes
                )
    if not expanded:
        channels["broadcast"].update(
            dict.fromkeys(("edges", "confirmed", "not_confirmed"), None)
        )
        channels["broadcast"]["not_confirmed_wake_prompt_logged"] = None
    return channels, excluded


def _returns(log: _Log) -> dict[str, int]:
    pairs = {
        "teammate_direct": 0,
        "teammate_broadcast": 0,
        "notification": 0,
        "harness_relayed": 0,
        "unknown_seq": 0,
    }
    for receiver, seq in log.returns:
        message = log.messages.get(seq)
        if message is None:
            pairs["unknown_seq"] += 1
            continue
        event, author, names = message
        _require(
            receiver != author,
            "an inbox returned the receiver's own message: not one job's log",
        )
        _require(
            names is None or receiver in names,
            "an inbox returned a non-recipient's message: not one job's log",
        )
        if event == "notify":
            pairs["notification"] += 1
        elif log.harness_relayed(seq, author):
            pairs["harness_relayed"] += 1
        else:
            pairs["teammate_broadcast" if names is None else "teammate_direct"] += 1
    return pairs


def _totals(channels: dict[str, dict[str, Any]], expanded: bool) -> dict[str, Any]:
    used = ("direct_single", "direct_group") + (("broadcast",) if expanded else ())
    totals: dict[str, Any] = {
        key: sum(channels[name][key] for name in used)
        for key in ("edges", "confirmed", "not_confirmed")
    }
    totals["includes_broadcast"] = expanded
    return totals


def _report(
    log: _Log, lines: int, markers: list[str], complete: bool, closed: bool
) -> dict[str, Any]:
    audience = _audience(log, markers, complete, closed)
    expanded = audience == "expanded"
    channels, excluded = _channels(log, expanded)
    pairs = _returns(log)
    excluded["notification_inbox_returns"] = pairs["notification"]
    excluded["harness_relayed_inbox_returns"] = pairs["harness_relayed"]
    tally = log.tally
    status = "incomplete" if markers else "complete" if complete else "unattested"
    teammate = sum(row["sends"] for row in channels.values())
    return {
        "schema": SCHEMA,
        "status": status,
        "markers": markers,
        "attested": {"complete_log": complete, "closed_roster": closed},
        "source": SOURCE,
        "input": {
            "lines": lines,
            "extension_events_ignored": tally["extension_events"],
        },
        "roster": {"registered": len(log.roles), "broadcast_audience": audience},
        "sends": {
            "teammate": teammate,
            "direct": channels["direct_single"]["sends"]
            + channels["direct_group"]["sends"],
            "broadcast": channels["broadcast"]["sends"],
            "refused_not_sent": tally["refused_sends"],
            "rate_limited_board_ops_any_action": tally["rate_limited_ops"],
        },
        "recipient_edges": _totals(channels, expanded),
        "channels": channels,
        "excluded": excluded,
        "inbox": {
            "calls": tally["inbox_calls"],
            "rows_returned": tally["inbox_rows"],
            "distinct_receiver_seq_pairs": len(log.returns),
            "teammate_direct_pairs": pairs["teammate_direct"],
            "teammate_broadcast_pairs": pairs["teammate_broadcast"],
            "unknown_seq_pairs": pairs["unknown_seq"],
        },
        "history": {
            "calls": tally["history_calls"],
            "rows_returned_unattributable": tally["history_rows"],
        },
        "meaning": MEANING,
    }


def analyze(path: str, complete: bool, closed: bool) -> dict[str, Any]:
    loaded = _read(path)
    if loaded is None:
        return {
            "schema": SCHEMA,
            "status": "missing",
            "source": SOURCE,
            "meaning": "no file at this path; not the same as a log with no messages",
        }
    data, changed = loaded
    lines, truncated = _split(data)
    log = _Log()
    for number, raw in enumerate(lines, start=1):
        reason = log.add(_decode(raw, number), number)
        if reason is not None:
            return {
                "schema": SCHEMA,
                "status": "unsupported",
                "reason": reason,
                "line": number,
                "source": SOURCE,
            }
    return _report(log, len(lines), log.markers(changed, truncated), complete, closed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Count teammate message recipient edges that a stock board log shows returned by an exact "
        "inbox seq. Output holds counts only.",
        epilog=EPILOG,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--complete-log",
        action="store_true",
        help="owner attests: this file is the whole board log of one finished job; not rotated, cut or appended",
    )
    parser.add_argument(
        "--closed-roster",
        action="store_true",
        help="owner attests: no agent joined after the first message; needed to count broadcast edges",
    )
    parser.add_argument(
        "log", metavar="OWNER_BOARD_LOG", help=f"a local {FILENAME} from your own job"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = analyze(
            args.log, complete=args.complete_log, closed=args.closed_roster
        )
    except (ValueError, TypeError, OverflowError, RecursionError, MemoryError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    sys.stdout.buffer.write((text + "\n").encode())
    sys.stdout.buffer.flush()
    return EXIT[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
