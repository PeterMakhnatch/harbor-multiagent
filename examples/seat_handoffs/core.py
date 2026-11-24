"""Receiver-boundary handoff findings and the ordered world log reader.

A Finding records whether a named receiver was served a message at a world
boundary, never whether a model understood it. A recorded commit is not task
success; only a native reward establishes success. Absence is reported only
from a closed log; an open prefix leaves missing evidence unknown. A torn final
frame without LF is ignored as in-flight data; any malformed complete line is
an error.

Basis world_return: world prepared the reply, not proof of network delivery.
Basis sealed_world: the same returned journal, closed after play.
Basis receiver_ack: the receiver acknowledged the reply in journal order; only
journals whose start record declares acknowledgements can give it.
"""

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "harbor.handoffs/v1"
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 128 * 1024
MAX_EDGES = 64
MAX_FRAMES = 2 + MAX_EDGES * 4
IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")

RECEIPTS = ("confirmed", "absent", "unknown")
TIMINGS = ("before", "after", "no_commit", "unknown")
COMMITS = ("recorded", "not_recorded", "unknown")
BASES = ("model_request", "world_return", "sealed_world", "receiver_ack", "none")
BOUNDARIES = ("returned", "acknowledged")

HEADER_KEYS = frozenset({"seq", "type", "schema", "run", "edges"})
ACK_HEADER_KEYS = HEADER_KEYS | {"acknowledgements"}
EDGE_KEYS = frozenset({"message", "sender", "receiver"})
EVENT_KEYS = {
    "sent": frozenset({"seq", "type", "message", "sender"}),
    "returned": frozenset({"seq", "type", "message", "receiver"}),
    "acknowledged": frozenset({"seq", "type", "message", "receiver"}),
    "commit": frozenset({"seq", "type", "receiver"}),
    "closed": frozenset({"seq", "type", "reward"}),
}


def check_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} is not a safe identifier")
    return value


def check_reward(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("reward must be a number or null")
    try:
        reward = float(value)
    except OverflowError as error:
        raise ValueError("reward must be within [0, 1]") from error
    if not math.isfinite(reward) or not 0.0 <= reward <= 1.0:
        raise ValueError("reward must be within [0, 1]")
    return reward


def check_choice(value: object, choices: tuple[str, ...], name: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    trial: str
    message: str
    sender: str
    receiver: str
    receipt: str
    timing: str
    commit: str
    reward: float | None
    basis: str

    def __post_init__(self) -> None:
        for name in ("trial", "message", "sender", "receiver"):
            check_identifier(getattr(self, name), name)
        check_choice(self.receipt, RECEIPTS, "receipt")
        check_choice(self.timing, TIMINGS, "timing")
        check_choice(self.commit, COMMITS, "commit")
        check_choice(self.basis, BASES, "basis")
        check_reward(self.reward)
        state = (self.receipt, self.commit)
        if self.timing in ("before", "after") and state != ("confirmed", "recorded"):
            raise ValueError(
                "before/after timing needs a confirmed receipt and a recorded commit"
            )
        if self.timing == "no_commit" and state != ("confirmed", "not_recorded"):
            raise ValueError(
                "no_commit timing needs a confirmed receipt and no recorded commit"
            )
        if self.receipt != "unknown" and self.basis == "none":
            raise ValueError("a confirmed or absent receipt needs an evidence basis")


@dataclass(frozen=True, slots=True)
class Edge:
    message: str
    sender: str
    receiver: str


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def reject_constant(name: str) -> None:
    raise ValueError(f"non-finite JSON number {name}")


def read_frames(path: Path) -> tuple[list[bytes], bool]:
    if not path.is_file():
        raise FileNotFoundError("handoff log is missing or not a regular file")
    with path.open("rb") as handle:
        data = handle.read(MAX_LOG_BYTES + 1)
    return frames_from_bytes(data)


def frames_from_bytes(data: bytes) -> tuple[list[bytes], bool]:
    if len(data) > MAX_LOG_BYTES:
        raise ValueError(f"handoff log exceeds {MAX_LOG_BYTES} bytes")
    frames = data.split(b"\n", MAX_FRAMES + 1)
    if len(frames) > MAX_FRAMES + 1:
        raise ValueError("journal exceeds the maximum event count for its schema")
    torn = frames.pop()
    return frames, torn != b""


def parse_record(frame: bytes, seq: int) -> dict[str, Any]:
    if len(frame) > MAX_LINE_BYTES:
        raise ValueError(f"line for seq {seq} exceeds {MAX_LINE_BYTES} bytes")
    try:
        text = frame.decode("utf-8")
        if not text or text != text.strip():
            raise ValueError("blank line or surrounding whitespace")
        record = json.loads(
            text, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except (ValueError, RecursionError) as error:
        raise ValueError(f"line for seq {seq} is not a valid JSON record") from error
    if not isinstance(record, dict):
        raise ValueError(f"line for seq {seq} is not a JSON object")
    found = record.get("seq")
    if type(found) is not int or found != seq:
        raise ValueError(f"expected contiguous seq {seq}")
    return record


def expect_keys(record: dict[str, Any], keys: frozenset[str], seq: int) -> None:
    if set(record) != keys:
        raise ValueError(f"seq {seq} has missing or extra fields")


def canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def parse_header(record: dict[str, Any]) -> tuple[str, tuple[Edge, ...], bool]:
    acks = "acknowledgements" in record
    expect_keys(record, ACK_HEADER_KEYS if acks else HEADER_KEYS, 0)
    if acks and record["acknowledgements"] is not True:
        raise ValueError("acknowledgements must be true when declared")
    if record["type"] != "start" or record["schema"] != SCHEMA:
        raise ValueError(f"seq 0 is not a {SCHEMA} start record")
    if not canonical_uuid(record["run"]):
        raise ValueError("world run id is not a canonical UUID")
    declared = record["edges"]
    if not isinstance(declared, list) or not 1 <= len(declared) <= MAX_EDGES:
        raise ValueError(f"start record needs 1 to {MAX_EDGES} edges")
    edges: list[Edge] = []
    senders: dict[str, str] = {}
    for item in declared:
        if not isinstance(item, dict) or set(item) != EDGE_KEYS:
            raise ValueError("edge needs exactly message, sender and receiver")
        edge = Edge(
            message=check_identifier(item["message"], "edge message"),
            sender=check_identifier(item["sender"], "edge sender"),
            receiver=check_identifier(item["receiver"], "edge receiver"),
        )
        if edge.sender == edge.receiver:
            raise ValueError("edge sender and receiver must differ")
        if senders.setdefault(edge.message, edge.sender) != edge.sender:
            raise ValueError("message is declared with two senders")
        if any((e.message, e.receiver) == (edge.message, edge.receiver) for e in edges):
            raise ValueError("edge is declared twice")
        edges.append(edge)
    return record["run"], tuple(edges), acks


@dataclass
class Replay:
    run: str
    edges: tuple[Edge, ...]
    acks: bool
    sent: set[str] = field(default_factory=set)
    returned: dict[tuple[str, str], int] = field(default_factory=dict)
    acknowledged: dict[tuple[str, str], int] = field(default_factory=dict)
    commits: dict[str, int] = field(default_factory=dict)
    closed: bool = False
    reward: float | None = None

    def apply(self, record: dict[str, Any], seq: int) -> None:
        if self.closed:
            raise ValueError(f"seq {seq} follows the closed record")
        kind = record.get("type")
        if not isinstance(kind, str) or kind not in EVENT_KEYS:
            raise ValueError(f"seq {seq} has an unknown event type")
        expect_keys(record, EVENT_KEYS[kind], seq)
        if kind == "sent":
            self.send(record, seq)
        elif kind == "returned":
            self.receive(record, seq)
        elif kind == "acknowledged":
            self.acknowledge(record, seq)
        elif kind == "commit":
            self.commit(record, seq)
        else:
            self.reward = check_reward(record["reward"])
            self.closed = True

    def send(self, record: dict[str, Any], seq: int) -> None:
        message = check_identifier(record["message"], f"seq {seq} message")
        sender = check_identifier(record["sender"], f"seq {seq} sender")
        if not any((e.message, e.sender) == (message, sender) for e in self.edges):
            raise ValueError(f"seq {seq} sends an undeclared message or sender")
        if message in self.sent:
            raise ValueError(f"seq {seq} repeats a send")
        self.sent.add(message)

    def edge_key(self, record: dict[str, Any], seq: int) -> tuple[str, str]:
        key = (
            check_identifier(record["message"], f"seq {seq} message"),
            check_identifier(record["receiver"], f"seq {seq} receiver"),
        )
        if not any((e.message, e.receiver) == key for e in self.edges):
            raise ValueError(f"seq {seq} names an undeclared edge")
        return key

    def receive(self, record: dict[str, Any], seq: int) -> None:
        key = self.edge_key(record, seq)
        if key[0] not in self.sent:
            raise ValueError(f"seq {seq} returns a message before it was sent")
        if key in self.returned:
            raise ValueError(f"seq {seq} repeats a return")
        self.returned[key] = seq

    def acknowledge(self, record: dict[str, Any], seq: int) -> None:
        if not self.acks:
            raise ValueError(
                f"seq {seq} acknowledges in a journal without acknowledgements"
            )
        key = self.edge_key(record, seq)
        if key not in self.returned:
            raise ValueError(f"seq {seq} acknowledges a message before it was returned")
        if key in self.acknowledged:
            raise ValueError(f"seq {seq} repeats an acknowledgement")
        self.acknowledged[key] = seq

    def commit(self, record: dict[str, Any], seq: int) -> None:
        receiver = check_identifier(record["receiver"], f"seq {seq} receiver")
        if not any(e.receiver == receiver for e in self.edges):
            raise ValueError(f"seq {seq} commits for an undeclared receiver")
        if receiver in self.commits:
            raise ValueError(f"seq {seq} repeats a commit")
        self.commits[receiver] = seq

    def finding(self, edge: Edge, boundary: str) -> Finding:
        key = (edge.message, edge.receiver)
        if boundary == "returned":
            seen = self.returned.get(key)
            basis = "sealed_world" if self.closed else "world_return"
        elif self.acks:
            seen, basis = self.acknowledged.get(key), "receiver_ack"
        else:
            seen, basis = None, "none"
        committed = self.commits.get(edge.receiver)
        if seen is None:
            receipt = "absent" if self.closed and basis != "none" else "unknown"
            timing = "unknown"
        elif committed is not None:
            receipt, timing = "confirmed", ("before" if seen < committed else "after")
        else:
            receipt, timing = "confirmed", ("no_commit" if self.closed else "unknown")
        if committed is not None:
            commit = "recorded"
        else:
            commit = "not_recorded" if self.closed else "unknown"
        return Finding(
            trial=self.run,
            message=edge.message,
            sender=edge.sender,
            receiver=edge.receiver,
            receipt=receipt,
            timing=timing,
            commit=commit,
            reward=self.reward,
            basis=basis,
        )


def replay_frames(frames: list[bytes]) -> Replay:
    if not frames:
        raise ValueError("handoff log has no complete start record")
    replay = Replay(*parse_header(parse_record(frames[0], 0)))
    for seq, frame in enumerate(frames[1:], start=1):
        replay.apply(parse_record(frame, seq), seq)
    return replay


def inspect(path: Path, *, boundary: str = "returned") -> list[Finding]:
    check_choice(boundary, BOUNDARIES, "boundary")
    frames, torn = read_frames(path)
    replay = replay_frames(frames)
    if torn and replay.closed:
        raise ValueError("data follows the closed record")
    return [replay.finding(edge, boundary) for edge in replay.edges]


def tally(rows: list[Finding], name: str, choices: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(getattr(row, name) for row in rows)
    return {choice: counts[choice] for choice in choices}


def trial_rewards(rows: list[Finding]) -> dict[str, float | None]:
    rewards: dict[str, float | None] = {}
    senders: dict[tuple[str, str], str] = {}
    edges: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, Finding):
            raise ValueError("summary rows must be Finding records")
        edge = (row.trial, row.message, row.receiver)
        if edge in edges:
            raise ValueError("repeated (trial, message, receiver) finding")
        edges.add(edge)
        if senders.setdefault((row.trial, row.message), row.sender) != row.sender:
            raise ValueError("conflicting senders for one trial message")
        reward = check_reward(row.reward)
        if rewards.setdefault(row.trial, reward) != reward:
            raise ValueError("conflicting rewards within one trial")
    return rewards


def on_time(rows: list[Finding], rewards: dict[str, float | None]) -> dict[str, int]:
    by_trial: dict[str, list[Finding]] = {}
    for row in rows:
        if rewards[row.trial] == 1.0:
            by_trial.setdefault(row.trial, []).append(row)
    before = proven = 0
    for group in by_trial.values():
        if any(row.receipt == "confirmed" and row.timing == "before" for row in group):
            before += 1
        elif all(
            row.receipt == "absent" or row.timing in ("after", "no_commit")
            for row in group
        ):
            proven += 1
    unresolved = len(by_trial) - before - proven
    return {
        "successful_trials": len(by_trial),
        "confirmed_before": before,
        "without_confirmed_before": proven + unresolved,
        "proven_not_before": proven,
        "unresolved": unresolved,
    }


def summarize(rows: list[Finding]) -> dict[str, Any]:
    rewards = trial_rewards(rows)
    graded = [reward for reward in rewards.values() if reward is not None]
    messages = sorted({row.message for row in rows})
    return {
        "rows": len(rows),
        "trials": len(rewards),
        "receipt": tally(rows, "receipt", RECEIPTS),
        "timing": tally(rows, "timing", TIMINGS),
        "commit": tally(rows, "commit", COMMITS),
        "basis": tally(rows, "basis", BASES),
        "receipt_timing": {
            receipt: tally(
                [row for row in rows if row.receipt == receipt], "timing", TIMINGS
            )
            for receipt in RECEIPTS
        },
        "reward": {
            "graded_trials": len(graded),
            "ungraded_trials": len(rewards) - len(graded),
            "mean": sum(graded) / len(graded) if graded else None,
            "full_reward_trials": sum(reward == 1.0 for reward in graded),
        },
        "success_without_on_time": on_time(rows, rewards),
        "success_without_on_time_by_message": {
            message: on_time([row for row in rows if row.message == message], rewards)
            for message in messages
        },
    }
