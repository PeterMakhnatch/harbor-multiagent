import argparse
import json
import sys
from fractions import Fraction
from importlib import import_module
from pathlib import Path
from typing import Any

TASK = "examples.tasks.exhibit-relay.environment.exhibit_relay"
MAX_CUT_BYTES = 128 * 1024


def _object(pairs: list[tuple[str, object]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def _cut(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("cut must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(MAX_CUT_BYTES + 1)
    if len(data) > MAX_CUT_BYTES:
        raise ValueError("cut exceeds byte limit")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise TypeError("expected a cut object")
    return value


def design(profile: str, first: int, count: int) -> dict[str, Any]:
    if not 1 <= count <= 128 or not 0 <= first <= 2**63 - count:
        raise ValueError("specify 1 to 128 cases with nonnegative 63-bit seeds")
    make_case = import_module(f"{TASK}.cases").make_case
    frontier = import_module(f"{TASK}.frontier").analyze_frontier
    rows = []
    for seed in range(first, first + count):
        case = make_case(seed, profile)
        bound = frontier(case, "linked")
        rows.append(
            {
                "dev_seed": seed,
                "brief_count": len(case["briefs"]),
                "total_weight": bound["weight"],
                "best_single_weight": bound["highest_single_weight"],
                "best_supported_weight": bound["ceiling_weight"],
                "registrar_cards_needed": bound["min_registrar"],
                "depot_cards_needed": bound["min_depot"],
            }
        )
    mean = (
        sum(
            (Fraction(r["best_supported_weight"], r["total_weight"]) for r in rows),
            Fraction(),
        )
        / count
    )
    return {
        "schema": "seatlab.exhibit-design/v1",
        "evidence": "exhaustive_card_cover",
        "profile": profile,
        "native_trials": 0,
        "model_trials": 0,
        "mean_best_supported_mass": str(mean),
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seatlab exhibit",
        description="Qualify a museum handoff without starting a team.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser(
        "design", help="enumerate attainable explicit evidence, not team rewards"
    )
    show.add_argument("--profile", choices=("fork", "mesh", "coupling"), default="fork")
    show.add_argument(
        "--seed",
        type=int,
        default=0,
        help="public development seed, never an enrollment seed",
    )
    show.add_argument("--cases", type=int, default=8)
    show.add_argument("--json", action="store_true")
    twins = sub.add_parser(
        "twins", help="check which information a flattened handoff loses"
    )
    twins.add_argument("--seed", type=int, default=0)
    twins.add_argument("--json", action="store_true")
    inspect = sub.add_parser(
        "inspect",
        help="explain an operator-supplied frozen cut without exposing card IDs",
    )
    inspect.add_argument("cut", type=Path)
    inspect.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report: dict[str, Any]
        if args.cmd == "inspect":
            note = import_module(f"{TASK}.diagnostics").observation(_cut(args.cut))
            report = {
                "source": "caller_supplied_cut_not_native_identity",
                "observation": note,
            }
        elif args.cmd == "twins":
            from .exhibit_twins import certify

            report = {
                "schema": "seatlab.exhibit-twins/v1",
                "evidence": "exhaustive_card_cover",
                "native_trials": 0,
                "model_trials": 0,
                **certify(args.seed),
            }
        else:
            report = design(args.profile, args.seed, args.cases)
        if args.json:
            print(
                json.dumps(
                    report, separators=(",", ":"), sort_keys=True, allow_nan=False
                )
            )
        elif args.cmd == "inspect":
            note = report["observation"]
            handoff = note["handoff"]
            print(
                f"{note['status']}; next phase: {handoff['stage']}; physical reward: {note['reward']}"
            )
            print(
                f"Weight with complete evidence: {handoff['visible_supported_weight']}/{handoff['total_weight']}. None means not measured."
            )
            print(
                "Source: caller-supplied evidence. This does not authenticate a native trial."
            )
        elif args.cmd == "twins":
            print(
                f"Two layouts, one identical flat depot view. Checked {report['legal_files']} legal filings."
            )
            print(report["conclusion"])
        else:
            print(
                f"{len(report['cases'])} public {report['profile']} cases. No Harbor or model trials."
            )
            print(
                "Mean maximum forecast mass with complete written evidence:",
                report["mean_best_supported_mass"],
            )
            print(
                "This is not a bound on physical reward. A loader may infer or guess a missing route."
            )
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(f"exhibit: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
