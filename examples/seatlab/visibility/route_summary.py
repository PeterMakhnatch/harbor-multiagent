"""Summarize the registered route experiment from reconstructed decision rows."""

import re
from collections import Counter
from dataclasses import dataclass
from itertools import product
from statistics import mean
from typing import Any, TypedDict

type _Document = dict[str, Any]

_POLICIES = range(3)
_ROUNDS = range(1, 7)
_ROTATIONS = range(3)
_BINARY = range(2)
_LENSES = ("duplicate", "complementary")
_CHANNELS = ("vote", "evidence")
_ARMS = tuple(product(_LENSES, _CHANNELS))
_BLOCKS = tuple(product(_POLICIES, _ROUNDS, _ROTATIONS, _BINARY, _BINARY))
_STATUS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

# Policy, round, rotation, swap, repetition. The last two indices are 0 or 1.
type _Block = tuple[int, int, int, int, int]
type _Arm = tuple[str, str]
type _Rows = dict[_Block, dict[_Arm, _Outcome]]
type _Terms = tuple[tuple[str, str, int], ...]

_CONTRASTS: dict[str, _Terms] = {
    "complementary_minus_duplicate_evidence": (
        ("complementary", "evidence", 1),
        ("duplicate", "evidence", -1),
    ),
    "complementary_minus_duplicate_vote": (
        ("complementary", "vote", 1),
        ("duplicate", "vote", -1),
    ),
    "evidence_minus_vote_duplicate": (
        ("duplicate", "evidence", 1),
        ("duplicate", "vote", -1),
    ),
    "evidence_minus_vote_complementary": (
        ("complementary", "evidence", 1),
        ("complementary", "vote", -1),
    ),
    "interaction": (
        ("complementary", "evidence", 1),
        ("complementary", "vote", -1),
        ("duplicate", "evidence", -1),
        ("duplicate", "vote", 1),
    ),
}


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    label: str | None
    best: str | None
    review_vote: str | None
    exact: bool | None

    @property
    def score(self) -> int | None:
        return None if self.label is None else int(self.label == self.best)


class _Contrast(TypedDict):
    registered_blocks: int
    complete_blocks: int
    registered_scored_cells: int
    known_scored_cells: int
    complete_positive: int
    complete_ties: int
    complete_negative: int
    paired_complete_delta: float | None
    worst_delta: float
    best_delta: float


def _index(row: _Document, field: str, domain: range) -> int:
    value = row[field]
    if type(value) is not int or value not in domain:
        raise ValueError(f"invalid {field} index")
    return value


def _choice(value: object, field: str, allowed: tuple[str, ...]) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"invalid {field}")
    return value


def _label(value: object, field: str) -> str | None:
    return None if value is None else _choice(value, field, ("A", "B"))


def _physical(label: str, swap: int) -> int:
    return int(label == "B") ^ swap


def _validated_rows(rows: list[_Document]) -> _Rows:
    if len(rows) != len(_BLOCKS) * len(_ARMS):
        raise ValueError("expected 864 registered decision rows")
    cells: _Rows = {}
    case_keys: dict[tuple[int, int], int | None] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("decision row is not a dictionary")
        try:
            policy = _index(row, "policy", _POLICIES)
            round_number = _index(row, "round", _ROUNDS)
            rotation = _index(row, "rotation", _ROTATIONS)
            swap = _index(row, "swap", _BINARY)
            rep = _index(row, "rep", _BINARY)
            lens = _choice(row["lens"], "lens", _LENSES)
            channel = _choice(row["channel"], "channel", _CHANNELS)
            status = row["status"]
            label = _label(row["label"], "label")
            best = _label(row["best"], "best")
            vote = _label(row["review_vote"], "review_vote")
            exact = row["exact"]
        except KeyError:
            raise ValueError("decision row is missing a required field") from None
        if type(status) is not str or not _STATUS.fullmatch(status):
            raise ValueError("invalid status category")
        if (status == "valid") != (label is not None):
            raise ValueError("status and label disagree")
        if exact is not None and type(exact) is not bool:
            raise ValueError("invalid exact flag")
        observed = label is not None or vote is not None or exact is not None
        if best is None and observed:
            raise ValueError("recorded outcome for an unknown source round")
        physical_best = None if best is None else _physical(best, swap)
        if case_keys.setdefault((policy, round_number), physical_best) != physical_best:
            raise ValueError("inconsistent answer key for a source round")
        block = cells.setdefault((policy, round_number, rotation, swap, rep), {})
        arm = (lens, channel)
        if arm in block:
            raise ValueError("duplicate registered decision row")
        block[arm] = _Outcome(status, label, best, vote, exact)

    # Valid dimensions plus exactly 864 unique cells imply full register coverage.
    for block in cells.values():
        for lens in _LENSES:
            vote_branch = block[lens, "vote"]
            evidence_branch = block[lens, "evidence"]
            if (vote_branch.review_vote, vote_branch.exact) != (
                evidence_branch.review_vote,
                evidence_branch.exact,
            ):
                raise ValueError("branches disagree on their shared reviewer")
    return cells


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _contrast(rows: _Rows, blocks: tuple[_Block, ...], terms: _Terms) -> _Contrast:
    known = total = low = high = positive = ties = negative = 0
    for block in blocks:
        delta = missing_low = missing_high = 0
        for lens, channel, sign in terms:
            score = rows[block][lens, channel].score
            if score is None:
                missing_low += min(0, sign)
                missing_high += max(0, sign)
            else:
                known += 1
                delta += sign * score
        low += delta + missing_low
        high += delta + missing_high
        if missing_low == missing_high == 0:
            total += delta
            positive += delta > 0
            ties += delta == 0
            negative += delta < 0
    complete = positive + ties + negative
    return {
        "registered_blocks": len(blocks),
        "complete_blocks": complete,
        "registered_scored_cells": len(blocks) * len(terms),
        "known_scored_cells": known,
        "complete_positive": positive,
        "complete_ties": ties,
        "complete_negative": negative,
        "paired_complete_delta": _fraction(total, complete),
        "worst_delta": low / len(blocks),
        "best_delta": high / len(blocks),
    }


def _combined_contrast(by_policy: dict[str, _Contrast]) -> _Document:
    stats = by_policy.values()
    deltas = [s["paired_complete_delta"] for s in stats]
    supported = [delta for delta in deltas if delta is not None]
    return {
        "registered_blocks": sum(s["registered_blocks"] for s in stats),
        "complete_blocks": sum(s["complete_blocks"] for s in stats),
        "registered_scored_cells": sum(s["registered_scored_cells"] for s in stats),
        "known_scored_cells": sum(s["known_scored_cells"] for s in stats),
        "complete_positive": sum(s["complete_positive"] for s in stats),
        "complete_ties": sum(s["complete_ties"] for s in stats),
        "complete_negative": sum(s["complete_negative"] for s in stats),
        "paired_complete_delta": (
            mean(supported) if len(supported) == len(stats) else None
        ),
        "worst_delta": mean(s["worst_delta"] for s in stats),
        "best_delta": mean(s["best_delta"] for s in stats),
        "by_policy": by_policy,
    }


def _pair_counts(
    rows: _Rows, blocks: tuple[_Block, ...], arm: _Arm, *, pair_swaps: bool
) -> _Document:
    registered = known_source = valid = agree = 0
    for block in blocks:
        policy, round_number, rotation, swap, rep = block
        if pair_swaps:
            if swap != 0:
                continue
            other = (policy, round_number, rotation, 1, rep)
        else:
            if rep != 0:
                continue
            other = (policy, round_number, rotation, swap, 1)
        registered += 1
        first = rows[block][arm]
        second = rows[other][arm]
        known_source += first.best is not None
        if first.label is not None and second.label is not None:
            valid += 1
            agree += _physical(first.label, swap) == _physical(second.label, other[3])
    return {
        "registered_pairs": registered,
        "known_source_pairs": known_source,
        "valid_pairs": valid,
        "physical_agreement": agree,
        "physical_disagreement": valid - agree,
    }


def _arm_summary(rows: _Rows, blocks: tuple[_Block, ...], arm: _Arm) -> _Document:
    statuses: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    known_source = correct = compared = followed = 0
    for block in blocks:
        outcome = rows[block][arm]
        statuses[outcome.status] += 1
        known_source += outcome.best is not None
        if outcome.label is not None:
            labels[outcome.label] += 1
            correct += outcome.label == outcome.best
            if outcome.review_vote is not None:
                compared += 1
                followed += outcome.label == outcome.review_vote
    valid = statuses["valid"]
    return {
        "registered_decisions": len(blocks),
        "known_source_decisions": known_source,
        "status_counts": dict(sorted(statuses.items())),
        "label_counts": {"A": labels["A"], "B": labels["B"]},
        "valid_decisions": valid,
        "correct_decisions": correct,
        "accuracy": _fraction(correct, valid),
        "recommendation_following": {
            "comparable": compared,
            "followed": followed,
            "fraction": _fraction(followed, compared),
        },
        "label_swap_pairs": _pair_counts(rows, blocks, arm, pair_swaps=True),
        "repeat_pairs": _pair_counts(rows, blocks, arm, pair_swaps=False),
    }


def _review_summary(rows: _Rows, blocks: tuple[_Block, ...], lens: str) -> _Document:
    known_source = known_vote = known_exact = exact = 0
    for block in blocks:
        # Branch copies agree; each block and lens has only one reviewer.
        outcome = rows[block][lens, "vote"]
        known_source += outcome.best is not None
        known_vote += outcome.review_vote is not None
        known_exact += outcome.exact is not None
        exact += outcome.exact is True
    return {
        "registered_reviewers": len(blocks),
        "known_source_reviewers": known_source,
        "known_recommendations": known_vote,
        "known_transcriptions": known_exact,
        "exact_transcriptions": exact,
        "incorrect_transcriptions": known_exact - exact,
        "exact_fraction": _fraction(exact, known_exact),
    }


def _population(rows: _Rows, blocks: tuple[_Block, ...]) -> _Document:
    registered_rounds = {block[:2] for block in blocks}
    known_rounds = set()
    scored_rounds = set()
    known_blocks = 0
    for block in blocks:
        outcomes = rows[block]
        if outcomes[_ARMS[0]].best is not None:
            known_blocks += 1
            known_rounds.add(block[:2])
        if any(outcome.label is not None for outcome in outcomes.values()):
            scored_rounds.add(block[:2])
    return {
        "registered_source_policies": len({p for p, _ in registered_rounds}),
        "source_policies_with_known_rounds": len({p for p, _ in known_rounds}),
        "source_policies_with_scored_decisions": len({p for p, _ in scored_rounds}),
        "registered_rounds": len(registered_rounds),
        "known_source_rounds": len(known_rounds),
        "rounds_with_scored_decisions": len(scored_rounds),
        "registered_blocks": len(blocks),
        "known_source_blocks": known_blocks,
        "registered_reviewers": len(blocks) * len(_LENSES),
        "registered_decisions": len(blocks) * len(_ARMS),
    }


def _descriptive(rows: _Rows, blocks: tuple[_Block, ...]) -> _Document:
    return {
        "population": _population(rows, blocks),
        "arms": {
            lens: {
                channel: _arm_summary(rows, blocks, (lens, channel))
                for channel in _CHANNELS
            }
            for lens in _LENSES
        },
        "reviews_by_lens": {
            lens: _review_summary(rows, blocks, lens) for lens in _LENSES
        },
    }


def summarize(rows: list[_Document]) -> _Document:
    """Return aggregate JSON data, with no choice rows or raw model text.

    The paired estimate uses complete blocks within each policy and gives each
    policy equal weight. It is null unless all three policies have support.
    Bounds instead use every registered block and allow each unscored choice
    to be correct or incorrect. Arm accuracy is descriptive among valid choices.
    """
    cells = _validated_rows(rows)
    policies = {str(p): tuple(b for b in _BLOCKS if b[0] == p) for p in _POLICIES}
    return {
        "schema": "seatlab-route-summary-v1",
        **_descriptive(cells, _BLOCKS),
        "by_policy": {
            policy: _descriptive(cells, blocks) for policy, blocks in policies.items()
        },
        "primary_contrast": "complementary_minus_duplicate_evidence",
        "contrasts": {
            name: _combined_contrast(
                {
                    policy: _contrast(cells, blocks, terms)
                    for policy, blocks in policies.items()
                }
            )
            for name, terms in _CONTRASTS.items()
        },
    }
