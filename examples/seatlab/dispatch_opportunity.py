"""Check Dispatch Handoff inputs and diagnose retained choices without running seats."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

if __package__:
    from . import dispatch_returns, run_panel
    from .capsule import _reject_constant, _unique_object
else:
    import dispatch_returns
    import run_panel
    from capsule import _reject_constant, _unique_object

_WORLD_SOURCE = "tasks/dispatch-handoff/environment/seatworld.py"
_RELEASE = Path(__file__).parent / "results/native-model-primary.json"
_RELEASE_SHA256 = "79eec3d776c0a00d218637aa88f118989cfc3f5b1116db221e88ad57a2da7bb9"

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


def _observed(path: Path, pin: str) -> dict[str, Any]:
    if __package__:
        from . import team_driver
    else:
        import team_driver

    try:
        raw = run_panel.dispatch_plan._read_regular(path)
    except (OSError, ValueError) as exc:
        raise ValueError(
            "study must be a readable regular file of at most 1 MiB"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("study bytes do not match the pinned SHA-256")
    try:
        study = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError("invalid study JSON") from None
    world = _checkout_world((run_panel.EXAMPLES_DIR / _WORLD_SOURCE).read_bytes())
    return dispatch_returns.reduce_returns(
        study, pin, team_driver._grid(), team_driver.EPISODES, world
    )


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
    observed = modes.add_parser(
        "observed",
        help="Diagnose the retained speaker-listener study",
        allow_abbrev=False,
    )
    observed.add_argument(
        "--report",
        type=Path,
        help="pinned JSON from team_report.collect; default: released study",
    )
    observed.add_argument(
        "--sha256", help="separately held study digest, required with --report"
    )
    trial = modes.add_parser(
        "trial",
        help="Diagnose one stationary native Dispatch trial",
        allow_abbrev=False,
    )
    trial.add_argument(
        "directory", type=Path, help="trusted local trial directory with result.json"
    )
    trial.add_argument(
        "--trial-id", required=True, help="expected native UUID from your job ledger"
    )
    trial.add_argument(
        "--require-bound",
        action="store_true",
        help="require an acknowledged view for every active role",
    )
    args = parser.parse_args(argv)
    try:
        if args.mode == "trial":
            if __package__:
                from .dispatch_native_return import inspect_trial
            else:
                from dispatch_native_return import inspect_trial
            world = _checkout_world(
                (run_panel.EXAMPLES_DIR / _WORLD_SOURCE).read_bytes()
            )
            result: dict[str, Any]
            try:
                result = inspect_trial(args.directory, args.trial_id, world)
                has_receipts = all(
                    basis == "bound" for basis in result["observations"].values()
                )
                code = (
                    0
                    if result["status"] == "measured"
                    and (not args.require_bound or has_receipts)
                    else 3
                )
            except FileNotFoundError:
                result = {
                    "schema": "seatlab.dispatch-native-return/v1",
                    "status": "missing_evidence",
                }
                code = 3
        elif args.mode == "observed":
            if (args.report is None) != (args.sha256 is None):
                parser.error(
                    "supply both --report and --sha256, or neither for the public study"
                )
            result = _observed(
                args.report or _RELEASE,
                args.sha256 if args.report is not None else _RELEASE_SHA256,
            )
            code = (
                0
                if result["coverage"]["registered"] == result["coverage"]["measured"]
                else 3
            )
        else:
            registration, _, sources = run_panel._pinned(args.registration, args.sha256)
            result = describe(
                registration, args.sha256, _checkout_world(sources[_WORLD_SOURCE])
            )
            code = {"distinguishable": 0, "indistinguishable": 1, "missing_arms": 3}[
                result["status"]
            ]
    except OSError:
        print("Invalid input: could not read a required source", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
