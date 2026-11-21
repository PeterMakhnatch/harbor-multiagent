import argparse
import json
from pathlib import Path

from ..visibility import calendar_packet
from . import audit, read_opportunities, team_source, team_views
from . import protocol as p

SCHEMA = "seatlab.two-read-opportunity/v1"
_FIELDS = ("run", "world", "arm", "trial_uuid", "result_sha256", "complete", "error")
_INVALID_EVIDENCE = (
    ValueError,
    OSError,
    LookupError,
    TypeError,
    AttributeError,
    RuntimeError,
)


def _phase_paths(directory: Path, cell: audit.Cell) -> dict[str, Path]:
    seats = directory / "trials" / cell.run / "seats"
    return {
        phase: seats / audit._actor(cell, phase) / "agent" / f"{phase}.json"
        for phase in p.PHASES
    }


def _stamp(path: Path) -> tuple[int, int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size


def _snapshot(
    directory: Path, paths: dict[str, Path]
) -> dict[Path, tuple[int, int, int, int, int]] | None:
    artifacts = {
        path
        for receipt in paths.values()
        for path in (receipt, *receipt.parents)
        if path.is_relative_to(directory)
    }
    try:
        return {path: _stamp(path) for path in artifacts}
    except OSError:
        return None


def _values(paths: dict[str, Path], native: audit.Row) -> audit.Row:
    values = {}
    for phase, path in paths.items():
        turn = audit._read(path)
        text = turn["response_text"]
        value = p.decode(text, phase)
        audit.require(
            turn["trial_uuid"] == native["trial_uuid"]
            and turn["status"] == "accepted"
            and p.digest(text) == turn["response_sha256"]
            and p.wire(value) == p.wire(turn["value"]),
            "replay_turn_binding",
        )
        values[phase] = value
    return values


def _measure(
    directory: Path,
    cell: audit.Cell,
    paths: dict[str, Path],
    native: audit.Row,
    worlds: tuple[calendar_packet.World, ...],
) -> dict[str, int]:
    data = audit.raw(directory / "trials" / cell.run / "result.json")
    result = audit._object(data)
    audit.require(
        p.digest(data) == native["result_sha256"]
        and result["id"] == native["trial_uuid"],
        "replay_result_binding",
    )
    if isinstance(cell, team_views.Cell):
        attempt = audit._attempt(directory, cell)
        config_data = audit._sealed(directory / "config.json")
        audit.require(
            p.digest(config_data) == attempt["config_sha256"]
            and attempt["source"] == native["source"],
            "replay_config_binding",
        )
        task = audit._object(config_data)["task"]
        audit.require(task == result["task_id"], "replay_task_binding")
        tables = team_source.tables(Path(task["path"]), cell)
    else:
        tables = worlds[cell.index].tables
    target = audit._target(tables)
    values = _values(paths, native)
    metrics = read_opportunities.measure(
        calendar_packet.starts(tables[p.SEATS.index(cell.receiver)]),
        values["initial"],
        values["revision"],
        values["pick"],
        values["choice"],
        target,
    )
    visible = native["joint_slot_in_final_report"]
    audit.require(
        metrics["actual_visible"] == visible
        and metrics["choice_correct"] == native["reward"]
        and metrics["actual_unique_correct"]
        == (visible and native["final_candidate_count"] == 1),
        "replay_factual_mismatch",
    )
    return metrics


def report(
    job: Path,
    spend_dir: Path,
    *,
    private_views: bool = False,
    role_views: bool = False,
) -> audit.Row:
    if private_views and role_views:
        raise ValueError("conflicting_view_modes")
    if role_views:
        cell_type, world_ids, arms = (
            team_views.RoleCell,
            team_views.WORLDS,
            team_views.ROLE_ARMS,
        )
    elif private_views:
        cell_type, world_ids, arms = team_views.Cell, team_views.WORLDS, team_views.ARMS
    else:
        cell_type, world_ids, arms = p.Cell, p.WORLDS, p.ARMS
    job = job.expanduser().absolute()
    receipts = {}
    for world in world_ids:
        for arm in arms:
            cell = cell_type(world, arm)
            directory = job / "cells" / cell.run
            paths = _phase_paths(directory, cell)
            receipts[cell.run] = (cell, directory, paths, _snapshot(directory, paths))
    native = audit.report(
        job,
        spend_dir,
        controls=False,
        private_views=private_views,
        role_views=role_views,
    )
    worlds = ()
    if not (private_views or role_views) and any(
        row["complete"] for row in native["cells"]
    ):
        try:
            worlds = calendar_packet.load(
                calendar_packet.PACKET, calendar_packet.PACKET_SHA256
            )
        except _INVALID_EVIDENCE:
            worlds = ()
    cells, eligible = [], {arm: [] for arm in arms}
    for row in native["cells"]:
        public = {key: row[key] for key in _FIELDS}
        public["metrics"] = None
        cells.append(public)
        if row["complete"]:
            try:
                cell, directory, paths, before = receipts[row["run"]]
                audit.require(
                    before is not None and _snapshot(directory, paths) == before,
                    "replay_receipts_changed",
                )
                metrics = _measure(directory, cell, paths, row, worlds)
                audit.require(
                    _snapshot(directory, paths) == before, "replay_receipts_changed"
                )
            except _INVALID_EVIDENCE:
                public.update(
                    complete=False, error="missing_or_invalid_replay_evidence"
                )
            else:
                public["metrics"] = metrics
                eligible[cell.arm].append(metrics)
    return {
        "schema": SCHEMA,
        "native_schema": native["schema"],
        "cells": cells,
        "arms": {
            arm: read_opportunities.totals(rows) for arm, rows in eligible.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--private-views", action="store_true")
    views.add_argument("--role-views", action="store_true")
    args = parser.parse_args(argv)
    try:
        evidence = report(
            args.job,
            args.spend_dir,
            private_views=args.private_views,
            role_views=args.role_views,
        )
    except _INVALID_EVIDENCE:
        parser.exit(1, "offer replay: native audit unavailable\n")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
