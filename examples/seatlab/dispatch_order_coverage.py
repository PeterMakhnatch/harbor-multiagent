from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from .dispatch_order import _add_source, _prepare, _Unavailable
from .receiver_panel import _print_json

if TYPE_CHECKING:
    from .dispatch_order_pair import OrderPair


_VisibleCase = tuple[int, int, int, int, frozenset[str], frozenset[str]]


def describe_cases(pairs: list[OrderPair]) -> dict[str, int]:
    prompt_hashes: set[str] = set()
    optimal_by_case: defaultdict[_VisibleCase, Counter[str]] = defaultdict(Counter)
    enrolled = 0
    for pair in pairs:
        prompt_hashes.add(pair.chronological.prompt_sha256)
        prompt_hashes.add(pair.reversed.prompt_sha256)
        enrolled += len(pair.cases)
        for case in pair.cases:
            utilities = case["utilities"]
            first, second = case["reports"]
            visible = (
                utilities["A"],
                utilities["B"],
                utilities["C"],
                utilities["D"],
                frozenset(first["available"]),
                frozenset(second["available"]),
            )
            optimal = max(case["after"], key=utilities.__getitem__)
            optimal_by_case[visible][optimal] += 1

    ambiguous = 0
    ambiguous_shipments = 0
    best_case_only_correct = 0
    for counts in optimal_by_case.values():
        best_case_only_correct += max(counts.values())
        if len(counts) > 1:
            ambiguous += 1
            ambiguous_shipments += sum(counts.values())

    distinct = len(optimal_by_case)
    return {
        "distinct_prompt_arrays": len(prompt_hashes),
        "distinct_visible_cases": distinct,
        "repeated_visible_occurrences": enrolled - distinct,
        "ambiguous_visible_cases": ambiguous,
        "shipments_in_ambiguous_cases": ambiguous_shipments,
        "best_case_only_correct": best_case_only_correct,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count distinct receiver inputs without changing the held design or running a seat.",
        allow_abbrev=False,
    )
    _add_source(parser)
    parser.add_argument("--draws", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--panel-sha256",
        help="optionally require the coordinator's already registered panel",
    )
    args = parser.parse_args(argv)
    try:
        plan, pairs = _prepare(args)
        if args.panel_sha256 is not None and args.panel_sha256 != plan["panel_sha256"]:
            raise ValueError("panel SHA-256 does not match the registered input")
        _print_json(
            {
                "schema": "seatlab.dispatch-order-coverage/v1",
                "panel_sha256": plan["panel_sha256"],
                "source": plan["source"],
                "worlds": len(pairs),
                "enrolled_shipments": plan["unique_shipments"],
                "expected_first_replies": plan["expected_first_replies"],
                "case_coverage": describe_cases(pairs),
            }
        )
        return 0
    except _Unavailable as exc:
        print(f"Unavailable: {exc}", file=sys.stderr)
        return 3
    except OSError:
        print("Could not read the registered inputs", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
