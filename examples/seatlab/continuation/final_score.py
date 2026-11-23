from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import equal_time, final_claims, portfolio, score, source

SCHEMA = "seatlab.final-board-report/v1"
OWNER_SCHEMA = "seatlab.final-board-owner/v1"
MEASURES = final_claims.MEASURES
BASELINES = ("full", "uniform", "busiest")
PAIRS = len(source.MODELS) * len(portfolio.SEAT_BUDGETS) * equal_time.PAIRS_PER_SET
CANDIDATES = 2
POSITIONS = ([0, 1], [1, 0])
RESPONSES = PAIRS * len(POSITIONS)
COVERAGE_REQUIRED = 12
GAIN = score.THRESHOLD
_PICKS = (1, 2)
_OWNER_FIELDS = frozenset(
    {
        "schema",
        "registration_commit",
        "portfolio_sha256",
        "index_sha256",
        "board_sha256",
        "pairs",
    }
)
_PAIR_FIELDS = frozenset(
    {"setting", "run_ids", "grades", "full", "busiest", "events", "claims", "orders"}
)
_ORDER_FIELDS = frozenset({"id", "prompt_sha256", "positions"})
_CODE = ("final_score.py", "final_claims.py", "final_board.py", "score.py")
_require = score._require
_num = portfolio._num


def _fixed(value: Any, length: int, label: str) -> list[Any]:
    _require(
        type(value) is list and len(value) == length,
        f"{label} must be a list of {length}",
    )
    return value


def _grade(value: Any, label: str) -> Fraction:
    _require(type(value) is str, f"{label} must be an exact fraction string")
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{label} must be an exact fraction string") from error
    _require(0 <= exact <= 1, f"{label} must be within [0, 1]")
    return exact


def _order(value: Any, positions: list[int], label: str) -> dict[str, Any]:
    order = score._record(value, _ORDER_FIELDS, label)
    _require(
        order["positions"] == positions
        and all(type(position) is int for position in order["positions"]),
        f"{label} positions must be {positions}",
    )
    score._sha256(order["id"], f"{label} id")
    score._sha256(order["prompt_sha256"], f"{label} prompt_sha256")
    return order


def _pair(value: Any, label: str) -> dict[str, Any]:
    pair = score._record(value, _PAIR_FIELDS, label)
    run_ids = _fixed(pair["run_ids"], CANDIDATES, f"{label} run_ids")
    _require(
        all(type(run) is str for run in run_ids) and run_ids[0] != run_ids[1],
        f"{label} run_ids must be two distinct names",
    )
    grades = _fixed(pair["grades"], CANDIDATES, f"{label} grades")
    orders = _fixed(pair["orders"], len(POSITIONS), f"{label} orders")
    return {
        "setting": score._text(pair["setting"], f"{label} setting"),
        "run_ids": run_ids,
        "grades": [_grade(grade, f"{label} grade") for grade in grades],
        "full": _grade(pair["full"], f"{label} full"),
        "busiest": _grade(pair["busiest"], f"{label} busiest"),
        "claims": _fixed(pair["claims"], CANDIDATES, f"{label} claims"),
        "orders": [
            _order(order, positions, f"{label} order")
            for order, positions in zip(orders, POSITIONS, strict=True)
        ],
    }


def _owner(owner: Any) -> list[dict[str, Any]]:
    record = score._record(owner, _OWNER_FIELDS, "owner")
    _require(record["schema"] == OWNER_SCHEMA, f"owner schema must be {OWNER_SCHEMA}")
    values = _fixed(record["pairs"], PAIRS, "owner pairs")
    pairs = [_pair(value, f"pair {index}") for index, value in enumerate(values)]
    ids = {order["id"] for pair in pairs for order in pair["orders"]}
    _require(len(ids) == RESPONSES, "order ids must be distinct")
    return pairs


def _pick(text: str) -> int | None:
    try:
        value = json.loads(
            text,
            object_pairs_hook=score._unique_pairs,
            parse_constant=score._reject_constant,
        )
    except (ValueError, RecursionError):
        return None
    if type(value) is not dict or value.keys() != score._PICK_FIELDS:
        return None
    pick = value["pick"]
    if type(pick) is not int or pick not in _PICKS:
        return None
    return pick


def _choice(pair: dict[str, Any]) -> tuple[str | None, list[int], Fraction]:
    measure, indices = final_claims.choose(pair["claims"])
    chosen = sorted(set(indices))
    _require(
        0 < len(chosen) == len(indices) and set(chosen) <= {0, 1},
        "claim selector must return distinct candidate indices",
    )
    _require(
        measure is not None or chosen == [0, 1],
        "claim selector abstention must fall back to uniform choice",
    )
    return measure, chosen, score._mean([pair["grades"][i] for i in chosen])


def _row(pair: dict[str, Any], received: dict[str, str]) -> dict[str, Any]:
    grades = pair["grades"]
    measure, indices, claim = _choice(pair)
    decisions = []
    for order in pair["orders"]:
        text = received.get(order["id"])
        pick = None if text is None else _pick(text)
        status = "missing" if text is None else "invalid" if pick is None else "valid"
        candidate = None if pick is None else order["positions"][pick - 1]
        decisions.append({"status": status, "pick": pick, "candidate": candidate})
    candidates = [decision["candidate"] for decision in decisions]
    both = None not in candidates
    return {
        **{key: pair[key] for key in ("setting", "run_ids", "grades", "full")},
        "uniform": score._mean(grades),
        "busiest": pair["busiest"],
        "measure": measure,
        "indices": indices,
        "claim": claim,
        "decisions": decisions,
        "both": both,
        "agree": both and candidates[0] == candidates[1],
        "reader": score._mean([grades[c] for c in candidates]) if both else None,
    }


def _outcome(
    rows: list[dict[str, Any]], key: str, base: dict[str, Fraction], eligible: bool
) -> dict[str, Any]:
    mean = score._mean([row[key] for row in rows])
    gain = mean - base["full"]
    better, worse, ties = score._signs([row[key] - row["full"] for row in rows])
    checks = {
        "gain_passes": gain >= GAIN,
        "beats_uniform": mean > base["uniform"],
        "beats_busiest_board": mean > base["busiest"],
    }
    return {
        **_num("mean_grade", mean),
        **_num("mean_delta_vs_full", gain),
        **_num("delta_vs_uniform", mean - base["uniform"]),
        **_num("delta_vs_busiest_board", mean - base["busiest"]),
        "pairs_better_than_full": better,
        "pairs_worse_than_full": worse,
        "pairs_tied_with_full": ties,
        **checks,
        "pass": eligible and all(checks.values()),
    }


def _claims_section(
    rows: list[dict[str, Any]], base: dict[str, Fraction]
) -> dict[str, Any]:
    comparable = [row for row in rows if row["measure"] is not None]
    covered = len(comparable) >= COVERAGE_REQUIRED
    return {
        "pairs": PAIRS,
        "comparable_pairs": len(comparable),
        "coverage_required": COVERAGE_REQUIRED,
        "coverage_passes": covered,
        "abstentions": PAIRS - len(comparable),
        **_num("abstention_fraction", Fraction(PAIRS - len(comparable), PAIRS)),
        "comparable_ties": sum(1 for row in comparable if len(row["indices"]) > 1),
        "measures": {
            name: sum(1 for row in comparable if row["measure"] == name)
            for name in MEASURES
        },
        **_outcome(rows, "claim", base, covered),
    }


def _reader_section(
    rows: list[dict[str, Any]], received: dict[str, str], base: dict[str, Fraction]
) -> dict[str, Any]:
    decisions = [decision for row in rows for decision in row["decisions"]]
    statuses = [decision["status"] for decision in decisions]
    counts = {name: statuses.count(name) for name in ("valid", "invalid", "missing")}
    complete = counts["valid"] == RESPONSES
    both = [row for row in rows if row["both"]]
    agree = sum(1 for row in both if row["agree"])
    performance = _outcome(rows, "reader", base, True) if complete else None
    return {
        "coverage": {
            "expected": RESPONSES,
            "received": len(received),
            **counts,
            **{
                f"{name}_fraction_exact": str(Fraction(count, RESPONSES))
                for name, count in counts.items()
            },
            "complete": complete,
            "pairs_with_both_orders_valid": len(both),
        },
        "order_swap": {
            "agree": agree,
            "denominator": len(both),
            "agreement_exact": str(Fraction(agree, len(both))) if both else None,
        },
        "pick_positions": {
            str(pick): sum(1 for decision in decisions if decision["pick"] == pick)
            for pick in _PICKS
        },
        "performance": performance,
        "pass": performance is not None and performance["pass"],
    }


def _pair_view(row: dict[str, Any], complete: bool) -> dict[str, Any]:
    runs = row["run_ids"]
    decisions = row["decisions"]
    return {
        "setting": row["setting"],
        "run_ids": runs,
        "grades_exact": [str(grade) for grade in row["grades"]],
        "full_exact": str(row["full"]),
        "uniform_exact": str(row["uniform"]),
        "busiest_board_exact": str(row["busiest"]),
        "claims": {
            "measure": row["measure"],
            "abstained": row["measure"] is None,
            "selected_run_ids": [runs[i] for i in row["indices"]],
            "grade_exact": str(row["claim"]),
        },
        "reader": {
            "statuses": [decision["status"] for decision in decisions],
            "selected_run_ids": [
                None if d["candidate"] is None else runs[d["candidate"]]
                for d in decisions
            ],
            "order_swap_agrees": row["agree"] if row["both"] else None,
            "grade_exact": str(row["reader"]) if complete else None,
        },
    }


def _provenance(owner: dict[str, Any], replies: list[dict[str, Any]]) -> dict[str, Any]:
    here = Path(__file__).resolve().parent
    return {
        "registration_commit": owner["registration_commit"],
        "portfolio_sha256": owner["portfolio_sha256"],
        "index_sha256": owner["index_sha256"],
        "boards_sha256": score._canonical_sha256(owner["board_sha256"]),
        "owner_sha256": score._canonical_sha256(owner),
        "replies_sha256": score._canonical_sha256(
            sorted(replies, key=lambda reply: reply["id"])
        ),
        "code_sha256": {
            name: hashlib.sha256((here / name).read_bytes()).hexdigest()
            for name in _CODE
        },
    }


def _design() -> dict[str, str]:
    return {
        "units": "18 half-size pairs: 3 per setting, 6 settings of model and seat budget",
        "claims": "registered final_claims.choose on final-hour claims; abstention takes uniform choice and stays in the denominator; ties average",
        "claims_pass": "at least 12 comparable pairs, mean gain over full size at least the threshold, and strictly above uniform and first-hour busiest selection",
        "reader": 'two presentation orders per pair; a reply must parse as exactly {"pick": 1|2}, indexing the displayed order',
        "reader_pass": "all 36 decisions valid, then the same gain and control tests on the mean over both orders and all pairs",
        "invalid_handling": "missing or invalid replies are incomplete, never a tie or a loss; an incomplete reader panel gets no grade or mean",
        "order_swap": "a pair agrees when both valid orders select the same team; denominator is pairs with both orders valid",
        "arithmetic": "exact rationals; floats are display only",
        "privacy": "no prompts, replies or claim values; run ids, grades and digests only",
    }


def report(owner: dict[str, Any], replies: list[dict[str, str]]) -> dict[str, Any]:
    pairs = _owner(owner)
    orders = {order["id"]: order for pair in pairs for order in pair["orders"]}
    received = score._replies(replies, orders)
    rows = [_row(pair, received) for pair in pairs]
    base = {name: score._mean([row[name] for row in rows]) for name in BASELINES}
    reader = _reader_section(rows, received, base)
    return {
        "schema": SCHEMA,
        "design": _design(),
        "baselines": {
            "weighting": "mean over the 18 pairs; equal to weighting the 6 settings equally",
            **_num("full_size_mean_grade", base["full"]),
            **_num("uniform_mean_grade", base["uniform"]),
            **_num("busiest_board_mean_grade", base["busiest"]),
            **_num("threshold_gain", GAIN),
        },
        "claims": _claims_section(rows, base),
        "reader": reader,
        "pairs": [_pair_view(row, reader["coverage"]["complete"]) for row in rows],
        "provenance": _provenance(owner, replies),
    }
