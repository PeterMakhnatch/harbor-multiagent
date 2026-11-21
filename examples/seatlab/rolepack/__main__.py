"""Run a replacement agent on recorded team observations from one or more tasks."""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..handoff.external_audit import _report
from ..turn_fork.packet import _decode, _read_bytes
from . import protocol

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_PACK = _RESULTS / "rolepack-observations.json"
_REPORT = _RESULTS / "rolepack-inspect-live.json"
type _Doc = dict[str, Any]


def _emit(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _load(path: Path | None, commitment: str | None) -> _Doc:
    if (path is None) != (commitment is None):
        raise ValueError("a custom pack requires its exact byte commitment")
    path = path or _PACK
    expected = (
        commitment or (_RESULTS / "rolepack.sha256").read_text(encoding="ascii").strip()
    )
    raw = _read_bytes(path, 16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("unexpected role pack bytes")

    return protocol.validate(_decode(raw, integers_only=True))


def _summary(pack: _Doc, report: _Doc) -> None:
    records = {r["id"]: r for r in pack["records"]}
    for task in sorted({r["task"] for r in records.values()}):
        selected = [r for r in records.values() if r["task"] == task]
        print(
            f"{task}: {len(selected)} original team observations in {len({r['world'] for r in selected})} worlds"
        )
        for arm in (("original",) if pack["schema"] == protocol.SINGLE_SCHEMA else ("original", "peer")):
            rows = [r for r in report["rows"] if r["task"] == task and r["arm"] == arm]
            done = [r for r in rows if r["state"] == "complete"]
            n = sum(len(r["outcome"]["correct"]) for r in done)
            right = sum(sum(r["outcome"]["correct"]) for r in done)
            score = f"{right}/{n} optimal actions" if n else "no scored actions"
            print(
                f"  {arm}: {score}; {len(done)}/{len(rows)} completed first attempts."
            )
    print(
        "Each task uses its own scorer. These are new role decisions, not new native team rewards."
    )


def _show_comparison(result: _Doc) -> None:
    print(
        "Matched optimal actions: baseline -> candidate. Bounds give the equal-world difference."
    )
    for task, data in result["tasks"].items():
        for arm, stats in data["by_arm"].items():
            old = stats["baseline"]["matched_optimal_actions"]
            new = stats["candidate"]["matched_optimal_actions"]
            n = stats["matched_actions_per_side"]
            bounds = stats["equal_world_candidate_minus_baseline_bounds"]
            interval = (
                "no eligible source"
                if bounds is None
                else f"[{100 * bounds[0]:+.2f}, {100 * bounds[1]:+.2f}] pp"
            )
            scores = f"{old}/{n} -> {new}/{n}" if n else "no matched actions"
            print(f"  {task} ({arm}): {scores}; {interval}.")
    print("The tasks use distinct original worlds. This is not a new team reward.")


def _dispatch(a: argparse.Namespace) -> int:
    if a.mode == "from-inspect":
        from . import import_source

        _emit(import_source.build(_report(a.index), a.inspect_logs))
        return 0
    if a.mode == "build":
        from . import calendar_source, shipment_source

        rows = calendar_source.export(a.calendar_checkout) + shipment_source.export(
            a.shipment_results
        )
        _emit(
            protocol.validate(
                {
                    "schema": "seatlab.rolepack/v1",
                    "records": sorted(rows, key=lambda r: (r["task"], r["id"])),
                }
            )
        )
        return 0
    pack = _load(a.pack, a.sha256)
    if pack["schema"] == protocol.SINGLE_SCHEMA and a.mode in ("effects", "compare", "stock"):
        raise ValueError("this command compares paired peer contexts; use the stage experiment's analysis")
    if a.mode in ("plan", "samples", "show", "grade"):
        rows = protocol.plan(pack)
        records = {r["id"]: r for r in pack["records"]}
        if a.mode == "plan":
            _emit(
                {
                    "pack_sha256": protocol.digest(pack),
                    "worlds_by_task": {
                        task: len(
                            {r["world"] for r in pack["records"] if r["task"] == task}
                        )
                        for task in sorted({r["task"] for r in pack["records"]})
                    },
                    "records": dict(Counter(r["task"] for r in pack["records"])),
                    "first_attempts": len(rows),
                    "available_first_attempts": sum(
                        r["prompt_sha256"] is not None for r in rows
                    ),
                    "rows": rows,
                }
            )
        elif a.mode == "samples":
            for row in rows:
                if row["prompt_sha256"] is not None:
                    _emit(
                        {
                            "id": row["sample_id"],
                            "input": protocol.prompt(
                                records[row["record_id"]], row["arm"]
                            ),
                        }
                    )
        elif a.mode == "show":
            if not 0 <= a.index < len(rows) or rows[a.index]["prompt_sha256"] is None:
                raise ValueError("unavailable sample")
            row = rows[a.index]
            sys.stdout.write(protocol.prompt(records[row["record_id"]], row["arm"]))
        else:
            row = next((r for r in rows if r["sample_id"] == a.sample_id), None)
            if row is None or row["prompt_sha256"] is None:
                raise ValueError("unavailable sample")
            raw = sys.stdin.buffer.read(protocol._MAX_TURN_BYTES + 1)
            if len(raw) > protocol._MAX_TURN_BYTES:
                raise ValueError("oversized response")
            _emit(
                {
                    "sample_id": row["sample_id"],
                    "prompt_sha256": row["prompt_sha256"],
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "outcome": protocol.grade(
                        records[row["record_id"]], raw.decode("utf-8")
                    ),
                }
            )
    elif a.mode in ("seal", "pending", "run", "report"):
        from . import work

        if a.mode == "seal":
            _emit(work.seal(a.study, pack, a.config))
        elif a.mode == "pending":
            _emit(work.pending(a.study, pack))
        elif a.mode == "run":
            _emit(work.run(a.study, pack, a.start, a.stop))
        else:
            from . import evidence

            if a.study is not None:
                _emit(evidence.collect_claimed(pack, *work.snapshot(a.study, pack)))
            else:
                _emit(evidence.collect(pack, a.inspect_logs))
    elif a.mode == "compare":
        from . import compare

        if a.pack is not None and (a.baseline is None or a.candidate is None):
            raise ValueError("a custom pack requires both receiver reports")
        if a.candidate is None:
            baseline, candidate = (
                _REPORT,
                a.baseline or _RESULTS / "rolepack-astra-live.json",
            )
        else:
            baseline, candidate = a.baseline, a.candidate
        result = compare.compare(pack, _report(baseline), _report(candidate))
        if a.json:
            _emit(result)
        else:
            _show_comparison(result)
    else:
        from . import evidence

        report = evidence.verify(pack, _report(a.report))
        if a.mode == "summary":
            _summary(pack, report)
        elif a.mode == "effects":
            from . import analysis

            _emit(analysis.summarize(pack, report))
        elif a.mode == "receipts":
            _emit(evidence.service(report, a.receipts, a.model))
        elif a.mode == "stock":
            from . import calendar_source, shipment_source

            _emit(
                {
                    "schema": "seatlab.rolepack-stock/v1",
                    "pack_sha256": protocol.digest(pack),
                    "calendar": calendar_source.check(
                        a.calendar_checkout, pack, report
                    ),
                    "shipment": shipment_source.check(
                        a.shipment_results, pack, report, a.native_study
                    ),
                    "measurement": "original_task_scorers_not_new_native_team_rewards",
                }
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in (
        "build",
        "from-inspect",
        "plan",
        "samples",
        "show",
        "grade",
        "seal",
        "pending",
        "run",
        "report",
        "summary",
        "effects",
        "compare",
        "receipts",
        "stock",
    ):
        p = modes.add_parser(name, allow_abbrev=False)
        if name == "from-inspect":
            p.add_argument(
                "--index",
                type=Path,
                required=True,
                help="Task-owned source IDs and grading rubric; never sent to the model.",
            )
            p.add_argument(
                "--inspect-logs",
                nargs="+",
                type=Path,
                required=True,
                help="Private original .eval files containing the explicitly selected role samples.",
            )
        if name not in ("build", "from-inspect"):
            p.add_argument(
                "--pack",
                type=Path,
                help="Custom observation pack. Also supply --sha256.",
            )
            p.add_argument("--sha256", help="SHA-256 of the exact pack bytes.")
        if name in ("build", "stock"):
            p.add_argument(
                "--calendar-checkout",
                type=Path,
                required=True,
                help="Trusted original calendar-list checkout. Its original scoring code will run.",
            )
            p.add_argument("--shipment-results", type=Path, default=_RESULTS)
            if name == "stock":
                p.add_argument("--native-study", type=Path, required=True)
        if name == "show":
            p.add_argument("--index", type=int, required=True)
        if name == "grade":
            p.add_argument("--sample-id", required=True)
        if name in ("seal", "pending", "run", "report"):
            if name == "report":
                source = p.add_mutually_exclusive_group(required=True)
                source.add_argument("--study", type=Path)
                source.add_argument(
                    "--inspect-logs",
                    nargs="+",
                    type=Path,
                    help="Original one-turn, one-epoch Inspect archives, including incomplete first attempts.",
                )
            else:
                p.add_argument("--study", type=Path, required=True)
            if name == "seal":
                p.add_argument(
                    "--config",
                    type=Path,
                    required=True,
                    help="Single trusted dispatch program. No other seat needed.",
                )
            if name == "run":
                p.add_argument("--start", type=int, required=True)
                p.add_argument(
                    "--stop",
                    type=int,
                    required=True,
                    help="Exclusive. Interruptions stay claimed.",
                )
        if name == "compare":
            p.add_argument(
                "--json",
                action="store_true",
                help="Include per-world changes, bounds, and action transitions.",
            )
            p.add_argument(
                "baseline",
                nargs="?",
                type=Path,
                help="Both reports: baseline first. One report: compare it as the candidate against published Sol.",
            )
            p.add_argument("candidate", nargs="?", type=Path)
        if name in ("summary", "effects", "receipts", "stock"):
            p.add_argument("report", nargs="?", type=Path, default=_REPORT)
            if name == "receipts":
                p.add_argument("--receipts", type=Path, required=True)
                p.add_argument("--model", required=True)
    try:
        return _dispatch(cli.parse_args(argv))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ImportError,
        RuntimeError,
        RecursionError,
        sqlite3.Error,
    ):
        print(
            "Cannot verify this role pack, original task scorer, worker claim, or Inspect evidence.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
