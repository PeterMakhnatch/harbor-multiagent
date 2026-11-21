from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cache, lru_cache
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any, Protocol, TypedDict, cast

SEEDS = tuple(range(5201, 5213))
ARMS = ("lifetime", "recent")

_BAYS = ("A", "B", "C", "D")
_DESKS = ("X", "Y")
_REGIMES = ("stable", "switch")
_STAGES = ("select", "review", "decide")
_SHIPMENTS = 20
_WARMUPS = 2
_SWITCH_STEP = 6
_MAX_REPLY_BYTES = 8192
_SCHEMA = "seatlab.feedback-protocol/v1"
_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tasks/dispatch-handoff/environment/seatworld.py"
)
_SELECT = (
    "Choose one desk to ask about available bays. For each desk, checked counts "
    "earlier reports compared with stock records. Exact counts the matches. "
    'Reply with only {"ask":"X"} or {"ask":"Y"}.'
)
_REVIEW = (
    "Copy the two available bay letters from the data. Reply with only a JSON object "
    'with the key "available" and an array of the two letters.'
)
_DECIDE = (
    "Choose an available bay with the highest utility. Use the reported bays and "
    'the utilities below. Reply with only a JSON object with the key "bay" and '
    "one bay letter."
)

type Pair = tuple[str, str]
type History = list[tuple[str, bool]]


def _validate_seed(seed: int) -> None:
    if type(seed) is not int or not 1 <= seed <= 2_147_483_647:
        raise ValueError("expected an integer seed from 1 to 2147483647")


@dataclass(frozen=True, slots=True)
class Block:
    seed: int
    regime: str
    rep: int

    def __post_init__(self) -> None:
        _validate_seed(self.seed)
        if type(self.regime) is not str or self.regime not in _REGIMES:
            raise ValueError("unknown feedback regime")
        if type(self.rep) is not int or self.rep not in (0, 1):
            raise ValueError("expected repetition 0 or 1")

    @property
    def key(self) -> str:
        return f"hz-s{self.seed}-{self.regime}-r{self.rep}"


def grid(seeds: tuple[int, ...] = SEEDS) -> tuple[Block, ...]:
    if type(seeds) is not tuple or len(seeds) > 64:
        raise ValueError("expected a tuple of at most 64 seeds")
    for seed in seeds:
        _validate_seed(seed)
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be distinct")
    return tuple(
        Block(seed, regime, rep)
        for seed in seeds
        for rep in (0, 1)
        for regime in (_REGIMES if (seed + rep) % 2 == 0 else _REGIMES[::-1])
    )


@dataclass(frozen=True, slots=True)
class Shipment:
    native_id: int
    before: Pair
    after: Pair
    utilities: tuple[int, ...]


class _NativeCase(TypedDict):
    id: int
    before: list[str]
    after: list[str]
    utilities: dict[str, int]


class _NativeEvidence(TypedDict):
    cases: list[_NativeCase]


class _NativeWorld(Protocol):
    def evidence(self) -> _NativeEvidence: ...


@cache
def _world_type() -> Callable[[dict[str, str | int]], _NativeWorld]:
    source = run_path(str(_WORLD_PATH))
    return cast(Callable[[dict[str, str | int]], _NativeWorld], source["World"])


@lru_cache(maxsize=64)
def _native_shipments(seed: int) -> tuple[Shipment, ...]:
    world = _world_type()(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": _SHIPMENTS,
            "condition": "pooled",
        }
    )
    return tuple(
        Shipment(
            row["id"],
            (row["before"][0], row["before"][1]),
            (row["after"][0], row["after"][1]),
            tuple(row["utilities"][bay] for bay in _BAYS),
        )
        for row in world.evidence()["cases"]
    )


def shipments(seed: int) -> tuple[Shipment, ...]:
    _validate_seed(seed)
    return _native_shipments(seed)[1::2]


def _validate_block(block: Block) -> None:
    if type(block) is not Block:
        raise ValueError("expected a feedback block")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def fingerprint(block: Block) -> str:
    _validate_block(block)
    payload = {
        "schema": _SCHEMA,
        "block": asdict(block),
        "shipments": [asdict(shipment) for shipment in _native_shipments(block.seed)],
    }
    return sha256(_json(payload).encode("ascii")).hexdigest()


def _role(stage: str) -> str:
    return "route_review" if stage == "review" else "route_decide"


@dataclass(frozen=True, slots=True)
class Turn:
    arm: str
    step: int
    stage: str
    role: str
    prompt: str

    def __post_init__(self) -> None:
        if (
            type(self.arm) is not str
            or self.arm not in ("warm", *ARMS)
            or type(self.step) is not int
            or not 0 <= self.step < _SHIPMENTS // 2
            or (self.arm == "warm") != (self.step < _WARMUPS)
            or type(self.stage) is not str
            or self.stage not in _STAGES
            or (self.arm == "warm" and self.stage == "select")
            or type(self.role) is not str
            or self.role != _role(self.stage)
        ):
            raise ValueError("invalid feedback turn")

    @property
    def suffix(self) -> str:
        return f"{self.arm}-{self.step}-{self.stage}"


def run_id(block: Block, turn: Turn) -> str:
    _validate_block(block)
    if type(turn) is not Turn:
        raise ValueError("expected a feedback turn")
    return f"{block.key}-{turn.suffix}"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _field(text: str, key: str) -> object:
    if (
        type(text) is not str
        or len(text) > _MAX_REPLY_BYTES
        or len(text.encode("utf-8")) > _MAX_REPLY_BYTES
    ):
        raise ValueError("invalid reply text")
    value = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict) or value.keys() != {key}:
        raise ValueError("unexpected reply fields")
    return value[key]


def _label(value: object, labels: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in labels:
        raise ValueError("invalid reply label")
    return value


def _select(text: str) -> str:
    return _label(_field(text, "ask"), _DESKS)


def _report(text: str) -> Pair:
    value = _field(text, "available")
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("expected two bays")
    first, second = (_label(bay, _BAYS) for bay in value)
    if first == second:
        raise ValueError("expected distinct bays")
    return first, second


def _choose(text: str) -> str:
    return _label(_field(text, "bay"), _BAYS)


def _prompt(instruction: str, data: object) -> str:
    return instruction + "\n\nData:\n" + _json(data)


def _select_prompt(history: History, arm: str) -> str:
    window = history if arm == "lifetime" else history[-2:]
    audit = []
    for desk in _DESKS:
        checks = [exact for used, exact in window if used == desk]
        audit.append({"desk": desk, "exact": sum(checks), "checked": len(checks)})
    return _prompt(_SELECT, {"audit": audit})


def _request[T](
    ask: Callable[[Turn], str | None],
    row: dict[str, Any],
    stage: str,
    prompt: str,
    parse: Callable[[str], T],
) -> T | None:
    reply = ask(Turn(row["arm"], row["step"], stage, _role(stage), prompt))
    if reply is None:
        row["status"] = f"missing_{stage}"
    else:
        try:
            return parse(reply)
        except (ValueError, RecursionError):
            row["status"] = f"invalid_{stage}"
    return None


def _best(pair: Pair, shipment: Shipment) -> str:
    return max(pair, key=lambda bay: shipment.utilities[_BAYS.index(bay)])


def _row(block: Block, arm: str, step: int, shipment: Shipment) -> dict[str, Any]:
    switched = block.regime == "switch" and step >= _SWITCH_STEP
    current_desk = _DESKS[(block.seed + block.rep + switched) % 2]
    return {
        "arm": arm,
        "step": step,
        "native_id": shipment.native_id,
        "desk": None,
        "current_desk": current_desk,
        "exact": None,
        "copied": None,
        "bay": None,
        "optimal": None,
        "feasible": None,
        "source_sensitive": (
            _best(shipment.before, shipment) != _best(shipment.after, shipment)
        ),
        "status": "blocked_history",
    }


def _decision(
    row: dict[str, Any],
    shipment: Shipment,
    history: History,
    ask: Callable[[Turn], str | None],
    forced_desk: str | None,
) -> None:
    desk = forced_desk
    if desk is None:
        desk = _request(
            ask, row, "select", _select_prompt(history, row["arm"]), _select
        )
        if desk is None:
            return
    row["desk"] = desk
    assigned = shipment.after if desk == row["current_desk"] else shipment.before
    report = _request(
        ask, row, "review", _prompt(_REVIEW, {"available": assigned}), _report
    )
    if report is None:
        return
    canonical = report if report[0] < report[1] else (report[1], report[0])
    exact = canonical == shipment.after
    row["exact"] = exact
    row["copied"] = canonical == assigned
    data = {
        "utilities": dict(zip(_BAYS, shipment.utilities, strict=True)),
        "available": report,
    }
    bay = _request(ask, row, "decide", _prompt(_DECIDE, data), _choose)
    if bay is None:
        return
    row["bay"] = bay
    row["optimal"] = bay == _best(shipment.after, shipment)
    row["feasible"] = bay in shipment.after
    row["status"] = "complete"
    history.append((desk, exact))


def episode(block: Block, ask: Callable[[Turn], str | None]) -> list[dict[str, Any]]:
    _validate_block(block)
    source = shipments(block.seed)
    rows: list[dict[str, Any]] = []
    warm_history: History = []
    warm_first = ((block.seed - SEEDS[0]) // 2 + block.rep) % 2
    warm_complete = True
    for step, shipment in enumerate(source[:_WARMUPS]):
        row = _row(block, "warm", step, shipment)
        if warm_complete:
            _decision(row, shipment, warm_history, ask, _DESKS[(warm_first + step) % 2])
        warm_complete = row["status"] == "complete"
        rows.append(row)

    histories = {arm: warm_history.copy() for arm in ARMS}
    active = dict.fromkeys(ARMS, warm_complete)
    for step, shipment in enumerate(source[_WARMUPS:], _WARMUPS):
        order = ARMS if (block.seed + block.rep + step) % 2 == 0 else ARMS[::-1]
        for arm in order:
            row = _row(block, arm, step, shipment)
            if active[arm]:
                _decision(row, shipment, histories[arm], ask, None)
            active[arm] = row["status"] == "complete"
            rows.append(row)
    return rows
