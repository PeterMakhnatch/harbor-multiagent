"""Replay first-attempt clarification receipts and compare matched delivery arms offline."""

import argparse
import json
import stat
from dataclasses import dataclass
from hashlib import sha256
from math import fsum
from pathlib import Path
from typing import Any

from ..capsule import encode_view
from . import clarify_views, dispatch_packet, pair_report
from .clarify_schedule import (
    ARMS,
    OFFSETS_MINUTES,
    SEEDS,
    WINDOW_MINUTES,
    Cell,
    grid,
    packet_path,
)

_RECEIPT_SCHEMA = "seatlab-clarification-turns-v1"
_RECEIPT_FIELDS = {
    "schema",
    "cell_id",
    "window",
    "seed",
    "mask",
    "arm",
    "packet_sha256",
    "status",
    "selected_case",
    "delivered_case",
    "actions",
    "grade",
    "turns",
}
_INCOMPLETE = {
    "started",
    "transport_failed",
    "budget_unresolved",
    "served_model_invalid",
    "evidence_invalid",
    "packet_changed",
    "outside_window",
}
_MODEL = pair_report._MODEL
_REVISION = pair_report._REVISION
_SERVED = {_MODEL, _REVISION, _REVISION.lower()}
_MAX_RECEIPT_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class _Result:
    status: str
    reason: str | None = None
    selected: int | None = None
    delivered: int | None = None
    grade: dict[str, Any] | None = None

    def public(self, cell: Cell) -> dict[str, Any]:
        return {
            "cell_id": cell.cell_id,
            "window": cell.window,
            "seed": cell.seed,
            "mask": cell.mask,
            "arm": cell.arm,
            "hidden": list(cell.hidden),
            "queued_case": cell.queued_case,
            "status": self.status,
            "reason": self.reason,
            "selected_case": self.selected,
            "delivered_case": self.delivered,
            "grade": self.grade,
        }


class _InvalidReceipt(ValueError):
    """An allowlisted rejection reason, safe to publish without receipt text."""


def _read_file(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("expected a bounded regular file")
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("file exceeds the size limit")
    return raw


def _read_packets(packets: Path) -> dict[int, tuple[str, dict[str, Any]]]:
    sources = {}
    for seed in SEEDS:
        raw = _read_file(packet_path(packets, seed), dispatch_packet._MAX_PACKET_BYTES)
        packet = dispatch_packet.verify_packet(dispatch_packet._load_json(raw))
        if (packet["seed"], packet["repetition"]) != (seed, 1):
            raise ValueError("packet does not name the registered native world")
        sources[seed] = (sha256(raw).hexdigest(), packet)
    return sources


def _turn_matches(turn: object, prompt: str) -> bool:
    return (
        isinstance(turn, dict)
        and turn.keys() == {"prompt_sha256", "served_model"}
        and isinstance(turn["served_model"], str)
        and turn["served_model"] in _SERVED
        and turn["prompt_sha256"] == sha256(prompt.encode("utf-8")).hexdigest()
    )


def _replay(row: object, cell: Cell, digest: str, packet: dict[str, Any]) -> _Result:
    if not isinstance(row, dict) or row.keys() != _RECEIPT_FIELDS:
        raise _InvalidReceipt("receipt_fields_invalid")
    if (
        row["schema"] != _RECEIPT_SCHEMA
        or row["cell_id"] != cell.cell_id
        or row["arm"] != cell.arm
        or any(
            type(row[key]) is not int or row[key] != getattr(cell, key)
            for key in ("window", "seed", "mask")
        )
    ):
        raise _InvalidReceipt("cell_identity_mismatch")
    if row["packet_sha256"] != digest:
        raise _InvalidReceipt("packet_sha256_mismatch")

    status = row["status"]
    if not isinstance(status, str) or (
        status != "complete" and status not in _INCOMPLETE
    ):
        raise _InvalidReceipt("status_invalid")
    turns = row["turns"]
    if not isinstance(turns, dict) or not turns.keys() <= {"select", "act"}:
        raise _InvalidReceipt("turns_invalid")
    if "select" in turns and not _turn_matches(
        turns["select"], clarify_views.build_initial(packet, cell)
    ):
        raise _InvalidReceipt("select_turn_mismatch")

    selected, delivered = row["selected_case"], row["delivered_case"]
    if selected is not None and (
        "select" not in turns or type(selected) is not int or selected not in range(4)
    ):
        raise _InvalidReceipt("selected_case_invalid")
    if (selected is None and delivered is not None) or (
        selected is not None
        and (type(delivered) is not int or delivered != cell.delivery(selected))
    ):
        raise _InvalidReceipt("delivered_case_mismatch")
    if status == "started" and (
        "act" in turns or ("select" in turns and selected is None)
    ):
        raise _InvalidReceipt("checkpoint_mismatch")
    if "act" in turns and (
        selected is None
        or not _turn_matches(
            turns["act"], clarify_views.build_final(packet, cell, selected)
        )
    ):
        raise _InvalidReceipt("act_turn_mismatch")

    actions, grade = row["actions"], row["grade"]
    if status != "complete":
        if actions is not None or grade is not None:
            raise _InvalidReceipt("outcome_on_incomplete_receipt")
        # A reserved delivery is not evidence that the act turn received it.
        observed_delivery = delivered if "act" in turns else None
        return _Result("incomplete", status, selected, observed_delivery)
    if "act" not in turns or actions is None or grade is None:
        raise _InvalidReceipt("completion_evidence_missing")
    try:
        normalized = clarify_views.validate_actions(packet, cell, json.dumps(actions))
        replayed = clarify_views.grade_actions(packet, cell, normalized)
    except (ValueError, TypeError, KeyError, RuntimeError) as error:
        raise _InvalidReceipt("actions_invalid") from error
    if encode_view(actions) != encode_view(normalized):
        raise _InvalidReceipt("actions_not_normalized")
    if encode_view(grade) != encode_view(replayed):
        raise _InvalidReceipt("grade_mismatch")
    return _Result("complete", selected=selected, delivered=delivered, grade=replayed)


def _inspect(path: Path, cell: Cell, digest: str, packet: dict[str, Any]) -> _Result:
    try:
        raw = _read_file(path, _MAX_RECEIPT_BYTES)
    except FileNotFoundError:
        return _Result("missing")
    except (OSError, ValueError):
        return _Result("invalid", "receipt_unreadable")
    try:
        return _replay(dispatch_packet._load_json(raw), cell, digest, packet)
    except _InvalidReceipt as error:
        return _Result("invalid", str(error))
    except (ValueError, TypeError, RecursionError):
        return _Result("invalid", "receipt_json_invalid")


def _totals(cells: list[Cell], results: dict[Cell, _Result]) -> dict[str, int]:
    totals = {
        "complete_cells": 0,
        "decisions": 0,
        "feasible": 0,
        "optimal": 0,
        "selected_hidden": 0,
        "selected_already_visible": 0,
        "delivered_hidden": 0,
        "delivered_already_visible": 0,
        "delivery_differs_from_selection": 0,
    }
    for cell in cells:
        row = results[cell]
        if row.selected is not None:
            key = (
                "selected_hidden"
                if row.selected in cell.hidden
                else "selected_already_visible"
            )
            totals[key] += 1
        if row.delivered is not None:
            key = (
                "delivered_hidden"
                if row.delivered in cell.hidden
                else "delivered_already_visible"
            )
            totals[key] += 1
            totals["delivery_differs_from_selection"] += row.delivered != row.selected
        if row.grade is not None:
            totals["complete_cells"] += 1
            totals["decisions"] += row.grade["episodes"]
            totals["feasible"] += row.grade["feasible"]
            totals["optimal"] += row.grade["optimal"]
    return totals


def _pairs(
    cells: tuple[Cell, ...], results: dict[Cell, _Result]
) -> list[dict[str, Any]]:
    pairs = []
    for active in cells:
        if active.arm != "active":
            continue
        queue = Cell(active.window, active.seed, active.mask, "queue")
        a, q = results[active], results[queue]
        deltas = None
        changed = None
        if a.grade is not None and q.grade is not None:
            deltas = {
                metric: (
                    a.grade[metric] / a.grade["episodes"]
                    - q.grade[metric] / q.grade["episodes"]
                )
                for metric in ("feasible", "optimal")
            }
            changed = a.delivered != q.delivered
        pairs.append(
            {
                "window": active.window,
                "seed": active.seed,
                "mask": active.mask,
                "active_cell": active.cell_id,
                "queue_cell": queue.cell_id,
                "active_status": a.status,
                "queue_status": q.status,
                "active_minus_queue": deltas,
                "different_delivered_case": changed,
            }
        )
    return pairs


def _mean_delta(deltas: list[dict[str, float]]) -> dict[str, float] | None:
    if not deltas:
        return None
    return {
        metric: fsum(delta[metric] for delta in deltas) / len(deltas)
        for metric in ("feasible", "optimal")
    }


def _summary(
    cells: list[Cell], pairs: list[dict[str, Any]], results: dict[Cell, _Result]
) -> dict[str, Any]:
    complete = [pair for pair in pairs if pair["active_minus_queue"] is not None]
    paired_ids = {pair[f"{arm}_cell"] for pair in complete for arm in ARMS}
    worlds = []
    for seed in SEEDS:
        world_deltas = [
            pair["active_minus_queue"] for pair in complete if pair["seed"] == seed
        ]
        worlds.append(
            {
                "seed": seed,
                "scheduled_pairs": sum(pair["seed"] == seed for pair in pairs),
                "complete_pairs": len(world_deltas),
                "active_minus_queue": _mean_delta(world_deltas),
            }
        )
    supported = [
        world["active_minus_queue"]
        for world in worlds
        if world["active_minus_queue"] is not None
    ]
    arms = {
        arm: _totals([cell for cell in cells if cell.arm == arm], results)
        for arm in ARMS
    }
    return {
        "scheduled_pairs": len(pairs),
        "complete_pairs": len(complete),
        "incomplete_pairs": len(pairs) - len(complete),
        "worlds_with_complete_pairs": len(supported),
        "worlds": worlds,
        "equal_world_mean_active_minus_queue": (
            _mean_delta(supported) if len(supported) == len(SEEDS) else None
        ),
        "pairs_with_different_deliveries": sum(
            pair["different_delivered_case"] for pair in complete
        ),
        "selections": {
            key: sum(arm[key] for arm in arms.values())
            for key in ("selected_hidden", "selected_already_visible")
        },
        "arms": arms,
        "paired_arms": {
            arm: _totals(
                [
                    cell
                    for cell in cells
                    if cell.arm == arm and cell.cell_id in paired_ids
                ],
                results,
            )
            for arm in ARMS
        },
    }


def collect(packets: Path | str, study: Path | str) -> dict[str, Any]:
    """Reduce only registered first-attempt receipts against the public packets."""
    study = Path(study)
    if not study.is_dir():
        raise ValueError("study must be a directory")
    cells_dir = study / "cells"
    cells = grid()
    names = {f"{cell.cell_id}.json" for cell in cells}
    if cells_dir.is_symlink() or (
        cells_dir.exists()
        and (
            not cells_dir.is_dir()
            or any(
                path.suffix == ".json" and path.name not in names
                for path in cells_dir.iterdir()
            )
        )
    ):
        raise ValueError("unexpected study cell set")
    sources = _read_packets(Path(packets))
    results = {
        cell: _inspect(cells_dir / f"{cell.cell_id}.json", cell, *sources[cell.seed])
        for cell in cells
    }
    pairs = _pairs(cells, results)
    missing = sum(row.status == "missing" for row in results.values())
    return {
        "schema": "seatlab-clarification-report-v1",
        "scope": "Derived clarification replay on four recorded worlds; not a native Harbor score.",
        "unit": "world; masks, windows, and arms are repeated observations, not independent tasks",
        "basis": (
            "Packet integrity, turn prompts, and grades are replayed. "
            "Served model is receipt-attested."
        ),
        "counting": (
            "All verified selections and evidenced deliveries count in arms. "
            "Only paired outcomes count in active-minus-queue differences."
        ),
        "independent_worlds": len(SEEDS),
        "requested_model": _MODEL,
        "registered_revision": _REVISION,
        "packet_sha256": {
            packet_path(Path(), seed).name: sources[seed][0] for seed in SEEDS
        },
        "counts": {
            "scheduled": len(cells),
            "attempted": len(cells) - missing,
            "missing": missing,
            **{
                status: sum(row.status == status for row in results.values())
                for status in ("complete", "incomplete", "invalid")
            },
        },
        "overall": _summary(list(cells), pairs, results),
        "windows": [
            {
                "window": window,
                "offset_minutes": offset,
                "duration_minutes": WINDOW_MINUTES,
                **_summary(
                    [cell for cell in cells if cell.window == window],
                    [pair for pair in pairs if pair["window"] == window],
                    results,
                ),
            }
            for window, offset in enumerate(OFFSETS_MINUTES)
        ],
        "pairs": pairs,
        "cells": [result.public(cell) for cell, result in results.items()],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets", type=Path)
    parser.add_argument("study", type=Path)
    args = parser.parse_args(argv)
    try:
        report = collect(args.packets, args.study)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        parser.exit(
            1, "Cannot verify the registered packets or read the clarification study.\n"
        )
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
