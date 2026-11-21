"""Run an independent reviewer on a frozen team decision with Inspect AI."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..decision_cross import inputs
from ..handoff.external_audit import _report
from . import protocol

_RESULTS = Path(__file__).resolve().parents[1] / "results"
type _Doc = dict[str, Any]


def _print(value: object) -> None:
    print(
        json.dumps(
            value,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    )


def _load(peer: Path) -> tuple[_Doc, _Doc, _Doc, _Doc]:
    packet, board, digest = inputs.build(
        _RESULTS / "turn-fork-packet.json",
        (_RESULTS / "turn-fork.sha256").read_text().strip(),
        _RESULTS / "turn-fork-direct-live.json",
        _RESULTS / "turn-fork-subscription-live.json",
    )
    direct = _report(peer)
    panel = protocol.build(packet, board, digest, direct)
    return packet, board, direct, panel


def _samples(packet: _Doc, board: _Doc, peer: _Doc, panel: _Doc, start: int, stop: int):
    if not 0 <= start < stop <= len(panel["rows"]):
        raise ValueError("range not in registration")
    for row in panel["rows"][start:stop]:
        if row["prompt_sha256"] is None:
            continue
        text = protocol.prompt(packet, board, peer, row)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != row["prompt_sha256"]:
            raise ValueError("changed reviewer prompt")
        yield {"id": row["sample_id"], "input": text}


def _stock(packet: _Doc, panel: _Doc, report: _Doc, native_study: Path) -> _Doc:
    from ..decision_cross.stock import _native_sources
    from ..turn_fork.stock import _score_choices

    worlds, original = _native_sources(inputs.source_index(packet), native_study)
    matched = optimal = feasible = targets = 0
    for row, registered in zip(report["rows"], panel["rows"], strict=True):
        if row["sample_id"] != registered["sample_id"]:
            raise ValueError("unregistered native comparison")
        if row["state"] != "complete":
            continue
        actions = row["outcome"]["actions"]
        native = _score_choices(worlds[row["source_id"]], actions)
        for recorded, actual in zip(actions, native["rows"], strict=True):
            if any(recorded[key] is not actual[key] for key in ("optimal", "feasible")):
                raise ValueError("conditional grade differs from native world")
        matched += native["episodes"]
        optimal += native["optimal"]
        feasible += native["feasible"]
        targets += row["outcome"]["target"]["optimal"]
    return {
        "schema": "seatlab.peer-update-stock/v1",
        "panel_sha256": protocol.panel_digest(panel),
        "native_source_teams": len(worlds),
        "original_native_actions": original["episodes"],
        "matched_reviewer_actions": matched,
        "optimal": optimal,
        "feasible": feasible,
        "target_optimal": targets,
        "measurement": "action_rescore_on_original_stock_not_new_harbor_rewards",
    }


def _dispatch(args: argparse.Namespace) -> int:
    packet, board, peer, panel = _load(args.peer_report)
    if args.mode == "panel":
        _print(panel)
    elif args.mode == "samples":
        for sample in _samples(
            packet,
            board,
            peer,
            panel,
            args.start,
            len(panel["rows"]) if args.stop is None else args.stop,
        ):
            _print(sample)
    elif args.mode == "run-inspect":
        from .inspect_backend import run_inspect
        import tempfile

        # Inspect receives only an ID and a user message. Stock remains in the scorer.
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "reviewers.jsonl"
            with dataset.open("x", encoding="utf-8") as output:
                count = 0
                for sample in _samples(
                    packet,
                    board,
                    peer,
                    panel,
                    args.start,
                    len(panel["rows"]) if args.stop is None else args.stop,
                ):
                    output.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    count += 1
            if count == 0:
                raise ValueError("no available first peer replies in the range")
            log = run_inspect(dataset, args.config, args.log_dir)
            _print(
                {
                    "inspect_log": str(log),
                    "enrolled": count,
                    "panel_sha256": protocol.panel_digest(panel),
                }
            )
    elif args.mode == "report":
        from . import evidence

        _print(evidence.collect(packet, board, panel, args.logs))
    else:
        from . import analysis

        result = _report(args.report)
        effects = analysis.summarize(packet, board, panel, peer, result)
        if args.mode == "effects":
            _print(effects)
        elif args.mode == "summary":
            coverage = effects["coverage"]
            print(
                f"{coverage['states']['complete']}/{coverage['scheduled_rows']} first attempts completed "
                f"on {coverage['registered_sources']} teams in {coverage['registered_worlds']} worlds."
            )
            for arm, data in effects["arms"].items():
                target = data["scopes"]["target"]
                all_items = data["scopes"]["all_shipments"]
                print(
                    f"  {arm}: target {target['numerator']}/{target['denominator']}; "
                    f"all shipments {all_items['numerator']}/{all_items['denominator']} optimal."
                )
            changed = effects["target_best_reported_bay"]
            print(
                f"The newer target report changed the best feasible bay for "
                f"{changed['changed']}/{changed['compared_sources']} original teams."
            )
            print("The score uses original stock. It is not a new native reward.")
        elif args.mode == "stock":
            _print(_stock(packet, panel, result, args.native_study))
        elif args.mode == "receipts":
            from . import evidence

            _print(evidence.service(result, args.receipts, args.model))
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in (
        "panel",
        "samples",
        "run-inspect",
        "report",
        "summary",
        "effects",
        "stock",
        "receipts",
    ):
        p = modes.add_parser(name, allow_abbrev=False)
        p.add_argument(
            "--peer-report",
            type=Path,
            default=_RESULTS / "decision-cross-direct-live.json",
        )
        if name in ("samples", "run-inspect"):
            p.add_argument("--start", type=int, default=0)
            p.add_argument(
                "--stop",
                type=int,
                help="Exclusive. Unavailable sources remain missing.",
            )
            if name == "run-inspect":
                p.add_argument(
                    "--config",
                    type=Path,
                    required=True,
                    help="Registered dispatch command, not a model API key.",
                )
                p.add_argument(
                    "--log-dir",
                    type=Path,
                    required=True,
                    help="New private Inspect log directory. Never repeat a claimed sample.",
                )
        elif name == "report":
            p.add_argument(
                "logs",
                nargs="+",
                type=Path,
                help="Original first-attempt Inspect .eval logs; each sample must occur at most once.",
            )
        elif name in ("summary", "effects", "stock", "receipts"):
            p.add_argument(
                "report",
                nargs="?",
                type=Path,
                default=_RESULTS / "peer-update-inspect-live.json",
            )
            if name == "stock":
                p.add_argument("--native-study", type=Path, required=True)
            elif name == "receipts":
                p.add_argument("--receipts", type=Path, required=True)
                p.add_argument("--model", required=True)
    try:
        return _dispatch(cli.parse_args(argv))
    except (
        OSError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        RuntimeError,
        RecursionError,
        ImportError,
    ):
        print(
            "Could not verify the peer panel, load the optional Inspect runtime, or join the original first attempts.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
