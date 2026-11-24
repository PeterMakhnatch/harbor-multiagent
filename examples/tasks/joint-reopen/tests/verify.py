"""Freeze the joint-reopen world, replay its cut, and publish the observation."""

import hashlib
import http.client
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from urllib.request import ProxyHandler, Request, build_opener

VERIFIER_DIR = Path("/logs/verifier")
FREEZE_URL = "http://127.0.0.1:8080/freeze"
FREEZE_BODY = b"{}"
OBSERVATION_NAME = "joint-reopen.json"
REWARD_NAME = "reward.json"
CUT_SCHEMA = "harbor.joint-reopen.cut/v1"
OBSERVATION_SCHEMA = "harbor.joint-reopen.observation/v1"
PERMUTATION_PREFIX = "joint-reopen/v1"
ROLES = ("east", "west")
OPTIONS = ("A", "B", "C", "D", "E", "F")
FIXTURES = ("none", "east", "west", "both")
CHANNELS = ("deliver", "withhold")
VETOED = {"none": (), "east": ("east",), "west": ("west",), "both": ("east", "west")}
KINDS = ("endorse", "card", "report", "inbox", "vote")
MAX_SEED = 2_147_483_647
MAX_SEED_DIGITS = len(str(MAX_SEED))
MAX_REPORT = 3
MAX_EVENTS = len(ROLES) * len(KINDS)
MAX_CUT_BYTES = 16384
CUT_KEYS = frozenset(
    ("schema", "run", "seed", "fixture", "channel", "incumbent", "cards", "events")
)
EVENT_KEYS = frozenset(("n", "role", "kind", "value"))


class CutError(Exception):
    pass


def _exact_int(value: object) -> bool:
    return type(value) is int


def _check_world(seed: object, fixture: object, channel: object) -> None:
    if not _exact_int(seed) or not 0 <= cast(int, seed) <= MAX_SEED:
        raise CutError("seed is outside the contract bounds")
    if type(fixture) is not str or fixture not in FIXTURES:
        raise CutError("fixture is not a contract fixture")
    if type(channel) is not str or channel not in CHANNELS:
        raise CutError("channel is not a contract channel")


def expected_world(environ: Mapping[str, str]) -> tuple[int, str, str]:
    seed = environ.get("JR_SEED")
    fixture = environ.get("JR_FIXTURE")
    channel = environ.get("JR_CHANNEL")
    if seed is None or fixture is None or channel is None:
        raise CutError("expected world is missing from the main environment")
    if not (seed.isascii() and seed.isdigit() and len(seed) <= MAX_SEED_DIGITS):
        raise CutError("expected seed is not ASCII decimal")
    value = int(seed)
    _check_world(value, fixture, channel)
    return value, fixture, channel


def permutation(seed: int) -> tuple[str, ...]:
    _check_world(seed, "none", "deliver")
    order = tuple(
        sorted(
            OPTIONS,
            key=lambda slot: hashlib.sha256(
                f"{PERMUTATION_PREFIX}:{seed}:{slot}".encode("ascii")
            ).digest(),
        )
    )
    assert len(order) == len(OPTIONS) and set(order) == set(OPTIONS)
    return order


def fixture_world(
    seed: int, fixture: str
) -> tuple[str, dict[str, frozenset[str]], str]:
    _check_world(seed, fixture, "deliver")
    incumbent, shared, east_only, west_only, _, _ = permutation(seed)
    cards = {
        "east": {shared, east_only},
        "west": {shared, west_only},
    }
    for role in ROLES:
        if role not in VETOED[fixture]:
            cards[role].add(incumbent)
    frozen = {role: frozenset(cards[role]) for role in ROLES}
    common = frozen["east"] & frozen["west"]
    optimal = incumbent if fixture == "none" else shared
    assert common == ({incumbent, shared} if fixture == "none" else {shared})
    assert optimal in common
    return incumbent, frozen, optimal


def _run(value: object) -> str:
    if type(value) is not str:
        raise CutError("cut run is not a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise CutError("cut run is not a UUID") from None
    if str(parsed) != value:
        raise CutError("cut run is not a canonical UUID")
    return value


def _labels(value: object, what: str, limit: int) -> list[str]:
    if type(value) is not list or len(cast(list[object], value)) > limit:
        raise CutError(f"{what} is not a bounded list")
    items = cast(list[object], value)
    if any(type(item) is not str or item not in OPTIONS for item in items):
        raise CutError(f"{what} names a slot outside the public options")
    labels = cast(list[str], items)
    if labels != sorted(set(labels)):
        raise CutError(f"{what} is not sorted distinct labels")
    return labels


def _check_cards(raw: object, cards: dict[str, frozenset[str]]) -> None:
    if type(raw) is not dict or cast(dict[str, object], raw).keys() != set(ROLES):
        raise CutError("cut cards do not name exactly the two roles")
    for role in ROLES:
        card = _labels(cast(dict[str, object], raw)[role], f"{role} card", 3)
        if card != sorted(cards[role]):
            raise CutError(f"{role} card does not match the seeded fixture")


def _check_header(cut: object) -> dict[str, object]:
    if type(cut) is not dict or cast(dict[str, object], cut).keys() != CUT_KEYS:
        raise CutError("cut fields do not match the contract")
    header = cast(dict[str, object], cut)
    if header["schema"] != CUT_SCHEMA:
        raise CutError("cut schema does not match the contract")
    _run(header["run"])
    _check_world(header["seed"], header["fixture"], header["channel"])
    incumbent, cards, _ = fixture_world(
        cast(int, header["seed"]), cast(str, header["fixture"])
    )
    if type(header["incumbent"]) is not str or header["incumbent"] != incumbent:
        raise CutError("cut incumbent does not match the seeded permutation")
    _check_cards(header["cards"], cards)
    events = header["events"]
    if type(events) is not list or len(cast(list[object], events)) > MAX_EVENTS:
        raise CutError("cut events are not a bounded list")
    return header


class Replay:
    def __init__(self, incumbent: str, channel: str) -> None:
        self.incumbent = incumbent
        self.delivered = channel == "deliver"
        self.values: dict[tuple[str, str], object] = {}
        self.order: dict[tuple[str, str], int] = {}
        self.timeline: list[dict[str, object]] = []

    def done(self, kind: str) -> int:
        return sum(1 for role in ROLES if (role, kind) in self.values)

    def phase(self) -> str:
        if self.done("endorse") < len(ROLES):
            return "endorse"
        if self.done("report") < len(ROLES):
            return "review"
        if self.done("vote") < len(ROLES):
            return "vote"
        return "complete"

    def apply(self, raw: object, n: int) -> None:
        if type(raw) is not dict or cast(dict[str, object], raw).keys() != EVENT_KEYS:
            raise CutError(f"event {n} fields do not match the contract")
        event = cast(dict[str, object], raw)
        if not _exact_int(event["n"]) or event["n"] != n:
            raise CutError(f"event {n} breaks the gapless order")
        role, kind = event["role"], event["kind"]
        if type(role) is not str or role not in ROLES:
            raise CutError(f"event {n} names an unknown role")
        if type(kind) is not str or kind not in KINDS:
            raise CutError(f"event {n} has an unknown kind")
        if (role, kind) in self.values:
            raise CutError(f"event {n} repeats a first {kind} by {role}")
        phase = self.phase()
        allowed = ("review", "vote") if kind == "card" else (self._phase_of(kind),)
        if phase not in allowed:
            raise CutError(f"event {n} is a {kind} during the {phase} phase")
        value = self._value(kind, event["value"], n)
        self.values[(role, kind)] = value
        self.order[(role, kind)] = n
        code = kind
        if kind == "inbox":
            code = "inbox_returned" if value is True else "inbox_withheld"
        self.timeline.append({"n": n, "role": role, "kind": code})
        assert len(self.timeline) == n <= MAX_EVENTS

    def before(self, role: str, kind: str, anchor: str) -> bool | None:
        mark = self.order.get((role, anchor))
        if mark is None:
            return None
        seen = self.order.get((role, kind))
        return seen is not None and seen < mark

    @staticmethod
    def _phase_of(kind: str) -> str:
        return {"endorse": "endorse", "report": "review"}.get(kind, "vote")

    def _value(self, kind: str, value: object, n: int) -> object:
        if kind == "endorse":
            if type(value) is not str or value != self.incumbent:
                raise CutError(f"event {n} endorses a slot other than the provisional")
            return value
        if kind == "card":
            if value is not True:
                raise CutError(f"event {n} card value is not true")
            return value
        if kind == "report":
            return _labels(value, f"event {n} report", MAX_REPORT)
        if kind == "inbox":
            if type(value) is not bool or value != self.delivered:
                raise CutError(f"event {n} inbox value contradicts the channel")
            return value
        if type(value) is not str or value not in OPTIONS:
            raise CutError(f"event {n} vote is not a public option")
        return value


def _role_summary(
    replay: Replay, role: str, card: frozenset[str], incumbent: str
) -> dict[str, object]:
    values = replay.values
    report = cast(list[str] | None, values.get((role, "report")))
    vote = cast(str | None, values.get((role, "vote")))
    peer_before = replay.before(role, "inbox", "vote")
    return {
        "endorsed": (role, "endorse") in values,
        "card_returned": (role, "card") in values,
        "card_before_vote": replay.before(role, "card", "vote"),
        "report_posted": report is not None,
        "report_true": None if not report else set(report) == card,
        "report_after_card": replay.before(role, "card", "report"),
        "inbox_requested": (role, "inbox") in values,
        "peer_report_returned": values.get((role, "inbox")) is True,
        "peer_report_before_vote": (
            None
            if peer_before is None
            else peer_before and values.get((role, "inbox")) is True
        ),
        "changed": None if vote is None else vote != incumbent,
        "voted": vote is not None,
    }


def _final(
    replay: Replay, cards: dict[str, frozenset[str]]
) -> tuple[str | None, bool | None, bool | None]:
    votes = [cast(str | None, replay.values.get((role, "vote"))) for role in ROLES]
    if any(vote is None for vote in votes):
        return None, None, None
    if votes[0] != votes[1]:
        return None, False, None
    common = cast(str, votes[0])
    return common, True, all(common in cards[role] for role in ROLES)


def evaluate(cut: object) -> dict[str, object]:
    header = _check_header(cut)
    seed, fixture = cast(int, header["seed"]), cast(str, header["fixture"])
    channel = cast(str, header["channel"])
    incumbent, cards, optimal = fixture_world(seed, fixture)
    replay = Replay(incumbent, channel)
    for n, raw in enumerate(cast(list[object], header["events"]), start=1):
        replay.apply(raw, n)
    incumbent_feasible = all(incumbent in cards[role] for role in ROLES)
    assert incumbent_feasible == (fixture == "none")
    common, final_joint, final_feasible = _final(replay, cards)
    reward = int(common is not None and common == optimal)
    observation: dict[str, object] = {
        "schema": OBSERVATION_SCHEMA,
        "run": header["run"],
        "seed": seed,
        "fixture": fixture,
        "channel": channel,
        "initial_joint": replay.done("endorse") == len(ROLES),
        "incumbent_feasible": incumbent_feasible,
        "roles": {
            role: _role_summary(replay, role, cards[role], incumbent) for role in ROLES
        },
        "timeline": [dict(entry) for entry in replay.timeline],
        "final_joint": final_joint,
        "final_feasible": final_feasible,
        "kept_preferred_incumbent": (
            common == incumbent if common is not None and incumbent_feasible else None
        ),
        "repaired_invalid_incumbent": (
            final_feasible if common is not None and not incumbent_feasible else None
        ),
        "reward": reward,
    }
    assert reward == int(
        final_joint is True
        and final_feasible is True
        and (
            observation["kept_preferred_incumbent"] is True
            or observation["repaired_invalid_incumbent"] is True
        )
    )
    return observation


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


def _check_expected(
    observation: dict[str, object], expected: tuple[int, str, str]
) -> None:
    seen = (observation["seed"], observation["fixture"], observation["channel"])
    if seen != expected:
        raise CutError("cut world does not match the expected world")


def _publish(name: str, data: bytes) -> None:
    staged = VERIFIER_DIR / f".{name}.tmp"
    staged.write_bytes(data)
    os.replace(staged, VERIFIER_DIR / name)


def _offline(path: str) -> int:
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        print(f"no grade: cut unreadable: {error.strerror}", file=sys.stderr)
        return 1
    try:
        observation = evaluate(parse_cut(data))
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    print(json.dumps(observation, sort_keys=True))
    return 0


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2:
        return _offline(argv[1])
    if len(argv) != 1:
        print("usage: verify.py [CUT_JSON]", file=sys.stderr)
        return 2
    try:
        expected = expected_world(os.environ)
        observation = evaluate(parse_cut(freeze_world()))
        _check_expected(observation, expected)
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    reward = observation["reward"]
    assert _exact_int(reward) and reward in (0, 1)
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    _publish(OBSERVATION_NAME, json.dumps(observation).encode("ascii") + b"\n")
    _publish(REWARD_NAME, json.dumps({"reward": reward}).encode("ascii") + b"\n")
    print(f"reward={reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
