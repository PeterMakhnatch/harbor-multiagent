from pathlib import Path

from ..visibility import calendar_packet as packet
from ..visibility import calendar_replica_source
from . import heldout, team_views
from . import protocol as p
from .heldout import MANIFEST, MANIFEST_SHA256

JOIN_MANIFEST = (
    Path(__file__).resolve().parents[1] / "results/flotilla-schedule-visible-24-47.json"
)
JOIN_MANIFEST_SHA256 = (
    "671425d16ca2a0b84e417fbb88d6065a7df254c98a482e57f66d6bbbdcbe02dd"
)


def _checked(
    task: Path, cell: team_views.Cell
) -> tuple[calendar_replica_source.Identity, tuple[packet.Table, packet.Table]]:
    if task.name != cell.task_name:
        raise packet._Invalid("heldout task name differs from the calendar world")
    if isinstance(cell, team_views.JoinCell):
        cohort = heldout._VISIBLE
        path, sha256 = JOIN_MANIFEST, JOIN_MANIFEST_SHA256
        task = heldout._check_schedule(task.parent, cohort) / task.name
    else:
        cohort = heldout._LEGACY
        path, sha256 = MANIFEST, MANIFEST_SHA256
        task = heldout._physical_directory(task)
    rows = heldout._pinned_manifest_rows(path, sha256, cohort)
    calendar, semantics = rows[cell.index - cohort.first]
    source, own_tables = heldout._checked_stock(
        task, cell.index, cell.task_name, cohort, expected_semantics_sha256=semantics
    )
    if heldout._calendar_digest(own_tables) != calendar:
        raise packet._Invalid("heldout task differs from the pinned calendar identity")
    return source, own_tables


def identity(task: Path, cell: team_views.Cell) -> calendar_replica_source.Identity:
    source, _ = _checked(task, cell)
    return source


def tables(task: Path, cell: team_views.Cell) -> tuple[packet.Table, packet.Table]:
    _, own_tables = _checked(task, cell)
    return own_tables


def seat_kwargs(task: Path, cell: team_views.Cell, seat: str) -> dict[str, str | int]:
    return _seat_kwargs(tables(task, cell), cell, seat)


def _seat_kwargs(
    own_tables: tuple[packet.Table, packet.Table], cell: team_views.Cell, seat: str
) -> dict[str, str | int]:
    if seat not in team_views.SEATS:
        raise ValueError("invalid_seat")
    table = own_tables[team_views.SEATS.index(seat)]
    kwargs: dict[str, str | int] = {
        "world": cell.world,
        "arm": cell.arm,
        "seat": seat,
        "control": cell.control,
        "calendar_sha256": p.digest(p.wire(table)),
    }
    if isinstance(cell, team_views.RepeatJoinCell):
        kwargs["repetition"] = cell.repetition
    if cell.control != "live":
        target = packet._shared(own_tables)
        kwargs["control_slot"] = (
            target if cell.control == "oracle" else int(target == 0)
        )
    return kwargs
