import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from .. import native_panel as panel
from .. import team_driver as teams
from ..handoff.__main__ import _meter, _print
from ..native_report import _read_json
from . import audit, protocol


def experiment() -> panel.Experiment:
    return panel.Experiment(
        tag="dispatch_answers",
        grid=protocol.grid,
        stage_roles=protocol.STAGES,
        reservation_prefix="danswer-",
        agent_import=protocol.AGENT_IMPORT,
        seat_sources=("__main__.py", "agent.py", "audit.py", "protocol.py"),
        inspect_trial=audit.inspect_trial,
        summarize=audit.summarize,
        channel_kwarg="channel_id",
        shared_seat_sources=(
            "attention/agent.py",
            "attention/audit.py",
            "attention/protocol.py",
            "briefing/agent.py",
            "briefing/channel.py",
            "briefing/protocol.py",
            "capsule.py",
            "export_capsules.py",
            "handoff/__main__.py",
            "native_panel.py",
            "native_report.py",
            "seat_agent.py",
            "team_driver.py",
            "team_report.py",
            "visibility/deepseek.py",
            "visibility/native_pairs.py",
            "visibility/route_audit.py",
            "visibility/route_cli.py",
            "visibility/route_run.py",
            "visibility/spend.py",
        ),
    )


def _occupied(root: Path, cell: panel.Record, ledger: panel.Record) -> bool:
    return os.path.lexists(panel._cell_dir(root, cell)) or panel._reserved(ledger, cell)


def _checked_meter(rows: list[panel.Record], ledger: panel.Record) -> panel.Record:
    outcomes = [row["outcome"] for row in rows if row["outcome"] is not None]
    meter = _meter(outcomes, ledger)
    count = len(outcomes) * len(protocol.STAGES)
    if meter["by_match"] != ({"matched": count} if count else {}):
        raise ValueError("A complete native trial has unverified direct model usage")
    return meter


def _stock(
    spec: panel.Experiment,
    cells: list[panel.Record],
    root: Path,
    args: argparse.Namespace,
) -> panel.Record:
    teams._check_path(root, 0o700, directory=True)
    teams._check_path(root / "cells", 0o700, directory=True)
    saved = panel.files._object(teams._read_sealed(root / "register.json"))["plan"]
    plan = panel._plan(spec, cells, "direct", None, source_revision=args.source_revision)
    if teams._bytes(saved) != teams._bytes(plan):
        raise ValueError("The study does not match its registered stock sources")
    published = _read_json(args.report)
    if (
        not isinstance(published, dict)
        or published.get("schema") != spec.schema("report")
        or teams._bytes(published["plan"]) != teams._bytes(plan)
        or not isinstance(published.get("rows"), list)
    ):
        raise ValueError("The public report does not match the registered study")
    checked = []
    for cell, row in zip(cells, published["rows"], strict=True):
        native = panel._observed(spec, root, cell, plan, None)
        if native["state"] == "unclaimed" and row["state"] == "reserved_without_trial":
            native["state"] = "reserved_without_trial"
        if teams._bytes(native) != teams._bytes(row):
            raise ValueError("The public report differs from the native trial evidence")
        checked.append(native)
    summary = spec.summarize(cells, checked)
    if teams._bytes(summary) != teams._bytes(published["summary"]):
        raise ValueError("The public summary differs from the native trial evidence")
    return {
        "schema": spec.schema("stock"),
        "matched_native_trials": summary["observed_cells"],
        "matched_native_actions": summary["observed_cells"] * protocol.EPISODES,
        "ledger_checked": False,
        "summary": summary,
    }


def _dispatch(args: argparse.Namespace) -> int:
    spec = experiment()
    cells = list(spec.grid())
    mode = args.mode
    if mode == "plan":
        _print(
            {
                "cells": cells,
                "episodes": protocol.EPISODES,
                "max_calls": spec.max_calls(cells),
            }
        )
        return 0
    root = panel._checked_study(args.study)
    if mode == "seal":
        panel._register(spec, cells, root, args)
        return 0
    if mode == "stock":
        _print(_stock(spec, cells, root, args))
        return 0
    plan, ledger = panel._load(
        spec, cells, root, source_revision=getattr(args, "source_revision", None)
    )
    if plan["transport"] != "direct" or ledger is None:
        raise ValueError("Expected a host direct DeepSeek study")
    if mode == "pending":
        pending = [
            cell["cell_id"] for cell in cells if not _occupied(root, cell, ledger)
        ]
        _print({"cells": pending})
        return 0
    if mode in ("run", "collect"):
        index = next(
            (i for i, cell in enumerate(cells) if cell["cell_id"] == args.cell), None
        )
        if index is None:
            raise ValueError("Unknown registered cell")
        cell = cells[index]
        if mode == "run":
            if index % 2 and not _occupied(root, cells[index - 1], ledger):
                raise ValueError("The first registered arm must start before the second")
            row = panel._run(spec, root, cell, plan, ledger, args.harbor)
            ledger = panel._ledger(spec, Path(os.environ["SEATLAB_SPEND_DIR"]), cells)
        else:
            row = panel._observed(spec, root, cell, plan, ledger)
        _checked_meter([row], ledger)
        _print(row)
        return 0 if row["state"] == "complete" else 1
    rows = [panel._observed(spec, root, cell, plan, ledger) for cell in cells]
    _print(
        {
            "schema": spec.schema("report"),
            "plan": plan,
            "rows": rows,
            "summary": spec.summarize(cells, rows),
            "meter": _checked_meter(rows, ledger),
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Native Dispatch Handoff eligibility answers", allow_abbrev=False
    )
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "seal", "pending", "run", "collect", "report", "stock"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        if mode != "plan":
            sub.add_argument("--study", type=Path, required=True)
        if mode == "seal":
            sub.add_argument("--transport", choices=("direct",), default="direct")
            sub.add_argument("--spend-dir", type=Path, required=True)
            sub.set_defaults(config=None)
        if mode in ("run", "collect"):
            sub.add_argument("--cell", required=True)
        if mode == "run":
            sub.add_argument("--harbor", type=Path, required=True)
        if mode in ("collect", "report", "stock"):
            sub.add_argument("--source-revision")
        if mode == "stock":
            sub.add_argument("report", type=Path)
    try:
        return _dispatch(parser.parse_args(argv))
    except (
        OSError,
        KeyError,
        TypeError,
        IndexError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ):
        print(
            "Cannot seal, launch, or verify this study. Keep existing first attempts closed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
