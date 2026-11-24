"""Freeze the ship-inflight world, replay its cut, and publish the drained observation.

Installed as `tests/inflight_verify.py` beside the stock `tests/verify.py`, whose
strict bounded parser, event replay, canonical run check, freeze client and
publisher it reuses. `python3 inflight_verify.py CUT_JSON` regrades a published
`ship-inflight-cut.json`.
"""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import cast

HERE = Path(__file__).resolve().parent
STOCK_PATHS = (
    HERE / "verify.py",
    HERE.parent / "tasks" / "ship-handover" / "tests" / "verify.py",
)


def _load_stock() -> ModuleType:
    path = next((path for path in STOCK_PATHS if path.is_file()), None)
    if path is None:
        raise ImportError("stock ship-handover verify.py is missing")
    spec = spec_from_file_location("ship_handover_stock_verify", path)
    if spec is None or spec.loader is None:
        raise ImportError("stock ship-handover verify.py is not loadable")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stock = _load_stock()
CutError: type[Exception] = stock.CutError

OBSERVATION_NAME = "ship-inflight.json"
CUT_NAME = "ship-inflight-cut.json"
REWARD_NAME = stock.REWARD_NAME
CUT_SCHEMA = "harbor.ship-inflight.cut/v1"
OBSERVATION_SCHEMA = "harbor.ship-inflight.observation/v1"
FATES = ("settles", "fails")
CHANNELS = ("deliver", "withhold")
REPLACEABLE = ("original", "draft")
IDENTITIES = ("original", "draft", "fresh")
OUTCOMES = ("omission", "exactly_one", "duplicate")
SETTLES = {"close": (), "fresh": ("fresh",), "original": (), "draft": ("draft",)}
JOINS = {"original": "join-original", "draft": "join-draft"}
MAX_SETTLED = 1
MAX_DRAINED = 2
CUT_KEYS = frozenset(
    ("schema", "run", "fate", "channel", "events", "replace", "settled", "drained")
)

assert tuple(CHANNELS) == tuple(stock.CHANNELS)
assert tuple(SETTLES) == tuple(stock.CHOICES["resolve"])
assert stock.MAX_CUT_BYTES > 0

Event = tuple[int, str]


def expected_world(environ: Mapping[str, str]) -> tuple[str, str]:
    fate = environ.get("SH_FATE")
    channel = environ.get("SH_CHANNEL")
    if fate is None or channel is None:
        raise CutError("expected world is missing from the main environment")
    return (
        stock._member(fate, FATES, "expected fate"),
        stock._member(channel, CHANNELS, "expected channel"),
    )


def _check_header(cut: object) -> dict[str, object]:
    if type(cut) is not dict or cast(dict[str, object], cut).keys() != CUT_KEYS:
        raise CutError("cut fields do not match the contract")
    header = cast(dict[str, object], cut)
    if header["schema"] != CUT_SCHEMA:
        raise CutError("cut schema does not match the contract")
    stock._run(header["run"])
    stock._member(header["fate"], FATES, "cut fate")
    stock._member(header["channel"], CHANNELS, "cut channel")
    return header


def _check_replace(raw: object, resolve: Event | None) -> str | None:
    if raw is None:
        return None
    replaced = stock._member(raw, REPLACEABLE, "cut replace")
    if resolve is None or resolve[1] != "fresh":
        raise CutError("cut replace lacks a fresh resolve event")
    return replaced


def replay_settlement(
    fate: str, resolve: Event | None, replaced: str | None
) -> tuple[list[str], list[str]]:
    assert fate in FATES and replaced in (None, *REPLACEABLE)
    assert replaced is None or (resolve is not None and resolve[1] == "fresh")
    settled = [] if resolve is None else list(SETTLES[resolve[1]])
    drained = list(settled)
    if fate == "settles" and replaced != "original":
        drained.append("original")
    assert len(settled) <= MAX_SETTLED and len(drained) <= MAX_DRAINED
    assert drained[: len(settled)] == settled
    assert len(set(drained)) == len(drained)
    return settled, drained


def _check_ledger(raw: object, expected: list[str], bound: int, what: str) -> list[str]:
    if type(raw) is not list or len(cast(list[object], raw)) > bound:
        raise CutError(f"cut {what} is not a bounded list")
    items = [
        stock._member(item, IDENTITIES, f"cut {what} item")
        for item in cast(list[object], raw)
    ]
    if items != expected:
        raise CutError(f"cut {what} does not match the replayed settlement")
    return items


def _relief_action(resolve: Event | None, replaced: str | None) -> str:
    if resolve is None:
        return "absent"
    if replaced is not None:
        return f"replace-{replaced}"
    return JOINS.get(resolve[1], resolve[1])


def evaluate(cut: object) -> dict[str, object]:
    header = _check_header(cut)
    fate, channel = cast(str, header["fate"]), cast(str, header["channel"])
    seen: dict[str, Event] = stock.replay_events(header["events"], channel)
    handoff, resolve = seen.get("handoff"), seen.get("resolve")
    replaced = _check_replace(header["replace"], resolve)
    expected_settled, expected_drained = replay_settlement(fate, resolve, replaced)
    settled = _check_ledger(header["settled"], expected_settled, MAX_SETTLED, "settled")
    drained = _check_ledger(header["drained"], expected_drained, MAX_DRAINED, "drained")
    grade_before_drain = int(len(settled) == 1)
    outcome = OUTCOMES[min(len(drained), 2)]
    reward = int(outcome == "exactly_one")
    assert reward == int(len(set(drained)) == 1)
    assert grade_before_drain in (0, 1) and reward in (0, 1)
    return {
        "schema": OBSERVATION_SCHEMA,
        "run": header["run"],
        "fate": fate,
        "channel": channel,
        "events": [
            {"seq": seq, "kind": kind, "choice": choice}
            for kind, (seq, choice) in seen.items()
        ],
        "replace": replaced,
        "settled": settled,
        "drained": drained,
        "grade_before_drain": grade_before_drain,
        "reward": reward,
        "outcome": outcome,
        "outgoing_handoff": "absent" if handoff is None else handoff[1],
        "relief_action": _relief_action(resolve, replaced),
    }


def _offline(path: str) -> int:
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(stock.MAX_CUT_BYTES + 1)
    except OSError as error:
        print(f"no grade: cut unreadable: {error.strerror}", file=sys.stderr)
        return 1
    try:
        observation = evaluate(stock.parse_cut(data))
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    print(json.dumps(observation))
    return 0


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2:
        return _offline(argv[1])
    if len(argv) != 1:
        print("usage: inflight_verify.py [CUT_JSON]", file=sys.stderr)
        return 2
    try:
        expected = expected_world(os.environ)
        cut: bytes = stock.freeze_world()
        observation = evaluate(stock.parse_cut(cut))
        if (observation["fate"], observation["channel"]) != expected:
            raise CutError("cut world does not match the expected world")
    except CutError as error:
        print(f"no grade: {error}", file=sys.stderr)
        return 1
    reward = observation["reward"]
    assert type(reward) is int and reward in (0, 1)
    assert 0 < len(cut) <= stock.MAX_CUT_BYTES
    stock.VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    stock._publish(CUT_NAME, cut)
    stock._publish(OBSERVATION_NAME, json.dumps(observation).encode("ascii") + b"\n")
    stock._publish(REWARD_NAME, json.dumps({"reward": reward}).encode("ascii") + b"\n")
    print(f"reward={reward}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
