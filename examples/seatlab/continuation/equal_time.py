from __future__ import annotations

import argparse
import hashlib
import signal
import sys
from collections.abc import Callable
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from . import __main__ as cli
from . import panel, score, source

SCHEMA = "seatlab.continuation-equal-time/v1"
RECEIPT_SCHEMA = "seatlab.continuation-preregistration/v1"
VIEW = "posts"
HOURS = (1, 2, 3, 4)
MINUTES_PER_HOUR = 60
BUDGET_TEAM_MINUTES = 240
SCREEN_MINUTES = 60
SETS = score.COHORTS
RUNS = score.RUNS
ORDERS = score.ORDERS
PAIRS_PER_SET = 3
TEAMS = SETS * RUNS
VOTES = SETS * ORDERS
MAX_RECEIPT_BYTES = 64 * 1024
REFERENCE = "one_team_uniform_4h"
PRIMARY = "pair_screen_busiest_board_3h"
_CODE = ("equal_time.py", "__main__.py", "panel.py", "score.py", "source.py")
_require = score._require

SCHEDULES = {
    "one_team": {"startups": 1, "screen_minutes_each": 0, "grade_hour": 4},
    "pair_screen": {"startups": 2, "screen_minutes_each": SCREEN_MINUTES, "grade_hour": 3},
    "triple_screen": {"startups": 3, "screen_minutes_each": SCREEN_MINUTES, "grade_hour": 2},
}

CONDITIONS = (
    {
        "name": REFERENCE,
        "schedule": "one_team",
        "role": "reference",
        "classification": "deployable_baseline",
        "can_exist": True,
        "information": "none",
        "rule": "start 1 of the 3 recorded teams uniformly at random and keep it for 240 minutes; exact mean of the 3 recorded 4h grades",
    },
    {
        "name": PRIMARY,
        "schedule": "pair_screen",
        "role": "primary",
        "classification": "deployable",
        "can_exist": True,
        "information": "count of public posts before minute 60",
        "rule": "for each of the 3 unordered pairs, keep the team with more first-hour public posts for 120 more minutes and score its 3h grade; ties average the tied 3h grades; average the 3 pairs",
    },
    {
        "name": "pair_screen_uniform_3h",
        "schedule": "pair_screen",
        "role": "diagnostic",
        "classification": "deployable_baseline",
        "can_exist": True,
        "information": "none",
        "rule": "for each pair, keep either team with probability 1/2; exact mean of the 2 recorded 3h grades; average the 3 pairs",
    },
    {
        "name": "pair_screen_hindsight_best_3h",
        "schedule": "pair_screen",
        "role": "diagnostic",
        "classification": "unavailable_oracle",
        "can_exist": False,
        "information": "recorded 3h grades, unknowable at minute 60",
        "rule": "for each pair, the higher recorded 3h grade; average the 3 pairs",
    },
    {
        "name": "triple_screen_busiest_board_2h",
        "schedule": "triple_screen",
        "role": "diagnostic",
        "classification": "deployable",
        "can_exist": True,
        "information": "count of public posts before minute 60",
        "rule": "keep the team with the most first-hour public posts for 60 more minutes and score its 2h grade; ties average the tied 2h grades",
    },
    {
        "name": "triple_screen_original_fulltext_plurality_2h",
        "schedule": "triple_screen",
        "role": "diagnostic",
        "classification": "deployable_needs_supervisor_calls",
        "can_exist": True,
        "information": "the published full-text supervisor's 3 sealed first replies per set",
        "rule": "keep the team with the most original full-text votes for 60 more minutes and score its 2h grade; ties average the tied 2h grades",
    },
    {
        "name": "triple_screen_uniform_2h",
        "schedule": "triple_screen",
        "role": "diagnostic",
        "classification": "deployable_baseline",
        "can_exist": True,
        "information": "none",
        "rule": "keep 1 of the 3 teams uniformly at random; exact mean of the 3 recorded 2h grades",
    },
    {
        "name": "triple_screen_hindsight_best_2h",
        "schedule": "triple_screen",
        "role": "diagnostic",
        "classification": "unavailable_oracle",
        "can_exist": False,
        "information": "recorded 2h grades, unknowable at minute 60",
        "rule": "the highest recorded 2h grade among the 3 teams",
    },
)
_NAMES = tuple(spec["name"] for spec in CONDITIONS)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex(value: Any, label: str) -> str:
    _require(
        type(value) is str and cli._HEX64.fullmatch(value) is not None,
        f"receipt {label} must be a lowercase hex SHA-256",
    )
    return value


def _check_receipt(
    receipt: dict[str, Any], loaded: dict[str, Any], owner: bytes, requests: bytes
) -> None:
    _require(receipt.get("schema") == RECEIPT_SCHEMA, f"receipt schema must be {RECEIPT_SCHEMA}")
    _require(receipt.get("task") == source.TASK, f"receipt task must be {source.TASK}")
    index = _hex(receipt.get("source_index_sha256"), "source_index_sha256")
    _require(index == loaded["index_sha256"], "receipt source index differs from the cache index")
    _require(receipt.get("cohorts") == SETS, f"receipt must declare {SETS} sets")
    _require(receipt.get("original_teams") == TEAMS, f"receipt must declare {TEAMS} teams")
    _require(
        receipt.get("cutoff_exclusive_minutes") == SCREEN_MINUTES,
        f"receipt cutoff must be {SCREEN_MINUTES} minutes",
    )
    posts = sum(run["public_posts"] for cohort in loaded["cohorts"] for run in cohort["runs"])
    _require(
        receipt.get("public_posts_before_cutoff") == posts,
        "receipt public post count differs from the cache",
    )
    views = receipt.get("views")
    view = views.get(VIEW) if type(views) is dict else None
    if not isinstance(view, dict):
        raise TypeError(f"receipt needs views.{VIEW}")
    _require(
        _hex(view.get("owner.json"), "owner.json") == _sha256(owner),
        f"rebuilt {VIEW} owner.json differs from the receipt",
    )
    _require(
        _hex(view.get("requests.jsonl"), "requests.jsonl") == _sha256(requests),
        f"rebuilt {VIEW} requests.jsonl differs from the receipt",
    )


def _inputs(cache: Path, receipt_path: Path, replies_path: Path) -> dict[str, Any]:
    cli._outside_checkout(cache)
    receipt_bytes = cli._read_bounded(receipt_path, MAX_RECEIPT_BYTES)
    receipt = cli._json_object(receipt_bytes, "receipt")
    loaded = source.load(cache)
    owner, requests = panel.prepare(loaded, view=VIEW)
    owner_bytes = cli._encode(owner)
    groups = cli._commitments(cli._json_object(owner_bytes, "owner"))
    lines = cli._request_lines(requests, groups)
    request_bytes = b"".join(lines[order_id] for order_id in cli._expected(groups))
    _check_receipt(receipt, loaded, owner_bytes, request_bytes)
    replies = cli._replies([replies_path], cli._expected(groups))
    _require(len(replies) == VOTES, f"expected {VOTES} first replies, found {len(replies)}")
    curves = source.load_curves(cache, receipt["source_index_sha256"])
    return {
        "receipt": receipt,
        "receipt_sha256": _sha256(receipt_bytes),
        "owner": owner,
        "owner_sha256": _sha256(owner_bytes),
        "requests_sha256": _sha256(request_bytes),
        "replies": replies,
        "curves": curves,
    }


def _rows(owner: dict[str, Any], replies: list[dict[str, str]]) -> list[dict[str, Any]]:
    view, _, cohorts = score._owner(owner)
    _require(view == VIEW, f"owner view must be {VIEW}")
    orders = {order["id"]: order for cohort in cohorts for order in cohort["orders"]}
    received = score._replies(replies, orders)
    _require(len(received) == VOTES, f"expected {VOTES} replies, found {len(received)}")
    rows = [score._score_cohort(cohort, received) for cohort in cohorts]
    _require(len(rows) == SETS, f"expected {SETS} sets")
    for row in rows:
        _require(row["valid"] == ORDERS, f"set {row['key']}: all {ORDERS} first votes must be valid")
        _require(row["plurality"] is not None, f"set {row['key']}: plurality missing")
    _require(sum(row["valid"] for row in rows) == VOTES, f"expected {VOTES} valid votes")
    return rows


def _curves(curves: Any, rows: list[dict[str, Any]]) -> dict[str, dict[int, Fraction]]:
    _require(type(curves) is dict, "load_curves must return a dict")
    run_ids = [run["run_id"] for row in rows for run in row["runs"]]
    _require(len(set(run_ids)) == TEAMS, f"expected {TEAMS} distinct runs")
    _require(set(curves) == set(run_ids), "curve run IDs differ from the enrolled runs")
    for row in rows:
        for run in row["runs"]:
            curve = curves[run["run_id"]]
            label = f"curve {run['run_id']}"
            _require(type(curve) is dict and set(curve) == set(HOURS), f"{label} needs hours {HOURS}")
            for hour in HOURS:
                grade = curve[hour]
                _require(type(grade) is Fraction and 0 <= grade <= 1, f"{label} hour {hour} must be a Fraction in [0, 1]")
            _require(curve[1] == run["mark60"], f"{label}: 1h grade differs from the original mark")
            _require(curve[4] == run["final"], f"{label}: 4h grade differs from the original final")
    return curves


def _tie(
    runs: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any], grade: Callable[[dict[str, Any]], Fraction]
) -> tuple[list[str], Fraction]:
    _require(len(runs) > 0, "tie needs runs")
    best = max(key(run) for run in runs)
    top = [run for run in runs if key(run) == best]
    _require(0 < len(top) <= len(runs), "tie lost its winners")
    return [run["run_id"] for run in top], score._mean([grade(run) for run in top])


def _pair(pair: tuple[dict[str, Any], ...], curves: dict[str, dict[int, Fraction]]) -> dict[str, Any]:
    _require(len(pair) == 2, "pair needs 2 runs")
    three = [curves[run["run_id"]][3] for run in pair]
    busiest_ids, busiest = _tie(list(pair), lambda run: run["public_posts"], lambda run: curves[run["run_id"]][3])
    return {
        "run_ids": [run["run_id"] for run in pair],
        "busiest_board_run_ids": busiest_ids,
        "values": {
            PRIMARY: busiest,
            "pair_screen_uniform_3h": score._mean(three),
            "pair_screen_hindsight_best_3h": max(three),
        },
    }


def _set(row: dict[str, Any], curves: dict[str, dict[int, Fraction]]) -> dict[str, Any]:
    runs = row["runs"]
    _require(len(runs) == RUNS, f"set {row['key']} needs {RUNS} runs")
    pairs = [_pair(pair, curves) for pair in combinations(runs, 2)]
    _require(len(pairs) == PAIRS_PER_SET, f"set {row['key']} needs {PAIRS_PER_SET} pairs")
    two = [curves[run["run_id"]][2] for run in runs]
    four = [curves[run["run_id"]][4] for run in runs]
    busiest_ids, busiest = _tie(runs, lambda run: run["public_posts"], lambda run: curves[run["run_id"]][2])
    plurality_ids, plurality = _tie(runs, lambda run: run["votes"], lambda run: curves[run["run_id"]][2])
    _require(plurality_ids == row["plurality_run_ids"], f"set {row['key']}: plurality winners disagree")
    _require(score._mean(four) == row["uniform"], f"set {row['key']}: uniform 4h disagrees")
    values = {
        REFERENCE: score._mean(four),
        **{
            name: score._mean([pair["values"][name] for pair in pairs])
            for name in pairs[0]["values"]
        },
        "triple_screen_busiest_board_2h": busiest,
        "triple_screen_original_fulltext_plurality_2h": plurality,
        "triple_screen_uniform_2h": score._mean(two),
        "triple_screen_hindsight_best_2h": max(two),
    }
    _require(tuple(sorted(values)) == tuple(sorted(_NAMES)), "set values must cover every condition")
    return {
        "row": row,
        "pairs": pairs,
        "values": values,
        "selected": {
            "triple_screen_busiest_board_2h": busiest_ids,
            "triple_screen_original_fulltext_plurality_2h": plurality_ids,
        },
    }


def _summary(sets: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    name = spec["name"]
    values = [item["values"][name] for item in sets]
    deltas = [item["values"][name] - item["values"][REFERENCE] for item in sets]
    _require(len(values) == SETS, f"{name} needs {SETS} sets")
    positive, negative, ties = score._signs(deltas)
    p_value = score._sign_p(positive, negative)
    mean = score._mean(values)
    delta = score._mean(deltas)
    return {
        **spec,
        "sets": len(values),
        "mean_grade": float(mean),
        "mean_grade_exact": str(mean),
        "mean_delta_vs_reference": float(delta),
        "mean_delta_vs_reference_exact": str(delta),
        "positive": positive,
        "negative": negative,
        "ties": ties,
        "sign_p": float(p_value),
        "sign_p_exact": str(p_value),
    }


def _primary(summary: dict[str, Any]) -> dict[str, Any]:
    delta = Fraction(summary["mean_delta_vs_reference_exact"])
    p_value = Fraction(summary["sign_p_exact"])
    mean_passes = delta >= score.THRESHOLD
    p_passes = p_value <= score.ALPHA
    passed = mean_passes and p_passes
    return {
        "condition": PRIMARY,
        "reference": REFERENCE,
        "metric": "per-set mean over 3 pairs of the busiest-board 3h grade minus the per-set uniform 4h grade; sets weighted equally",
        "test": "exact one-sided sign test across the 9 set deltas, zero deltas omitted; the 27 pairs are not independent",
        "threshold_mean": float(score.THRESHOLD),
        "threshold_mean_exact": str(score.THRESHOLD),
        "alpha": float(score.ALPHA),
        "alpha_exact": str(score.ALPHA),
        "mean_delta": float(delta),
        "mean_delta_exact": str(delta),
        "positive": summary["positive"],
        "negative": summary["negative"],
        "ties": summary["ties"],
        "sign_p": float(p_value),
        "sign_p_exact": str(p_value),
        "mean_passes": mean_passes,
        "p_passes": p_passes,
        "pass": passed,
        "verdict": "recommend_prospective_replication" if passed else "do_not_recommend",
    }


def _time() -> dict[str, Any]:
    schedules = {}
    for name, plan in SCHEDULES.items():
        wall = plan["grade_hour"] * MINUTES_PER_HOUR
        continued = wall - plan["screen_minutes_each"]
        team = plan["startups"] * plan["screen_minutes_each"] + continued
        _require(team == BUDGET_TEAM_MINUTES, f"schedule {name} must use {BUDGET_TEAM_MINUTES} team-minutes")
        schedules[name] = {
            **plan,
            "kept_teams": 1,
            "kept_team_minutes_after_screen": continued,
            "team_minutes": team,
            "implied_wall_clock_minutes": wall,
        }
    seat = {str(size): BUDGET_TEAM_MINUTES * size for size in score.TEAM_SIZES}
    return {
        "budget_team_minutes": BUDGET_TEAM_MINUTES,
        "basis": "cumulative team-minutes from the schedule; wall clock assumes all started teams run in parallel on enough machines; no sandbox was resumed and no job was stopped",
        "schedules": schedules,
        "seat_minutes_by_team_size": seat,
        "seat_minutes_range": [min(seat.values()), max(seat.values())],
        "money": "not inferred; schedules do not necessarily cost the same in tokens or dollars",
    }


def _set_view(item: dict[str, Any], curves: dict[str, dict[int, Fraction]]) -> dict[str, Any]:
    row = item["row"]
    reference = item["values"][REFERENCE]
    return {
        "key": row["key"],
        "model": row["model"],
        "team_size": row["team_size"],
        "seat_minutes_per_schedule": BUDGET_TEAM_MINUTES * row["team_size"],
        "runs": [
            {
                "run_id": run["run_id"],
                "public_posts_before_minute_60": run["public_posts"],
                "original_fulltext_votes": run["votes"],
                "grades": {f"{hour}h": float(curves[run["run_id"]][hour]) for hour in HOURS},
            }
            for run in row["runs"]
        ],
        "pairs": [
            {
                "run_ids": pair["run_ids"],
                "busiest_board_run_ids": pair["busiest_board_run_ids"],
                **{name: float(value) for name, value in pair["values"].items()},
            }
            for pair in item["pairs"]
        ],
        "selected_run_ids": item["selected"],
        "conditions": {
            name: {
                "grade": float(value),
                "grade_exact": str(value),
                "delta_vs_reference": float(value - reference),
                "delta_vs_reference_exact": str(value - reference),
            }
            for name, value in item["values"].items()
        },
    }


def _provenance(inputs: dict[str, Any], curves: dict[str, dict[int, Fraction]]) -> dict[str, Any]:
    here = Path(__file__).resolve().parent
    return {
        "source_index_sha256": inputs["receipt"]["source_index_sha256"],
        "receipt_sha256": inputs["receipt_sha256"],
        "receipt_protocol_revision": inputs["receipt"].get("protocol_revision"),
        "posts_owner_sha256": inputs["owner_sha256"],
        "posts_requests_sha256": inputs["requests_sha256"],
        "replies_sha256": score._canonical_sha256(sorted(inputs["replies"], key=lambda reply: reply["id"])),
        "curves_sha256": score._canonical_sha256(
            {run: {str(hour): str(grade) for hour, grade in curve.items()} for run, curve in curves.items()}
        ),
        "code_sha256": {name: _sha256((here / name).read_bytes()) for name in _CODE},
    }


def analyze(cache: Path, receipt_path: Path, replies_path: Path) -> dict[str, Any]:
    inputs = _inputs(cache, receipt_path, replies_path)
    rows = _rows(inputs["owner"], inputs["replies"])
    curves = _curves(inputs["curves"], rows)
    sets = [_set(row, curves) for row in rows]
    summaries = {spec["name"]: _summary(sets, spec) for spec in CONDITIONS}
    provenance = _provenance(inputs, curves)
    return {
        "schema": SCHEMA,
        "question": "Is screening worth doing at equal cumulative team time?",
        "provenance": provenance,
        "provenance_sha256": score._canonical_sha256(provenance),
        "design": {
            "units": "set: one model and one team size, 3 recorded teams",
            "sets": SETS,
            "teams": TEAMS,
            "pairs_per_set": PAIRS_PER_SET,
            "original_votes": VOTES,
            "weighting": "average pairs within a set, then weight the 9 sets equally",
            "ties": "every tie averages the tied teams' grades",
            "arithmetic": "exact rationals from the shortest decimal form of each recorded grade; floats are display only",
            "exclusions": "no message text, private posts or supervisor reply text appear in this report",
        },
        "time": _time(),
        "primary": _primary(summaries[PRIMARY]),
        "conditions": summaries,
        "classification": {
            "can_exist": [spec["name"] for spec in CONDITIONS if spec["can_exist"]],
            "oracle_unavailable": [spec["name"] for spec in CONDITIONS if not spec["can_exist"]],
        },
        "sets": [_set_view(item, curves) for item in sets],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.continuation.equal_time",
        description="Compare equal team-time screening schedules on the recorded checkpoint grades.",
        allow_abbrev=False,
    )
    parser.add_argument("cache", type=Path, metavar="CACHE")
    parser.add_argument("receipt", type=Path, metavar="PUBLIC_INPUT_RECEIPT")
    parser.add_argument("replies", type=Path, metavar="POSTS_FIRST.jsonl")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output = cli._encode(analyze(args.cache, args.receipt, args.replies))
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
