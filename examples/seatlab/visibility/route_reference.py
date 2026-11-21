from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, TypedDict, override

from . import route_packet, route_protocol


class Counts(TypedDict):
    n: int
    correct: int
    tied: int


class Source(TypedDict):
    policy: int
    source_rounds: int
    eligible_rounds: int
    own: Counts
    complementary: Counts
    fixed: int
    regressed: int
    conflict_rows: int
    conflicting_cells: int
    rounds_with_prior_decision_pending: int


def _choice(margin: int) -> int | None:
    return None if margin == 0 else int(margin > 0)


def _complementary(
    own: route_protocol.View, peer: route_protocol.View
) -> tuple[int | None, int]:
    by_field: dict[str, list[tuple[int, int]]] = {}
    for report in (own, peer):
        for field, a, b in zip(report.fields, report.a, report.b, strict=True):
            by_field.setdefault(field, []).append((a, b))
    twice_margin = sum(
        sum(b - a for a, b in scores) * (2 // len(scores))
        for scores in by_field.values()
    )
    conflicts = sum(
        a != b
        for scores in by_field.values()
        if len(scores) == 2
        for a, b in zip(scores[0], scores[1], strict=True)
    )
    return _choice(twice_margin), conflicts


def _score(counts: Counts, choice: int | None, best: int) -> bool:
    correct = choice == best
    counts["n"] += 1
    counts["correct"] += correct
    counts["tied"] += choice is None
    return correct


def _prior_pending(
    case: route_packet.Case, source: Sequence[route_packet.Case]
) -> bool:
    fetch = case.fetch_seq
    return fetch is not None and any(
        prior.trial_sha256 == case.trial_sha256
        and prior.round < case.round
        and prior.decision_seq is not None
        and prior.decision_seq > fetch
        for prior in source
    )


def _source(
    policy: int, cases: Sequence[route_packet.Case], views: set[route_protocol.View]
) -> Source:
    own_counts = Counts(n=0, correct=0, tied=0)
    complementary_counts = Counts(n=0, correct=0, tied=0)
    eligible = fixed = regressed = conflict_rows = conflicting_cells = pending = 0
    for case in cases:
        if case.reports is None:
            continue
        rankings = []
        for rotation in range(3):
            block = route_protocol.Block(policy, case.round, rotation, swap=0, rep=0)
            own = route_protocol.view(case, block, "duplicate")
            peer = route_protocol.view(case, block, "complementary")
            views.add(own)
            own_choice = _choice(sum(own.b) - sum(own.a))
            complementary_choice, conflicts = _complementary(own, peer)
            rankings.append((own_choice, complementary_choice, conflicts))
        best = case.best
        if best is None or case.truth is None:
            raise ValueError("missing physical answer key")
        eligible += 1
        pending += _prior_pending(case, cases)
        for own_choice, complementary_choice, conflicts in rankings:
            own_correct = _score(own_counts, own_choice, best)
            complementary_correct = _score(
                complementary_counts, complementary_choice, best
            )
            fixed += not own_correct and complementary_correct
            regressed += own_correct and not complementary_correct
            conflict_rows += conflicts > 0
            conflicting_cells += conflicts
    return Source(
        policy=policy,
        source_rounds=len(cases),
        eligible_rounds=eligible,
        own=own_counts,
        complementary=complementary_counts,
        fixed=fixed,
        regressed=regressed,
        conflict_rows=conflict_rows,
        conflicting_cells=conflicting_cells,
        rounds_with_prior_decision_pending=pending,
    )


def describe(cases: Sequence[route_packet.Case]) -> dict[str, object]:
    grouped: dict[int, list[route_packet.Case]] = {}
    coordinates = {(case.trial_sha256, case.round) for case in cases}
    if len(coordinates) != len(cases):
        raise ValueError("duplicate physical source round")
    for case in cases:
        grouped.setdefault(case.policy, []).append(case)
    views: set[route_protocol.View] = set()
    sources = [_source(policy, grouped[policy], views) for policy in sorted(grouped)]
    snapshots = {
        (case.truth, case.reports) for case in cases if case.reports is not None
    }
    return {
        "schema": "seatlab.route-reference/v1",
        "scope": (
            "Descriptive numeric reference on stored evidence. No agent, model, or "
            "native outcome was rerun. One comparison per eligible source round and "
            "coordinator rotation in physical route order, without display swaps or "
            "repetitions. Ties earn no correct choice. Timing counts source rounds "
            "with a recorded lower-numbered decision after the current fetch in the "
            "same trial."
        ),
        "source_rounds": len(cases),
        "eligible_rounds": sum(source["eligible_rounds"] for source in sources),
        "distinct_source_trials": len({case.trial_sha256 for case in cases}),
        "distinct_physical_truth_arrays": len({truth for truth, _ in snapshots}),
        "distinct_numeric_snapshots": len(snapshots),
        "distinct_own_views": len(views),
        "sources": sources,
    }


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        super().error("expected a packet path and a SHA-256 commitment")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description="Describe the offline routed-review reference.")
    parser.add_argument("packet", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args(argv)
    try:
        report = describe(route_packet.load(args.packet, args.sha256))
    except (OSError, ValueError):
        print("Cannot verify the route packet.", file=sys.stderr)
        return 1
    report["packet_sha256"] = args.sha256
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
