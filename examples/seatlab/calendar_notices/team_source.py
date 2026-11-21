from pathlib import Path

from ..visibility import calendar_packet as packet
from ..visibility import calendar_replica_source
from . import heldout, team_views
from . import protocol as p

MANIFEST = (
    Path(__file__).resolve().parents[1] / "results/flotilla-schedule-heldout-12-23.json"
)
MANIFEST_SHA256 = "7539109a5a7ec4dc5ffa0d26455021a8270391ec19d03e367e20c79a7fa0c192"


def _expected(cell: team_views.Cell) -> tuple[str, str]:
    path = heldout._physical_directory(MANIFEST.parent) / MANIFEST.name
    raw = packet._read(path, heldout._MAX_MANIFEST_BYTES)
    if packet._digest(raw) != MANIFEST_SHA256:
        raise packet._Invalid("heldout manifest checksum mismatch")
    manifest = packet._object(packet._json(raw))
    rows = packet._list(manifest["worlds"])
    row = packet._object(
        rows[team_views.WORLDS.index(cell.world)],
        "instance",
        "calendar_sha256",
        "stock_semantics_sha256",
    )
    if row["instance"] != cell.index:
        raise packet._Invalid("heldout manifest instance mismatch")
    return row["calendar_sha256"], row["stock_semantics_sha256"]


def _checked(
    task: Path, cell: team_views.Cell
) -> tuple[calendar_replica_source.Identity, tuple[packet.Table, packet.Table]]:
    if task.name != cell.task_name:
        raise packet._Invalid("heldout task name differs from the calendar world")
    task = heldout._physical_directory(task)
    calendar, semantics = _expected(cell)
    heldout._check_layout(task)
    source = calendar_replica_source._fingerprint(task)
    if source.semantics_sha256 != semantics:
        raise packet._Invalid("heldout task differs from the pinned stock semantics")
    own_tables = packet._checked_source_tables(
        task, cell.index, cell.task_name, stock_limits=heldout._STOCK_LIMITS
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
    if seat not in team_views.SEATS:
        raise ValueError("invalid_seat")
    own_tables = tables(task, cell)
    table = own_tables[team_views.SEATS.index(seat)]
    kwargs: dict[str, str | int] = {
        "world": cell.world,
        "arm": cell.arm,
        "seat": seat,
        "control": cell.control,
        "calendar_sha256": p.digest(p.wire(table)),
    }
    if cell.control != "live":
        target = packet._shared(own_tables)
        kwargs["control_slot"] = (
            target if cell.control == "oracle" else int(target == 0)
        )
    return kwargs
