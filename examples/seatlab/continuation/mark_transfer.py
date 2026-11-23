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

from . import mark_selector as ms
from . import source

SCHEMA = "seatlab.continuation-mark-transfer/v1"
REGISTRATION_COMMIT = "83112eba1cc688d6d1a0d21f970708302466787b"
INDEX_SHA256 = "68cc6ae215bd81fb94ac88ae43d42387409d1eb6f6bb7fff40b2923870295282"
PROG = "python3.12 -BS -m examples.seatlab.continuation.mark_transfer"
CHECKOUT = Path(__file__).resolve().parents[3]
DESIGNS = (
    ("single_agent", "k1", "team", 1, "Single agent", "1 agent"),
    (
        "without_communication",
        "k8-control",
        "control",
        8,
        "Without communication",
        "8 agents, no communication",
    ),
)
RUN_KEYS = frozenset(
    ("runId", "label", "teamSize", "setting", "settingLabel", "rollout")
    + ("metrics", "note", "agents")
)
METRIC_KEYS = frozenset(("score", "marks", "costUsd", "toolCalls"))
MARK_INDEX = source.MARK_HOURS.index(3)
FINAL_INDEX = source.MARK_HOURS.index(source.FINAL_HOUR)
POLICIES = ("selected", "uniform", "perfect")
RUNS = len(DESIGNS) * len(source.MODELS) * len(source.ROLLOUTS)
PAIRS = ms.SETTINGS * ms.PAIRS_PER_SETTING
LIMITATIONS = (
    "retrospective release cohort: labels were inspected before registration, grades were joined only here",
    "the 3h mark is the live company value so far, not a terminal verifier grade of a stopped run",
    "the 3 pairs in a setting share runs and are not independent samples; no p-value is assigned",
    "both candidates run the full 4 hours; no equal money, token or dollar cost is assumed or estimated",
    "no comparison with a larger or full-size team and no estimate of the effect of disabling communication",
    "the 8 control seats share one company and repository; they are not 8 independent businesses",
)

_require = ms._require


def _agents(run: dict[str, Any], rid: str, team_size: int) -> None:
    where = f"index.{rid}.agents"
    seats: list[str] = []
    boards = 0
    for position, agent in enumerate(source._field(run, "agents", list, where)):
        at = f"{where}[{position}]"
        kind = source._field(source._object(agent, at), "kind", str, at)
        ident = source._field(agent, "id", str, at)
        path = source._field(agent, "trajectoryPath", str, at)
        _require(source._field(agent, "steps", int, at) >= 0, f"{at}.steps: negative")
        if kind == "board":
            boards += 1
            _require(
                ident == "board" and path == source.board_member(rid),
                f"{at}: board id or trajectoryPath mismatch",
            )
            continue
        _require(kind == "agent", f"{at}.kind: unexpected {kind!r}")
        seats.append(source._field(agent, "name", str, at))
        _require(
            path.startswith(f"public-rollout-data/trajectories/{source.TASK}/{rid}/"),
            f"{at}.trajectoryPath: outside {rid}",
        )
    _require(
        len(seats) == team_size and len(set(seats)) == team_size,
        f"{where}: expected {team_size} distinct agent seats, found {len(seats)}",
    )
    _require(
        boards == 1 or (team_size == 1 and boards == 0),
        f"{where}: {boards} boards for {team_size} seats",
    )


def _metrics(run: dict[str, Any], rid: str) -> tuple[Fraction, Fraction]:
    at = f"index.{rid}.metrics"
    metrics = source._field(run, "metrics", dict, f"index.{rid}")
    _require(
        metrics.keys() == METRIC_KEYS,
        f"{at}: fields {sorted(metrics)} != {sorted(METRIC_KEYS)}",
    )
    marks = source._marks(run, rid)
    _require(
        source._number(metrics["costUsd"], f"{at}.costUsd") >= 0,
        f"{at}.costUsd: negative",
    )
    calls = metrics["toolCalls"]
    _require(
        calls is None or (type(calls) is int and calls >= 0),
        f"{at}.toolCalls: expected null or a non-negative integer",
    )
    score = ms._unit(metrics["score"], f"{at}.score")
    final = ms._unit(marks[FINAL_INDEX], f"{at}.marks[4h]")
    _require(
        score == final,
        f"{at}: score {metrics['score']!r} (exact {score}) differs from "
        f"marks[4h] {marks[FINAL_INDEX]!r} (exact {final}); no substitution",
    )
    return ms._unit(marks[MARK_INDEX], f"{at}.marks[3h]"), score


def _run(
    models: dict[str, Any], model: str, design: tuple[Any, ...], rollout: int
) -> tuple[str, Fraction, Fraction]:
    _, tag, setting, team_size, setting_label, label = design
    rid = f"{model}-{tag}-r{rollout}"
    where = f"index.tasks.{source.TASK}.models.{model}"
    _require(model in models, f"{where}: missing")
    runs = source._field(source._object(models[model], where), "runs", list, where)
    matches = [run for run in runs if isinstance(run, dict) and run.get("runId") == rid]
    _require(len(matches) == 1, f"index: expected one run {rid}, found {len(matches)}")
    run, at = matches[0], f"index.{rid}"
    _require(
        run.keys() == RUN_KEYS, f"{at}: fields {sorted(run)} != {sorted(RUN_KEYS)}"
    )
    fields = (
        ("setting", str),
        ("teamSize", int),
        ("rollout", int),
        ("settingLabel", str),
        ("label", str),
    )
    identity = tuple(source._field(run, key, kind, at) for key, kind in fields)
    expected = (setting, team_size, rollout, setting_label, f"{label} · run {rollout}")
    _require(identity == expected, f"{at}: identity {identity} != {expected}")
    source._field(run, "note", str, at)
    _agents(run, rid, team_size)
    mark, final = _metrics(run, rid)
    return rid, mark, final


def _load(
    cache: Path,
) -> tuple[str, list[dict[str, Any]], dict[str, Fraction], dict[str, Fraction]]:
    cache = Path(cache).resolve()
    _require(
        not cache.is_relative_to(CHECKOUT),
        f"{cache}: cache must be outside the checkout {CHECKOUT}",
    )
    data = source._read_member(cache, source.INDEX_MEMBER, source.INDEX_MAX_BYTES)
    _require(len(data) <= source.INDEX_MAX_BYTES, "index: grew past its size bound")
    digest = hashlib.sha256(data).hexdigest()
    _require(digest == INDEX_SHA256, f"index: sha256 {digest} != pinned {INDEX_SHA256}")
    models = source._models(source._json(data, "index"))
    marks: dict[str, Fraction] = {}
    finals: dict[str, Fraction] = {}
    settings = []
    for design in DESIGNS:
        for model in source.MODELS:
            ids = []
            for rollout in source.ROLLOUTS:
                rid, marks[rid], finals[rid] = _run(models, model, design, rollout)
                ids.append(rid)
            key = f"{model}:{design[0]}"
            settings.append(
                {"key": key, "design": design[0], "model": model, "run_ids": ids}
            )
    _require(
        len(marks) == len(finals) == RUNS and len(settings) == ms.SETTINGS,
        f"expected {RUNS} distinct runs in {ms.SETTINGS} settings",
    )
    return digest, settings, marks, finals


def _picks(setting: dict[str, Any], marks: dict[str, Fraction]) -> list[dict[str, Any]]:
    picks = []
    for number, pair in enumerate(combinations(setting["run_ids"], 2), 1):
        selected = ms.choose({rid: marks[rid] for rid in pair})
        picks.append(
            {
                "pair_id": f"{setting['key']}/pair{number}",
                "run_ids": list(pair),
                "interim_3h_exact": {rid: str(marks[rid]) for rid in pair},
                "selected_run_ids": selected,
                "tie_size": len(selected),
            }
        )
    _require(len(picks) == ms.PAIRS_PER_SETTING, f"{setting['key']}: pair count")
    return picks


def _means(means: dict[str, Fraction]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in POLICIES:
        out |= ms._num(f"{name}_mean", means[name])
    out |= ms._num("delta_vs_uniform", means["selected"] - means["uniform"])
    return out | ms._num("attainable_gain", means["perfect"] - means["uniform"])


def _grade(
    setting: dict[str, Any], picks: list[dict[str, Any]], finals: dict[str, Fraction]
) -> dict[str, Any]:
    rows = []
    values: dict[str, list[Fraction]] = {name: [] for name in POLICIES}
    for pick in picks:
        pair = [finals[rid] for rid in pick["run_ids"]]
        grade = {
            "selected": ms._mean([finals[rid] for rid in pick["selected_run_ids"]]),
            "uniform": ms._mean(pair),
            "perfect": max(pair),
        }
        row = {
            "pair_id": pick["pair_id"],
            "final_4h_exact": {rid: str(finals[rid]) for rid in pick["run_ids"]},
        }
        for name in POLICIES:
            values[name].append(grade[name])
            row |= ms._num(f"{name}_grade", grade[name])
        rows.append(
            row | ms._num("delta_vs_uniform", grade["selected"] - grade["uniform"])
        )
    means = {name: ms._mean(items) for name, items in values.items()}
    summary = {key: setting[key] for key in ("key", "design", "model")} | _means(means)
    summary["vs_uniform"] = ms._sign(means["selected"] - means["uniform"])
    summary["tied_pairs"] = sum(pick["tie_size"] == 2 for pick in picks)
    return {"rows": rows, "summary": summary, "means": means}


def _aggregate(
    items: list[dict[str, Any]],
) -> tuple[dict[str, Fraction], dict[str, Any]]:
    means = {
        name: ms._mean([item["means"][name] for item in items]) for name in POLICIES
    }
    signs = [item["summary"]["vs_uniform"] for item in items]
    counts = {
        f"settings_{sign}": signs.count(sign) for sign in ("better", "worse", "tied")
    }
    tied = sum(item["summary"]["tied_pairs"] for item in items)
    return means, {
        "settings": len(items),
        **_means(means),
        **counts,
        "tied_pairs": tied,
    }


def analyze(cache: Path) -> dict[str, Any]:
    digest, settings, marks, finals = _load(cache)
    picks = [_picks(setting, marks) for setting in settings]
    graded = [_grade(s, p, finals) for s, p in zip(settings, picks, strict=True)]
    _require(sum(map(len, picks)) == PAIRS, f"expected {PAIRS} pairs")
    designs = {
        design[0]: _aggregate(
            [g for s, g in zip(settings, graded) if s["design"] == design[0]]
        )[1]
        for design in DESIGNS
    }
    means, overall = _aggregate(graded)
    checks = {
        "gain_passes": means["selected"] - means["uniform"] >= ms.GAIN,
        "positive_passes": overall["settings_better"] >= ms.WINS_REQUIRED,
    }
    result = {
        "schema": SCHEMA,
        "registration_commit": REGISTRATION_COMMIT,
        "question": "Without team chat, does keeping the pair output with the higher 3h live mark beat uniform choice between the same 2 candidates?",
        "design": {
            "cohort": "3 single-agent and 3 eight-seat Without communication runs per model; 6 model/design settings",
            "pairs": "all 3 unordered pairs per setting, 18 in total",
            "selector": "keep the run with the higher 3h live company mark; a tie keeps both and averages",
            "information": "only the 2 candidates' 3h marks; picks are fixed before any 4h grade is joined",
            "grade": "4h terminal metrics.score, checked equal to the 4h mark; exact rationals",
            "comparator": "uniform choice between the same 2 candidates",
            "attainable": "perfect hindsight choice of the higher 4h grade; a bound, not a selector",
            "weighting": "average the 3 pairs per setting, then weight the 6 settings equally",
            "inference": "descriptive; no p-value",
        },
        "pairs_before_join": [pick for items in picks for pick in items],
        "pair_grades_4h": [row for item in graded for row in item["rows"]],
        "settings": [item["summary"] for item in graded],
        "designs": designs,
        "overall": overall,
        "gate": {
            **ms._num("threshold_gain", ms.GAIN),
            "positive_required": ms.WINS_REQUIRED,
            **checks,
            "pass": all(checks.values()),
        },
        "provenance": {"index_member": source.INDEX_MEMBER, "index_sha256": digest},
        "limitations": list(LIMITATIONS),
    }
    result["provenance"]["final_sha256"] = hashlib.sha256(_encode(result)).hexdigest()
    return result


def _encode(result: dict[str, Any]) -> bytes:
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return (text + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Grade the 3h mark selector on single-agent and no-communication runs.",
        allow_abbrev=False,
    )
    parser.add_argument("cache", type=Path, metavar="CACHE")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output = _encode(analyze(args.cache))
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
