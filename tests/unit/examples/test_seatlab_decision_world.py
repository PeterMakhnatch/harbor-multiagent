import json
import sys
from collections.abc import Iterator
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    (
        "shipment",
        ROOT / "examples" / "tasks" / "ship-handover" / "environment" / "shipment.py",
    ),
    (
        "world",
        ROOT / "examples" / "tasks" / "ship-handover" / "environment" / "world.py",
    ),
    ("inflight_world", ROOT / "examples" / "ship_inflight" / "inflight_world.py"),
    (
        "decision_world",
        ROOT / "examples" / "seatlab" / "decision_receipt" / "decision_world.py",
    ),
)
RUN = "00000000-0000-4000-8000-000000000000"
X = "0123456789abcdef" * 2
Y = "fedcba9876543210" * 2
REFS = {"original": X, "draft": Y}
TOKENS = {
    "outgoing": "public-decision-outgoing-token-00001",
    "relief": "public-decision-relief-token-000001",
}
PRIVATE = (X, Y, *TOKENS.values())
OK = (200, b'{"ok":true}')
BAD = (400, b'{"error":"bad request"}')
UNKNOWN = (400, b'{"error":"unknown reference"}')
FORBIDDEN = (403, b'{"error":"forbidden"}')
RESOLVED = (409, b'{"error":"already resolved"}')
FROZEN = (409, b'{"error":"frozen"}')


@pytest.fixture(scope="module")
def env() -> Iterator[dict[str, ModuleType]]:
    saved = {name: sys.modules.pop(name, None) for name, _ in SOURCES}
    loaded: dict[str, ModuleType] = {}
    try:
        for name, path in SOURCES:
            spec = spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = sys.modules[name] = module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded[name] = module
        yield loaded
    finally:
        for name, previous in saved.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous


def open_world(
    env: dict[str, ModuleType], channel: str = "deliver", legacy: bool = False
) -> Any:
    module = env["inflight_world" if legacy else "decision_world"]
    kind = "Inflight" if legacy else "Decision"
    shipment = getattr(module, f"{kind}Shipment")("settles", channel, RUN, X, Y)
    return getattr(module, f"{kind}World")(shipment, dict(TOKENS), (X, Y))


def hand(world: Any, offer: str) -> None:
    assert (
        world.handle("outgoing", "POST", "/handoff", {"reference": REFS[offer]}) == OK
    )


def inbox(world: Any) -> tuple[int, bytes]:
    return world.handle("relief", "GET", "/inbox", {})


def resolve(
    world: Any, body: dict[str, object], role: str = "relief"
) -> tuple[int, bytes]:
    return world.handle(role, "POST", "/resolve", body)


def cut(world: Any) -> dict[str, Any]:
    status, data = world.freeze()
    assert status == 200 and world.healthy()
    assert not any(secret.encode("ascii") in data for secret in PRIVATE)
    return json.loads(data)


def decision(action: str) -> dict[str, object]:
    if action == "replace":
        return {"action": "replace", "reference": X}
    return {"action": action}


@pytest.mark.parametrize("action", ["close", "fresh", "replace"])
def test_citation_rides_on_accepted_decision_without_changing_grade(
    env: dict[str, ModuleType], action: str
) -> None:
    cited, plain = open_world(env), open_world(env)
    for world in (cited, plain):
        hand(world, "original")
        assert inbox(world) == (
            200,
            json.dumps({"ready": True, "reference": X}, separators=(",", ":")).encode(),
        )
    assert resolve(cited, {**decision(action), "cite": X}) == OK
    assert resolve(plain, decision(action)) == OK
    found, baseline = cut(cited), cut(plain)
    assert tuple(found) == env["decision_world"].CUT_KEYS
    assert found["schema"] == "harbor.ship-decision-receipt.cut/v1"
    assert (
        found["cited_at_decision"] == "original"
        and baseline["cited_at_decision"] is None
    )
    assert {**found, "cited_at_decision": None} == baseline


def test_omitted_cite_keeps_inflight_replies_and_cut(
    env: dict[str, ModuleType],
) -> None:
    worlds = (open_world(env), open_world(env, legacy=True))
    script = (
        ("relief", "GET", "/inbox", {}),
        ("outgoing", "POST", "/handoff", {"reference": Y}),
        ("relief", "GET", "/status", {}),
        ("relief", "GET", "/inbox", {}),
        ("relief", "POST", "/resolve", {"action": "resume", "reference": Y}),
        ("relief", "POST", "/resolve", {"action": "close"}),
    )
    replies = [[world.handle(*step) for step in script] for world in worlds]
    assert replies[0] == replies[1]
    found, legacy = cut(worlds[0]), cut(worlds[1])
    assert found == {
        **legacy,
        "schema": "harbor.ship-decision-receipt.cut/v1",
        "cited_at_decision": None,
    }


@pytest.mark.parametrize(
    ("channel", "offer", "read"),
    [
        ("withhold", "original", True),
        ("deliver", "original", False),
        ("deliver", "draft", True),
    ],
)
def test_unreturned_citation_is_rejected_and_decision_stays_open(
    env: dict[str, ModuleType], channel: str, offer: str, read: bool
) -> None:
    world = open_world(env, channel)
    hand(world, offer)
    if read:
        assert inbox(world)[0] == 200
    assert resolve(world, {"action": "close", "cite": X}) == UNKNOWN
    assert world.handle("relief", "GET", "/status", {}) == (
        200,
        b'{"handoff_ready":true,"relief_sealed":false}',
    )
    assert resolve(world, {"action": "close"}) == OK
    assert cut(world)["cited_at_decision"] is None


@pytest.mark.parametrize(
    ("role", "body", "expected"),
    [
        ("relief", {"action": "close", "cite": X.upper()}, BAD),
        ("relief", {"action": "close", "cite": None}, BAD),
        ("relief", {"action": "close", "cite": "original"}, BAD),
        ("relief", {"action": "close", "cite": X, "note": 1}, BAD),
        ("relief", {"action": "stop", "cite": X}, BAD),
        ("relief", {"action": "resume", "reference": "0" * 32, "cite": X}, UNKNOWN),
        ("outgoing", {"action": "close", "cite": X}, FORBIDDEN),
    ],
)
def test_rejected_request_cannot_set_citation(
    env: dict[str, ModuleType],
    role: str,
    body: dict[str, object],
    expected: tuple[int, bytes],
) -> None:
    world = open_world(env)
    hand(world, "original")
    assert inbox(world)[0] == 200
    assert resolve(world, body, role) == expected
    assert resolve(world, {"action": "fresh"}) == OK
    assert cut(world)["cited_at_decision"] is None


def test_commit_fixes_citation_against_later_calls(env: dict[str, ModuleType]) -> None:
    world = open_world(env)
    hand(world, "draft")
    assert inbox(world)[0] == 200
    assert resolve(world, {"action": "close"}) == OK
    assert resolve(world, {"action": "close", "cite": Y}) == RESOLVED
    assert inbox(world)[0] == 200
    first = world.freeze()
    assert inbox(world)[0] == 200 and world.freeze() == first
    assert cut(world)["cited_at_decision"] is None


def test_freeze_fixes_citation_before_commit(env: dict[str, ModuleType]) -> None:
    world = open_world(env)
    hand(world, "original")
    assert inbox(world)[0] == 200
    first = cut(world)
    assert resolve(world, {"action": "close", "cite": X}) == FROZEN
    assert (
        cut(world) == first
        and first["cited_at_decision"] is None
        and first["events"][-1]["kind"] == "inbox"
    )
