"""Prepare and score a Dispatch receiver replay in which only report order changes."""

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from . import run_panel
from .capsule import view_hash
from .dispatch_native_return import checked_trial
from .dispatch_opportunity import _WORLD_SOURCE, _checkout_world
from .dispatch_order_pair import OrderPair, build_pair, report_bests
from .dispatch_order_repeat import allocate, equal_world_excess, score_repeats
from .dispatch_order_score import score_pairs
from .dispatch_returns import _latent_evidence
from .receiver_panel import _print_json, _read_replies, _shard

_MAX_WORLDS = 128
_MAX_SHIPMENTS = 4096
_MATERIAL = (
    "seatlab/capsule.py",
    "seatlab/dispatch_native_return.py",
    "seatlab/dispatch_opportunity.py",
    "seatlab/dispatch_order.py",
    "seatlab/dispatch_order_pair.py",
    "seatlab/dispatch_order_repeat.py",
    "seatlab/dispatch_order_score.py",
    "seatlab/dispatch_plan.py",
    "seatlab/dispatch_returns.py",
    "seatlab/evidence.py",
    "seatlab/export_capsules.py",
    "seatlab/receiver_panel.py",
    "seatlab/run_panel.py",
    _WORLD_SOURCE,
    "tasks/dispatch-handoff/tests/score.py",
)


class _Unavailable(ValueError):
    pass


def _planned(packet: Path, pin: str) -> tuple[str, list[list[dict[str, Any]]]]:
    registration, _, sources = run_panel._pinned(packet, pin)
    plan = registration["plan"]
    if "reordered" not in plan["conditions"]:
        raise _Unavailable("the registered design has no two-report condition")
    seeds, episodes = plan["seeds"], plan["episodes"]
    if len(seeds) > _MAX_WORLDS or len(seeds) * episodes > _MAX_SHIPMENTS:
        raise ValueError("panel exceeds 128 worlds or 4096 distinct shipments")
    world = _checkout_world(sources[_WORLD_SOURCE])
    blocks = []
    for seed in seeds:
        cases, _ = _latent_evidence(world, seed, episodes)
        for case in cases:
            case["reports"] = [
                {"revision": 1, "available": case["before"]},
                {"revision": 2, "available": case["after"]},
            ]
        blocks.append(cases)
    return pin, blocks


def _retained(
    paths: list[Path], ids: list[str]
) -> tuple[str, list[list[dict[str, Any]]]]:
    if not 1 <= len(paths) == len(ids) <= _MAX_WORLDS or len(set(ids)) != len(ids):
        raise ValueError(
            "supply one distinct trial UUID for every native directory, at most 128"
        )
    world = _checkout_world((run_panel.EXAMPLES_DIR / _WORLD_SOURCE).read_bytes())
    sources = []
    blocks = []
    seeds: set[int] = set()
    shipments = 0
    for path, expected in zip(paths, ids, strict=True):
        try:
            receipt, dossier, bases = checked_trial(path, expected, world)
        except FileNotFoundError:
            raise _Unavailable(
                "a registered native result or world dossier is missing"
            ) from None
        if receipt["condition"] != "reordered" or bases.get("dispatch") != "bound":
            raise _Unavailable(
                "a source lacks two delivered reports or the matching receiver acknowledgment"
            )
        if receipt["seed"] in seeds:
            raise ValueError("register at most one native source per world seed")
        seeds.add(receipt["seed"])
        shipments += receipt["score"]["episodes"]
        if shipments > _MAX_SHIPMENTS:
            raise ValueError("panel exceeds 4096 distinct shipments")
        cases = [
            {k: c[k] for k in ("id", "utilities", "before", "after", "reports")}
            for c in dossier["cases"]
        ]
        sources.append([expected, receipt["fingerprint"], view_hash(cases)])
        blocks.append(cases)
    return view_hash(sources), blocks


def _prepare(args: argparse.Namespace) -> tuple[dict[str, Any], list[OrderPair]]:
    if args.registration is not None:
        if args.sha256 is None or args.trial_id:
            raise ValueError(
                "a registration requires its separate --sha256 and accepts no trial ID"
            )
        source, blocks = _planned(args.registration, args.sha256)
        origin = "planned_truthful_reports"
    else:
        if args.sha256 is not None:
            raise ValueError("--sha256 belongs to --registration")
        source, blocks = _retained(args.trial, args.trial_id)
        origin = "retained_acknowledged_reports"
    material = {
        name: hashlib.sha256((run_panel.EXAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in _MATERIAL
    }
    scope = view_hash(
        ["seatlab.dispatch-order-source/v1", origin, source, material, args.draws]
    )
    pairs = [build_pair(block, scope, cases) for block, cases in enumerate(blocks)]
    subjects = [[allocate(p, wave).id for wave in range(2 * args.draws)] for p in pairs]
    pin = view_hash(["seatlab.dispatch-order-panel/v1", scope, subjects])
    different = 0
    for p in pairs:
        for case in p.cases:
            old, new = report_bests(case)
            different += old != new
    return {
        "schema": "seatlab.dispatch-order-panel/v1",
        "source": origin,
        "panel_sha256": pin,
        "worlds": len(pairs),
        "unique_shipments": sum(len(p.cases) for p in pairs),
        "different_recommendations": different,
        "draws_per_order": args.draws,
        "expected_first_replies": 2 * args.draws * len(pairs),
    }, pairs


def _add_source(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--registration", type=Path, help="source-pinned native Dispatch registration"
    )
    group.add_argument(
        "--trial",
        type=Path,
        action="append",
        help="stationary trusted native directory; one per distinct world",
    )
    parser.add_argument("--sha256", help="separately held native registration digest")
    parser.add_argument(
        "--trial-id",
        action="append",
        default=[],
        help="expected native UUID, in the same order as --trial",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    setup = modes.add_parser(
        "prepare", help="describe and pin the order-only replay", allow_abbrev=False
    )
    emit = modes.add_parser(
        "emit", help="emit one counterbalanced worker wave", allow_abbrev=False
    )
    score = modes.add_parser(
        "score",
        help="score first replies from separate, fresh receivers",
        allow_abbrev=False,
    )
    for sub in (setup, emit, score):
        _add_source(sub)
        sub.add_argument(
            "--draws",
            type=int,
            choices=(1, 2),
            default=1,
            help="independent first replies per order; 2 adds same-prompt comparisons",
        )
    for sub in (emit, score):
        sub.add_argument(
            "--panel-sha256", required=True, help="separately held digest from prepare"
        )
    emit.add_argument("--wave", required=True, type=int, choices=range(4))
    emit.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    score.add_argument(
        "replies",
        nargs="*",
        type=Path,
        help="first-reply JSONL files; use no files to show missing coverage",
    )
    args = parser.parse_args(argv)
    try:
        plan, pairs = _prepare(args)
        if args.mode == "prepare":
            _print_json(plan)
            return 0 if plan["different_recommendations"] else 1
        if args.panel_sha256 != plan["panel_sha256"]:
            raise ValueError(
                "panel SHA-256 does not match the inputs and scoring protocol"
            )
        if args.mode == "emit":
            if args.wave >= 2 * args.draws:
                raise ValueError("this wave is not in the registered design")
            slot, size = args.shard
            for pair in pairs:
                if pair.block % size == slot:
                    arm = allocate(pair, args.wave)
                    _print_json(
                        {
                            "id": arm.id,
                            "prompt_sha256": arm.prompt_sha256,
                            "messages": arm.messages,
                        }
                    )
            return 0
        if len(args.replies) > plan["expected_first_replies"]:
            raise ValueError("more first-reply files than requested replies")
        assigned = (allocate(p, wave) for p in pairs for wave in range(2 * args.draws))
        routes = {arm.id: arm for arm in assigned}
        try:
            replies = _read_replies(args.replies, routes)
        except (OSError, ValueError, RecursionError):
            raise ValueError(
                "invalid worker file, duplicate subject or mismatched prompt digest"
            ) from None
        if args.draws == 2:
            result = score_repeats(pairs, replies)
            result["equal_world_excess"] = equal_world_excess(result)
            complete = result["coverage"]["complete_worlds"]
        else:
            result = score_pairs(pairs, replies)
            complete = result["coverage"]["paired_worlds"]
        result.update(panel_sha256=plan["panel_sha256"], source=plan["source"])
        _print_json(result)
        return 0 if complete == len(pairs) else 3
    except _Unavailable as exc:
        print(f"Unavailable: {exc}", file=sys.stderr)
        return 3
    except OSError:
        print("Invalid input: a required local file cannot be read", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
