import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from math import comb, isfinite
from typing import Any, NoReturn

SCHEMA = "seatlab.continuation-report/v1"
OWNER_SCHEMA = "seatlab.continuation-owner/v1"
TEAM_SIZES = (2, 4, 8)
MODELS = 3
RUNS = 3
ORDERS = 3
COHORTS = MODELS * len(TEAM_SIZES)
RESPONSES = COHORTS * ORDERS
THRESHOLD = Fraction(3, 100)
ALPHA = Fraction(1, 20)
SCREEN_MINUTES = 60
FINISH_MINUTES = 240
_HEX = frozenset("0123456789abcdef")
_OWNER_FIELDS = frozenset({"schema", "view", "index_sha256", "cohorts"})
VIEWS = ("posts", "activity")
_COHORT_FIELDS = frozenset({"key", "model", "team_size", "runs", "orders"})
_RUN_FIELDS = frozenset({"run_id", "board_sha256", "public_posts", "mark60", "final"})
_ORDER_FIELDS = frozenset({"id", "prompt_sha256", "run_ids"})
_REPLY_FIELDS = frozenset({"id", "prompt_sha256", "reply"})
_PICK_FIELDS = frozenset({"pick"})
_PICKS = (1, 2, 3)

_COMPARISONS = (
    {
        "name": "plurality",
        "role": "primary",
        "information": "panel replies",
        "rule": "only cohorts with all three replies valid: continue the run with the most votes; ties take the uniform expectation over the top vote recipients",
    },
    {
        "name": "uniform",
        "role": "baseline",
        "information": "none",
        "rule": "exact expected final of a uniform random choice among the three runs",
    },
    {
        "name": "busiest_posts",
        "role": "deployable_descriptive",
        "information": "count of public posts before minute 60, visible to the panel",
        "rule": "continue the run with the most first-hour public posts; ties averaged",
    },
    {
        "name": "highest_mark60",
        "role": "privileged_descriptive",
        "information": "verifier mark at minute 60, hidden from the panel",
        "rule": "continue the run with the highest minute-60 verifier mark; ties averaged",
    },
    {
        "name": "oracle",
        "role": "unavailable_descriptive",
        "information": "recorded 4h finals, unknowable at minute 60",
        "rule": "hindsight best final among the three runs",
    },
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _record(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    _require(
        type(value) is dict and value.keys() == fields,
        f"{label} fields must be exactly {sorted(fields)}",
    )
    return value


def _sha256(value: Any, label: str) -> str:
    _require(
        type(value) is str and len(value) == 64 and set(value) <= _HEX,
        f"{label} must be a lowercase sha256 hex digest",
    )
    return value


def _text(value: Any, label: str) -> str:
    _require(type(value) is str and value != "", f"{label} must be a non-empty string")
    return value


def _exact(value: Any, label: str) -> Fraction:
    _require(type(value) in (int, float), f"{label} must be a JSON number")
    if type(value) is float:
        _require(isfinite(value), f"{label} must be finite")
        return Fraction(Decimal(repr(value)))
    return Fraction(value)


def _unit(value: Any, label: str) -> Fraction:
    exact = _exact(value, label)
    _require(0 <= exact <= 1, f"{label} must be within [0, 1]")
    return exact


def _mean(values: list[Fraction]) -> Fraction:
    _require(len(values) > 0, "mean needs at least one value")
    return sum(values, Fraction(0)) / len(values)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _run(value: Any, label: str) -> dict[str, Any]:
    run = _record(value, _RUN_FIELDS, label)
    posts = run["public_posts"]
    _require(
        type(posts) is int and posts >= 0,
        f"{label} public_posts must be a non-negative int",
    )
    return {
        "run_id": _text(run["run_id"], f"{label} run_id"),
        "board_sha256": _sha256(run["board_sha256"], f"{label} board_sha256"),
        "public_posts": posts,
        "mark60": _unit(run["mark60"], f"{label} mark60"),
        "final": _unit(run["final"], f"{label} final"),
    }


def _orders(values: Any, run_ids: list[str], label: str) -> list[dict[str, Any]]:
    _require(
        type(values) is list and len(values) == ORDERS, f"{label} needs {ORDERS} orders"
    )
    orders = []
    for index, value in enumerate(values):
        order = _record(value, _ORDER_FIELDS, f"{label} order {index}")
        shown = order["run_ids"]
        _require(
            type(shown) is list
            and len(shown) == RUNS
            and all(type(item) is str for item in shown)
            and sorted(shown) == sorted(run_ids),
            f"{label} order {index} must show each cohort run once",
        )
        orders.append(
            {
                "id": _text(order["id"], f"{label} order {index} id"),
                "prompt_sha256": _sha256(
                    order["prompt_sha256"], f"{label} order {index} prompt_sha256"
                ),
                "run_ids": tuple(shown),
            }
        )
    for position in range(RUNS):
        _require(
            len({order["run_ids"][position] for order in orders}) == RUNS,
            f"{label} orders must be cyclic: each run once in each position",
        )
    return orders


def _cohort(value: Any, label: str) -> dict[str, Any]:
    cohort = _record(value, _COHORT_FIELDS, label)
    model = _text(cohort["model"], f"{label} model")
    size = cohort["team_size"]
    _require(
        type(size) is int and size in TEAM_SIZES,
        f"{label} team_size must be one of {TEAM_SIZES}",
    )
    _require(
        cohort["key"] == f"{model}:k{size}", f"{label} key must be '{model}:k{size}'"
    )
    values = cohort["runs"]
    _require(type(values) is list and len(values) == RUNS, f"{label} needs {RUNS} runs")
    runs = [_run(item, f"{label} run {index}") for index, item in enumerate(values)]
    run_ids = [run["run_id"] for run in runs]
    _require(len(set(run_ids)) == RUNS, f"{label} run_ids must be distinct")
    return {
        "key": cohort["key"],
        "model": model,
        "team_size": size,
        "runs": runs,
        "orders": _orders(cohort["orders"], run_ids, label),
    }


def _owner(owner: Any) -> tuple[str, str, list[dict[str, Any]]]:
    record = _record(owner, _OWNER_FIELDS, "owner")
    _require(record["schema"] == OWNER_SCHEMA, f"owner schema must be {OWNER_SCHEMA}")
    index = _sha256(record["index_sha256"], "owner index_sha256")
    view = record["view"]
    _require(type(view) is str and view in VIEWS, f"owner view must be one of {VIEWS}")
    values = record["cohorts"]
    _require(
        type(values) is list and len(values) == COHORTS,
        f"owner needs {COHORTS} cohorts",
    )
    cohorts = [_cohort(item, f"cohort {index}") for index, item in enumerate(values)]
    models = {cohort["model"] for cohort in cohorts}
    _require(len(models) == MODELS, f"owner needs exactly {MODELS} models")
    cells = {(cohort["model"], cohort["team_size"]) for cohort in cohorts}
    _require(
        cells == {(model, size) for model in models for size in TEAM_SIZES},
        "owner cohorts must cover every model and team size exactly once",
    )
    runs = [run["run_id"] for cohort in cohorts for run in cohort["runs"]]
    _require(
        len(set(runs)) == COHORTS * RUNS, "run_ids must be distinct across cohorts"
    )
    orders = [order for cohort in cohorts for order in cohort["orders"]]
    _require(
        len({order["id"] for order in orders}) == RESPONSES,
        "order ids must be distinct",
    )
    return view, index, cohorts


def _replies(values: Any, orders: dict[str, dict[str, Any]]) -> dict[str, str]:
    _require(type(values) is list, "replies must be a list")
    received: dict[str, str] = {}
    for index, value in enumerate(values):
        reply = _record(value, _REPLY_FIELDS, f"reply {index}")
        identifier = reply["id"]
        _require(
            type(identifier) is str and identifier in orders,
            f"reply {index} has an unknown id",
        )
        _require(
            identifier not in received, f"reply {index} duplicates id {identifier}"
        )
        _require(
            reply["prompt_sha256"] == orders[identifier]["prompt_sha256"],
            f"reply {index} is stale: prompt_sha256 does not match the owner order",
        )
        _require(type(reply["reply"]) is str, f"reply {index} reply must be a string")
        received[identifier] = reply["reply"]
    return received


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _pick(text: str) -> int | None:
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        return None
    if type(value) is not dict or value.keys() != _PICK_FIELDS:
        return None
    pick = value["pick"]
    if type(pick) is not int or pick not in _PICKS:
        return None
    return pick


def _tie_mean(
    runs: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]
) -> tuple[list[str], Fraction]:
    best = max(key(run) for run in runs)
    top = [run for run in runs if key(run) == best]
    return [run["run_id"] for run in top], _mean([run["final"] for run in top])


def _responses(
    cohort: dict[str, Any], received: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    votes = {run["run_id"]: 0 for run in cohort["runs"]}
    responses = []
    for order in cohort["orders"]:
        text = received.get(order["id"])
        pick = None if text is None else _pick(text)
        chosen = None if pick is None else order["run_ids"][pick - 1]
        if chosen is not None:
            votes[chosen] += 1
        status = "missing" if text is None else "invalid" if pick is None else "valid"
        responses.append(
            {
                "id": order["id"],
                "prompt_sha256": order["prompt_sha256"],
                "run_ids": list(order["run_ids"]),
                "status": status,
                "pick": pick,
                "run_id": chosen,
                "reply_sha256": None
                if text is None
                else hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    return responses, votes


def _score_cohort(cohort: dict[str, Any], received: dict[str, str]) -> dict[str, Any]:
    runs = cohort["runs"]
    responses, votes = _responses(cohort, received)
    valid = sum(votes.values())
    uniform = _mean([run["final"] for run in runs])
    plurality_ids: list[str] = []
    plurality: Fraction | None = None
    largest: Fraction | None = None
    if valid == ORDERS:
        plurality_ids, plurality = _tie_mean(runs, lambda run: votes[run["run_id"]])
        largest = Fraction(max(votes.values()), valid)
    posts_ids, posts = _tie_mean(runs, lambda run: run["public_posts"])
    mark_ids, mark = _tie_mean(runs, lambda run: run["mark60"])
    oracle_ids, oracle = _tie_mean(runs, lambda run: run["final"])
    return {
        "key": cohort["key"],
        "model": cohort["model"],
        "team_size": cohort["team_size"],
        "runs": [{**run, "votes": votes[run["run_id"]]} for run in runs],
        "responses": responses,
        "valid": valid,
        "largest_vote_fraction": largest,
        "plurality_run_ids": plurality_ids,
        "plurality": plurality,
        "uniform": uniform,
        "busiest_posts_run_ids": posts_ids,
        "busiest_posts": posts,
        "highest_mark60_run_ids": mark_ids,
        "highest_mark60": mark,
        "oracle_run_ids": oracle_ids,
        "oracle": oracle,
    }


def _number(value: Fraction | None) -> float | None:
    return None if value is None else float(value)


def _signs(deltas: list[Fraction]) -> tuple[int, int, int]:
    positive = sum(1 for delta in deltas if delta > 0)
    negative = sum(1 for delta in deltas if delta < 0)
    return positive, negative, len(deltas) - positive - negative


def _sign_p(positive: int, negative: int) -> Fraction:
    trials = positive + negative
    tail = sum(comb(trials, count) for count in range(positive, trials + 1))
    return Fraction(tail, 2**trials)


def _assignment_p(rows: list[dict[str, Any]]) -> Fraction:
    _require(len(rows) == COHORTS, f"assignment test needs {COHORTS} complete cohorts")
    support: dict[Fraction, int] = {Fraction(0): 1}
    total = 1
    observed = Fraction(0)
    for row in rows:
        _require(row["plurality"] is not None, "assignment test needs complete cohorts")
        finals = [run["final"] for run in row["runs"]]
        outcomes = [
            _mean(list(subset))
            for subset in combinations(finals, len(row["plurality_run_ids"]))
        ]
        merged: dict[Fraction, int] = {}
        for value, count in support.items():
            for outcome in outcomes:
                merged[value + outcome] = merged.get(value + outcome, 0) + count
        support = merged
        total *= len(outcomes)
        observed += row["plurality"]
    _require(sum(support.values()) == total, "assignment support lost multiplicity")
    return Fraction(
        sum(count for value, count in support.items() if value >= observed), total
    )


def _strategy(rows: list[dict[str, Any]], spec: dict[str, str]) -> dict[str, Any]:
    name = spec["name"]
    pairs = [(row[name], row["uniform"]) for row in rows if row[name] is not None]
    deltas = [value - uniform for value, uniform in pairs]
    positive, negative, ties = _signs(deltas)
    count = len(pairs)
    return {
        **spec,
        "cohorts": count,
        "mean_final": None
        if count == 0
        else float(_mean([value for value, _ in pairs])),
        "mean_delta_vs_uniform": None if count == 0 else float(_mean(deltas)),
        "positive": positive,
        "negative": negative,
        "ties": ties,
    }


def _primary(rows: list[dict[str, Any]], complete: bool) -> dict[str, Any]:
    deltas = [
        row["plurality"] - row["uniform"]
        for row in rows
        if row["plurality"] is not None
    ]
    positive, negative, ties = _signs(deltas)
    count = len(deltas)
    mean = None if count == 0 else _mean(deltas)
    p_value = _sign_p(positive, negative)
    mean_passes = mean is not None and mean >= THRESHOLD
    p_passes = p_value <= ALPHA
    scored = complete and count == COHORTS
    assignment = _assignment_p(rows) if scored else None
    assignment_passes = assignment is not None and assignment <= ALPHA
    passed = scored and mean_passes and p_passes and assignment_passes
    return {
        "metric": "per-cohort plurality final minus uniform expected final; cohorts are the units",
        "test": "exact one-sided sign test, zero deltas omitted, H0 equal sign probability",
        "threshold_mean": float(THRESHOLD),
        "alpha": float(ALPHA),
        "cohorts": count,
        "complete": complete,
        "mean_paired_improvement": _number(mean),
        "mean_paired_improvement_exact": None if mean is None else str(mean),
        "positive": positive,
        "negative": negative,
        "ties": ties,
        "sign_p": float(p_value),
        "sign_p_exact": str(p_value),
        "mean_passes": mean_passes,
        "p_passes": p_passes,
        "assignment_test": "exact one-sided randomization test of the sum of plurality finals; computed only when all 9 cohorts are complete, otherwise null",
        "assignment_null": "conditional on each cohort's vote tallies and its three finals, cards map to outcomes uniformly at random, independently across cohorts; with s tied plurality winners each of the C(3,s) outcome subsets is equally likely and scores their mean final; p is the probability that the summed plurality finals reach the observed sum",
        "assignment_p_exact": None if assignment is None else str(assignment),
        "assignment_p": _number(assignment),
        "assignment_passes": assignment_passes,
        "pass": passed,
        "verdict": "incomplete" if not complete else "pass" if passed else "fail",
    }


def _coverage(rows: list[dict[str, Any]], received: dict[str, str]) -> dict[str, Any]:
    statuses = [response["status"] for row in rows for response in row["responses"]]
    valid = statuses.count("valid")
    return {
        "expected": RESPONSES,
        "received": len(received),
        "valid": valid,
        "invalid": statuses.count("invalid"),
        "missing": statuses.count("missing"),
        "complete": valid == RESPONSES,
        "cohorts": len(rows),
        "cohorts_with_votes": sum(1 for row in rows if row["valid"] > 0),
        "cohorts_fully_valid": sum(1 for row in rows if row["valid"] == ORDERS),
    }


def _clock() -> dict[str, Any]:
    continued = FINISH_MINUTES - SCREEN_MINUTES
    strategy = RUNS * SCREEN_MINUTES + continued
    full = RUNS * FINISH_MINUTES
    return {
        "basis": "recorded team wall-clock runtime per cohort; every arm uses the same team size k within a cohort; not measured compute, tokens or money",
        "screen_runs": RUNS,
        "screen_minutes_each": SCREEN_MINUTES,
        "continued_runs": 1,
        "continued_minutes": continued,
        "strategy_team_minutes": strategy,
        "full_team_minutes": full,
        "ratio": strategy / full,
        "cohorts": COHORTS,
        "total_strategy_team_minutes": COHORTS * strategy,
        "total_full_team_minutes": COHORTS * full,
    }


def _cohort_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": row["key"],
        "model": row["model"],
        "team_size": row["team_size"],
        "runs": [
            {**run, "mark60": float(run["mark60"]), "final": float(run["final"])}
            for run in row["runs"]
        ],
        "responses": row["responses"],
        "valid": row["valid"],
        "largest_vote_fraction": _number(row["largest_vote_fraction"]),
        "plurality_run_ids": row["plurality_run_ids"],
        "plurality_final": _number(row["plurality"]),
        "uniform_final": float(row["uniform"]),
        "delta_vs_uniform": None
        if row["plurality"] is None
        else float(row["plurality"] - row["uniform"]),
        "busiest_posts_run_ids": row["busiest_posts_run_ids"],
        "busiest_posts_final": float(row["busiest_posts"]),
        "highest_mark60_run_ids": row["highest_mark60_run_ids"],
        "highest_mark60_final": float(row["highest_mark60"]),
        "oracle_run_ids": row["oracle_run_ids"],
        "oracle_final": float(row["oracle"]),
    }


def _positions(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {str(pick): 0 for pick in _PICKS}
    for row in rows:
        for response in row["responses"]:
            if response["pick"] is not None:
                counts[str(response["pick"])] += 1
    return counts


def report(owner: dict[str, Any], replies: list[dict[str, Any]]) -> dict[str, Any]:
    view, index, cohorts = _owner(owner)
    orders = {order["id"]: order for cohort in cohorts for order in cohort["orders"]}
    received = _replies(replies, orders)
    rows = [_score_cohort(cohort, received) for cohort in cohorts]
    coverage = _coverage(rows, received)
    return {
        "schema": SCHEMA,
        "view": view,
        "index_sha256": index,
        "owner_sha256": _canonical_sha256(owner),
        "replies_sha256": _canonical_sha256(
            sorted(replies, key=lambda reply: reply["id"])
        ),
        "design": {
            "units": "cohort",
            "cohorts": COHORTS,
            "runs_per_cohort": RUNS,
            "orders_per_cohort": ORDERS,
            "responses_expected": RESPONSES,
            "independence": "the 27 responses are 3 cyclic presentation orders per cohort and are not independent; the 9 disjoint cohorts are the analysis units",
            "pick": 'reply must parse as exactly {"pick": 1|2|3}; pick indexes the displayed order',
            "invalid_handling": "missing or invalid replies cast no vote and never score as zero; a cohort shows its tallies but gets no plurality outcome or paired effect until all three replies are valid; the primary pass needs all 9 cohorts",
            "arithmetic": "exact rationals from the shortest decimal form of each JSON number; floats are display only",
        },
        "comparisons": [dict(spec) for spec in _COMPARISONS],
        "coverage": coverage,
        "primary": _primary(rows, coverage["complete"]),
        "strategies": {spec["name"]: _strategy(rows, spec) for spec in _COMPARISONS},
        "pick_positions": _positions(rows),
        "clock": _clock(),
        "cohorts": [_cohort_view(row) for row in rows],
    }
