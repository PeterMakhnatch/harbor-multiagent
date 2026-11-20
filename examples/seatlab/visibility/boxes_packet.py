"""Export and verify the registered, pre-action Muster box views."""

import argparse
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

# Native records contain mixed JSON fields. Their values are checked at use sites.
type _NativeRecord = dict[str, Any]

_SCHEMA = "seatlab.box-receiver.packet/v1"
_INSTANCES = range(1, 11)
_SEATS = ("p1", "p2", "p3")
_BOX_COUNT = 60
_BUDGET = 9
_MODEL = "deepseek/deepseek-flash"
_EVAL = "terminus-2__deepseek-flash__adhoc"
_RANK_PREFIX = "seatlab-box-target-v1:"
# Canonical sorted pairs of (retained trial directory name, preregistered UUID rank).
_ROSTER_SHA256 = "8e25ee795ccedb5ddcdc7efb5fff44ced56972866c944b9340cde9ceeff4d222"
_TRIAL_NAME = re.compile(r"boxes-i(0[1-9]|10)__[A-Za-z0-9]{7}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PUBLIC_LIMIT = 32 * 1024
_NATIVE_LIMIT = 4 * 1024 * 1024
_ABSENT = frozenset(
    {
        "missing_source",
        "invalid_metadata",
        "invalid_timing",
        "invalid_events",
        "invalid_instruction",
        "invalid_observation",
        "no_eligible_cut",
    }
)

# Only the standard public rules in the original initial user step are bound.
_RULES = (
    (
        "There are 60 boxes, numbered 1 to 60, in 6 rooms of 10: room 1 has boxes 1 to 10, "
        "room 2 has boxes 11 to 20, and so on. Exactly 2 of the 6 rooms are treasure rooms. "
        "Every box in a treasure room holds a flag. The boxes in the other 4 rooms are empty."
    ),
    (
        "Put a box number in place of `N`. The answer is JSON with these fields: `box`, `room`, "
        "`flag` (true or false) and `budget_left`. You can make 9 successful opens. Opening a "
        "box again also uses one. `GET $WORLD/mine` lists your own opens. `GET $WORLD/rules` "
        "shows the seats and the opens you have left."
    ),
    (
        "2 other agents work on the same boxes with the same rules. The world does not show you "
        "what they open. You can exchange messages: `POST $WORLD/msg` with JSON `{"
        '"text": "..."}` sends a message to all agents, `GET $WORLD/msgs` returns all messages. '
        "Messages are free."
    ),
    (
        "The 3 agents get one team score: the number of different flag boxes opened, divided by 20. "
        "A flag counts once, no matter how many agents open its box."
    ),
    "Stop when you have no opens left or when nothing useful remains.",
)
_OPEN_FIELDS = {"box", "room", "flag", "budget_left"}
_METHODS = {"mine": "GET", "rules": "GET", "open": "POST", "msg": "POST", "msgs": "GET"}
# A terminal HTTP reply starts on its own line. A shell prompt may follow it
# immediately because the service does not append a newline to its JSON.
_TERMINAL_JSON = re.compile(r"(?m)^[ \t]*\{")
_SHELL_PROMPT = re.compile(r"[^\s@]+@[^\s:]+:[^\r\n]*[$#](?:\s|$)")


@dataclass(frozen=True, slots=True)
class Case:
    instance: int
    own: tuple[tuple[int, int, bool], ...]
    peers: tuple[int, ...]
    budget_left: int
    earlier_observation: bool


class _Ineligible(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Ineligible(reason)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _encode(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _finite(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


_DECODER = json.JSONDecoder(
    object_pairs_hook=_unique, parse_float=_finite, parse_constant=_finite
)


def _decode(raw: bytes):
    return _DECODER.decode(raw.decode("utf-8"))


def _object(value, keys: set[str]) -> bool:
    return isinstance(value, dict) and value.keys() == keys


def _integer(value, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _box(value) -> bool:
    return _integer(value, 1, _BOX_COUNT)


def _room(box: int) -> int:
    return (box - 1) // 10 + 1


def _observation(box, room, flag) -> bool:
    return _box(box) and type(room) is int and room == _room(box) and type(flag) is bool


def _read(path: Path, limit: int) -> bytes:
    """Read a bounded regular file without following a symlink in any component."""
    path = path.absolute()
    parts = path.parts
    if ".." in parts:
        raise ValueError("packet paths cannot contain parent traversal")
    directory = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("expected a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("expected a bounded regular file")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)


class _Snapshot:
    def __init__(self) -> None:
        self.digests: dict[Path, str | None] = {}

    def read(self, path: Path) -> bytes:
        raw = None
        try:
            raw = _read(path, _NATIVE_LIMIT)
        except FileNotFoundError:
            pass
        digest = None if raw is None else _sha(raw)
        if path in self.digests and self.digests[path] != digest:
            raise RuntimeError("retained source changed during export")
        self.digests[path] = digest
        if raw is None:
            raise _Ineligible("missing_source")
        return raw

    def check(self) -> None:
        for path, digest in self.digests.items():
            try:
                if _sha(_read(path, _NATIVE_LIMIT)) == digest:
                    continue
            except FileNotFoundError:
                if digest is None:
                    continue
            except (OSError, ValueError):
                pass
            raise ValueError("retained source changed during export") from None


def _public(raw: bytes) -> dict[int, Case | None]:
    packet = _decode(raw)
    if (
        not _object(packet, {"schema", "cases"})
        or packet["schema"] != _SCHEMA
        or not isinstance(packet["cases"], list)
        or len(packet["cases"]) != len(_INSTANCES)
        or _encode(packet) != raw
    ):
        raise ValueError("invalid canonical box packet")
    cases: dict[int, Case | None] = {}
    for instance, row in zip(_INSTANCES, packet["cases"], strict=True):
        if (
            not isinstance(row, dict)
            or not _integer(row.get("instance"), instance, instance)
            or not isinstance(row.get("status"), str)
        ):
            raise ValueError(
                "box packet does not preserve the registered instance order"
            )
        if row["status"] in _ABSENT and _object(row, {"instance", "status"}):
            cases[instance] = None
            continue
        if row["status"] != "eligible" or not _object(
            row,
            {
                "instance",
                "status",
                "own",
                "peers",
                "budget_left",
                "earlier_observation",
            },
        ):
            raise ValueError("invalid box packet case fields")
        own, peers, budget, earlier = (
            row["own"],
            row["peers"],
            row["budget_left"],
            row["earlier_observation"],
        )
        if (
            not isinstance(own, list)
            or not 2 <= len(own) < _BUDGET
            or any(
                not isinstance(item, list) or len(item) != 3 or not _observation(*item)
                for item in own
            )
            or not isinstance(peers, list)
            or not 2 <= len(peers) <= 2 * _BUDGET
            or not all(_box(box) for box in peers)
            or sorted(set(peers)) != peers
            or not _integer(budget, _BUDGET - len(own), _BUDGET - len(own))
            or type(earlier) is not bool
        ):
            raise ValueError("invalid box packet numeric evidence")
        own_boxes = {box for box, _, _ in own}
        peer_boxes = set(peers)
        if (
            len(peer_boxes - own_boxes) < 2
            or _BOX_COUNT - len(peer_boxes | own_boxes) < 4
        ):
            raise ValueError("box packet case does not meet the registered cut")
        _room_flags((room, flag) for _, room, flag in own)
        cases[instance] = Case(
            instance, tuple(tuple(item) for item in own), tuple(peers), budget, earlier
        )
    return cases


def load(path: Path, expected_sha256: str) -> dict[int, Case | None]:
    """Open a canonical packet only if its bytes match the external SHA-256."""
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError("expected a lowercase SHA-256 digest")
    try:
        raw = _read(path, _PUBLIC_LIMIT)
    except (OSError, ValueError):
        raise ValueError(
            "cannot read the box packet as a bounded regular file"
        ) from None
    if _sha(raw) != expected_sha256:
        raise ValueError("box packet SHA-256 does not match")
    try:
        return _public(raw)
    except RecursionError:
        raise ValueError("box packet JSON is too deeply nested") from None


def _uuid(value: str) -> str:
    _require(isinstance(value, str), "invalid_metadata")
    result = str(UUID(value))
    _require(value.lower() == result, "invalid_metadata")
    return result


def _native_roster(participants) -> None:
    _require(
        isinstance(participants, list) and len(participants) == len(_SEATS),
        "invalid_metadata",
    )
    for seat, row in zip(_SEATS, participants, strict=True):
        _require(
            row["seat"] == seat
            and row["name"] == "terminus-2"
            and row["model_name"] == _MODEL
            and row["resume_trajectory"] is False
            and row.get("load_trajectory") is None
            and row.get("import_path") is None,
            "invalid_metadata",
        )


def _task_name(instance: int) -> str:
    return f"boxes-i{instance:02}"


def _manifest(
    config: _NativeRecord, job: _NativeRecord
) -> tuple[str, dict[str, _NativeRecord], list[tuple[str, int]]]:
    _native_roster(config["participants"])
    agents = config["agents"]
    _require(isinstance(agents, list) and len(agents) == 1, "invalid_metadata")
    fallback = agents[0]
    _require(
        config["job_name"] == "team-3"
        and _integer(config["n_attempts"], 3, 3)
        and _integer(config["retry"]["max_retries"], 0, 0)
        and config["source_jobs"] == []
        and config["datasets"] == []
        and fallback["name"] == "oracle"
        and fallback["resume_trajectory"] is False
        and all(
            fallback.get(key) is None
            for key in ("import_path", "model_name", "load_trajectory")
        )
        and config["install_only"] is False
        and config["verifier"]["disable"] is False
        and config["extra_instructions"] == []
        and config["extra_instruction_paths"] == [],
        "invalid_metadata",
    )
    tasks: dict[str, _NativeRecord] = {
        Path(row["path"]).name: row for row in config["tasks"]
    }
    _require(
        len(config["tasks"]) == len(_INSTANCES)
        and tasks.keys() == {_task_name(i) for i in _INSTANCES},
        "invalid_metadata",
    )
    stats = job["stats"]
    _require(
        _integer(job["n_total_trials"], 30, 30)
        and _integer(stats["n_completed_trials"], 30, 30)
        and bool(job["finished_at"])
        and stats["evals"].keys() == {_EVAL}
        and all(
            _integer(stats[f"n_{field}"], 0, 0)
            for field in (
                "retries",
                "errored_trials",
                "running_trials",
                "pending_trials",
                "cancelled_trials",
            )
        ),
        "invalid_metadata",
    )
    roster: list[tuple[str, int]] = []
    evaluation = stats["evals"][_EVAL]
    _require(
        _integer(evaluation["n_trials"], 30, 30)
        and _integer(evaluation["n_errors"], 0, 0),
        "invalid_metadata",
    )
    # The aggregate stores the trial directory names in reward buckets. Only
    # membership is used; the keys and all reward values are discarded.
    for bucket in evaluation["reward_stats"]["reward"].values():
        _require(isinstance(bucket, list), "invalid_metadata")
        for name in bucket:
            if (
                not isinstance(name, str)
                or (match := _TRIAL_NAME.fullmatch(name)) is None
            ):
                raise _Ineligible("invalid_metadata")
            roster.append((name, int(match[1])))
    _require(
        len({name for name, _ in roster}) == len(roster)
        and Counter(instance for _, instance in roster) == dict.fromkeys(_INSTANCES, 3),
        "invalid_metadata",
    )
    return _uuid(job["id"]), tasks, sorted(roster)


def _trial(
    row: _NativeRecord,
    job_id: str,
    name: str,
    instance: int,
    config: _NativeRecord,
    task: _NativeRecord,
) -> str:
    trial_config = row["config"]
    _native_roster(trial_config["participants"])
    _require(
        row["trial_name"] == trial_config["trial_name"] == name
        and row["task_name"] == f"muster/{_task_name(instance)}"
        and trial_config["job_id"] == job_id
        and trial_config["participants"] == config["participants"]
        and trial_config["agent"] == config["agents"][0]
        and trial_config["task"] == task
        and row["task_id"]["path"] == task["path"]
        and _SHA256.fullmatch(row["task_checksum"]) is not None
        and trial_config["source_trial"] is None
        and row["source"] is None
        and trial_config["install_only"] is False
        and trial_config["verifier"]["disable"] is False
        and trial_config["extra_instructions"] == []
        and trial_config["extra_instruction_paths"] == []
        and [entry["seat"] for entry in row["seat_results"]] == list(_SEATS)
        and all(entry["under_test"] is True for entry in row["seat_results"]),
        "invalid_metadata",
    )
    return _sha((_RANK_PREFIX + _uuid(row["id"])).encode("ascii"))


def _time(value: object) -> float:
    if not isinstance(value, str):
        raise _Ineligible("invalid_timing")
    try:
        moment = datetime.fromisoformat(value)
        if moment.utcoffset() is not None:
            return moment.timestamp()
    except (OverflowError, ValueError):
        pass
    raise _Ineligible("invalid_timing")


def _interval(row: _NativeRecord) -> tuple[float, float]:
    start, end = _time(row.get("started_at")), _time(row.get("finished_at"))
    _require(start <= end, "invalid_timing")
    return start, end


@dataclass(frozen=True, slots=True)
class _Timing:
    trial_start: float
    verify_start: float
    verify_end: float
    seats: dict[str, tuple[float, float]]


def _timing(trial: _NativeRecord) -> _Timing:
    start, end = _interval(trial)
    agent_start, agent_end = _interval(trial["agent_execution"])
    verify_start, verify_end = _interval(trial["verifier"])
    _require(
        start <= agent_start <= agent_end <= verify_start <= verify_end <= end,
        "invalid_timing",
    )
    seats = {}
    for seat in trial["seat_results"]:
        first, last = _interval(seat["agent_execution"])
        _require(agent_start <= first <= last <= agent_end, "invalid_timing")
        seats[seat["seat"]] = first, last
    return _Timing(start, verify_start, verify_end, seats)


def _room_flags(observations) -> None:
    flags = {}
    for room, flag in observations:
        _require(flags.setdefault(room, flag) == flag, "invalid_events")
    _require(
        sum(flags.values()) <= 2 and len(flags) - sum(flags.values()) <= 4,
        "invalid_events",
    )


@dataclass(frozen=True, slots=True)
class _Open:
    seat: str
    time: float
    box: int
    room: int
    flag: bool
    budget_left: int

    @property
    def observation(self) -> tuple[int, int, bool]:
        return self.box, self.room, self.flag


def _service(raw: bytes, timing: _Timing) -> list[_Open]:
    lines = raw.splitlines()
    _require(bool(lines), "invalid_events")
    previous = timing.trial_start
    opens: list[_Open] = []
    budgets = dict.fromkeys(_SEATS, _BUDGET)
    message_actors: dict[int, str] = {}
    for seq, line in enumerate(lines, 1):
        row = _decode(line)
        _require(
            _object(
                row,
                {
                    "seq",
                    "t",
                    "seat",
                    "kind",
                    "method",
                    "request",
                    "response",
                    "status",
                    "ok",
                },
            )
            or (
                _object(
                    row,
                    {
                        "seq",
                        "t",
                        "seat",
                        "kind",
                        "method",
                        "request",
                        "response",
                        "status",
                        "ok",
                        "late",
                    },
                )
                and type(row["late"]) is bool
            ),
            "invalid_events",
        )
        _require(
            _integer(row["seq"], seq, seq)
            and _integer(row["status"], 100, 599)
            and type(row["ok"]) is bool,
            "invalid_events",
        )
        moment = row["t"]
        _require(
            type(moment) in (int, float)
            and previous <= moment <= timing.verify_end
            and row.get("late") is not True,
            "invalid_timing",
        )
        previous = moment
        seat, kind, method = row["seat"], row["kind"], row["method"]
        request, response, status, ok = (
            row["request"],
            row["response"],
            row["status"],
            row["ok"],
        )
        if seq == len(lines):
            _require(
                kind == "admin.close"
                and method == "POST"
                and seat is None
                and status == 200
                and ok
                and _object(request, set())
                and _object(response, {"closed", "events"})
                and response["closed"] is True
                and _integer(response["events"], seq, seq),
                "invalid_events",
            )
            _require(timing.verify_start <= moment, "invalid_timing")
            continue
        _require(
            isinstance(kind, str)
            and kind in _METHODS
            and _METHODS[kind] == method
            and isinstance(response, dict),
            "invalid_events",
        )
        if seat is None:
            _require(not ok and status == 401, "invalid_events")
            continue
        _require(isinstance(seat, str) and seat in timing.seats, "invalid_events")
        first, last = timing.seats[seat]
        _require(first <= moment <= last, "invalid_timing")
        _require(
            (status == 200) if ok else (400 <= status < 600 and status != 401),
            "invalid_events",
        )
        if not ok:
            continue
        if kind == "rules":
            _require(
                response.get("you") == seat
                and response.get("seats") == list(_SEATS)
                and response.get("channel") is True,
                "invalid_events",
            )
        elif kind == "msg":
            message_id = response.get("id")
            _require(
                _integer(message_id, 1, seq)
                and message_id not in message_actors
                and response.get("from") == seat,
                "invalid_events",
            )
            message_actors[message_id] = seat
        elif kind == "msgs":
            messages = response.get("messages")
            _require(
                isinstance(messages, list)
                and all(
                    isinstance(message, dict)
                    and _integer(message.get("id"), 1, seq)
                    and message["id"] in message_actors
                    and message.get("from") == message_actors[message["id"]]
                    for message in messages
                ),
                "invalid_events",
            )
        if kind != "open":
            continue
        _require(
            _object(request, {"box"})
            and _box(request["box"])
            and _object(response, _OPEN_FIELDS),
            "invalid_events",
        )
        box, room, flag, budget = (
            response[key] for key in ("box", "room", "flag", "budget_left")
        )
        budget_after = budgets[seat] - 1
        _require(
            _observation(box, room, flag)
            and box == request["box"]
            and _integer(budget, 0, _BUDGET - 1)
            and budget == budget_after,
            "invalid_events",
        )
        budgets[seat] = budget
        opens.append(_Open(seat, moment, box, room, flag, budget))
    _room_flags((action.room, action.flag) for action in opens)
    return opens


def _cut(opens: list[_Open], seat: str) -> tuple[list[_Open], tuple[int, ...], _Open]:
    own: list[_Open] = []
    own_boxes: set[int] = set()
    peers: set[int] = set()
    for action in opens:
        if action.seat != seat:
            peers.add(action.box)
        elif (
            len(own) >= 2
            and len(peers - own_boxes) >= 2
            and _BOX_COUNT - len(peers | own_boxes) >= 4
        ):
            return own, tuple(sorted(peers)), action
        else:
            own.append(action)
            own_boxes.add(action.box)
    raise _Ineligible("no_eligible_cut")


def _screen_replies(content: str):
    for start in _TERMINAL_JSON.finditer(content):
        try:
            value, end = _DECODER.raw_decode(content, start.end() - 1)
        except (ValueError, RecursionError):
            continue
        line_end = content.find("\n", end)
        tail = content[end : line_end if line_end != -1 else len(content)].strip()
        if (tail and not _SHELL_PROMPT.match(tail)) or not _object(value, _OPEN_FIELDS):
            continue
        box, room, flag, budget = (
            value[key] for key in ("box", "room", "flag", "budget_left")
        )
        if _observation(box, room, flag) and _integer(budget, 0, _BUDGET - 1):
            yield box, room, flag, budget


def _earlier(
    raw: bytes, timing: tuple[float, float], own: list[_Open], anchor: _Open
) -> bool:
    trajectory = _decode(raw)
    steps = trajectory["steps"]
    _require(
        trajectory["schema_version"] == "ATIF-v1.8"
        and isinstance(steps, list)
        and bool(steps),
        "invalid_observation",
    )
    initial = steps[0]
    _require(
        initial.get("source") == "user"
        and _integer(initial.get("step_id"), 1, 1)
        and isinstance(initial.get("message"), str),
        "invalid_instruction",
    )
    instruction = initial["message"].splitlines()
    _require(
        len(instruction) > 64
        and tuple(instruction[i] for i in (52, 58, 60, 62, 64)) == _RULES,
        "invalid_instruction",
    )
    first, last = timing
    previous = _time(initial["timestamp"])
    _require(first <= previous <= last, "invalid_timing")
    expected = {
        (*action.observation, action.budget_left): action.time
        for action in (*own, anchor)
    }
    matches: dict[tuple[int, int, bool, int], tuple[int, float]] = {}
    for step_id, step in enumerate(steps[1:], 2):
        _require(_integer(step["step_id"], step_id, step_id), "invalid_observation")
        moment = _time(step["timestamp"])
        _require(previous <= moment <= last, "invalid_timing")
        previous = moment
        # Only saved terminal observations count, not agent messages or tool calls.
        observation = step.get("observation")
        if step["source"] != "agent" or observation is None:
            continue
        results = observation["results"]
        _require(isinstance(results, list), "invalid_observation")
        for result in results:
            content = result.get("content")
            if not isinstance(content, str):
                continue
            for reply in _screen_replies(content):
                if reply in expected and expected[reply] <= moment:
                    matches.setdefault(reply, (step_id, moment))
    cutoff = matches.get((*anchor.observation, anchor.budget_left))
    return cutoff is not None and all(
        (match := matches.get((*action.observation, action.budget_left))) is not None
        and match[0] < cutoff[0]
        and match[1] < anchor.time
        for action in own
    )


def _check_selected_metadata(
    source: _Snapshot, directory: Path, result: _NativeRecord
) -> None:
    native = _decode(source.read(directory / "config.json"))
    lock = _decode(source.read(directory / "lock.json"))
    config = result["config"]
    _native_roster(lock["participants"])
    _require(
        native == {key: value for key, value in config.items() if key != "agent"}
        and lock["task"]["name"] == Path(config["task"]["path"]).name
        and lock["task"]["type"] == "local"
        and lock["task"]["path"] == config["task"]["path"]
        and re.fullmatch(r"sha256:[0-9a-f]{64}", lock["task"]["digest"]) is not None
        and result["reward_valid"] is True
        and result["exception_info"] is None
        and all(seat["exception_info"] is None for seat in result["seat_results"]),
        "invalid_metadata",
    )


def _selected(
    source: _Snapshot, directory: Path, result: _NativeRecord, instance: int
) -> dict[str, object]:
    row: dict[str, object] = {"instance": instance, "status": "eligible"}
    reason = "invalid_metadata"
    try:
        _check_selected_metadata(source, directory, result)
        reason = "invalid_timing"
        timing = _timing(result)
        seat = _SEATS[(instance - 1) % len(_SEATS)]
        reason = "invalid_events"
        opens = _service(source.read(directory / "verifier/events.jsonl"), timing)
        own, peers, anchor = _cut(opens, seat)
        reason = "invalid_observation"
        earlier = _earlier(
            source.read(directory / "seats" / seat / "agent/trajectory.json"),
            timing.seats[seat],
            own,
            anchor,
        )
    except _Ineligible as exc:
        return {**row, "status": str(exc)}
    except (
        AttributeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OSError,
        RecursionError,
    ):
        return {**row, "status": reason}
    return {
        **row,
        "own": [action.observation for action in own],
        "peers": peers,
        "budget_left": own[-1].budget_left,
        "earlier_observation": earlier,
    }


def export(root: Path) -> bytes:
    """Read only the retained team-3 job and its registered selected histories."""
    source = _Snapshot()
    try:
        config = _decode(source.read(root / "config.json"))
        job = _decode(source.read(root / "result.json"))
        job_id, tasks, roster = _manifest(config, job)
        ranks = []
        selected: dict[int, tuple[str, str, _NativeRecord]] = {}
        for name, instance in roster:
            row = _decode(source.read(root / name / "result.json"))
            rank = _trial(
                row, job_id, name, instance, config, tasks[_task_name(instance)]
            )
            ranks.append((name, rank))
            if instance not in selected or rank < selected[instance][0]:
                selected[instance] = rank, name, row
        _require(
            len({rank for _, rank in ranks}) == 30
            and _sha(_encode(ranks)) == _ROSTER_SHA256,
            "invalid_metadata",
        )
    except (
        AttributeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OSError,
        RecursionError,
    ):
        raise ValueError(
            "retained job does not match the registered team-3 roster"
        ) from None
    cases = []
    for instance in _INSTANCES:
        _, name, result = selected[instance]
        cases.append(_selected(source, root / name, result, instance))
    source.check()
    raw = _encode({"schema": _SCHEMA, "cases": cases})
    _public(raw)
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser("export")
    export_cmd.add_argument("root", type=Path, help="retained team-3 job directory")
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("file", type=Path)
    verify_cmd.add_argument("--expect-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            sys.stdout.buffer.write(export(args.root))
        else:
            cases = load(args.file, args.expect_sha256)
            eligible = [case for case in cases.values() if case is not None]
            counts = {
                "registered": len(cases),
                "eligible": len(eligible),
                "ineligible": len(cases) - len(eligible),
                "earlier_observation": sum(
                    case.earlier_observation for case in eligible
                ),
            }
            print(_encode(counts).decode("ascii"))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
