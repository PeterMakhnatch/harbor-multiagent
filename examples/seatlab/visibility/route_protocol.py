"""Public prompts and response grammar for the registered routed reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .route_packet import Case

# The packet uses the listener's registered report order: scout_3, scout_1, scout_2.
_FIELDS = (("latency", "reliability"), ("latency", "safety"), ("reliability", "safety"))
_LENS_CODES = {"duplicate": "d", "complementary": "c"}
_STAGE_CODES = {"review": "r", "vote": "v", "evidence": "e"}
_MAX_RESPONSE_BYTES = 8192
_OBJECTIVE = (
    "Choose between route A and route B. Each route's total is the sum of its "
    "latency, reliability, and safety scores. Maximize the total. "
    "The available observations may be incomplete or incorrect. "
)


@dataclass(frozen=True)
class Block:
    policy: int
    round: int
    rotation: int
    swap: int
    rep: int

    @property
    def id(self) -> str:
        return (
            f"rv-p{self.policy}-r{self.round}-s{self.rotation}-m{self.swap}-d{self.rep}"
        )


def blocks() -> tuple[Block, ...]:
    return tuple(
        Block(policy, round_, rotation, swap, rep)
        for rep in range(2)
        for policy in range(3)
        for round_ in range(1, 7)
        for rotation in range(3)
        for swap in range(2)
    )


def lenses(block: Block) -> tuple[str, str]:
    parity = (
        block.policy + block.round - 1 + block.rotation + block.swap + block.rep
    ) % 2
    return ("complementary", "duplicate") if parity else ("duplicate", "complementary")


def channels(block: Block, lens: str) -> tuple[str, str]:
    if lens not in _LENS_CODES:
        raise ValueError("unknown review lens")
    # Both lens order and channel order reverse across repetitions. Do not
    # confound one lens with always seeing its evidence branch first.
    vote_first = (block.rotation + block.rep + (lens == "complementary")) % 2 == 0
    return ("vote", "evidence") if vote_first else ("evidence", "vote")


def run_id(block: Block, lens: str, stage: str) -> str:
    try:
        return f"{block.id}-{_LENS_CODES[lens]}-{_STAGE_CODES[stage]}"
    except KeyError as error:
        raise ValueError("unknown review lens or stage") from error


@dataclass(frozen=True)
class View:
    fields: tuple[str, str]
    a: tuple[int, int]
    b: tuple[int, int]


@dataclass(frozen=True)
class Review:
    vote: str
    a: tuple[int, int]
    b: tuple[int, int]


def view(case: Case, block: Block, lens: str) -> View:
    if lens not in _LENS_CODES:
        raise ValueError("unknown review lens")
    if (case.policy, case.round) != (block.policy, block.round):
        raise ValueError("case does not match block")
    reports = case.reports
    if reports is None or len(reports) != len(_FIELDS):
        raise ValueError("case has no complete reports")
    index = (block.rotation + (lens == "complementary")) % len(_FIELDS)
    a, b = reports[index]
    if block.swap:
        a, b = b, a
    return View(_FIELDS[index], a, b)


def _score_pair(values: object) -> tuple[int, int]:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError("expected two scores")
    a, b = values
    if (
        type(a) is not int
        or type(b) is not int
        or not (0 <= a <= 100 and 0 <= b <= 100)
    ):
        raise ValueError("scores must be integers from 0 to 100")
    return a, b


def _label(value: object) -> str:
    if not isinstance(value, str) or value not in ("A", "B"):
        raise ValueError("expected label A or B")
    return value


def _observations(
    fields: tuple[str, str], a: tuple[int, int], b: tuple[int, int]
) -> dict[str, object]:
    if fields not in _FIELDS:
        raise ValueError("unexpected observation fields")
    return {"fields": fields, "a": _score_pair(a), "b": _score_pair(b)}


def review_prompt(view: View) -> str:
    data = _observations(view.fields, view.a, view.b)
    return (
        _OBJECTIVE + "Use only these observations to recommend a route. "
        'Reply with exactly one JSON object of the shape {"vote":"A|B","a":[...,...],'
        '"b":[...,...]}. Use one label for vote. Copy both scores for each route in '
        "the order of the fields. Do not add text or keys.\n\nData:\n"
        + json.dumps(data, separators=(",", ":"))
    )


def decision_prompt(
    own: View, peer_fields: tuple[str, str], review: Review, channel: str
) -> str:
    peer: dict[str, object] = {"vote": _label(review.vote)}
    if channel == "evidence":
        peer.update(_observations(peer_fields, review.a, review.b))
    elif channel != "vote":
        raise ValueError("unknown decision channel")
    data = {"own": _observations(own.fields, own.a, own.b), "review": peer}
    return (
        _OBJECTIVE + "Use your observations and consider the recommendation. "
        "Treat repeated observations of an attribute as estimates of the same score. "
        "Count each attribute once per route. "
        'Reply with exactly one JSON object, {"choice":"A"} or {"choice":"B"}, '
        "and no other text.\n\nData:\n" + json.dumps(data, separators=(",", ":"))
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _response(text: str, keys: set[str]) -> dict[str, object]:
    if (
        not isinstance(text, str)
        or len(text) > _MAX_RESPONSE_BYTES
        or len(text.encode("utf-8")) > _MAX_RESPONSE_BYTES
    ):
        raise ValueError("expected a JSON response of at most 8192 bytes")
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except RecursionError as error:
        raise ValueError("JSON response is nested too deeply") from error
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError("unexpected JSON response keys")
    return cast(
        dict[str, object], value
    )  # The strict key set above contains strings only.


def parse_review(text: str) -> Review:
    value = _response(text, {"vote", "a", "b"})
    return Review(
        _label(value["vote"]), _score_pair(value["a"]), _score_pair(value["b"])
    )


def parse_decision(text: str) -> str:
    return _label(_response(text, {"choice"})["choice"])
