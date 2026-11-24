"""Freeze the ship-decision-receipt world, replay its cut, and publish the observation.

Installed as `tests/decision_verify.py` beside the pinned `tests/inflight_verify.py`
and stock `tests/verify.py`, whose strict parser, replay, freeze client and publisher
it reuses. `python3 decision_verify.py CUT_JSON` regrades a published
`ship-decision-receipt-cut.json`; ship-inflight v1 cuts are refused as unsupported.
"""

import json
import os
import sys
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import cast

HERE = Path(__file__).resolve().parent
INFLIGHT_PATHS = (
    HERE / "inflight_verify.py",
    HERE.parent.parent / "ship_inflight" / "inflight_verify.py",
)


def _load_inflight() -> ModuleType:
    path = next((path for path in INFLIGHT_PATHS if path.is_file()), None)
    if path is None:
        raise ImportError("pinned ship-inflight inflight_verify.py is missing")
    spec = spec_from_file_location("ship_inflight_pinned_verify", path)
    if spec is None or spec.loader is None:
        raise ImportError("pinned ship-inflight inflight_verify.py is not loadable")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inflight = _load_inflight()
stock: ModuleType = inflight.stock
CutError: type[Exception] = inflight.CutError

OBSERVATION_NAME = "ship-decision-receipt.json"
CUT_NAME = "ship-decision-receipt-cut.json"
REWARD_NAME = inflight.REWARD_NAME
CUT_SCHEMA = "harbor.ship-decision-receipt.cut/v1"
OBSERVATION_SCHEMA = "harbor.ship-decision-receipt.observation/v1"
CITATION = "cited_at_decision"
CITABLE = ("original", "draft")
STATUSES = (
    "no_decision",
    "decided_without_returned_reference",
    "returned_not_cited",
    "cited_at_decision",
)
CUT_KEYS = frozenset((*inflight.CUT_KEYS, CITATION))
ADDED_KEYS = frozenset((CITATION, "citation_status"))

assert CITATION not in inflight.CUT_KEYS
assert len(CUT_KEYS) == len(inflight.CUT_KEYS) + 1
assert set(CITABLE) == set(stock.CHOICES["inbox"]) - {"none"}
assert CUT_SCHEMA not in (inflight.CUT_SCHEMA, stock.CUT_SCHEMA)
assert OBSERVATION_SCHEMA != inflight.OBSERVATION_SCHEMA
assert CUT_NAME != inflight.CUT_NAME and OBSERVATION_NAME != inflight.OBSERVATION_NAME
assert len(set(STATUSES)) == len(STATUSES) == 4

Event = tuple[int, str]


def _check_header(cut: object) -> dict[str, object]:
    if type(cut) is not dict:
        raise CutError("cut fields do not match the contract")
    header = cast(dict[str, object], cut)
    if header.get("schema") in (inflight.CUT_SCHEMA, stock.CUT_SCHEMA):
        raise CutError("cut is an earlier unsupported schema with no citation field")
    if header.keys() != CUT_KEYS:
        raise CutError("cut fields do not match the contract")
    if header["schema"] != CUT_SCHEMA:
        raise CutError("cut schema does not match the contract")
    return header


def _project(header: dict[str, object]) -> dict[str, object]:
    assert header.keys() == CUT_KEYS and header["schema"] == CUT_SCHEMA
    projected = {key: header[key] for key in inflight.CUT_KEYS}
    projected["schema"] = inflight.CUT_SCHEMA
    assert projected.keys() == inflight.CUT_KEYS
    return projected


def _replayed_events(observation: dict[str, object]) -> dict[str, Event]:
    events = cast(list[dict[str, object]], observation["events"])
    seen = {
        cast(str, event["kind"]): (cast(int, event["seq"]), cast(str, event["choice"]))
        for event in events
    }
    assert len(seen) == len(events) <= len(stock.CHOICES)
    return seen


def returned_before_decision(seen: dict[str, Event]) -> str | None:
    inbox, resolve = seen.get("inbox"), seen.get("resolve")
    if inbox is None or resolve is None or inbox[1] not in CITABLE:
        return None
    assert inbox[0] != resolve[0]
    return inbox[1] if inbox[0] < resolve[0] else None


def _check_citation(raw: object, decided: bool, returned: str | None) -> str | None:
    assert returned is None or decided
    if raw is None:
        return None
    cited: str = stock._member(raw, CITABLE, "cut cited_at_decision")
    if not decided:
        raise CutError("cut citation lacks a resolve event")
    if returned is None:
        raise CutError("cut citation lacks an inbox reference returned before resolve")
    if cited != returned:
        raise CutError("cut citation differs from the reference returned first")
    return cited


def citation_status(decided: bool, returned: str | None, cited: str | None) -> str:
    assert returned is None or decided
    assert cited is None or cited == returned
    if not decided:
        return "no_decision"
    if returned is None:
        return "decided_without_returned_reference"
    return "returned_not_cited" if cited is None else "cited_at_decision"


def evaluate(cut: object) -> dict[str, object]:
    header = _check_header(cut)
    base = cast(dict[str, object], inflight.evaluate(_project(header)))
    seen = _replayed_events(base)
    decided = "resolve" in seen
    assert decided == (base["relief_action"] != "absent")
    returned = returned_before_decision(seen)
    cited = _check_citation(header[CITATION], decided, returned)
    status = citation_status(decided, returned, cited)
    assert status in STATUSES and ADDED_KEYS.isdisjoint(base)
    observation = {**base, "schema": OBSERVATION_SCHEMA, CITATION: cited}
    observation["citation_status"] = status
    assert observation.keys() == base.keys() | ADDED_KEYS
    assert all(observation[key] == base[key] for key in base if key != "schema")
    return observation


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
        print("usage: decision_verify.py [CUT_JSON]", file=sys.stderr)
        return 2
    try:
        expected = inflight.expected_world(os.environ)
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
