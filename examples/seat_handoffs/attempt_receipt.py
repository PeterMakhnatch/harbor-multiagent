import hashlib
import os
import stat
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from ..seatlab.workbench.account_contract import DIGEST, GRADES, _attempt_row
from ..seatlab.workbench.seat_stream import MAX_SEATS, SEAT, UUID, Record, _require
from .core import (
    COMMITS,
    IDENTIFIER,
    MAX_EDGES,
    MAX_LOG_BYTES,
    RECEIPTS,
    TIMINGS,
    Replay,
    frames_from_bytes,
    replay_frames,
)

SCHEMA = "seatlab.attempt-handoffs/v1"
SOURCES = ("sealed", "world_prefix", "unavailable", "unusable")
OBSERVED = ("sealed", "world_prefix")
MESSAGE_DOMAIN = b"harbor.handoff.message/v1\x00"
SEALED = ("verifier", "handoffs.jsonl")
WORLD = ("artifacts", "logs", "world", "handoffs.jsonl")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
RECEIPT_KEYS = frozenset(
    {
        "schema",
        "job",
        "slot",
        "trial",
        "roster",
        "grade_state",
        "reward",
        "source",
        "world",
        "edges",
    }
)
EDGE_KEYS = frozenset(
    {"message", "sender", "receiver", "commit", "returned", "acknowledged"}
)
BOUNDARY_KEYS = frozenset({"receipt", "timing"})
SOURCE_COMMITS = {
    "sealed": ("recorded", "not_recorded"),
    "world_prefix": ("recorded", "unknown"),
}
SOURCE_RETURNS = {
    "sealed": ("confirmed", "absent"),
    "world_prefix": ("confirmed", "unknown"),
}
SOURCE_ACKS = {"sealed": RECEIPTS, "world_prefix": ("confirmed", "unknown")}
CONFIRMED_TIMINGS = {
    "recorded": ("before", "after"),
    "not_recorded": ("no_commit",),
    "unknown": ("unknown",),
}


class _Unusable(Exception):
    pass


def edge_digest(message: str) -> str:
    _require(
        type(message) is str and message.isascii(), "handoff message is not ASCII text"
    )
    return hashlib.sha256(MESSAGE_DOMAIN + message.encode("ascii")).hexdigest()


def _read_tail(fd: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_LOG_BYTES:
        chunk = os.read(fd, MAX_LOG_BYTES + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > MAX_LOG_BYTES:
        raise _Unusable("journal exceeds the size bound")
    return b"".join(chunks)


def _read_below(root: int, parts: tuple[str, ...]) -> bytes | None:
    assert 1 <= len(parts) <= len(WORLD)
    opened: list[int] = []
    try:
        current = root
        for part in parts[:-1]:
            current = os.open(part, DIR_FLAGS, dir_fd=current)
            opened.append(current)
        fd = os.open(parts[-1], FILE_FLAGS, dir_fd=current)
        opened.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOG_BYTES:
            raise _Unusable("journal is not a bounded regular file")
        return _read_tail(fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise _Unusable("journal is inaccessible") from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _sealed(sealed: bytes, world: bytes | None) -> Replay:
    frames, torn = frames_from_bytes(sealed)
    replay = replay_frames(frames)
    if torn or not replay.closed:
        raise _Unusable("sealed journal is torn or not closed")
    if world is None:
        return replay
    prefix, prefix_torn = frames_from_bytes(world)
    if (
        prefix_torn
        or not prefix
        or len(prefix) >= len(frames)
        or prefix != frames[: len(prefix)]
    ):
        raise _Unusable("world journal is not a frozen prefix of the sealed journal")
    return replay


def _world_prefix(world: bytes) -> Replay:
    replay = replay_frames(frames_from_bytes(world)[0])
    if replay.closed:
        raise _Unusable("world journal closed without a verifier seal")
    return replay


def _observe(trial_dir: Path) -> tuple[str, Replay | None]:
    try:
        root = os.open(trial_dir, DIR_FLAGS)
    except OSError:
        raise _Unusable("trial directory is inaccessible") from None
    try:
        sealed = _read_below(root, SEALED)
        world = _read_below(root, WORLD)
    finally:
        os.close(root)
    if sealed is not None:
        return "sealed", _sealed(sealed, world)
    if world is not None:
        return "world_prefix", _world_prefix(world)
    return "unavailable", None


def _edges(replay: Replay, roster: list[str], attempt: Record) -> list[Record]:
    names = set(roster)
    if any(
        edge.sender not in names or edge.receiver not in names for edge in replay.edges
    ):
        raise _Unusable("journal edge names a seat outside the attempt roster")
    if (
        attempt["grade_state"] == "valid"
        and replay.reward is not None
        and replay.reward != attempt["reward"]
    ):
        raise _Unusable("sealed closing reward disagrees with the valid native reward")
    rows: list[Record] = []
    for edge in replay.edges:
        returned = replay.finding(edge, "returned")
        acknowledged = replay.finding(edge, "acknowledged")
        assert returned.commit == acknowledged.commit
        rows.append(
            {
                "message": edge_digest(edge.message),
                "sender": edge.sender,
                "receiver": edge.receiver,
                "commit": returned.commit,
                "returned": {"receipt": returned.receipt, "timing": returned.timing},
                "acknowledged": {
                    "receipt": acknowledged.receipt,
                    "timing": acknowledged.timing,
                },
            }
        )
    return sorted(
        rows, key=lambda row: (row["message"], row["sender"], row["receiver"])
    )


def project(trial_dir: Path, *, job: str, attempt: Record) -> Record:
    _require(
        type(job) is str and UUID.fullmatch(job) is not None,
        "job id is not a canonical lowercase UUID",
    )
    row = _attempt_row(attempt)
    _require(isinstance(trial_dir, Path), "trial directory is not a path")
    name = trial_dir.name
    _require(name not in ("", ".", ".."), "trial directory has no slot name")
    _require(
        hashlib.sha256(os.fsencode(name)).hexdigest() == row["slot"],
        "trial directory is not the attempt slot",
    )
    roster = sorted(seat["name"] for seat in row["seats"])
    receipt: Record = {
        "schema": SCHEMA,
        "job": job,
        "slot": row["slot"],
        "trial": row["trial"],
        "roster": roster,
        "grade_state": row["grade_state"],
        "reward": row["reward"],
        "source": "unusable",
        "world": None,
        "edges": [],
    }
    try:
        source, replay = _observe(trial_dir)
        if replay is not None:
            receipt.update(
                source=source, world=replay.run, edges=_edges(replay, roster, row)
            )
        else:
            receipt.update(source=source)
    except (_Unusable, ValueError):
        receipt.update(source="unusable", world=None, edges=[])
    return validate(receipt)


def _fields(value: object, keys: frozenset[str], reason: str) -> dict[str, Any]:
    _require(type(value) is dict and value.keys() == keys, reason)
    return cast(dict[str, Any], value)


def _choice(value: object, choices: tuple[str, ...], reason: str) -> str:
    _require(type(value) is str and value in choices, reason)
    assert isinstance(value, str)
    return value


def _boundary(value: object, receipts: tuple[str, ...], commit: str) -> Record:
    item = _fields(value, BOUNDARY_KEYS, "unsupported handoff boundary fields")
    receipt = _choice(
        item["receipt"], receipts, "handoff receipt contradicts the source"
    )
    timing = _choice(item["timing"], TIMINGS, "unsupported handoff timing")
    allowed = CONFIRMED_TIMINGS[commit] if receipt == "confirmed" else ("unknown",)
    _require(timing in allowed, "handoff timing contradicts the receipt and commit")
    return {"receipt": receipt, "timing": timing}


def _edge(value: object, source: str, roster: set[str]) -> Record:
    item = _fields(value, EDGE_KEYS, "unsupported handoff edge fields")
    message = item["message"]
    _require(
        type(message) is str and DIGEST.fullmatch(message) is not None,
        "handoff message is not a digest",
    )
    names = (item["sender"], item["receiver"])
    _require(
        all(
            type(n) is str and n in roster and IDENTIFIER.fullmatch(n) is not None
            for n in names
        ),
        "handoff edge names a seat outside the roster",
    )
    _require(names[0] != names[1], "handoff edge sender and receiver must differ")
    commit = _choice(
        item["commit"], SOURCE_COMMITS[source], "handoff commit contradicts the source"
    )
    assert commit in COMMITS
    returned = _boundary(item["returned"], SOURCE_RETURNS[source], commit)
    acknowledged = _boundary(item["acknowledged"], SOURCE_ACKS[source], commit)
    if acknowledged["receipt"] == "confirmed":
        _require(
            returned["receipt"] == "confirmed",
            "acknowledgement without a confirmed return",
        )
        _require(
            acknowledged["timing"] != "before" or returned["timing"] == "before",
            "acknowledgement precedes its return",
        )
        _require(
            returned["timing"] != "after" or acknowledged["timing"] == "after",
            "acknowledgement precedes its return",
        )
    return {
        "message": message,
        "sender": names[0],
        "receiver": names[1],
        "commit": commit,
        "returned": returned,
        "acknowledged": acknowledged,
    }


def _edge_set(value: object, source: str, roster: list[str]) -> list[Record]:
    _require(
        type(value) is list and 1 <= len(value) <= MAX_EDGES,
        "observed journal needs 1 to 64 edges",
    )
    assert isinstance(value, list)
    names = set(roster)
    edges = [_edge(item, source, names) for item in value]
    keys = [(e["message"], e["sender"], e["receiver"]) for e in edges]
    _require(
        all(a < b for a, b in pairwise(keys)), "handoff edges are not strictly sorted"
    )
    senders: dict[str, str] = {}
    commits: dict[str, str] = {}
    for edge in edges:
        _require(
            senders.setdefault(edge["message"], edge["sender"]) == edge["sender"],
            "handoff message has two senders",
        )
        _require(
            commits.setdefault(edge["receiver"], edge["commit"]) == edge["commit"],
            "receiver commit state differs across edges",
        )
    _require(
        len({(e["message"], e["receiver"]) for e in edges}) == len(edges),
        "handoff edge is repeated",
    )
    if source == "sealed":
        unknown = [e["acknowledged"]["receipt"] == "unknown" for e in edges]
        _require(
            all(unknown) or not any(unknown), "sealed acknowledgement coverage is mixed"
        )
    return edges


def _reward(grade_state: str, value: object) -> float | int | None:
    if grade_state != "valid":
        _require(value is None, "unverified grades cannot carry a reward")
        return None
    _require(type(value) in (int, float), "invalid numeric reward")
    assert isinstance(value, int | float)
    _require(-1e100 <= value <= 1e100, "invalid numeric reward")
    return value


def validate(receipt: object) -> Record:
    row = _fields(receipt, RECEIPT_KEYS, "unsupported attempt handoff receipt")
    _require(row["schema"] == SCHEMA, "unsupported attempt handoff receipt")
    for key in ("job", "trial"):
        _require(
            type(row[key]) is str and UUID.fullmatch(row[key]) is not None,
            "receipt identity is not a canonical UUID",
        )
    _require(
        type(row["slot"]) is str and DIGEST.fullmatch(row["slot"]) is not None,
        "receipt slot is not a digest",
    )
    roster = row["roster"]
    _require(
        type(roster) is list and len(roster) <= MAX_SEATS, "invalid receipt roster"
    )
    _require(
        all(type(n) is str and SEAT.fullmatch(n) is not None for n in roster),
        "invalid receipt roster",
    )
    _require(
        all(a < b for a, b in pairwise(roster)), "receipt roster is not strictly sorted"
    )
    grade_state = _choice(
        row["grade_state"], tuple(sorted(GRADES)), "invalid grade state"
    )
    reward = _reward(grade_state, row["reward"])
    source = _choice(row["source"], SOURCES, "unsupported receipt source")
    world, edges = row["world"], row["edges"]
    if source in OBSERVED:
        _require(
            type(world) is str and UUID.fullmatch(world) is not None,
            "observed journal needs a world UUID",
        )
        edges = _edge_set(edges, source, list(roster))
    else:
        _require(
            world is None and type(edges) is list and not edges,
            "unobserved journal carries evidence",
        )
        edges = []
    return {
        "schema": SCHEMA,
        "job": row["job"],
        "slot": row["slot"],
        "trial": row["trial"],
        "roster": list(roster),
        "grade_state": grade_state,
        "reward": reward,
        "source": source,
        "world": world,
        "edges": edges,
    }
