from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from statistics import fmean
from typing import Any, TypedDict, cast

from .feedback_protocol import ARMS, Block, grid

_REGIMES = ("stable", "switch")
_DESKS = ("X", "Y")
_BAYS = ("A", "B", "C", "D")
_STEPS = range(2, 10)
_POST = range(6, 10)
_PRE = range(2, 6)
_STAGE = {
    "blocked_history": 0,
    "missing_select": 0,
    "invalid_select": 0,
    "missing_review": 1,
    "invalid_review": 1,
    "missing_decide": 2,
    "invalid_decide": 2,
    "complete": 3,
}
_ASSETS = Path(__file__).resolve().parents[2] / "tasks/dispatch-handoff"


class _Row(TypedDict):
    arm: str
    step: int
    native_id: int
    desk: str | None
    current_desk: str
    exact: bool | None
    copied: bool | None
    bay: str | None
    optimal: bool | None
    feasible: bool | None
    source_sensitive: bool
    status: str


class _SeedEstimate(TypedDict):
    seed: int
    scheduled_pairs: int
    complete_pairs: int
    paired_complete_delta: float | None
    worst_delta: float
    best_delta: float


@dataclass(frozen=True, slots=True)
class _Stock:
    optimal: str
    feasible: frozenset[str]
    sensitive: bool


type _Rows = dict[tuple[str, int], _Row]
type _Stocks = dict[int, tuple[_Stock, ...]]
type _Pair = tuple[int, bool, _Row | None, _Row | None]


def _stock_references(seeds: tuple[int, ...]) -> _Stocks:
    if not seeds:
        return {}
    world_type = run_path(str(_ASSETS / "environment/seatworld.py"))["World"]
    score = run_path(str(_ASSETS / "tests/score.py"))["score"]
    result: _Stocks = {}
    for seed in seeds:
        settings = {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": 20,
            "condition": "pooled",
        }
        scores: dict[str, list[dict[str, Any]]] = {}
        cases: list[dict[str, Any]] = []
        for bay in _BAYS:
            world = world_type(settings)
            world.submit(
                "dispatch", {"rows": [{"id": i, "bay": bay} for i in range(20)]}
            )
            evidence = world.evidence()
            cases = evidence["cases"]
            scores[bay] = score(evidence)["rows"]
        shipments = []
        for native_id in range(1, 20, 2):
            (optimal,) = (bay for bay in _BAYS if scores[bay][native_id]["optimal"])
            feasible = frozenset(
                bay for bay in _BAYS if scores[bay][native_id]["feasible"]
            )
            case = cases[native_id]
            before_optimal = max(case["before"], key=case["utilities"].__getitem__)
            shipments.append(_Stock(optimal, feasible, before_optimal != optimal))
        result[seed] = tuple(shipments)
    return result


def _register(
    blocks: Sequence[Mapping[str, object]],
) -> dict[Block, Mapping[str, object]]:
    entries: dict[Block, Mapping[str, object]] = {}
    seeds: dict[int, None] = {}
    if len(blocks) > 256:
        raise ValueError("feedback register exceeds 64 seeds")
    for public in blocks:
        if not isinstance(public, Mapping):
            raise ValueError("invalid feedback block")
        seed, regime, rep = public.get("seed"), public.get("regime"), public.get("rep")
        if (
            type(seed) is not int
            or not 1 <= seed <= 2147483647
            or type(regime) is not str
            or regime not in _REGIMES
            or type(rep) is not int
            or rep not in (0, 1)
        ):
            raise ValueError("invalid feedback block coordinates")
        block = Block(seed, regime, rep)
        if block in entries or public.get("block") != block.key:
            raise ValueError("invalid feedback block identity")
        entries[block] = public
        seeds[seed] = None
    if entries and entries.keys() != set(grid(tuple(seeds))):
        raise ValueError("incomplete feedback register")
    return entries


def _checked_row(raw: object, block: Block, stock: tuple[_Stock, ...]) -> _Row:
    if not isinstance(raw, Mapping) or raw.keys() != _Row.__required_keys__:
        raise ValueError("invalid feedback row fields")
    arm, step, status = raw["arm"], raw["step"], raw["status"]
    if (
        type(arm) is not str
        or type(step) is not int
        or not ((arm == "warm" and step in (0, 1)) or (arm in ARMS and step in _STEPS))
        or type(status) is not str
        or status not in _STAGE
    ):
        raise ValueError("invalid feedback row coordinates or status")
    if arm == "warm" and status in ("missing_select", "invalid_select"):
        raise ValueError("unexpected warmup selector")
    reference = stock[step]
    native_id = raw["native_id"]
    if type(native_id) is not int or native_id != 2 * step + 1:
        raise ValueError("invalid native shipment identity")
    if raw["source_sensitive"] is not reference.sensitive:
        raise ValueError("feedback sensitivity disagrees with native stock")
    switched = block.regime == "switch" and step in _POST
    current = _DESKS[((block.seed + block.rep) % 2) ^ switched]
    if type(raw["current_desk"]) is not str or raw["current_desk"] != current:
        raise ValueError("feedback current desk disagrees with schedule")
    observed = _STAGE[status]
    desk, exact, copied = raw["desk"], raw["exact"], raw["copied"]
    if observed >= 1:
        if type(desk) is not str or desk not in _DESKS:
            raise ValueError("missing desk for a reached review")
        if arm == "warm":
            first = ((block.seed - 5201) // 2 + block.rep) % 2
            if desk != _DESKS[first ^ step]:
                raise ValueError("feedback warmup desk disagrees with schedule")
    elif desk is not None:
        raise ValueError("unobserved desk has a value")
    if observed >= 2:
        if type(exact) is not bool or type(copied) is not bool:
            raise ValueError("missing comparison for a valid review")
        if (desk == current and exact != copied) or (
            desk != current and exact and copied
        ):
            raise ValueError("contradictory feedback review comparisons")
    elif exact is not None or copied is not None:
        raise ValueError("unobserved review has a comparison")
    bay, optimal, feasible = raw["bay"], raw["optimal"], raw["feasible"]
    if observed == 3:
        if type(bay) is not str or bay not in _BAYS:
            raise ValueError("invalid completed feedback choice")
        if optimal is not (bay == reference.optimal) or feasible is not (
            bay in reference.feasible
        ):
            raise ValueError("feedback choice disagrees with stock score")
    elif bay is not None or optimal is not None or feasible is not None:
        raise ValueError("unobserved feedback choice has a score")
    return cast(_Row, raw)


def _validated_rows(
    public: Mapping[str, object], block: Block, stock: tuple[_Stock, ...]
) -> _Rows:
    state = public.get("state")
    if (
        type(state) is not str
        or state not in ("claimed", "unclaimed", "unbound")
        or "rows" not in public
    ):
        raise ValueError("invalid public feedback block state")
    raw_rows = public["rows"]
    if state != "claimed" and (raw_rows is None or raw_rows == []):
        return {}
    if not isinstance(raw_rows, list) or len(raw_rows) != 18:
        raise ValueError("invalid feedback row register")
    rows: _Rows = {}
    for raw in raw_rows:
        row = _checked_row(raw, block, stock)
        key = (row["arm"], row["step"])
        if key in rows:
            raise ValueError("duplicate feedback row")
        rows[key] = row
    if state != "claimed" and rows["warm", 0]["status"] != "missing_review":
        raise ValueError("unclaimed feedback block contains observations")
    warm = _check_history(rows, "warm", range(2), blocked=False)
    for arm in ARMS:
        _check_history(rows, arm, _STEPS, blocked=warm)
    return rows if state == "claimed" else {}


def _check_history(rows: _Rows, arm: str, steps: range, *, blocked: bool) -> bool:
    for step in steps:
        status = rows[arm, step]["status"]
        if (status == "blocked_history") != blocked:
            raise ValueError("feedback rows contradict completed history")
        blocked = status != "complete"
    return blocked


def _mean(values: Sequence[int | float]) -> float | None:
    return fmean(values) if values else None


def _optimal(row: _Row | None) -> bool | None:
    return None if row is None else row["optimal"]


def _contrast(pairs: Sequence[_Pair]) -> dict[str, Any]:
    seeds: dict[int, list[tuple[bool | None, bool | None]]] = {}
    for seed, _, lifetime, recent in pairs:
        seeds.setdefault(seed, []).append((_optimal(lifetime), _optimal(recent)))
    by_seed: list[_SeedEstimate] = []
    covered: list[float] = []
    for seed, matches in seeds.items():
        complete = []
        low = high = 0
        for lifetime, recent in matches:
            if lifetime is not None and recent is not None:
                complete.append(int(recent) - int(lifetime))
            low += int(recent is True) - int(lifetime is not False)
            high += int(recent is not False) - int(lifetime is True)
        delta = _mean(complete)
        if delta is not None:
            covered.append(delta)
        by_seed.append(
            {
                "seed": seed,
                "scheduled_pairs": len(matches),
                "complete_pairs": len(complete),
                "paired_complete_delta": delta,
                "worst_delta": low / len(matches),
                "best_delta": high / len(matches),
            }
        )
    return {
        "contrast": "recent_minus_lifetime",
        "endpoint": "optimal_current_stock",
        "cluster_unit": "seed",
        "enrolled_seeds": len(by_seed),
        "seeds_with_pairs": len(covered),
        "scheduled_pairs": len(pairs),
        "complete_pairs": sum(row["complete_pairs"] for row in by_seed),
        "paired_complete_delta": _mean(covered)
        if len(covered) == len(by_seed)
        else None,
        "worst_delta": _mean([row["worst_delta"] for row in by_seed]),
        "best_delta": _mean([row["best_delta"] for row in by_seed]),
        "by_seed": by_seed,
    }


def _tally(flags: Sequence[bool | None]) -> dict[str, int | float | None]:
    observed = sum(flag is not None for flag in flags)
    positive = sum(flag is True for flag in flags)
    return {
        "observed": observed,
        "positive": positive,
        "rate": positive / observed if observed else None,
    }


def _current_choice(row: _Row | None) -> bool | None:
    if row is None or row["desk"] is None:
        return None
    return row["desk"] == row["current_desk"]


def _behavior(rows: Sequence[_Row | None]) -> dict[str, Any]:
    return {
        "scheduled": len(rows),
        "status_counts": dict(
            Counter(row["status"] if row else "unobserved" for row in rows)
        ),
        "select_current_desk": _tally([_current_choice(row) for row in rows]),
        "report_matches_current": _tally(
            [row["exact"] if row else None for row in rows]
        ),
        "report_matches_assigned": _tally(
            [row["copied"] if row else None for row in rows]
        ),
        "choose_optimal": _tally([_optimal(row) for row in rows]),
        "choose_feasible": _tally([row["feasible"] if row else None for row in rows]),
    }


def _paired_rows(
    cells: Mapping[Block, _Rows], stocks: _Stocks, regime: str, steps: range
) -> list[_Pair]:
    return [
        (
            block.seed,
            stocks[block.seed][step].sensitive,
            rows.get(("lifetime", step)),
            rows.get(("recent", step)),
        )
        for block, rows in cells.items()
        if block.regime == regime
        for step in steps
    ]


def _repeat_drift(
    cells: Mapping[Block, _Rows], seeds: tuple[int, ...], regime: str, arm: str
) -> dict[str, Any]:
    desk_drift: list[bool | None] = []
    bay_drift: list[bool | None] = []
    outcomes: Counter[int] = Counter({0: 0, 1: 0, 2: 0})
    for seed in seeds:
        left, right = (cells[Block(seed, regime, rep)] for rep in (0, 1))
        for step in _STEPS:
            first, second = left.get((arm, step)), right.get((arm, step))
            first_current, second_current = (
                _current_choice(first),
                _current_choice(second),
            )
            desk_drift.append(
                None
                if first_current is None or second_current is None
                else first_current != second_current
            )
            first_bay = first["bay"] if first else None
            second_bay = second["bay"] if second else None
            bay_drift.append(
                None
                if first_bay is None or second_bay is None
                else first_bay != second_bay
            )
            outcomes[
                len({bay for bay in (first_bay, second_bay) if bay is not None})
            ] += 1
    return {
        "current_desk_choice_differs": _tally(desk_drift),
        "final_bay_differs": _tally(bay_drift),
        "distinct_final_bays_by_seed_and_step": {
            str(k): v for k, v in outcomes.items()
        },
    }


def summarize(blocks: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    entries = _register(blocks)
    seeds = tuple(dict.fromkeys(block.seed for block in entries))
    stocks = _stock_references(seeds)
    cells = {
        block: _validated_rows(public, block, stocks[block.seed])
        for block, public in entries.items()
    }
    switch_post = _paired_rows(cells, stocks, "switch", _POST)
    return {
        "analysis": "descriptive",
        "primary": _contrast(switch_post),
        "secondary": {
            "stable_post": _contrast(_paired_rows(cells, stocks, "stable", _POST)),
            "stable_pre": _contrast(_paired_rows(cells, stocks, "stable", _PRE)),
            "switch_pre": _contrast(_paired_rows(cells, stocks, "switch", _PRE)),
        },
        "behavior": {
            regime: {
                arm: _behavior(
                    [
                        rows.get((arm, step))
                        for block, rows in cells.items()
                        if block.regime == regime
                        for step in _STEPS
                    ]
                )
                for arm in ARMS
            }
            for regime in _REGIMES
        },
        "recovery": {
            regime: {
                arm: [
                    {
                        "step": step,
                        **_behavior(
                            [
                                rows.get((arm, step))
                                for block, rows in cells.items()
                                if block.regime == regime
                            ]
                        ),
                    }
                    for step in _POST
                ]
                for arm in ARMS
            }
            for regime in _REGIMES
        },
        "source_sensitivity": {
            label: {
                "distinct_native_shipments": sum(
                    stock.sensitive is sensitive
                    for world in stocks.values()
                    for stock in world
                ),
                "switch_post": {
                    arm: _behavior(
                        [pair[index] for pair in switch_post if pair[1] is sensitive]
                    )
                    for arm, index in (("lifetime", 2), ("recent", 3))
                },
            }
            for label, sensitive in (("sensitive", True), ("insensitive", False))
        },
        "repeat_drift": {
            regime: {arm: _repeat_drift(cells, seeds, regime, arm) for arm in ARMS}
            for regime in _REGIMES
        },
    }
