from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import signal
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "seatlab.continuation-mark-selector/v1"
REGISTRATION_COMMIT = "59864e00e8c11514e6f2b6c378d331e5d9cd20c1"
MARK = "3h"
FINAL = "4h"
GAIN = Fraction(3, 100)
WINS_REQUIRED = 4
SETTINGS = 6
PAIRS_PER_SETTING = 3
BENCHMARKS = ("uniform", "busiest_board")
PORTFOLIO_MAX_BYTES = 1024 * 1024
PROG = "python3.12 -BS -m examples.seatlab.continuation.mark_selector"
LIMITATIONS = (
    "retrospective: the 4h grades were already published and used in the equal-time and portfolio results",
    "the 3h mark is the live company value so far, not a terminal verifier grade of a stopped team",
    "an owner sees marks only on marked jobs and with no guarantee that a mark is ready at exactly 3h",
    "pairs and settings share recorded runs, so no p-value is assigned",
    "both teams run the full 4 hours; scheduled seat-minutes are not tokens, dollars or measured spending",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _unit(value: Any, label: str) -> Fraction:
    _require(
        type(value) in (int, float, Decimal),
        f"{label} must be a non-boolean JSON number",
    )
    number = value if type(value) is Decimal else Decimal(str(value))
    _require(number.is_finite(), f"{label} must be finite")
    _require(0 <= number <= 1, f"{label} must be within [0, 1]")
    exponent = number.as_tuple().exponent
    _require(
        isinstance(exponent, int) and abs(exponent) <= 100,
        f"{label} exceeds supported decimal precision",
    )
    return Fraction(number)


def _mean(values: list[Fraction]) -> Fraction:
    _require(len(values) > 0, "mean needs at least one value")
    return sum(values, Fraction(0)) / len(values)


def _num(name: str, value: Fraction) -> dict[str, Any]:
    return {name: float(value), f"{name}_exact": str(value)}


def _sign(delta: Fraction) -> str:
    return "better" if delta > 0 else "worse" if delta < 0 else "tied"


def choose(marks: dict[str, Fraction]) -> list[str]:
    _require(
        len(marks) == 2 and all(type(key) is str and key for key in marks),
        "choose needs 2 distinct team ids",
    )
    _require(
        all(type(mark) is Fraction and 0 <= mark <= 1 for mark in marks.values()),
        "marks must be in [0, 1]",
    )
    best = max(marks.values())
    return [run_id for run_id, mark in marks.items() if mark == best]


def _choose_cli(args: argparse.Namespace) -> dict[str, Any]:
    named = (
        (args.first_run_id, args.first_mark),
        (args.second_run_id, args.second_mark),
    )
    _require(all(len(text) <= 80 for _, text in named), "mark exceeds 80 characters")
    marks = {
        run_id: _unit(
            json.loads(text, parse_float=Decimal, parse_constant=_reject_constant),
            run_id,
        )
        for run_id, text in named
    }
    selected = choose(marks)
    return {
        "schema": SCHEMA,
        "registration_commit": REGISTRATION_COMMIT,
        "mark": MARK,
        "marks_exact": {run_id: str(mark) for run_id, mark in marks.items()},
        "selected_run_ids": selected,
        "tie_size": len(selected),
        "pick": secrets.choice(selected),
    }


def _marks(document: dict[str, Any]) -> dict[str, dict[str, Fraction]]:
    runs: dict[str, dict[str, Fraction]] = {}
    for row in document["sets"]:
        for run in row["runs"]:
            run_id, grades = run["run_id"], run["grades"]
            _require(run_id not in runs, f"{run_id}: repeated run")
            runs[run_id] = {
                key: _unit(grades.get(key), f"{run_id}.grades.{key}")
                for key in (MARK, FINAL)
            }
    return runs


def _setting(
    item: dict[str, Any], marks: dict[str, dict[str, Fraction]]
) -> dict[str, Any]:
    _require(
        len(item["pairs"]) == PAIRS_PER_SETTING,
        f"{item['key']}: needs {PAIRS_PER_SETTING} pairs",
    )
    picks: list[dict[str, Any]] = []
    for index, view in enumerate(item["pairs"], 1):
        selected = choose({run_id: marks[run_id][MARK] for run_id in view["run_ids"]})
        interim = {run_id: str(marks[run_id][MARK]) for run_id in view["run_ids"]}
        picks.append(
            {
                "pair_id": f"{item['key']}/pair{index}",
                "run_ids": view["run_ids"],
                "interim_3h_exact": interim,
            }
            | {"selected_run_ids": selected, "tie_size": len(selected)}
        )
    full = Fraction(item["full_size"]["mean_grade_exact"])
    values = [
        _mean([marks[run_id][FINAL] for run_id in pick["selected_run_ids"]])
        for pick in picks
    ]
    grades = [
        {
            "pair_id": pick["pair_id"],
            "final_4h_exact": {key: str(marks[key][FINAL]) for key in pick["run_ids"]},
        }
        | _num("selected_grade", value)
        | _num("delta_vs_full", value - full)
        for pick, value in zip(picks, values, strict=True)
    ]
    mean = _mean(values)
    summary = {
        "key": item["key"],
        **_num("mean_grade", mean),
        **_num("full_size_mean_grade", full),
    }
    for name in BENCHMARKS:
        summary |= _num(
            f"delta_vs_{name}",
            mean - Fraction(item["policies"][name]["mean_grade_exact"]),
        )
    summary |= {**_num("delta_vs_full", mean - full), "vs_full": _sign(mean - full)}
    summary["tied_pairs"] = sum(pick["tie_size"] == 2 for pick in picks)
    return {
        "picks": picks,
        "grades": grades,
        "summary": summary,
        "mean": mean,
        "full": full,
    }


def _overall(
    settings: list[dict[str, Any]], expected: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    mean = _mean([item["mean"] for item in settings])
    full = Fraction(expected["overall"]["full_size_mean_grade_exact"])
    _require(
        full == _mean([item["full"] for item in settings]),
        "full-size mean disagrees with the portfolio",
    )
    policies = expected["overall"]["policies"]
    deltas = {
        name: mean - Fraction(policies[name]["mean_grade_exact"]) for name in BENCHMARKS
    }
    signs = [item["summary"]["vs_full"] for item in settings]
    counts = {
        f"settings_{sign}": signs.count(sign) for sign in ("better", "worse", "tied")
    }
    overall = {
        **_num("mean_grade", mean),
        **_num("full_size_mean_grade", full),
        **_num("delta_vs_full", mean - full),
    }
    for name, delta in deltas.items():
        overall |= _num(f"delta_vs_{name}", delta)
    overall |= counts | {
        "tied_pairs": sum(item["summary"]["tied_pairs"] for item in settings)
    }
    checks = {
        "gain_passes": mean - full >= GAIN,
        "beats_uniform": deltas["uniform"] > 0,
        "beats_busiest_board": deltas["busiest_board"] > 0,
        "wins_passes": counts["settings_better"] >= WINS_REQUIRED,
    }
    gate = {
        **_num("threshold_gain", GAIN),
        "wins_required": WINS_REQUIRED,
        **checks,
        "pass": all(checks.values()),
    }
    return overall, gate


def analyze(equal_time: Path, portfolio_path: Path) -> dict[str, Any]:
    from . import __main__ as cli
    from . import portfolio

    _require(
        MARK in portfolio.GRADE_KEYS and FINAL == portfolio.FINAL,
        "mark hours are not registered grade keys",
    )
    expected = portfolio.analyze(equal_time)
    with portfolio_path.open("rb") as handle:
        data = handle.read(PORTFOLIO_MAX_BYTES + 1)
    _require(
        len(data) <= PORTFOLIO_MAX_BYTES,
        f"{portfolio_path}: larger than {PORTFOLIO_MAX_BYTES} bytes",
    )
    _require(
        cli._encode(cli._decode(data)) == cli._encode(expected),
        "portfolio differs from its recomputation",
    )
    marks = _marks(portfolio._read(equal_time))
    _require(
        len(expected["settings"]) == SETTINGS, f"portfolio needs {SETTINGS} settings"
    )
    settings = [_setting(item, marks) for item in expected["settings"]]
    overall, gate = _overall(settings, expected)
    return {
        "schema": SCHEMA,
        "registration_commit": REGISTRATION_COMMIT,
        "question": "At hour 3, does keeping the pair output with the higher live mark beat one full-size team?",
        "design": {
            "selector": "keep the team with the higher 3h live company mark; ties keep the whole tie set and average",
            "information": "only the 2 enrolled teams' 3h marks; picks are fixed before any 4h grade is joined",
            "grade": "4h terminal verifier grade only; exact rationals, settings weighted equally",
            "benchmarks": ["full_size", "uniform", "busiest_board"],
            "final_hour_count": "not evaluated: needs separate final-board input; not part of the gate",
            "inference": "descriptive; no p-value",
        },
        "pairs_before_join": [pick for item in settings for pick in item["picks"]],
        "pair_grades_4h": [grade for item in settings for grade in item["grades"]],
        "settings": [item["summary"] for item in settings],
        "overall": overall,
        "gate": gate,
        "provenance": {
            "equal_time_sha256": portfolio.INPUT_SHA256,
            "portfolio_sha256": hashlib.sha256(data).hexdigest(),
        },
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Pick a pair output from 3h marks.", allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser(
        "choose", allow_abbrev=False, help="choose from 2 teams' 3h marks"
    )
    for name in ("first_run_id", "first_mark", "second_run_id", "second_mark"):
        pick.add_argument(name, metavar=name.upper())
    grade = commands.add_parser(
        "score", allow_abbrev=False, help="grade the selector on the released 4h grades"
    )
    grade.add_argument("equal_time", type=Path, metavar="CORRECTED_EQUAL_TIME_JSON")
    grade.add_argument("portfolio", type=Path, metavar="PORTFOLIO_JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = (
            _choose_cli(args)
            if args.command == "choose"
            else analyze(args.equal_time, args.portfolio)
        )
        output = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n"
        ).encode()
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
