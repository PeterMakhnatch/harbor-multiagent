"""Freeze the shift-board world, replay its cut, and publish the observation."""

import hashlib
import http.client
import json
import os
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TypeGuard, cast
from urllib.request import ProxyHandler, Request, build_opener

VERIFIER_DIR = Path("/logs/verifier")
FREEZE_URL = "http://127.0.0.1:8080/freeze"
FREEZE_BODY = b"{}"
CUT_NAME = "shift-board-cut.json"
OBSERVATION_NAME = "shift-board.json"
REWARD_NAME = "reward.json"
CUT_SCHEMA = "harbor.shift-board.cut/v1"
OBSERVATION_SCHEMA = "harbor.shift-board.observation/v1"
LETTERS = ("A", "B", "C", "D")
FIXTURES = frozenset(("planner-fit", "dispatcher-fit"))
RULES = frozenset(("observe", "guard"))
SEATS = frozenset(("planner", "dispatcher"))
OUTCOMES = frozenset(("accepted", "rejected"))
MAX_SEED = 2_147_483_647
MAX_SEED_DIGITS = len(str(MAX_SEED))
MAX_EVENTS = 24
MAX_CUT_BYTES = 16384
CUT_KEYS = frozenset(("schema", "run", "seed", "fixture", "rule", "events"))
EVENT_KEYS = {
    "view": frozenset(("seq", "type", "seat", "rev")),
    "prepare": frozenset(("seq", "type", "seat", "base", "slot")),
    "publish": frozenset(("seq", "type", "seat", "base", "slot", "outcome")),
    "leave": frozenset(("seq", "type", "seat")),
}


class CutError(Exception):
    pass


def _exact_int(value: object) -> bool:
    return type(value) is int


def _check_expected(seed: object, fixture: object, rule: object) -> None:
    if not _exact_int(seed) or not 0 <= cast(int, seed) <= MAX_SEED:
        raise CutError("expected seed is invalid")
    if type(fixture) is not str or fixture not in FIXTURES:
        raise CutError("expected fixture is invalid")
    if type(rule) is not str or rule not in RULES:
        raise CutError("expected rule is invalid")


def expected_state(environ: Mapping[str, str]) -> tuple[int, str, str]:
    seed = environ.get("BOARD_SEED")
    fixture = environ.get("BOARD_FIXTURE")
    rule = environ.get("BOARD_RULE")
    if seed is None or fixture is None or rule is None:
        raise CutError("expected world state is missing from the main environment")
    if not (seed.isascii() and seed.isdigit() and len(seed) <= MAX_SEED_DIGITS):
        raise CutError("expected seed is not ASCII decimal")
    value = int(seed)
    _check_expected(value, fixture, rule)
    return value, fixture, rule


def fixture_slots(seed: int, fixture: str) -> tuple[dict[str, frozenset[str]], str]:
    _check_expected(seed, fixture, "guard")
    x, y, z, _ = sorted(
        LETTERS,
        key=lambda letter: (
            hashlib.sha256(f"shift-board/v1:{seed}:{letter}".encode("ascii")).digest(),
            letter,
        ),
    )
    if fixture == "planner-fit":
        available = {"planner": frozenset((x, z)), "dispatcher": frozenset((x, y))}
        answer = x
    else:
        available = {"planner": frozenset((x, y)), "dispatcher": frozenset((y, z))}
        answer = y
    assert available["planner"] & available["dispatcher"] == {answer}
    return available, answer


def _run(value: object) -> str:
    if type(value) is not str:
        raise CutError("cut run is not a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise CutError("cut run is not a UUID") from None
    if parsed.version != 4 or str(parsed) != value:
        raise CutError("cut run is not a canonical UUID4")
    return value


def _has_cut_fields(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and type(value) is dict and value.keys() == CUT_KEYS


def _check_header(cut: object, seed: int, fixture: str, rule: str) -> str:
    if not _has_cut_fields(cut):
        raise CutError("cut fields do not match the contract")
    if cut["schema"] != CUT_SCHEMA:
        raise CutError("cut schema does not match the contract")
    run = _run(cut["run"])
    if not _exact_int(cut["seed"]) or cut["seed"] != seed:
        raise CutError("cut seed does not match the expected seed")
    if type(cut["fixture"]) is not str or cut["fixture"] != fixture:
        raise CutError("cut fixture does not match the expected fixture")
    if type(cut["rule"]) is not str or cut["rule"] != rule:
        raise CutError("cut rule does not match the expected rule")
    events = cut["events"]
    if (
        not isinstance(events, list)
        or type(events) is not list
        or len(events) > MAX_EVENTS
    ):
        raise CutError("cut events are not a bounded list")
    return run


def _event(raw: object, seq: int) -> dict[str, object]:
    if type(raw) is not dict:
        raise CutError(f"event {seq} is not an object")
    event = cast(dict[str, object], raw)
    kind = event.get("type")
    if type(kind) is not str or set(event) != EVENT_KEYS.get(kind):
        raise CutError(f"event {seq} has an unknown type or fields")
    if not _exact_int(event["seq"]) or event["seq"] != seq:
        raise CutError(f"event {seq} breaks the contiguous seq order")
    if type(event["seat"]) is not str or event["seat"] not in SEATS:
        raise CutError(f"event {seq} names an unknown seat")
    return event


class Replay:
    def __init__(self, available: dict[str, frozenset[str]], rule: str) -> None:
        self.available = available
        self.rule = rule
        self.rev = 0
        self.standing: str | None = None
        self.incumbent: str | None = None
        self.draft: str | None = None
        self.closed = False
        self.final_action = "none"
        self.viewed: set[tuple[str, int]] = set()
        self.attempts = 0
        self.stale: list[tuple[bool, str, str]] = []

    def apply(self, event: dict[str, object]) -> None:
        kind, seat = event["type"], cast(str, event["seat"])
        if self.closed:
            raise CutError(f"event {event['seq']} follows the terminal action")
        if kind == "view":
            self._view(event, seat)
        elif seat != "dispatcher" and kind != "publish":
            raise CutError(f"event {event['seq']} is a {kind} by the {seat}")
        elif kind == "prepare":
            self._prepare(event)
        elif kind == "leave":
            self._leave(event)
        elif seat == "planner":
            self._planner_publish(event)
        else:
            self._dispatcher_publish(event)

    def _slot(self, event: dict[str, object], seat: str) -> str:
        slot = event["slot"]
        if type(slot) is not str or slot not in self.available[seat]:
            raise CutError(f"event {event['seq']} slot is outside {seat} availability")
        return slot

    def _base(self, event: dict[str, object], seat: str) -> int:
        base = event["base"]
        if not _exact_int(base) or (seat, base) not in self.viewed:
            raise CutError(f"event {event['seq']} base was never returned to {seat}")
        return cast(int, base)

    def _view(self, event: dict[str, object], seat: str) -> None:
        rev = event["rev"]
        if not _exact_int(rev) or rev != self.rev:
            raise CutError(f"event {event['seq']} views a revision that is not current")
        if seat == "planner" and self.draft is None:
            raise CutError(f"event {event['seq']} is a planner view before prepare")
        if (seat, rev) in self.viewed:
            raise CutError(f"event {event['seq']} repeats a {seat} view")
        self.viewed.add((seat, cast(int, rev)))

    def _prepare(self, event: dict[str, object]) -> None:
        if self.draft is not None or self.rev != 0:
            raise CutError(f"event {event['seq']} prepares twice or late")
        if self._base(event, "dispatcher") != 0:
            raise CutError(f"event {event['seq']} prepares on a later revision")
        self.draft = self._slot(event, "dispatcher")

    def _leave(self, event: dict[str, object]) -> None:
        if self.rev != 1 or self.closed:
            raise CutError(
                f"event {event['seq']} leaves before the incumbent or after close"
            )
        self.closed = True
        self.final_action = "leave"

    def _planner_publish(self, event: dict[str, object]) -> None:
        if self.draft is None or self.incumbent is not None or self.rev != 0:
            raise CutError(
                f"event {event['seq']} is a premature or repeated planner publish"
            )
        if self._base(event, "planner") != self.rev or event["outcome"] != "accepted":
            raise CutError(
                f"event {event['seq']} planner publish is not a fresh acceptance"
            )
        self.incumbent = self.standing = self._slot(event, "planner")
        self.rev = 1

    def _dispatcher_publish(self, event: dict[str, object]) -> None:
        seq = event["seq"]
        if self.rev != 1 or self.closed:
            raise CutError(
                f"event {seq} dispatcher publish is premature or after close"
            )
        stale = self._base(event, "dispatcher") != self.rev
        slot = self._slot(event, "dispatcher")
        outcome = event["outcome"]
        if type(outcome) is not str or outcome not in OUTCOMES:
            raise CutError(f"event {seq} has an unknown outcome")
        refused = stale and self.rule == "guard"
        if outcome != ("rejected" if refused else "accepted"):
            raise CutError(f"event {seq} outcome contradicts the {self.rule} rule")
        if refused and any(prior[2] == "rejected" for prior in self.stale):
            raise CutError(f"event {seq} repeats a refused stale token")
        self.attempts += 1
        if stale:
            self.stale.append((("dispatcher", self.rev) in self.viewed, slot, outcome))
        if outcome == "accepted":
            self.standing = slot
            self.rev = 2
            self.closed = True
            self.final_action = "publish"


def _correct(slot: str | None, answer: str) -> bool | None:
    return None if slot is None else slot == answer


def _effect(before: bool | None, after: bool | None) -> str:
    assert type(before) is bool and type(after) is bool
    if before == after:
        return "unchanged"
    return "repair" if after else "damage"


def evaluate(cut: object, *, seed: int, fixture: str, rule: str) -> dict[str, object]:
    _check_expected(seed, fixture, rule)
    run = _check_header(cut, seed, fixture, rule)
    available, answer = fixture_slots(seed, fixture)
    replay = Replay(available, rule)
    events = cast(list[object], cast(dict[str, object], cut)["events"])
    for seq, raw in enumerate(events, start=1):
        replay.apply(_event(raw, seq))
    incumbent_correct = _correct(replay.incumbent, answer)
    final_correct = _correct(replay.standing, answer)
    first = replay.stale[0] if replay.stale else None
    return {
        "schema": OBSERVATION_SCHEMA,
        "run": run,
        "seed": seed,
        "fixture": fixture,
        "rule": rule,
        "prepared": replay.draft is not None,
        "incumbent_correct": incumbent_correct,
        "draft_correct": _correct(replay.draft, answer),
        "attempts": replay.attempts,
        "stale_attempts": len(replay.stale),
        "stale_refusals": sum(1 for prior in replay.stale if prior[2] == "rejected"),
        "first_stale_current_returned": None if first is None else first[0],
        "first_stale_effect": (
            None if first is None else _effect(incumbent_correct, first[1] == answer)
        ),
        "final_action": replay.final_action,
        "final_correct": final_correct,
        "reward": int(final_correct is True),
        "observed_change": (
            None
            if replay.final_action == "none"
            else _effect(incumbent_correct, final_correct)
        ),
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise CutError("duplicate JSON key")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise CutError(f"non-finite JSON number {name}")


def parse_cut(data: bytes) -> object:
    if not data or len(data) > MAX_CUT_BYTES:
        raise CutError("cut is empty or too large")
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CutError("cut is not valid JSON") from None


def freeze_world() -> bytes:
    request = Request(FREEZE_URL, data=FREEZE_BODY, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            status, body = response.status, response.read(MAX_CUT_BYTES + 1)
    except (OSError, http.client.HTTPException):
        raise CutError("world did not freeze") from None
    if status != 200 or not body or len(body) > MAX_CUT_BYTES:
        raise CutError("world freeze reply is not a bounded cut")
    return body


def _publish(name: str, data: bytes) -> None:
    staged = VERIFIER_DIR / f".{name}.tmp"
    staged.write_bytes(data)
    os.replace(staged, VERIFIER_DIR / name)


def main() -> int:
    try:
        seed, fixture, rule = expected_state(os.environ)
        body = freeze_world()
        VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
        _publish(CUT_NAME, body)
        observation = evaluate(parse_cut(body), seed=seed, fixture=fixture, rule=rule)
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    reward = observation["reward"]
    assert reward in (0, 1) and _exact_int(reward)
    _publish(OBSERVATION_NAME, json.dumps(observation).encode("ascii") + b"\n")
    _publish(REWARD_NAME, json.dumps({"reward": reward}).encode("ascii") + b"\n")
    print(f"reward={reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
