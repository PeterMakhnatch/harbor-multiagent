import argparse
import os
import re
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, override

from ..calendar_case import _directory as _open_directory
from ..receiver_panel import _shard
from ..visibility import calendar_packet as packet
from ..visibility import calendar_replica_source
from . import heldout, team_source, team_views
from . import protocol as p

ATTEMPT_SCHEMA = "seatlab.native-calendar-visible-transfer/v1"
AUDIT_SCHEMA = "seatlab.native-calendar-visible-transfer-results/v1"
_COHORT_FILE = "calendar-cohort.json"
_MIN_START = 48
_MAX_INDEX = 999_999
_MAX_COUNT = 32
_MAX_SEED = (1 << 32) - 1
_WORLD = re.compile(r"w[0-9]{2,6}")
_Rows = tuple[tuple[str, str], ...]


def prefix(cohort_sha256: str) -> str:
    return f"cvt-{packet._check_digest(cohort_sha256)[:20]}-"


@dataclass(frozen=True, slots=True)
class TransferCell(team_views.JoinCell):
    cohort_sha256: str = ""

    @override
    def __post_init__(self) -> None:
        if (
            type(self.world) is not str
            or not _WORLD.fullmatch(self.world)
            or not _MIN_START <= self.index <= _MAX_INDEX
            or self.world != f"w{self.index:02d}"
            or self.arm not in team_views.JOIN_ARMS
            or self.control not in ("live", "oracle", "wrong")
        ):
            raise ValueError("invalid_cell")
        packet._check_digest(self.cohort_sha256)

    @property
    @override
    def first(self) -> str:
        index = self.index
        label = f"seatlab-calendar-transfer-order-v1:{self.cohort_sha256}:{index // 4}:{index % 2}"
        bit = int(p.digest(label)[:2], 16) & 1
        return "lists" if (index // 2) % 2 == bit else "checked"

    @property
    @override
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"{prefix(self.cohort_sha256)}{mode}{self.world}-{self.arm}"


def _cohort(start: int, count: int, seed: int) -> heldout._Cohort:
    if (
        type(start) is not int
        or type(count) is not int
        or type(seed) is not int
        or not 4 <= count <= _MAX_COUNT
        or count % 4 != 0
        or not _MIN_START <= start <= _MAX_INDEX - count + 1
        or start % 4 != 0
        or not 1 <= seed <= _MAX_SEED
    ):
        raise packet._Invalid("invalid transfer cohort range or seed")
    return heldout._Cohort(
        start,
        start + count - 1,
        (seed, 4000, 200, 600.0),
        team_source.JOIN_MANIFEST_SHA256,
    )


def _render(cohort: heldout._Cohort, rows: _Rows) -> bytes:
    if len({calendar for calendar, _ in rows}) != len(rows):
        raise packet._Invalid("transfer cohort repeats a calendar")
    return heldout._render_manifest(
        {
            **heldout._manifest_fields(cohort),
            "worlds": [
                {
                    "instance": index,
                    "calendar_sha256": calendar,
                    "stock_semantics_sha256": semantics,
                }
                for index, (calendar, semantics) in enumerate(rows, start=cohort.first)
            ],
        }
    )


def _read_manifest(path: Path, *, sealed: bool) -> bytes:
    try:
        with _open_directory(path.parent) as directory:
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            fd = os.open(path.name, flags, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or (
                    info.st_size > heldout._MAX_MANIFEST_BYTES
                ):
                    raise packet._Invalid("expected a size-limited regular manifest")
                if sealed and (
                    stat.S_IMODE(info.st_mode) != 0o400
                    or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                ):
                    raise packet._Invalid("unsealed transfer cohort")
                raw = stream.read(heldout._MAX_MANIFEST_BYTES + 1)
    except packet._Invalid:
        raise
    except (OSError, ValueError):
        raise packet._Invalid("missing or unsafe transfer manifest") from None
    if len(raw) > heldout._MAX_MANIFEST_BYTES:
        raise packet._Invalid("transfer manifest exceeds size limit")
    return raw


def _load(
    manifest: Path, cohort_sha256: str, *, sealed: bool
) -> tuple[heldout._Cohort, _Rows]:
    expected = packet._check_digest(cohort_sha256)
    manifest = heldout._physical_directory(manifest.parent) / manifest.name
    raw = _read_manifest(manifest, sealed=sealed)
    if packet._digest(raw) != expected:
        raise packet._Invalid("transfer manifest checksum mismatch")
    fields = packet._object(packet._json(raw))
    extent = packet._list(fields.get("instance_range"))
    if len(extent) != 2:
        raise packet._Invalid("invalid transfer cohort range")
    start, end = extent
    seed = fields.get("seed")
    if type(start) is not int or type(end) is not int or type(seed) is not int:
        raise packet._Invalid("invalid transfer cohort range or seed")
    cohort = _cohort(start, end - start + 1, seed)
    rows = heldout._pinned_manifest_rows(manifest, expected, cohort)
    if raw != _render(cohort, rows):
        raise packet._Invalid("noncanonical transfer manifest")
    return cohort, rows


def _worlds(cohort: heldout._Cohort) -> tuple[str, ...]:
    return tuple(f"w{index:02d}" for index in range(cohort.first, cohort.last + 1))


def public_rows(manifest: Path, cohort_sha256: str) -> dict[str, tuple[str, str]]:
    cohort, rows = _load(manifest, cohort_sha256, sealed=False)
    return dict(zip(_worlds(cohort), rows, strict=True))


def worlds(job: Path, cohort_sha256: str) -> tuple[str, ...]:
    cohort, _ = _load(job / _COHORT_FILE, cohort_sha256, sealed=True)
    return _worlds(cohort)


def checked(
    job: Path, task: Path, cell: TransferCell
) -> tuple[calendar_replica_source.Identity, tuple[packet.Table, packet.Table]]:
    cohort, rows = _load(job / _COHORT_FILE, cell.cohort_sha256, sealed=True)
    if not cohort.first <= cell.index <= cohort.last or task.name != cell.task_name:
        raise packet._Invalid("task differs from the registered transfer world")
    root = _schedule(task.parent, cohort)
    calendar, semantics = rows[cell.index - cohort.first]
    source, tables = heldout._checked_stock(
        root / cell.task_name,
        cell.index,
        cell.task_name,
        cohort,
        expected_semantics_sha256=semantics,
    )
    if heldout._calendar_digest(tables) != calendar:
        raise packet._Invalid("task differs from the registered calendar identity")
    return source, tables


def _schedule(root: Path, cohort: heldout._Cohort) -> Path:
    root = heldout._check_schedule(root, cohort)
    for name in cohort.names().values():
        packet._directory(root / name)
    return root


def _freeze(root: Path, *, start: int, count: int, seed: int) -> bytes:
    cohort = _cohort(start, count, seed)
    root = _schedule(root, cohort)
    rows = []
    for index, name in cohort.names().items():
        source, tables = heldout._checked_stock(root / name, index, name, cohort)
        rows.append((heldout._calendar_digest(tables), source.semantics_sha256))
    return _render(cohort, tuple(rows))


def _plan(
    manifest: Path, cohort_sha256: str, shard: tuple[int, int]
) -> Iterator[bytes]:
    registered = tuple(public_rows(manifest, cohort_sha256))
    index, count = shard
    total = len(registered)
    if count > total:
        raise packet._Invalid("use at most one shard per registered world")
    first = index * total // count
    last = (index + 1) * total // count
    for world in registered[first:last]:
        lists = TransferCell(world, "lists", cohort_sha256=cohort_sha256)
        checked_cell = TransferCell(world, "checked", cohort_sha256=cohort_sha256)
        cells = (
            (lists, checked_cell) if lists.first == "lists" else (checked_cell, lists)
        )
        for order, cell in enumerate(cells):
            yield heldout._render_manifest(
                {
                    "world": cell.world,
                    "arm": cell.arm,
                    "receiver": cell.receiver,
                    "order": order,
                    "run": cell.run,
                    "cohort_sha256": cell.cohort_sha256,
                }
            )


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "transfer registration: invalid arguments; use --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Register and plan stock transfer calendars.", allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", allow_abbrev=False)
    freeze.add_argument("tasks", type=Path)
    freeze.add_argument("--start", type=int, required=True)
    freeze.add_argument("--count", type=int, required=True)
    freeze.add_argument("--seed", type=int, required=True)
    plan = commands.add_parser("plan", allow_abbrev=False)
    plan.add_argument("manifest", type=Path)
    plan.add_argument("--sha256", required=True)
    plan.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            sys.stdout.buffer.write(
                _freeze(args.tasks, start=args.start, count=args.count, seed=args.seed)
            )
        else:
            sys.stdout.buffer.writelines(_plan(args.manifest, args.sha256, args.shard))
    except packet._Invalid as exc:
        print(f"transfer registration: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RecursionError):
        print("transfer registration: invalid or unreadable input", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
