from collections.abc import Callable
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast
from uuid import UUID

from .policy import render
from .records import NativeCase

_TASK = Path(__file__).resolve().parents[2] / "tasks" / "joint-reopen"
_SCAN_LIMIT = 16_384
_ROLES = ("east", "west")


def _load(name: str, path: Path) -> ModuleType:
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load native module from {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_meeting = _load("coupled_retention_meeting", _TASK / "environment" / "meeting.py")
_verify = _load("coupled_retention_verify", _TASK / "tests" / "verify.py")


class _Meeting(Protocol):
    def handle(
        self, role: str, method: str, path: str, body: dict[str, object]
    ) -> tuple[int, dict[str, object]]: ...

    def freeze(self) -> dict[str, object]: ...


Meeting = cast(Callable[[int, str, str, str], _Meeting], _meeting.Meeting)
permutation = cast(Callable[[int], tuple[str, ...]], _meeting.permutation)
MAX_SEED = cast(int, _meeting.MAX_SEED)


def balanced_seeds(start: int) -> tuple[int, ...]:
    if type(start) is not int or not 0 <= start <= MAX_SEED:
        raise ValueError("start must be a native seed")
    incumbent, *alternatives = permutation(start)[:4]
    labels = frozenset(alternatives)
    found: dict[tuple[str, ...], int] = {}
    for seed in range(start, min(start + _SCAN_LIMIT, MAX_SEED + 1)):
        order = permutation(seed)
        triple = order[1:4]
        if order[0] == incumbent and frozenset(triple) == labels:
            found.setdefault(triple, seed)
            if len(found) == 6:
                return tuple(found[triple] for triple in sorted(found))
    raise ValueError("no balanced native cohort within the bounded seed scan")


def _call(
    world: _Meeting,
    role: str,
    method: str,
    path: str,
    body: dict[str, object],
) -> dict[str, object]:
    status, reply = world.handle(role, method, path, body)
    if status != 200:
        raise RuntimeError(f"native {role} {method} {path}: {status} {reply}")
    return reply


def _stage(
    world: _Meeting, sender: str
) -> tuple[tuple[bytes, bytes], tuple[bytes, bytes], tuple[str, ...]]:
    briefs = {role: _call(world, role, "GET", "/brief", {}) for role in _ROLES}
    for role in _ROLES:
        _call(world, role, "POST", "/endorse", {"slot": briefs[role]["incumbent"]})
    cards = {
        role: tuple(cast(list[str], _call(world, role, "GET", "/card", {})["slots"]))
        for role in _ROLES
    }
    for role in _ROLES:
        report = list(cards[role]) if sender == "truthful" else []
        _call(world, role, "POST", "/report", {"slots": report})
    inboxes = {role: _call(world, role, "GET", "/inbox", {}) for role in _ROLES}
    packets: dict[str, tuple[bytes, bytes]] = {}
    actions = tuple(cast(list[str], briefs["east"]["options"]))
    for role in _ROLES:
        brief, inbox = briefs[role], inboxes[role]
        options = tuple(cast(list[str], brief["options"]))
        if (
            brief["role"] != role
            or options != actions
            or inbox["delivered"] is not True
        ):
            raise RuntimeError("unexpected native role view")
        peer = tuple(cast(list[str], inbox["slots"]))
        args = (role, cast(str, brief["incumbent"]), options, cards[role])
        packets[role] = (render(*args, None), render(*args, peer))
    return packets["east"], packets["west"], actions


def grade_pair(world: _Meeting, east: str, west: str) -> int:
    branch = deepcopy(world)
    _call(branch, "east", "POST", "/vote", {"slot": east})
    _call(branch, "west", "POST", "/vote", {"slot": west})
    observation = cast(dict[str, object], _verify.evaluate(branch.freeze()))
    roles = cast(dict[str, dict[str, object]], observation["roles"])
    if not all(role["peer_report_before_vote"] is True for role in roles.values()):
        raise RuntimeError("native branch lacks prior peer report delivery")
    reward = observation["reward"]
    if type(reward) is not int or reward not in (0, 1):
        raise RuntimeError("native verifier did not return a binary reward")
    return reward


def prepare_case(
    start: int, sender: str, index: int
) -> tuple[_Meeting, tuple[bytes, bytes], tuple[bytes, bytes], tuple[str, ...]]:
    if (
        sender not in ("truthful", "pass")
        or type(index) is not int
        or not 0 <= index < 6
    ):
        raise ValueError("invalid ballot assignment")
    seed = balanced_seeds(start)[index]
    world = Meeting(seed, "both", "deliver", str(UUID(int=seed)))
    east, west, actions = _stage(world, sender)
    return world, east, west, actions


def native_cases(start: int, sender: str) -> list[NativeCase]:
    if sender not in ("truthful", "pass"):
        raise ValueError("sender must be truthful or pass")
    cases = []
    for seed in balanced_seeds(start):
        world = Meeting(seed, "both", "deliver", str(UUID(int=seed)))
        east, west, actions = _stage(world, sender)
        scores = tuple(tuple(grade_pair(world, a, b) for b in actions) for a in actions)
        cases.append(NativeCase(seed, east, west, actions, scores))
    return cases
