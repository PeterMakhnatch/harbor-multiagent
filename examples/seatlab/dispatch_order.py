"""Prepare and score a Dispatch receiver replay in which only report order changes."""

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from . import run_panel
from .capsule import encode_view, view_hash
from .dispatch_native_return import checked_trial
from .dispatch_opportunity import _WORLD_SOURCE, _checkout_world
from .dispatch_order_pair import OrderPair, build_pair, report_bests
from .dispatch_order_queue import assignments, dispatch_status, emit_wave, within_limits
from .dispatch_order_repeat import allocate, equal_world_excess, score_repeats
from .dispatch_order_score import score_pairs
from .dispatch_returns import _latent_evidence
from .receiver_panel import _print_json, _read_replies, _shard

_MAX_WORLDS = 128
_MAX_SHIPMENTS = 4096
_EXAMPLE = "seatlab/results/dispatch-order/registration.json"
_EXAMPLE_SHA256 = "73c2bef70dc72a4705d3e4e0348481d24bc46a003da53e0930ffe29b7339a75a"
_MATERIAL = (
    "seatlab/capsule.py",
    "seatlab/dispatch_issue.py",
    "seatlab/dispatch_native_return.py",
    "seatlab/dispatch_opportunity.py",
    "seatlab/dispatch_order.py",
    "seatlab/dispatch_order_pair.py",
    "seatlab/dispatch_order_queue.py",
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
    registration, pin = args.registration, args.sha256
    if args.example:
        if pin is not None:
            raise ValueError("the released example has its own held source digest")
        registration, pin = run_panel.EXAMPLES_DIR / _EXAMPLE, _EXAMPLE_SHA256
    if registration is not None:
        if pin is None or args.trial_id:
            raise ValueError(
                "a registration requires its separate --sha256 and accepts no trial ID"
            )
        source, blocks = _planned(registration, pin)
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
    message_sizes = [
        len(encode_view(arm.messages))
        for p in pairs
        for arm in (p.chronological, p.reversed)
    ]
    return {
        "schema": "seatlab.dispatch-order-panel/v1",
        "source": origin,
        "panel_sha256": pin,
        "worlds": len(pairs),
        "unique_shipments": sum(len(p.cases) for p in pairs),
        "different_recommendations": different,
        "draws_per_order": args.draws,
        "expected_first_replies": 2 * args.draws * len(pairs),
        "canonical_message_bytes": {
            "panel": args.draws * sum(message_sizes),
            "minimum_request": min(message_sizes),
            "maximum_request": max(message_sizes),
        },
    }, pairs


def _add_source(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--example",
        action="store_true",
        help="use the released, source-pinned two-world registration; no setup or model needed",
    )
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    setup = modes.add_parser(
        "prepare", help="describe and pin the order-only replay", allow_abbrev=False
    )
    emit = modes.add_parser(
        "emit", help="emit one counterbalanced worker wave", allow_abbrev=False
    )
    claim = modes.add_parser(
        "claim", help="reserve before emitting each worker request", allow_abbrev=False
    )
    status = modes.add_parser(
        "status",
        help="join local reservations with first replies without scoring a team",
        allow_abbrev=False,
    )
    score = modes.add_parser(
        "score",
        help="score first replies from separate, fresh receivers",
        allow_abbrev=False,
    )
    for sub in (setup, emit, claim, status, score):
        _add_source(sub)
        sub.add_argument(
            "--draws",
            type=int,
            choices=(1, 2),
            default=1,
            help="independent first replies per order; 2 adds same-prompt comparisons",
        )
    for sub in (emit, claim, status, score):
        sub.add_argument(
            "--panel-sha256", required=True, help="separately held digest from prepare"
        )
    for sub in (emit, claim):
        sub.add_argument("--wave", required=True, type=int, choices=range(4))
        sub.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    for sub in (claim, status):
        sub.add_argument(
            "--spool",
            required=True,
            type=Path,
            help="one preexisting owner-only mode-0700 directory on a trusted local filesystem",
        )
    claim.add_argument(
        "--max-requests",
        type=int,
        required=True,
        help="ceiling for the whole registered panel, not this wave or shard",
    )
    claim.add_argument(
        "--max-input-bytes",
        type=int,
        help="optional ceiling on canonical message JSON bytes for the whole panel, not tokens",
    )
    for sub in (score, status):
        sub.add_argument(
            "replies",
            nargs="*",
            type=Path,
            help="first-reply JSONL files; omit to show missing coverage",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan, pairs = _prepare(args)
        if args.mode == "prepare":
            _print_json(plan)
            return 0 if plan["different_recommendations"] else 1
        if args.panel_sha256 != plan["panel_sha256"]:
            raise ValueError(
                "panel SHA-256 does not match the inputs and scoring protocol"
            )
        if args.mode in {"claim", "emit"}:
            if args.wave >= 2 * args.draws:
                raise ValueError("this wave is not in the registered design")
            if args.mode == "claim" and not within_limits(
                plan, args.max_requests, args.max_input_bytes
            ):
                print(
                    "Refused: the complete registered panel exceeds the supplied limit",
                    file=sys.stderr,
                )
                return 1
            emit_wave(
                pairs,
                args.wave,
                args.shard,
                args.spool if args.mode == "claim" else None,
            )
            return 0
        if len(args.replies) > plan["expected_first_replies"]:
            raise ValueError("more first-reply files than requested replies")
        routes = {arm.id: arm for _, _, arm in assignments(pairs, args.draws)}
        try:
            replies = _read_replies(args.replies, routes)
        except (OSError, ValueError, RecursionError):
            raise ValueError(
                "invalid worker file, duplicate subject or mismatched prompt digest"
            ) from None
        if args.mode == "status":
            report, code = dispatch_status(pairs, args.draws, args.spool, replies)
            report.update(panel_sha256=plan["panel_sha256"])
            _print_json(report)
            return code
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
        print(
            "Local I/O failure: could not read inputs or persist the worker handoff",
            file=sys.stderr,
        )
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
