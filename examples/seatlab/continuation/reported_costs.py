from __future__ import annotations

import argparse
import hashlib
import signal
import sys
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from . import mark_selector as ms
from . import mark_transfer as mt
from . import source

SCHEMA = "seatlab.continuation-reported-costs/v1"
REGISTRATION = "examples/seatlab/continuation-study.md: Does equal seat-time also match the publisher's reported model cost?"
SELECTION_SHA256 = "cd59f33ab0e00e6dbb6ba8eee9c49b75e3789ea5d19b6bcafa3945e7c7a92fcc"
SELECTION = (
    Path(__file__).resolve().parents[1] / "results" / "continuation-mark-selector.json"
)
SELECTION_MAX_BYTES = 1 << 20
PROG = "python3.12 -BS -m examples.seatlab.continuation.reported_costs"
BUDGETS = ((4, 2), (8, 4))
LOW = Fraction(9, 10)
HIGH = Fraction(11, 10)
CLOSE_REQUIRED = 5
LIMITATIONS = (
    "retrospective: the 4h terminal grades and the 3h picks were known before the cost ratios were calculated",
    "costUsd is the publisher-reported terminal total of each run; the release gives no pricing route or per-mark cost series",
    "both smaller teams run the full 4 hours; each pair is charged both reported totals, not the selected team alone",
    "the 3 pairs in a setting share runs, and the 4-seat teams appear in 2 settings; the counts are descriptive",
    "a terminal reported total cannot set or enforce a budget for a later run",
)

_require = ms._require


def _text(value: Any, label: str) -> Fraction:
    _require(
        type(value) is str and 0 < len(value) <= 80, f"{label}: expected exact text"
    )
    try:
        number = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{label}: {error}") from error
    _require(
        str(number) == value and 0 <= number <= 1,
        f"{label}: not a canonical grade in [0, 1]",
    )
    return number


def _selection(path: Path) -> tuple[str, dict[str, Any]]:
    with Path(path).open("rb") as handle:
        data = handle.read(SELECTION_MAX_BYTES + 1)
    _require(
        len(data) <= SELECTION_MAX_BYTES,
        f"{path}: larger than {SELECTION_MAX_BYTES} bytes",
    )
    digest = hashlib.sha256(data).hexdigest()
    _require(
        digest == SELECTION_SHA256,
        f"{path}: sha256 {digest} != published {SELECTION_SHA256}",
    )
    document = source._object(source._json(data, "selection"), "selection")
    _require(document.get("schema") == ms.SCHEMA, "selection: unexpected schema")
    return digest, document


def _setting(
    model: str, seats: int, small: int, picks: list[Any], grades: list[Any], row: Any
) -> dict[str, Any]:
    key, at = f"{model}:s{seats}", f"selection.{model}:s{seats}"
    _require(
        source._field(source._object(row, at), "key", str, at) == key,
        f"{at}: out of order",
    )
    ids = [source.run_id(model, small, rollout) for rollout in source.ROLLOUTS]
    pairs: list[dict[str, Any]] = []
    values: list[Fraction] = []
    for number, (pair, pick, grade) in enumerate(
        zip(combinations(ids, 2), picks, grades, strict=True), 1
    ):
        pair_id = f"{key}/pair{number}"
        pick, grade = source._object(pick, pair_id), source._object(grade, pair_id)
        _require(
            pick.get("pair_id") == pair_id == grade.get("pair_id")
            and pick.get("run_ids") == list(pair),
            f"{pair_id}: pair identity mismatch",
        )
        interim = source._field(pick, "interim_3h_exact", dict, pair_id)
        finals = source._field(grade, "final_4h_exact", dict, pair_id)
        _require(
            interim.keys() == finals.keys() == set(pair), f"{pair_id}: run ids mismatch"
        )
        selected = ms.choose(
            {rid: _text(interim[rid], f"{pair_id}.3h.{rid}") for rid in pair}
        )
        _require(
            pick.get("selected_run_ids") == selected,
            f"{pair_id}: pick differs from the 3h rule",
        )
        value = ms._mean(
            [_text(finals[rid], f"{pair_id}.4h.{rid}") for rid in selected]
        )
        _require(
            _text(grade.get("selected_grade_exact"), f"{pair_id}.selected_grade")
            == value,
            f"{pair_id}: selected grade mismatch",
        )
        values.append(value)
        pairs.append({"pair_id": pair_id, "run_ids": list(pair)})
    mean = ms._mean(values)
    _require(
        _text(row.get("mean_grade_exact"), f"{at}.mean_grade") == mean,
        f"{at}: mean grade mismatch",
    )
    return {
        "key": key,
        "pairs": pairs,
        "full_run_ids": [source.run_id(model, seats, r) for r in source.ROLLOUTS],
        "selected": mean,
        "full": _text(
            row.get("full_size_mean_grade_exact"), f"{at}.full_size_mean_grade"
        ),
    }


def _settings(document: dict[str, Any]) -> list[dict[str, Any]]:
    picks = source._field(document, "pairs_before_join", list, "selection")
    grades = source._field(document, "pair_grades_4h", list, "selection")
    rows = source._field(document, "settings", list, "selection")
    _require(
        len(picks) == len(grades) == mt.PAIRS and len(rows) == ms.SETTINGS,
        f"selection: expected {mt.PAIRS} pairs in {ms.SETTINGS} settings",
    )
    keys = [(m, seats, small) for m in source.MODELS for seats, small in BUDGETS]
    span = ms.PAIRS_PER_SETTING
    return [
        _setting(
            *key,
            picks[n * span : (n + 1) * span],
            grades[n * span : (n + 1) * span],
            rows[n],
        )
        for n, key in enumerate(keys)
    ]


def _costs(cache: Path, ids: set[str]) -> tuple[str, dict[str, Fraction]]:
    cache = Path(cache).resolve()
    _require(
        not cache.is_relative_to(mt.CHECKOUT),
        f"{cache}: cache must be outside the checkout {mt.CHECKOUT}",
    )
    data = source._read_member(cache, source.INDEX_MEMBER, source.INDEX_MAX_BYTES)
    _require(len(data) <= source.INDEX_MAX_BYTES, "index: grew past its size bound")
    digest = hashlib.sha256(data).hexdigest()
    _require(
        digest == mt.INDEX_SHA256, f"index: sha256 {digest} != pinned {mt.INDEX_SHA256}"
    )
    models = source._models(source._json(data, "index"))
    costs: dict[str, Fraction] = {}
    for model in source.MODELS:
        for team_size in source.TEAM_SIZES:
            for rollout in source.ROLLOUTS:
                rid, run = source._find_run(models, model, team_size, rollout)
                source._seats_and_board(run, rid, team_size)
                metrics = source._field(run, "metrics", dict, f"index.{rid}")
                at = f"index.{rid}.metrics.costUsd"
                costs[rid] = source._exact(source._number(metrics.get("costUsd"), at))
                _require(costs[rid] >= 0, f"{at}: negative")
    _require(
        len(costs) == source.TEAM_RUNS and costs.keys() == ids,
        f"selection and release do not bind the same {source.TEAM_RUNS} runs",
    )
    return digest, costs


def _compare(setting: dict[str, Any], costs: dict[str, Fraction]) -> dict[str, Any]:
    rows, sums = [], []
    for pair in setting["pairs"]:
        total = sum((costs[rid] for rid in pair["run_ids"]), Fraction(0))
        sums.append(total)
        rows.append(
            {"pair_id": pair["pair_id"], "run_ids": pair["run_ids"]}
            | ms._num("pair_cost_usd", total)
        )
    pair_cost = ms._mean(sums)
    full_cost = ms._mean([costs[rid] for rid in setting["full_run_ids"]])
    _require(full_cost > 0, f"{setting['key']}: zero full-size reported cost")
    ratio = pair_cost / full_cost
    selected, full = setting["selected"], setting["full"]
    dominates = (
        pair_cost <= full_cost
        and selected >= full
        and (pair_cost < full_cost or selected > full)
    )
    summary = {"key": setting["key"], "full_run_ids": setting["full_run_ids"]}
    summary |= ms._num("pair_mean_cost_usd", pair_cost)
    summary |= ms._num("full_size_mean_cost_usd", full_cost)
    summary |= ms._num("cost_ratio", ratio) | {"ratio_in_band": LOW <= ratio <= HIGH}
    summary |= ms._num("selected_mean_grade", selected)
    summary |= ms._num("full_size_mean_grade", full)
    summary |= ms._num("grade_delta", selected - full) | {"dominates": dominates}
    return {"pairs": rows, "summary": summary, "ratio": ratio}


def analyze(cache: Path, selection: Path = SELECTION) -> dict[str, Any]:
    selection_digest, document = _selection(selection)
    settings = _settings(document)
    ids = {rid for s in settings for p in s["pairs"] for rid in p["run_ids"]}
    ids |= {rid for s in settings for rid in s["full_run_ids"]}
    _require(
        len(ids) == source.TEAM_RUNS,
        f"selection binds {len(ids)} runs, not {source.TEAM_RUNS}",
    )
    index_digest, costs = _costs(cache, ids)
    compared = [_compare(setting, costs) for setting in settings]
    mean_ratio = ms._mean([item["ratio"] for item in compared])
    in_band = sum(item["summary"]["ratio_in_band"] for item in compared)
    checks = {
        "settings_passes": in_band >= CLOSE_REQUIRED,
        "mean_passes": LOW <= mean_ratio <= HIGH,
    }
    return {
        "schema": SCHEMA,
        "registration": REGISTRATION,
        "question": "Does equal seat-time also match the publisher's reported model cost?",
        "design": {
            "cohort": "27 communicating teams: 2, 4 and 8 seats x 3 models x 3 rollouts",
            "cost": "metrics.costUsd, the terminal total in the pinned release index; exact rationals",
            "charge": "each of the 3 unique smaller pairs per setting is charged the sum of both members' totals",
            "ratio": "mean pair charge / mean total of the 3 full-size teams in the same setting",
            "grades": "3h picks and 4h terminal grades from the sha256-pinned mark-selector receipt, rechecked",
            "band": "0.90 to 1.10 inclusive in at least 5 of 6 settings and for the unweighted mean ratio",
            "dominance": "pair mean cost no more and selected grade no worse than one full-size team, one strictly",
            "inference": "descriptive",
        },
        "run_costs": [
            {"run_id": rid} | ms._num("reported_cost_usd", cost)
            for rid, cost in costs.items()
        ],
        "pair_costs": [row for item in compared for row in item["pairs"]],
        "settings": [item["summary"] for item in compared],
        "overall": {
            "settings": len(compared),
            "settings_in_band": in_band,
            "settings_dominating": sum(
                item["summary"]["dominates"] for item in compared
            ),
            **ms._num("mean_cost_ratio", mean_ratio),
        },
        "gate": {
            **ms._num("band_low", LOW),
            **ms._num("band_high", HIGH),
            "settings_required": CLOSE_REQUIRED,
            **checks,
            "close_match": all(checks.values()),
        },
        "provenance": {
            "index_member": source.INDEX_MEMBER,
            "index_sha256": index_digest,
            "selection_schema": ms.SCHEMA,
            "selection_sha256": selection_digest,
        },
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Compare pair and full-size reported model cost on the released teams.",
        allow_abbrev=False,
    )
    parser.add_argument("cache", type=Path, metavar="CACHE")
    parser.add_argument("--selection", type=Path, default=SELECTION, metavar="PATH")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output = mt._encode(analyze(args.cache, args.selection))
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
