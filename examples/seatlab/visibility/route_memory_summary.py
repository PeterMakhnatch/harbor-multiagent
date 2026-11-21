import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import mean
from typing import Any, TypedDict

from . import route_memory as m
from .route_protocol import Block

_ARMS = ("bound", "anonymous")
_LABELS = ("A", "B")
_SELECTORS = ("X", "Y")
_FIELDS = {
    "policy",
    "round",
    "rotation",
    "swap",
    "rep",
    "arm",
    "status",
    "selected",
    "source_index",
    "better_history",
    "current_exact",
    "label",
    "best",
}
_STATUS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    selected: str | None
    source_index: int | None
    better_history: bool | None
    current_exact: bool | None
    label: str | None
    best: str | None

    @property
    def score(self) -> int | None:
        return None if self.label is None else int(self.label == self.best)


type _Rows = dict[Block, tuple[_Outcome, _Outcome]]


class _Contrast(TypedDict):
    registered_blocks: int
    registered_scored_cells: int
    known_scored_cells: int
    complete_blocks: int
    complete_positive: int
    complete_ties: int
    complete_negative: int
    bound_only: int
    anonymous_only: int
    neither_scored: int
    paired_complete_delta: float | None
    worst_delta: float
    best_delta: float


def _index(value: object) -> int:
    if type(value) is not int:
        raise ValueError("invalid block coordinate")
    return value


def _choice(value: object, allowed: tuple[str, ...]) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError("invalid decision category")
    return value


def _optional_choice(value: object, allowed: tuple[str, ...]) -> str | None:
    return None if value is None else _choice(value, allowed)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError("invalid selection flag")
    return value


def _physical(label: str, swap: int) -> int:
    return int(label == "B") ^ swap


def _outcome(row: Mapping[str, object], block: Block) -> _Outcome:
    status = row["status"]
    if type(status) is not str or not _STATUS.fullmatch(status):
        raise ValueError("invalid decision status")
    selected = _optional_choice(row["selected"], _SELECTORS)
    label = _optional_choice(row["label"], _LABELS)
    best = _optional_choice(row["best"], _LABELS)
    source_index = row["source_index"]
    better_history = _optional_bool(row["better_history"])
    current_exact = _optional_bool(row["current_exact"])
    if (status == "valid") != (label is not None):
        raise ValueError("status and final label disagree")
    if selected is None:
        if any(
            v is not None for v in (source_index, better_history, current_exact, label)
        ):
            raise ValueError("decision contains outcomes without a selection")
        source_index = None
    else:
        x_is_next = (block.policy + block.round + block.swap + block.rep) % 2 == 0
        offset = 1 if (selected == "X") == x_is_next else -1
        if (
            type(source_index) is not int
            or source_index != (block.rotation + offset) % 3
        ):
            raise ValueError("selection does not match the scheduled reporter")
        if best is None or current_exact is None or status == "ineligible":
            raise ValueError("selection lacks eligible source evidence")
    return _Outcome(
        status, selected, source_index, better_history, current_exact, label, best
    )


def _shared(bound: _Outcome, anonymous: _Outcome) -> bool:
    return bound.selected is not None and bound.selected == anonymous.selected


def _validate_pair(bound: _Outcome, anonymous: _Outcome) -> None:
    if _shared(bound, anonymous):
        if bound != anonymous:
            raise ValueError("conflicting outcomes on a shared final branch")
    elif bound.selected is not None and anonymous.selected is not None:
        left, right = bound.better_history, anonymous.better_history
        if (left, right) not in ((None, None), (True, False), (False, True)):
            raise ValueError("conflicting history ranks for different reporters")
    if (bound.status == "ineligible") != (anonymous.status == "ineligible"):
        raise ValueError("arms disagree on block eligibility")


def _validated_rows(rows: Sequence[Mapping[str, object]]) -> _Rows:
    register = m.blocks()
    if not isinstance(rows, Sequence) or len(rows) != len(register) * len(_ARMS):
        raise ValueError("expected 288 registered decision rows")
    scheduled = set(register)
    cases: dict[tuple[int, int], int | None] = {}
    cells: dict[Block, dict[str, _Outcome]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.keys() != _FIELDS:
            raise ValueError("unexpected decision row fields")
        block = Block(
            _index(row["policy"]),
            _index(row["round"]),
            _index(row["rotation"]),
            _index(row["swap"]),
            _index(row["rep"]),
        )
        if block not in scheduled:
            raise ValueError("unexpected block coordinate")
        arm = _choice(row["arm"], _ARMS)
        pair = cells.setdefault(block, {})
        if arm in pair:
            raise ValueError("duplicate registered decision row")
        outcome = _outcome(row, block)
        best = None if outcome.best is None else _physical(outcome.best, block.swap)
        if cases.setdefault((block.policy, block.round), best) != best:
            raise ValueError("inconsistent physical answer key for a source round")
        pair[arm] = outcome

    result: _Rows = {}
    for block in register:
        bound, anonymous = (cells[block][arm] for arm in _ARMS)
        _validate_pair(bound, anonymous)
        result[block] = (bound, anonymous)
    return result


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _contrast(cells: _Rows) -> _Contrast:
    positive = ties = negative = 0
    bound_only = anonymous_only = neither = low = high = 0
    for bound, anonymous in cells.values():
        left, right = bound.score, anonymous.score
        if left is not None and right is not None:
            delta = left - right
            positive += delta > 0
            ties += delta == 0
            negative += delta < 0
            low += delta
            high += delta
        else:
            bound_only += left is not None
            anonymous_only += right is not None
            neither += left is None and right is None
            # A missing shared final has one unknown outcome, not two.
            if not _shared(bound, anonymous):
                low += (0 if left is None else left) - (1 if right is None else right)
                high += (1 if left is None else left) - (0 if right is None else right)
    complete = positive + ties + negative
    return {
        "registered_blocks": len(cells),
        "registered_scored_cells": len(cells) * len(_ARMS),
        "known_scored_cells": complete * len(_ARMS) + bound_only + anonymous_only,
        "complete_blocks": complete,
        "complete_positive": positive,
        "complete_ties": ties,
        "complete_negative": negative,
        "bound_only": bound_only,
        "anonymous_only": anonymous_only,
        "neither_scored": neither,
        "paired_complete_delta": _fraction(positive - negative, complete),
        "worst_delta": low / len(cells),
        "best_delta": high / len(cells),
    }


def _primary(cells: _Rows) -> dict[str, Any]:
    by_source: dict[int, _Rows] = {}
    for block, pair in cells.items():
        by_source.setdefault(block.policy, {})[block] = pair
    by_policy = {str(p): _contrast(rows) for p, rows in by_source.items()}
    means = [stats["paired_complete_delta"] for stats in by_policy.values()]
    supported = [value for value in means if value is not None]
    combined = _contrast(cells)
    combined["paired_complete_delta"] = (
        mean(supported) if len(supported) == len(means) else None
    )
    return {"contrast": "bound_minus_anonymous", **combined, "by_policy": by_policy}


def _population(cells: _Rows) -> dict[str, int]:
    registered = {(block.policy, block.round) for block in cells}
    known = set()
    scored = set()
    known_blocks = 0
    for block, (bound, anonymous) in cells.items():
        if bound.best is not None:
            known.add((block.policy, block.round))
            known_blocks += 1
        if bound.label is not None or anonymous.label is not None:
            scored.add((block.policy, block.round))
    return {
        "registered_source_policies": len({p for p, _ in registered}),
        "source_policies_with_known_rounds": len({p for p, _ in known}),
        "source_policies_with_scored_decisions": len({p for p, _ in scored}),
        "registered_rounds": len(registered),
        "known_source_rounds": len(known),
        "rounds_with_scored_decisions": len(scored),
        "registered_blocks": len(cells),
        "known_source_blocks": known_blocks,
        "registered_decisions": len(cells) * len(_ARMS),
    }


def _match_count(flags: Sequence[bool | None]) -> dict[str, int | float | None]:
    compared = sum(flag is not None for flag in flags)
    matches = sum(flag is True for flag in flags)
    return {
        "compared": compared,
        "matches": matches,
        "fraction": _fraction(matches, compared),
    }


def _arm_routing(outcomes: Sequence[_Outcome]) -> dict[str, Any]:
    valid = sum(outcome.label is not None for outcome in outcomes)
    correct = sum(outcome.score == 1 for outcome in outcomes)
    return {
        "registered_decisions": len(outcomes),
        "selections": sum(outcome.selected is not None for outcome in outcomes),
        "better_history": _match_count(
            [outcome.better_history for outcome in outcomes]
        ),
        "current_exact": _match_count([outcome.current_exact for outcome in outcomes]),
        "valid_decisions": valid,
        "correct_decisions": correct,
        "accuracy": _fraction(correct, valid),
    }


def _routing(cells: _Rows) -> dict[str, Any]:
    neither = one = same = different = 0
    shared_valid = shared_correct = unshared_valid = unshared_correct = 0
    two_valid_different = 0
    for bound, anonymous in cells.values():
        if _shared(bound, anonymous):
            same += 1
            shared_valid += bound.label is not None
            shared_correct += bound.score == 1
            continue
        if bound.selected is None and anonymous.selected is None:
            neither += 1
        elif bound.selected is None or anonymous.selected is None:
            one += 1
        else:
            different += 1
            two_valid_different += (
                bound.label is not None and anonymous.label is not None
            )
        for outcome in (bound, anonymous):
            unshared_valid += outcome.label is not None
            unshared_correct += outcome.score == 1
    return {
        "by_arm": {
            arm: _arm_routing([pair[index] for pair in cells.values()])
            for index, arm in enumerate(_ARMS)
        },
        "selectors": {
            "neither": neither,
            "one": one,
            "same": same,
            "different": different,
        },
        "final_branches": {
            "shared_valid": shared_valid,
            "shared_correct": shared_correct,
            "unshared_valid": unshared_valid,
            "unshared_correct": unshared_correct,
            "distinct_valid": shared_valid + unshared_valid,
            "distinct_correct": shared_correct + unshared_correct,
            "blocks_with_two_valid_branches": two_valid_different,
        },
    }


def _agreement(compared: int, agreed: int) -> dict[str, int]:
    return {
        "comparable_pairs": compared,
        "physical_agreement": agreed,
        "physical_disagreement": compared - agreed,
    }


def _variation(cells: _Rows, index: int, *, pair_swaps: bool) -> dict[str, Any]:
    registered = selected = same_reporter = decided = same_route = 0
    for block, pair in cells.items():
        first_index = block.swap if pair_swaps else block.rep
        if first_index != 0:
            continue
        other = replace(block, swap=1) if pair_swaps else replace(block, rep=1)
        first, second = pair[index], cells[other][index]
        registered += 1
        if first.source_index is not None and second.source_index is not None:
            selected += 1
            same_reporter += first.source_index == second.source_index
        if first.label is not None and second.label is not None:
            decided += 1
            first_route = _physical(first.label, block.swap)
            same_route += first_route == _physical(second.label, other.swap)
    return {
        "registered_pairs": registered,
        "reporter": _agreement(selected, same_reporter),
        "final": _agreement(decided, same_route),
    }


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    cells = _validated_rows(rows)
    return {
        "population": _population(cells),
        "primary": _primary(cells),
        "routing": _routing(cells),
        "variation": {
            arm: {
                "repeat_pairs": _variation(cells, index, pair_swaps=False),
                "label_swap_pairs": _variation(cells, index, pair_swaps=True),
            }
            for index, arm in enumerate(_ARMS)
        },
    }
