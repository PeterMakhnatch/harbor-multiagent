from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from . import __main__ as cli
from . import equal_time, score, source

SCHEMA = "seatlab.continuation-portfolio/v1"
INPUT_SHA256 = "bfc0d88061046e4a829247327126bfd0ffac7d1e6fce9771b89625f36955e691"
INPUT_COMMIT = "2ae698a5d339bc6f1abe693e55b7f3722388b4f4"
REGISTRATION_COMMIT = "65aff2acbb4e38090a465213c7033ba2cd9c5e1c"
REGISTRATION = "examples/seatlab/continuation-study.md: What if all the smaller teams finish?"
INPUT_MAX_BYTES = 64 * 1024
SEAT_BUDGETS = (4, 8)
POLICIES = ("uniform", "busiest_board", "perfect")
GAIN = score.THRESHOLD
WINS_REQUIRED = 4
GRADE_KEYS = tuple(f"{hour}h" for hour in source.CURVE_HOURS)
FINAL = f"{source.FINAL_HOUR}h"
MINUTES = equal_time.BUDGET_TEAM_MINUTES
_RUN_FIELDS = frozenset({"grades", "original_fulltext_votes", "public_posts_before_minute_60", "run_id"})
_SET_FIELDS = frozenset(
    {"conditions", "key", "model", "pairs", "runs", "seat_minutes_per_schedule", "selected_run_ids", "team_size"}
)
_CODE = ("portfolio.py", "equal_time.py", "score.py", "source.py", "panel.py", "__main__.py")
_require = score._require

LIMITATIONS = (
    "retrospective: the 4-hour grades were already published and used in the equal-time result",
    "the 4-seat source teams are the full-size arm at 4 seats and the half-size arm at 8 seats",
    "no paired seeds and no independence between settings or pairs, so no p-value is assigned",
    "perfect selection reads the final grade in hindsight; it is a feasibility bound, not a selector result",
    "the archive records public post counts; the posts describe work but do not independently verify it",
    "seat-minutes and elapsed minutes are scheduled limits, not measured active time, tokens or dollars",
    "a pair doubles startups and cumulative team-minutes; equal seat-minutes do not imply equal spending",
    "one task, 3 models and 3 recorded rollouts per cell; a source repeat is not proof of an independent world",
)


def _num(name: str, value: Fraction) -> dict[str, Any]:
    return {name: float(value), f"{name}_exact": str(value)}


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = handle.read(INPUT_MAX_BYTES + 1)
    _require(len(data) <= INPUT_MAX_BYTES, f"{path}: larger than {INPUT_MAX_BYTES} bytes")
    digest = hashlib.sha256(data).hexdigest()
    _require(digest == INPUT_SHA256, f"{path}: sha256 {digest} is not the released {INPUT_SHA256}")
    document = json.loads(data, object_pairs_hook=cli._unique_keys, parse_constant=cli._reject_constant)
    _require(type(document) is dict, "input must be a JSON object")
    _require(document.get("schema") == equal_time.SCHEMA, f"input schema must be {equal_time.SCHEMA}")
    provenance = document.get("provenance")
    _require(type(provenance) is dict, "input provenance must be an object")
    _require(
        document.get("provenance_sha256") == score._canonical_sha256(provenance),
        "input provenance_sha256 does not match its provenance",
    )
    for name in ("source_index_sha256", "receipt_sha256", "curves_sha256", "replies_sha256"):
        score._sha256(provenance.get(name), f"provenance.{name}")
    return document


def _run(value: Any, expected: str) -> dict[str, Any]:
    run = score._record(value, _RUN_FIELDS, expected)
    _require(run["run_id"] == expected, f"run {run['run_id']!r} must be {expected}")
    posts = run["public_posts_before_minute_60"]
    _require(type(posts) is int and posts >= 0, f"{expected}: first-hour posts must be a non-negative integer")
    votes = run["original_fulltext_votes"]
    _require(type(votes) is int and 0 <= votes <= equal_time.ORDERS, f"{expected}: votes out of range")
    grades = score._record(run["grades"], frozenset(GRADE_KEYS), f"{expected}.grades")
    exact = {key: score._unit(grades[key], f"{expected}.grades.{key}") for key in GRADE_KEYS}
    return {"run_id": expected, "posts": posts, "final": exact[FINAL]}


def _cells(document: dict[str, Any]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    sets = document["sets"]
    _require(type(sets) is list and len(sets) == equal_time.SETS, f"input needs {equal_time.SETS} sets")
    cells: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for index, value in enumerate(sets):
        row = score._record(value, _SET_FIELDS, f"sets[{index}]")
        model, size = row["model"], row["team_size"]
        _require(model in source.MODELS, f"sets[{index}]: unregistered model {model!r}")
        _require(type(size) is int and size in source.TEAM_SIZES, f"sets[{index}]: unregistered size {size!r}")
        _require(row["key"] == f"{model}:k{size}" and (model, size) not in cells, f"sets[{index}]: bad or repeated key")
        _require(row["seat_minutes_per_schedule"] == size * MINUTES, f"{row['key']}: seat-minutes disagree")
        _require(type(row["runs"]) is list and len(row["runs"]) == len(source.ROLLOUTS), f"{row['key']}: run count")
        runs = [
            _run(item, source.run_id(model, size, rollout))
            for item, rollout in zip(row["runs"], source.ROLLOUTS, strict=True)
        ]
        uniform = Fraction(row["conditions"][equal_time.REFERENCE]["grade_exact"])
        _require(uniform == score._mean([run["final"] for run in runs]), f"{row['key']}: uniform 4h disagrees")
        cells[(model, size)] = runs
    ids = [run["run_id"] for runs in cells.values() for run in runs]
    _require(len(ids) == len(set(ids)) == source.TEAM_RUNS, f"input must enroll {source.TEAM_RUNS} unique runs")
    return cells


def _pair(pair: tuple[dict[str, Any], ...], full: Fraction) -> tuple[dict[str, Any], dict[str, Fraction]]:
    _require(len(pair) == 2, "pair needs 2 runs")
    runs = list(pair)
    busiest_ids, busiest = equal_time._tie(runs, lambda run: run["posts"], lambda run: run["final"])
    perfect_ids, perfect = equal_time._tie(runs, lambda run: run["final"], lambda run: run["final"])
    values = {"uniform": score._mean([run["final"] for run in runs]), "busiest_board": busiest, "perfect": perfect}
    _require(all(value <= perfect for value in values.values()), "perfect selection must bound every policy")
    selected = {"uniform": [run["run_id"] for run in runs], "busiest_board": busiest_ids, "perfect": perfect_ids}
    view = {
        "run_ids": [run["run_id"] for run in runs],
        "first_hour_public_posts": {run["run_id"]: run["posts"] for run in runs},
        "policies": {
            name: {
                "selected_run_ids": selected[name],
                **_num("grade", values[name]),
                **_num("delta_vs_full", values[name] - full),
            }
            for name in POLICIES
        },
    }
    return view, values


def _resources(budget: int) -> dict[str, Any]:
    half = budget // 2
    arms = {
        "full_size": {"teams": 1, "seats_per_team": budget, "team_minutes": MINUTES},
        "half_size_pair": {"teams": 2, "seats_per_team": half, "team_minutes": 2 * MINUTES},
    }
    for arm in arms.values():
        arm["seat_minutes"] = arm["teams"] * arm["seats_per_team"] * MINUTES
        arm["elapsed_minutes"] = MINUTES
    _require(arms["full_size"]["seat_minutes"] == arms["half_size_pair"]["seat_minutes"], "seat-minutes must match")
    return arms


def _setting(model: str, budget: int, cells: dict[tuple[str, int], list[dict[str, Any]]]) -> dict[str, Any]:
    full_runs, half_runs = cells[(model, budget)], cells[(model, budget // 2)]
    full = score._mean([run["final"] for run in full_runs])
    pairs = [_pair(pair, full) for pair in combinations(half_runs, 2)]
    _require(len(pairs) == equal_time.PAIRS_PER_SET, f"{model}:s{budget} needs {equal_time.PAIRS_PER_SET} pairs")
    values = {name: score._mean([pair[name] for _, pair in pairs]) for name in POLICIES}
    policies = {}
    for name in POLICIES:
        deltas = [pair[name] - full for _, pair in pairs]
        better, worse, ties = score._signs(deltas)
        policies[name] = {
            **_num("mean_grade", values[name]),
            **_num("delta_vs_full", values[name] - full),
            "pairs_better_than_full": better,
            "pairs_worse_than_full": worse,
            "pairs_tied_with_full": ties,
        }
    return {
        "key": f"{model}:s{budget}",
        "model": model,
        "seat_budget": budget,
        "full_size": {"team_size": budget, "run_ids": [run["run_id"] for run in full_runs], **_num("mean_grade", full)},
        "half_size": {"team_size": budget // 2, "run_ids": [run["run_id"] for run in half_runs]},
        "resources": _resources(budget),
        "policies": policies,
        "pairs": [view for view, _ in pairs],
        "full": full,
        "values": values,
    }


def _aggregate(settings: list[dict[str, Any]], count: int) -> dict[str, Any]:
    _require(0 < len(settings) == count, f"aggregate needs {count} settings")
    full = score._mean([item["full"] for item in settings])
    policies = {}
    for name in POLICIES:
        deltas = [item["values"][name] - item["full"] for item in settings]
        better, worse, ties = score._signs(deltas)
        policies[name] = {
            **_num("mean_grade", score._mean([item["values"][name] for item in settings])),
            **_num("mean_delta_vs_full", score._mean(deltas)),
            "settings_better_than_full": better,
            "settings_worse_than_full": worse,
            "settings_tied_with_full": ties,
        }
    return {"settings": [item["key"] for item in settings], **_num("full_size_mean_grade", full), "policies": policies}


def _gate(overall: dict[str, Any], count: int) -> dict[str, Any]:
    perfect = overall["policies"]["perfect"]
    gain = Fraction(perfect["mean_delta_vs_full_exact"])
    wins = perfect["settings_better_than_full"]
    passes = gain >= GAIN and wins >= WINS_REQUIRED
    return {
        "rule": "perfect selection gains at least the threshold over one full-size team and is better in enough settings",
        "kind": "feasibility upper bound from hindsight final grades; not a selector result or an operator decision",
        **_num("threshold_gain", GAIN),
        "wins_required": WINS_REQUIRED,
        "settings": count,
        **_num("perfect_mean_gain", gain),
        "perfect_settings_better": wins,
        "gain_passes": gain >= GAIN,
        "wins_passes": wins >= WINS_REQUIRED,
        "pass": passes,
        "verdict": "design_paid_blinded_selector" if passes else "do_not_design_paid_selector",
        "later_selector_must_beat": ["full_size", "uniform", "busiest_board"],
    }


def _provenance(document: dict[str, Any]) -> dict[str, Any]:
    upstream = document["provenance"]
    here = Path(__file__).resolve().parent
    return {
        "input_file": "continuation-equal-time.json",
        "input_sha256": INPUT_SHA256,
        "input_commit": INPUT_COMMIT,
        "input_schema": document["schema"],
        "input_provenance_sha256": document["provenance_sha256"],
        "registration_commit": REGISTRATION_COMMIT,
        "registration": REGISTRATION,
        "source_release_index": f"https://{source.HOST}{source.BASE_PATH}{source.INDEX_MEMBER}",
        "source_index_sha256": upstream["source_index_sha256"],
        "receipt_sha256": upstream["receipt_sha256"],
        "receipt_protocol_revision": upstream.get("receipt_protocol_revision"),
        "curves_sha256": upstream["curves_sha256"],
        "code_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in _CODE},
    }


def analyze(path: Path) -> dict[str, Any]:
    _require(
        all(budget in source.TEAM_SIZES and budget // 2 in source.TEAM_SIZES for budget in SEAT_BUDGETS),
        "every seat budget and its half must be a registered team size",
    )
    document = _read(path)
    cells = _cells(document)
    settings = [_setting(model, budget, cells) for model in source.MODELS for budget in SEAT_BUDGETS]
    count = len(source.MODELS) * len(SEAT_BUDGETS)
    overall = _aggregate(settings, count)
    by_budget = {
        str(budget): _aggregate([item for item in settings if item["seat_budget"] == budget], len(source.MODELS))
        for budget in SEAT_BUDGETS
    }
    by_model = {
        model: _aggregate([item for item in settings if item["model"] == model], len(SEAT_BUDGETS))
        for model in source.MODELS
    }
    return {
        "schema": SCHEMA,
        "question": "Do two isolated half-size teams beat one full-size team at equal seats and a 4-hour clock?",
        "design": {
            "settings": "each registered model at a total of 4 or 8 seats",
            "full_size": "one team using all seats; exact mean of its 3 recorded 4h grades",
            "half_size": "every unordered pair of the 3 recorded half-size teams, both run for 4 hours",
            "uniform": "either pair output with probability 1/2",
            "busiest_board": "the output of the team with more public posts before minute 60; ties average",
            "perfect": "the higher 4h grade of the pair; hindsight",
            "weighting": "average the 3 pairs within a setting, then weight the settings equally",
            "arithmetic": "exact rationals from the shortest decimal form of each published grade; floats are display",
            "inference": "descriptive; no p-value",
        },
        "gate": _gate(overall, count),
        "overall": overall,
        "by_seat_budget": by_budget,
        "by_model": by_model,
        "settings": [{key: value for key, value in item.items() if key not in ("full", "values")} for item in settings],
        "provenance": _provenance(document),
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.continuation.portfolio",
        description="Compare one full-size team with pairs of half-size teams on the released equal-time grades.",
        allow_abbrev=False,
    )
    parser.add_argument("published", type=Path, metavar="PUBLISHED_EQUAL_TIME_JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output = cli._encode(analyze(args.published))
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
