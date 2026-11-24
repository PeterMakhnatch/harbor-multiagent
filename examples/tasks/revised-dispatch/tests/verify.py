"""Grade a finished revised-dispatch world and seal its handoff log."""

import json
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.request import ProxyHandler, Request, build_opener

WORLD_DIR = Path("/logs/world")
VERIFIER_DIR = Path("/logs/verifier")
FREEZE_URL = "http://127.0.0.1:8080/freeze"
FREEZE_BODY = b"{}"
SCHEMA = "harbor.handoffs/v1"
DECISION_SCHEMA = "harbor.revised-dispatch.decision/v1"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
SLOTS = frozenset(("A", "B", "C"))
MESSAGES = ("initial", "revision")
VARIANTS = frozenset(("replace", "confirm"))
MAX_SEED = 1_000_000
MAX_SEED_DIGITS = len(str(MAX_SEED))
MAX_FILE_BYTES = 16384
MAX_LINE_BYTES = 1024
MAX_EVENTS = 8
STATE_KEYS = frozenset(
    (
        "run",
        "variant",
        "seed",
        "initial",
        "final",
        "draft",
        "commit",
        "reported_revision",
        "events",
        "fault",
        "frozen",
    )
)
EVENT_KEYS = {
    "sent": frozenset(("seq", "type", "message", "sender")),
    "returned": frozenset(("seq", "type", "message", "receiver")),
    "acknowledged": frozenset(("seq", "type", "message", "receiver")),
    "commit": frozenset(("seq", "type", "receiver")),
}


class WorldEvidenceError(Exception):
    pass


@dataclass(frozen=True)
class LogFacts:
    run: str
    events: int
    sent: tuple[str, ...]
    returned: tuple[str, ...]
    acknowledged: tuple[str, ...]
    committed: bool


@dataclass(frozen=True)
class Cut:
    run: str
    events: int


@dataclass(frozen=True)
class Fixture:
    variant: str
    seed: int

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise WorldEvidenceError("expected world fixture is invalid")
        if type(self.seed) is not int or not 0 <= self.seed <= MAX_SEED:
            raise WorldEvidenceError("expected world fixture is invalid")


@dataclass(frozen=True)
class Grade:
    reward: int
    sealed: bytes
    decision: dict[str, object]


def expected_fixture(environ: Mapping[str, str]) -> Fixture:
    variant = environ.get("HANDOFF_VARIANT")
    seed = environ.get("HANDOFF_SEED")
    if variant is None or seed is None:
        raise WorldEvidenceError("expected world fixture is missing")
    if not (seed.isascii() and seed.isdigit() and len(seed) <= MAX_SEED_DIGITS):
        raise WorldEvidenceError("expected world fixture is invalid")
    return Fixture(variant, int(seed))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise WorldEvidenceError("duplicate JSON key")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise WorldEvidenceError(f"non-finite JSON number {name}")


def _object(data: bytes, what: str) -> dict[str, object]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WorldEvidenceError(f"{what} is not valid JSON") from None
    if not isinstance(value, dict):
        raise WorldEvidenceError(f"{what} is not a JSON object")
    return cast(dict[str, object], value)


def _lines(log: bytes) -> list[dict[str, object]]:
    if not log or len(log) > MAX_FILE_BYTES:
        raise WorldEvidenceError("handoff log is empty or too large")
    if not log.endswith(b"\n"):
        raise WorldEvidenceError("handoff log ends with a torn frame")
    raw_lines = log[:-1].split(b"\n")
    if len(raw_lines) > MAX_EVENTS:
        raise WorldEvidenceError("handoff log has too many events")
    events: list[dict[str, object]] = []
    for seq, raw in enumerate(raw_lines):
        if len(raw) > MAX_LINE_BYTES:
            raise WorldEvidenceError(f"event {seq} is too large")
        event = _object(raw, f"event {seq}")
        if type(event.get("seq")) is not int or event["seq"] != seq:
            raise WorldEvidenceError(f"event {seq} breaks the contiguous seq order")
        events.append(event)
    return events


def _header_run(header: dict[str, object]) -> str:
    if (
        set(header) != {"seq", "type", "schema", "run", "edges", "acknowledgements"}
        or header["acknowledgements"] is not True
        or header["type"] != "start"
        or header["schema"] != SCHEMA
        or header["edges"] != EDGES
    ):
        raise WorldEvidenceError("handoff log header does not match this task")
    run = header["run"]
    if not isinstance(run, str):
        raise WorldEvidenceError("world run id is not a string")
    try:
        canonical = str(uuid.UUID(run))
    except ValueError:
        raise WorldEvidenceError("world run id is not a UUID") from None
    if canonical != run:
        raise WorldEvidenceError("world run id is not a canonical UUID")
    return run


def _follow(event: dict[str, object], prior: list[str], seen: list[str]) -> None:
    message, seq, kind = event["message"], event["seq"], event["type"]
    if event["receiver"] != "booker" or message not in prior:
        raise WorldEvidenceError(f"event {seq} is {kind} out of order")
    if message in seen:
        raise WorldEvidenceError(f"event {seq} is {kind} twice")
    seen.append(str(message))


def parse_log(log: bytes) -> LogFacts:
    events = _lines(log)
    run = _header_run(events[0])
    sent: list[str] = []
    returned: list[str] = []
    acknowledged: list[str] = []
    committed = False
    for event in events[1:]:
        seq, kind = event["seq"], event.get("type")
        if not isinstance(kind, str) or set(event) != EVENT_KEYS.get(kind):
            raise WorldEvidenceError(f"event {seq} has an unknown type or fields")
        if kind == "sent":
            expected = MESSAGES[len(sent)] if len(sent) < len(MESSAGES) else None
            if event["sender"] != "reporter" or event["message"] != expected:
                raise WorldEvidenceError(f"event {seq} sends out of order")
            if expected is None:
                raise WorldEvidenceError(f"event {seq} sends a third message")
            sent.append(expected)
        elif kind == "returned":
            _follow(event, sent, returned)
        elif kind == "acknowledged":
            _follow(event, returned, acknowledged)
        else:
            if event["receiver"] != "booker" or committed:
                raise WorldEvidenceError(f"event {seq} is a second or foreign commit")
            committed = True
    return LogFacts(
        run,
        len(events),
        tuple(sent),
        tuple(returned),
        tuple(acknowledged),
        committed,
    )


def _slot(value: object, nullable: bool, field: str) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or value not in SLOTS:
        raise WorldEvidenceError(f"state {field} is not a valid slot")
    return value


def _check_state(state: dict[str, object], facts: LogFacts, expected: Fixture) -> None:
    if set(state) != STATE_KEYS:
        raise WorldEvidenceError("world state has unexpected fields")
    if state["fault"] is not None:
        raise WorldEvidenceError("world recorded a fault")
    if state["frozen"] is not True:
        raise WorldEvidenceError("world state is not frozen")
    if state["run"] != facts.run:
        raise WorldEvidenceError("world state and handoff log name different runs")
    events = state["events"]
    if (
        not isinstance(events, int)
        or isinstance(events, bool)
        or events != facts.events
    ):
        raise WorldEvidenceError("world state and handoff log disagree on events")
    if (
        type(state["seed"]) is not int
        or state["seed"] != expected.seed
        or type(state["variant"]) is not str
        or state["variant"] != expected.variant
    ):
        raise WorldEvidenceError("world fixture does not match the expected fixture")
    initial = _slot(state["initial"], False, "initial")
    final = _slot(state["final"], False, "final")
    if (initial == final) != (expected.variant == "confirm"):
        raise WorldEvidenceError("world state truth does not match its variant")
    draft = _slot(state["draft"], True, "draft")
    commit = _slot(state["commit"], True, "commit")
    revised = _slot(state["reported_revision"], True, "reported_revision")
    if (commit is not None) != facts.committed:
        raise WorldEvidenceError("world state commit disagrees with the commit event")
    if (revised is not None) != ("revision" in facts.sent):
        raise WorldEvidenceError("world state revision disagrees with the sent event")
    if revised is not None and draft is None:
        raise WorldEvidenceError("a revision was sent before any draft")


def grade(log: bytes, state_bytes: bytes, cut: Cut, expected: Fixture) -> Grade:
    facts = parse_log(log)
    if cut.run != facts.run or cut.events != facts.events:
        raise WorldEvidenceError("freeze cut does not match the handoff log")
    state = _object(state_bytes, "world state")
    _check_state(state, facts, expected)
    draft, commit, final = state["draft"], state["commit"], state["final"]
    reward = int(commit is not None and commit == final)
    closed = {"seq": facts.events, "type": "closed", "reward": reward}
    sealed = log + json.dumps(closed, separators=(",", ":")).encode("ascii") + b"\n"
    decision: dict[str, object] = {
        "schema": DECISION_SCHEMA,
        "run": facts.run,
        "draft_changed": None if draft is None or commit is None else draft != commit,
        "decision_correct": None if commit is None else commit == final,
        "event_counts": {
            "sent": len(facts.sent),
            "returned": len(facts.returned),
            "acknowledged": len(facts.acknowledged),
            "commit": int(facts.committed),
        },
    }
    return Grade(reward, sealed, decision)


def freeze_world() -> Cut:
    request = Request(FREEZE_URL, data=FREEZE_BODY, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            status, body = response.status, response.read(1024)
    except OSError:
        raise WorldEvidenceError("world did not freeze") from None
    reply = _object(body, "freeze reply")
    if status != 200 or set(reply) != {"run", "events"}:
        raise WorldEvidenceError("world freeze reply is not a cut")
    run, events = reply["run"], reply["events"]
    if (
        not isinstance(run, str)
        or not isinstance(events, int)
        or isinstance(events, bool)
    ):
        raise WorldEvidenceError("world freeze reply is not a cut")
    if events < 1:
        raise WorldEvidenceError("world freeze reply is not a cut")
    return Cut(run, events)


def _snapshot() -> tuple[bytes, bytes]:
    try:
        return (
            (WORLD_DIR / "handoffs.jsonl").read_bytes(),
            (WORLD_DIR / "state.json").read_bytes(),
        )
    except OSError as error:
        raise WorldEvidenceError(f"world record missing: {error.strerror}") from None


def _publish(name: str, data: bytes) -> None:
    staged = VERIFIER_DIR / f".{name}.tmp"
    staged.write_bytes(data)
    os.replace(staged, VERIFIER_DIR / name)


def main() -> int:
    try:
        expected = expected_fixture(os.environ)
        cut = freeze_world()
        result = grade(*_snapshot(), cut, expected)
    except WorldEvidenceError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    _publish("handoffs.jsonl", result.sealed)
    _publish("decision.json", json.dumps(result.decision).encode("ascii") + b"\n")
    _publish("reward.json", json.dumps({"reward": result.reward}).encode() + b"\n")
    print(f"reward={result.reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
