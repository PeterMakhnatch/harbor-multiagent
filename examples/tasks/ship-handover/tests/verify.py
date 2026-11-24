"""Freeze the ship-handover world, replay its cut, and publish the observation."""

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
OBSERVATION_NAME = "ship-handover.json"
REWARD_NAME = "reward.json"
CUT_SCHEMA = "harbor.ship-handover.cut/v1"
OBSERVATION_SCHEMA = "harbor.ship-handover.observation/v1"
PRIORS = ("committed", "dropped")
CHANNELS = ("deliver", "withhold")
CHOICES = {
    "handoff": ("original", "draft", "none"),
    "inbox": ("original", "draft", "none"),
    "resolve": ("close", "fresh", "original", "draft"),
}
IDENTITIES = ("original", "draft", "fresh")
OUTCOMES = ("omission", "exactly_one", "duplicate")
MAX_CUT_BYTES = 4096
CUT_KEYS = frozenset(("schema", "run", "prior", "channel", "events", "effects"))
EVENT_KEYS = frozenset(("seq", "kind", "choice"))

Event = tuple[int, str]


class CutError(Exception):
    pass


def _member(value: object, allowed: Sequence[str], what: str) -> str:
    if type(value) is not str or value not in allowed:
        raise CutError(f"{what} is not a contract value")
    return value


def expected_world(environ: Mapping[str, str]) -> tuple[str, str]:
    prior = environ.get("SH_PRIOR")
    channel = environ.get("SH_CHANNEL")
    if prior is None or channel is None:
        raise CutError("expected world is missing from the main environment")
    return (
        _member(prior, PRIORS, "expected prior"),
        _member(channel, CHANNELS, "expected channel"),
    )


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


def _check_inbox(handoff: Event | None, choice: str, channel: str, seq: int) -> None:
    if handoff is None:
        raise CutError(f"event {seq} inbox precedes the handoff")
    shown = handoff[1] if channel == "deliver" else "none"
    if choice != shown:
        raise CutError(f"event {seq} inbox contradicts the channel and handoff")


def replay_events(raw: object, channel: str) -> dict[str, Event]:
    if type(raw) is not list or len(cast(list[object], raw)) > len(CHOICES):
        raise CutError("cut events are not a bounded list")
    seen: dict[str, Event] = {}
    for seq, item in enumerate(cast(list[object], raw), start=1):
        if type(item) is not dict or cast(dict[str, object], item).keys() != EVENT_KEYS:
            raise CutError(f"event {seq} fields do not match the contract")
        event = cast(dict[str, object], item)
        if type(event["seq"]) is not int or event["seq"] != seq:
            raise CutError(f"event {seq} breaks the gapless order")
        kind = _member(event["kind"], tuple(CHOICES), f"event {seq} kind")
        if kind in seen:
            raise CutError(f"event {seq} repeats a {kind}")
        choice = _member(event["choice"], CHOICES[kind], f"event {seq} choice")
        if kind == "inbox":
            _check_inbox(seen.get("handoff"), choice, channel, seq)
        seen[kind] = (seq, choice)
    assert len(seen) == len(cast(list[object], raw)) <= len(CHOICES)
    return seen


def replay_ledger(prior: str, resolve: Event | None) -> tuple[list[str], list[str]]:
    staged = ["original"] if prior == "committed" else []
    accepted = list(staged)
    if resolve is not None and resolve[1] != "close" and resolve[1] not in accepted:
        accepted.append(resolve[1])
    assert accepted[: len(staged)] == staged and len(accepted) - len(staged) <= 1
    assert len(set(accepted)) == len(accepted) <= 2
    return staged, accepted


def _check_effects(raw: object, accepted: list[str]) -> list[str]:
    if type(raw) is not list or len(cast(list[object], raw)) > len(IDENTITIES):
        raise CutError("cut effects are not a bounded list")
    effects = [
        _member(item, IDENTITIES, "cut effect") for item in cast(list[object], raw)
    ]
    if effects != accepted:
        raise CutError("target ledger does not match the replayed effects")
    return effects


def _check_header(cut: object) -> dict[str, object]:
    if type(cut) is not dict or cast(dict[str, object], cut).keys() != CUT_KEYS:
        raise CutError("cut fields do not match the contract")
    header = cast(dict[str, object], cut)
    if header["schema"] != CUT_SCHEMA:
        raise CutError("cut schema does not match the contract")
    _run(header["run"])
    _member(header["prior"], PRIORS, "cut prior")
    _member(header["channel"], CHANNELS, "cut channel")
    return header


def _change(staged: list[str], accepted: list[str]) -> str:
    if len(accepted) == len(staged):
        return "unchanged"
    return "first_dispatch" if not staged else "duplicate_dispatch"


def evaluate(cut: object) -> dict[str, object]:
    header = _check_header(cut)
    prior, channel = cast(str, header["prior"]), cast(str, header["channel"])
    seen = replay_events(header["events"], channel)
    handoff, inbox, resolve = (
        seen.get("handoff"),
        seen.get("inbox"),
        seen.get("resolve"),
    )
    staged, accepted = replay_ledger(prior, resolve)
    effects = _check_effects(header["effects"], accepted)
    inbox_first = (
        None if resolve is None else inbox is not None and inbox[0] < resolve[0]
    )
    outcome = OUTCOMES[min(len(effects), 2)]
    reward = int(outcome == "exactly_one")
    assert reward == int(len(set(effects)) == 1)
    return {
        "schema": OBSERVATION_SCHEMA,
        "run": header["run"],
        "prior": prior,
        "channel": channel,
        "events": [
            {"seq": seq, "kind": kind, "choice": choice}
            for kind, (seq, choice) in seen.items()
        ],
        "effects": effects,
        "reward": reward,
        "outcome": outcome,
        "outgoing_handoff": "absent" if handoff is None else handoff[1],
        "relief_action": "absent" if resolve is None else resolve[1],
        "inbox_before_resolve": inbox_first,
        "reference_before_resolve": cast(Event, inbox)[1] if inbox_first else None,
        "effect_change": _change(staged, accepted),
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


def _offline(path: str) -> int:
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_CUT_BYTES + 1)
    except OSError as error:
        print(f"no grade: cut unreadable: {error.strerror}", file=sys.stderr)
        return 1
    try:
        observation = evaluate(parse_cut(data))
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    print(json.dumps(observation))
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
        if (observation["prior"], observation["channel"]) != expected:
            raise CutError("cut world does not match the expected world")
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    reward = observation["reward"]
    assert type(reward) is int and reward in (0, 1)
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    _publish(OBSERVATION_NAME, json.dumps(observation).encode("ascii") + b"\n")
    _publish(REWARD_NAME, json.dumps({"reward": reward}).encode("ascii") + b"\n")
    print(f"reward={reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
