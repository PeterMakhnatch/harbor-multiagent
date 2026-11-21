"""Compare independent dispatch programs while holding their incoming messages fixed."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ..handoff.external_audit import _report
from . import inputs

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_DOC = dict[str, Any]


def _print(value: object) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False))


def _inputs(args: argparse.Namespace) -> tuple[_DOC, _DOC, str]:
    if (args.packet is None) != (args.sha256 is None):
        raise ValueError("provide the packet and its commitment together")
    if (args.direct_senders is None) != (args.command_senders is None):
        raise ValueError("provide both frozen sender cohorts")
    return inputs.build(
        args.packet or _RESULTS / "turn-fork-packet.json",
        args.sha256
        or (_RESULTS / "turn-fork.sha256").read_text(encoding="ascii").strip(),
        args.direct_senders or _RESULTS / "turn-fork-direct-live.json",
        args.command_senders or _RESULTS / "turn-fork-subscription-live.json",
    )


def _collect(packet: _DOC, board: _DOC, digest: str, study: Path) -> _DOC:
    from . import audit, journal, run

    sources = inputs.source_index(packet)
    observations = {obs["obs_id"]: obs for obs in board["observations"]}
    rows = [
        journal.collect(
            study,
            digest,
            draw,
            sources[draw["source_id"]],
            observations[draw["obs_id"]],
        )
        for draw in inputs.draws(board)
    ]
    report = {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": run.load(study, digest)["transport"],
        "rows": rows,
    }
    return audit.verify(packet, board, digest, report)


def _dispatch(args: argparse.Namespace) -> int:
    packet, board, digest = _inputs(args)
    if args.mode == "board":
        _print(board)
        return 0
    draws = inputs.draws(board)
    n = len(draws)
    if args.mode == "plan":
        used = sum(r["obs_id"] is not None for r in board["selections"])
        _print(
            {
                "board_sha256": digest,
                "registered_selections": len(board["selections"]),
                "observed_selections": used,
                "unique_in_source_prompts": len(board["observations"]),
                "first_calls_per_receiver": n,
                "calls_saved_per_receiver": 2 * used - n,
                "draws": draws,
            }
        )
    elif args.mode == "show":
        if not 0 <= args.index < n:
            raise ValueError("unregistered draw")
        draw = draws[args.index]
        obs = next(
            obs for obs in board["observations"] if obs["obs_id"] == draw["obs_id"]
        )
        print(inputs.prompt(packet, obs))
    elif args.mode in ("inspect-samples", "grade-external"):
        from . import inspect_port

        if args.mode == "inspect-samples":
            for sample in inspect_port.samples(packet, board, digest):
                _print(sample)
        else:
            raw = sys.stdin.buffer.read(8193)
            if len(raw) > 8192:
                raise ValueError("oversized external reply")
            _print(
                inspect_port.grade_sample(
                    packet, board, digest, args.obs_id, raw.decode("utf-8")
                )
            )
    elif args.mode in ("seal", "pending", "run"):
        from . import run

        if args.mode == "seal":
            _print(
                run.seal(
                    args.study,
                    board,
                    digest,
                    args.transport,
                    args.config,
                    args.spend_dir,
                )
            )
        elif args.mode == "pending":
            _print(run.pending(packet, board, digest, args.study))
        else:
            stop = args.start + 1 if args.stop is None else args.stop
            if not 0 <= args.start < stop <= n:
                raise ValueError("unregistered range")
            rows = run.run_cells(
                packet, board, digest, args.study, range(args.start, stop)
            )
            _print(rows)
            return int(any(row["state"] == "incomplete" for row in rows))
    elif args.mode == "report":
        _print(_collect(packet, board, digest, args.study))
    elif args.mode == "effects":
        from . import analysis, audit

        d = audit.verify(packet, board, digest, _report(args.direct_receivers))
        s = audit.verify(packet, board, digest, _report(args.command_receivers))
        _print(analysis.summarize(board, d, s))
    elif args.mode == "compare":
        from . import audit, diagnosis

        baseline = audit.verify(packet, board, digest, _report(args.baseline))
        candidate = audit.verify(packet, board, digest, _report(args.candidate))
        _print(diagnosis.compare(packet, board, digest, baseline, candidate))
    else:
        from . import audit

        report = audit.verify(packet, board, digest, _report(args.report))
        if args.mode == "verify":
            _print(
                {
                    "schema": report["schema"],
                    "transport": report["transport"],
                    "board_sha256": digest,
                    "draws": len(report["rows"]),
                    "complete": sum(r["state"] == "complete" for r in report["rows"]),
                    "measurement": "stock_scores_not_native_rewards",
                }
            )
        elif args.mode == "diagnose":
            from . import diagnosis

            _print(diagnosis.describe(packet, board, digest, report))
        elif args.mode == "receipts":
            _print(audit.service(report, args.receipts, args.model))
        elif args.mode == "meter":
            from ..visibility import spend

            _print(
                audit.meter(
                    report,
                    spend.report(args.spend_dir, run_prefix="dx-" + digest[:12] + "-"),
                )
            )
        elif args.mode == "stock":
            from .stock import compare

            _print(compare(packet, board, report, args.native_study))
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in (
        "plan",
        "board",
        "show",
        "inspect-samples",
        "grade-external",
        "seal",
        "pending",
        "run",
        "report",
        "verify",
        "effects",
        "compare",
        "diagnose",
        "stock",
        "receipts",
        "meter",
    ):
        parser = modes.add_parser(name, allow_abbrev=False)
        parser.add_argument(
            "--packet",
            type=Path,
            help="Verified native checkpoint packet; also supply --sha256.",
        )
        parser.add_argument("--sha256", help="Pin the exact native checkpoint bytes.")
        parser.add_argument(
            "--direct-senders",
            type=Path,
            help="Direct two-role source cohort; supply both sender reports.",
        )
        parser.add_argument(
            "--command-senders", type=Path, help="Command two-role source cohort."
        )
        if name == "show":
            parser.add_argument("--index", type=int, required=True)
        elif name == "grade-external":
            parser.add_argument(
                "--obs-id",
                required=True,
                help="Exact Inspect sample ID; read the original UTF-8 reply from stdin.",
            )
        elif name == "compare":
            parser.add_argument("baseline", type=Path)
            parser.add_argument("candidate", type=Path)
        elif name in ("seal", "pending", "run", "report"):
            parser.add_argument("--study", type=Path, required=True)
            if name == "seal":
                parser.add_argument(
                    "--transport", choices=("direct", "commands"), required=True
                )
                parser.add_argument("--spend-dir", type=Path)
                parser.add_argument("--config", type=Path)
            elif name == "run":
                parser.add_argument("--start", type=int, required=True)
                parser.add_argument(
                    "--stop",
                    type=int,
                    help="Exclusive; defaults to a single fresh draw.",
                )
        elif name == "effects":
            parser.add_argument(
                "--direct-receivers",
                type=Path,
                default=_RESULTS / "decision-cross-direct-live.json",
            )
            parser.add_argument(
                "--command-receivers",
                type=Path,
                default=_RESULTS / "decision-cross-subscription-live.json",
            )
        elif name in ("verify", "diagnose", "stock", "receipts", "meter"):
            parser.add_argument("report", type=Path)
            if name == "stock":
                parser.add_argument("--native-study", type=Path, required=True)
            elif name == "receipts":
                parser.add_argument("--receipts", type=Path, required=True)
                parser.add_argument("--model", required=True)
            elif name == "meter":
                parser.add_argument("--spend-dir", type=Path, required=True)
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
        sqlite3.Error,
    ):
        print(
            "Cannot verify the saved observation, registered receiver draw, or first-attempt evidence.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
