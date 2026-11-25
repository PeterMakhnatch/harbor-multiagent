import argparse
import json
import sys
from fractions import Fraction
from typing import Any

from .core import BoundaryError, World, analyze, conflict_witness
from .inputs import census, read_input
from .joint import ARMS, qualify
from .pair import compare

SCHEMA = "seatlab.decision-boundary/v1"
SCOPE = "Finite optima for declared worlds, priors, role inputs and action rules. Not a model success rate or a causal estimate."
Study = tuple[int, list[World]]


def native(names: tuple[str, ...], seed: int) -> dict[str, Study]:
    if not 0 <= seed < 2**63:
        raise BoundaryError("invalid development seed")
    groups = {name.partition("/")[0] for name in names}
    values: dict[str, Study] = {}
    if not names or "exhibit" in groups:
        from . import exhibit

        values.update(exhibit.studies(seed))
    if not names or "ship" in groups:
        from . import shipping

        values.update(shipping.studies(seed))
    if any(name not in values for name in names):
        raise BoundaryError("unknown native study; omit its name to list all studies")
    return {name: values[name] for name in names} if names else values


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether a teammate's message can change a finite decision. No trial or model starts.",
        allow_abbrev=False,
    )
    subs = parser.add_subparsers(dest="command", required=True)
    examples = subs.add_parser(
        "native", help="inspect bundled native development cases"
    )
    examples.add_argument("study", nargs="?")
    own = subs.add_parser(
        "inspect", help="inspect a local finite world census or standard input"
    )
    own.add_argument("file", help="regular JSON file, or - for standard input")
    paired = subs.add_parser(
        "compare", help="check two observation boundaries on the same decision problem"
    )
    source = paired.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--native", metavar=("BEFORE", "AFTER"), nargs=2, help="two native study names"
    )
    source.add_argument(
        "--files",
        metavar=("BEFORE", "AFTER"),
        nargs=2,
        help="two finite world files, at most one standard input (-)",
    )
    paired.add_argument(
        "--expect-relation",
        choices=("equivalent", "refinement", "coarsening", "incomparable"),
    )
    paired.add_argument("--expect-gain", choices=("negative", "zero", "positive"))
    joint = subs.add_parser("joint", help="qualify two sources on one finite decision")
    joint.add_argument(
        "--files",
        required=True,
        nargs=4,
        metavar=("NEITHER", "FIRST", "SECOND", "BOTH"),
        help="four matched finite world files; use - once for standard input",
    )
    joint.add_argument(
        "--expect-each-needed",
        choices=("yes", "no"),
        help="check whether removing either source strictly lowers the attainable joint value",
    )
    for p in (paired, examples):
        p.add_argument(
            "--seed",
            type=int,
            default=7,
            help="Exhibit Relay development seed; shipping uses fixed paired references",
        )
    for p in (examples, own):
        p.add_argument(
            "--expect-gap",
            choices=("positive", "zero"),
            help="exit 1 if any selected study contradicts the expected information gap",
        )
        p.add_argument(
            "--witness",
            action="store_true",
            help="show indices of input rows with no common optimal rule",
        )
    return parser.parse_args(argv)


def _finite(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    tasks = (
        native((args.study,) if args.study else (), args.seed)
        if args.command == "native"
        else {"input": census(read_input(args.file))}
    )
    matches = True
    reports: dict[str, dict[str, Any]] = {}
    for name, (scale, worlds) in sorted(tasks.items()):
        record: dict[str, Any] = dict(analyze(worlds, scale))
        record["objective"] = (
            "complete_documentary_coverage"
            if name.startswith("exhibit/")
            else "exactly_one_supplier_dispatch"
            if name.startswith("ship/")
            else "author_declared"
        )
        if args.witness:
            record["witness"] = conflict_witness(worlds, scale)
        if args.expect_gap is not None:
            met = (record["gap"] != "0") == (args.expect_gap == "positive")
            record["expectation_met"] = met
            matches = matches and met
        reports[name] = record
    return {"studies": reports, "expect_gap": args.expect_gap}, matches


def _paired(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    names = tuple(args.native or ())
    if names:
        values = native(names, args.seed)
        left_scale, left = values[names[0]]
        right_scale, right = values[names[1]]
    else:
        if args.files == ["-", "-"]:
            raise BoundaryError("at most one comparison file can be standard input")
        left_scale, left = census(read_input(args.files[0]))
        right_scale, right = census(read_input(args.files[1]))
    row = compare(left, left_scale, right, right_scale)
    gain = Fraction(row["gain"])
    direction = "positive" if gain > 0 else "negative" if gain < 0 else "zero"
    matches = args.expect_relation in (None, row["relation"]) and args.expect_gain in (
        None,
        direction,
    )
    return {
        "comparison": row,
        "native_studies": names or None,
        "expect_relation": args.expect_relation,
        "expect_gain": args.expect_gain,
        "expectation_met": matches
        if args.expect_gain or args.expect_relation
        else None,
    }, matches


def _joint(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    if args.files.count("-") > 1:
        raise BoundaryError("at most one joint input can be standard input")
    arms: dict[str, list[World]] = {}
    scale = None
    for name, path in zip(ARMS, args.files, strict=True):
        incoming, worlds = census(read_input(path))
        if type(incoming) is not int or (scale is not None and incoming != scale):
            raise BoundaryError("joint inputs must use the same integer scale")
        scale = incoming
        arms[name] = worlds
    if scale is None:
        raise BoundaryError("missing joint inputs")
    joint = qualify(arms, scale)
    expected = args.expect_each_needed
    matches = expected is None or joint["each_source_adds_value_given_other"] == (
        expected == "yes"
    )
    return {
        "qualification": joint,
        "expect_each_needed": expected,
        "expectation_met": matches if expected else None,
    }, matches


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        if args.command == "joint":
            result, matches = _joint(args)
        elif args.command == "compare":
            result, matches = _paired(args)
        else:
            result, matches = _finite(args)
    except BoundaryError as exc:
        print(f"decision-boundary: {exc}", file=sys.stderr)
        return 2
    except (
        OSError,
        ValueError,
        RecursionError,
        AssertionError,
        TypeError,
        KeyError,
        IndexError,
    ):
        print(
            "decision-boundary: invalid or inaccessible census, study or native input",
            file=sys.stderr,
        )
        return 2
    result.update({"schema": SCHEMA, "scope": SCOPE, "model_calls": 0})
    print(json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
