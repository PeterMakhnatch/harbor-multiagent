import json
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ..decision_boundary.core import BoundaryError, World, analyze
from ..decision_boundary.pair import partition

POLICIES = ("full", "oldest", "newest", "none", "refresh")
_MAX_CASES = 1024
_MAX_FRAMES = 256
_MAX_DEPTH = 32
_MAX_NODES = 100_000
_MAX_BYTES = 1 << 20
_MAX_TOTAL_BYTES = 32 << 20
_FRAME_FIELDS = {"method", "path", "body", "status", "reply"}


@dataclass(frozen=True)
class Episode:
    name: str
    mass: int
    instruction: str
    history: tuple[dict[str, Any], ...]
    refresh: dict[str, Any]
    scores: dict[str, int]


@dataclass(frozen=True)
class Selector:
    method: str
    path: str
    sequence: tuple[str, ...]
    field: str


def _label(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value.isprintable()
        and value == value.strip()
    )


def _validate_selector(selector: Selector) -> None:
    if (
        type(selector) is not Selector
        or not all(
            _label(value) for value in (selector.method, selector.path, selector.field)
        )
        or type(selector.sequence) is not tuple
        or len(selector.sequence) > _MAX_DEPTH
        or not all(_label(part) for part in selector.sequence)
    ):
        raise BoundaryError("invalid selector")


def _validate_json(root: Any) -> None:
    pending = [(root, 0)]
    nodes = 1
    characters = 0
    while pending:
        value, depth = pending.pop()
        if depth > _MAX_DEPTH:
            raise BoundaryError("JSON depth limit exceeded")
        kind = type(value)
        if kind is str:
            characters += len(value)
            if characters > _MAX_BYTES:
                raise BoundaryError("JSON text limit exceeded")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise BoundaryError("invalid JSON string") from None
        elif kind is dict or kind is list:
            nodes += len(value) * (2 if kind is dict else 1)
            if nodes > _MAX_NODES:
                raise BoundaryError("JSON node limit exceeded")
            if kind is dict:
                for key, item in value.items():
                    if type(key) is not str:
                        raise BoundaryError("invalid JSON key")
                    pending.extend(((key, depth + 1), (item, depth + 1)))
            else:
                pending.extend((item, depth + 1) for item in value)
        elif kind is int:
            if not -(1 << 63) <= value < (1 << 63):
                raise BoundaryError("JSON integer outside limits")
        elif kind is float:
            if not math.isfinite(value):
                raise BoundaryError("nonfinite JSON number")
        elif value is not None and kind is not bool:
            raise BoundaryError("invalid JSON value")


def _validate_frame(frame: dict[str, Any]) -> None:
    if (
        type(frame) is not dict
        or frame.keys() != _FRAME_FIELDS
        or not _label(frame["method"])
        or not _label(frame["path"])
        or type(frame["status"]) is not int
        or not 100 <= frame["status"] <= 599
    ):
        raise BoundaryError("invalid reader frame")


def _matches(frame: dict[str, Any], selector: Selector) -> bool:
    return (frame["method"], frame["path"]) == (selector.method, selector.path)


def _values(frame: dict[str, Any], selector: Selector) -> list[str]:
    if not 200 <= frame["status"] < 300:
        raise BoundaryError("matching reader frame was not successful")
    collection = frame["reply"]
    for part in selector.sequence:
        if type(collection) is not dict or part not in collection:
            raise BoundaryError("missing matching reply sequence")
        collection = collection[part]
    if type(collection) is not list:
        raise BoundaryError("matching reply sequence must be a list")
    values = []
    for item in collection:
        if type(item) is not dict or selector.field not in item:
            raise BoundaryError("missing matching candidate field")
        value = item[selector.field]
        if type(value) is not str or not value:
            raise BoundaryError("matching candidate must be a nonempty string")
        values.append(value)
    return values


def _notes(case: Episode, selector: Selector) -> tuple[str, str]:
    _validate_selector(selector)
    if (
        type(case) is not Episode
        or type(case.instruction) is not str
        or not case.instruction
        or type(case.history) is not tuple
        or not 2 <= len(case.history) <= _MAX_FRAMES
    ):
        raise BoundaryError("invalid episode or reader history")
    _validate_json([case.instruction, *case.history, case.refresh])
    first = last = None
    for index, frame in enumerate(case.history):
        _validate_frame(frame)
        if not _matches(frame, selector):
            continue
        values = _values(frame, selector)
        if index < len(case.history) - 1 and values:
            if first is None:
                first = values[0]
            last = values[-1]
    _validate_frame(case.refresh)
    if not _matches(case.refresh, selector):
        raise BoundaryError("refresh is not a matching reader frame")
    _values(case.refresh, selector)
    if first is None or last is None:
        raise BoundaryError("no prior matching reader candidate")
    return first, last


def _encode(value: Any) -> bytes:
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if len(data) > _MAX_BYTES:
        raise BoundaryError("observation byte limit exceeded")
    return data


def _memory(case: Episode, policy: str, notes: tuple[str, str]) -> Any:
    if policy == "full":
        return list(case.history[:-1])
    if policy == "oldest":
        return {"note": notes[0]}
    if policy == "newest":
        return {"note": notes[1]}
    return []


def _packet(case: Episode, policy: str, memory: Any) -> bytes:
    return _encode(
        {
            "instruction": case.instruction,
            "current": case.history[-1],
            "memory": memory,
            "additional_read": case.refresh if policy == "refresh" else None,
        }
    )


def packet(case: Episode, policy: str, selector: Selector) -> bytes:
    if type(policy) is not str or policy not in POLICIES:
        raise BoundaryError("unknown retention policy")
    return _packet(case, policy, _memory(case, policy, _notes(case, selector)))


def qualify(cases: list[Episode], scale: int, selector: Selector) -> dict[str, Any]:
    if type(cases) is not list or not 1 <= len(cases) <= _MAX_CASES:
        raise BoundaryError("invalid episode count")
    worlds: dict[str, list[World]] = {policy: [] for policy in POLICIES}
    sizes = dict.fromkeys(POLICIES, 0)
    equal_notes = equal_packets = True
    for case in cases:
        notes = _notes(case, selector)
        memories = {policy: _memory(case, policy, notes) for policy in POLICIES}
        packets = {
            policy: _packet(case, policy, memories[policy]) for policy in POLICIES
        }
        equal_notes &= len(_encode(memories["oldest"])) == len(
            _encode(memories["newest"])
        )
        equal_packets &= len(packets["oldest"]) == len(packets["newest"])
        for policy, data in packets.items():
            sizes[policy] += len(data)
            if sizes[policy] > _MAX_TOTAL_BYTES:
                raise BoundaryError("aggregate observation limit exceeded")
            worlds[policy].append(World(case.name, case.mass, data, case.scores))
    analyses = {policy: analyze(rows, scale) for policy, rows in worlds.items()}
    values = {
        policy: Fraction(result["observed"]) for policy, result in analyses.items()
    }
    pairs = (
        ("newest", "full"),
        ("none", "oldest"),
        ("oldest", "newest"),
        ("none", "full"),
        ("none", "refresh"),
    )
    relations = {}
    for before, after in pairs:
        relation, _ = partition(worlds[before], worlds[after])
        gain = values[after] - values[before]
        if (
            (relation == "equivalent" and gain != 0)
            or (relation == "refinement" and gain < 0)
            or (relation == "coarsening" and gain > 0)
        ):
            raise BoundaryError("partition value monotonicity violated")
        if (before, after) != ("oldest", "newest") and relation not in (
            "equivalent",
            "refinement",
        ):
            raise BoundaryError("packet construction violated refinement")
        relations[f"{before}_to_{after}"] = relation
    retention_gain = values["newest"] - values["oldest"]
    lost_value = values["full"] - values["none"]
    refresh_gain = values["refresh"] - values["none"]
    pattern = (
        equal_notes
        and equal_packets
        and relations["newest_to_full"] == "equivalent"
        and values["newest"] == values["full"]
        and relations["none_to_oldest"] == "equivalent"
        and values["none"] == values["oldest"]
        and relations["oldest_to_newest"] == "refinement"
        and retention_gain > 0
    )
    return {
        "values": {policy: str(value) for policy, value in values.items()},
        "equal_note_bytes": equal_notes,
        "equal_packet_bytes": equal_packets,
        "retention_pattern": pattern,
        "refresh_restores_full_value": lost_value > 0 and refresh_gain == lost_value,
        "relations": relations,
        "observations": {
            policy: result["observations"] for policy, result in analyses.items()
        },
        "worlds": analyses["full"]["worlds"],
        "actions": analyses["full"]["actions"],
        "retention_gain": str(retention_gain),
        "lost_value": str(lost_value),
        "refresh_gain": str(refresh_gain),
        "limits": "Finite declared reader windows and native score matrices only; supplied frames are not independently authenticated. No model evidence or token-cost claim.",
    }
