"""Review the discriminating power of Dispatch Handoff inputs without running seats."""

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

if __package__:
    from . import run_panel
else:
    import run_panel

_WORLD_SOURCE = "tasks/dispatch-handoff/environment/seatworld.py"

_REQUIRED_ARMS = ("fresh", "reordered")
_COUNT_KEYS = (
    "unchanged",
    "changed_same_best",
    "old_best_feasible_suboptimal",
    "old_best_infeasible",
)
_BAD_EVIDENCE = "Invalid Dispatch Handoff world evidence"


def _availability(value: object, utilities: dict[str, int]) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(bay) is not str or bay not in utilities for bay in value)
        or value[0] == value[1]
    ):
        raise RuntimeError(_BAD_EVIDENCE)
    return cast(list[str], value)


def _category(case: object, episode: int) -> str:
    if (
        not isinstance(case, dict)
        or type(case.get("id")) is not int
        or case.get("id") != episode
    ):
        raise RuntimeError(_BAD_EVIDENCE)
    data = cast(dict[str, Any], case)
    ranks = data.get("utilities")
    if (
        not isinstance(ranks, dict)
        or set(ranks) != {"A", "B", "C", "D"}
        or any(type(rank) is not int for rank in ranks.values())
        or set(ranks.values()) != {1, 2, 3, 4}
    ):
        raise RuntimeError(_BAD_EVIDENCE)
    utilities = cast(dict[str, int], ranks)
    before = _availability(data.get("before"), utilities)
    after = _availability(data.get("after"), utilities)
    old_best = max(before, key=utilities.__getitem__)
    new_best = max(after, key=utilities.__getitem__)
    if before[0] in after and before[1] in after:
        assert old_best == new_best
        return "unchanged"
    if old_best == new_best:
        return "changed_same_best"
    if old_best not in after:
        return "old_best_infeasible"
    assert utilities[old_best] < utilities[new_best]
    return "old_best_feasible_suboptimal"


def _count_seed(
    world_type: type, seed: int, episodes: int, counts: dict[str, int]
) -> None:
    settings = {
        "schema": "seatlab.scenario/v1",
        "seed": seed,
        "episodes": episodes,
        "condition": "reordered",
    }
    evidence = world_type(settings).evidence()
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != "seatlab.world/v1"
        or evidence.get("settings") != settings
        or evidence.get("complete") is not False
        or not isinstance(cases := evidence.get("cases"), list)
        or len(cases) != episodes
        or not cases
    ):
        raise RuntimeError(_BAD_EVIDENCE)
    for episode, case in enumerate(cases):
        counts[_category(case, episode)] += 1


def describe(
    registration: dict[str, Any], pin: str, world_type: type
) -> dict[str, Any]:
    plan = registration["plan"]
    missing = [arm for arm in _REQUIRED_ARMS if arm not in plan["conditions"]]
    report: dict[str, Any] = {
        "schema": "seatlab.dispatch-revision-opportunity/v1",
        "registration_sha256": pin,
        "assumption": "truthful_inventory_reports",
        "status": "missing_arms",
        "missing_arms": missing,
        "paired_teams": 0,
        "unique_shipments": None,
        "counts": None,
        "optimality_opportunities": None,
        "feasibility_opportunities": None,
    }
    if missing:
        return report

    seeds, episodes = plan["seeds"], plan["episodes"]
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    for seed in seeds:
        _count_seed(world_type, seed, episodes, counts)
    unique_shipments = len(seeds) * episodes
    if not 0 < unique_shipments == sum(counts.values()):
        raise RuntimeError(_BAD_EVIDENCE)
    feasibility = counts["old_best_infeasible"]
    optimality = counts["old_best_feasible_suboptimal"] + feasibility
    report.update(
        status="distinguishable" if optimality else "indistinguishable",
        paired_teams=2 * len(seeds),
        unique_shipments=unique_shipments,
        counts=counts,
        optimality_opportunities=optimality,
        feasibility_opportunities=feasibility,
    )
    return report


def _checkout_world(source: bytes) -> type:
    module = ModuleType("_seatlab_pinned_dispatch_world")
    # Only the current checkout supplies code. No packet field selects a path or supplies bytes.
    exec(compile(source, _WORLD_SOURCE, "exec", dont_inherit=True), module.__dict__)  # noqa: S102
    return module.World


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    design = modes.add_parser(
        "design",
        help="Check a registered pair without running a seat",
        allow_abbrev=False,
    )
    design.add_argument("registration", type=Path)
    design.add_argument(
        "--sha256", required=True, help="separately held registration digest"
    )
    args = parser.parse_args(argv)
    try:
        registration, _, sources = run_panel._pinned(args.registration, args.sha256)
        result = describe(
            registration, args.sha256, _checkout_world(sources[_WORLD_SOURCE])
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return {"distinguishable": 0, "indistinguishable": 1, "missing_arms": 3}[
        result["status"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
