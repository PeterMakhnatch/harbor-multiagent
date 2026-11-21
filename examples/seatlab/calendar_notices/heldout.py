import argparse
import json
import sys
from dataclasses import dataclass
from os import scandir
from pathlib import Path
from typing import Literal

from ..visibility import calendar_packet as packet
from ..visibility import calendar_replica_source as replica

SCHEMA = "seatlab.flotilla-heldout/v1"
GENERATOR_COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
MANIFEST = (
    Path(__file__).resolve().parents[1] / "results/flotilla-schedule-heldout-12-23.json"
)
MANIFEST_SHA256 = "7539109a5a7ec4dc5ffa0d26455021a8270391ec19d03e367e20c79a7fa0c192"
_MAX_MANIFEST_BYTES = 8192


@dataclass(frozen=True, slots=True)
class _Cohort:
    first: int
    last: int
    stock_limits: tuple[int, int, int, float]
    prior_heldout_manifest_sha256: str | None = None

    def names(self) -> dict[int, str]:
        return {i: f"schedule-{i:03d}-talk" for i in range(self.first, self.last + 1)}


_LEGACY = _Cohort(12, 23, (1, 4000, 200, 600.0))
_VISIBLE = _Cohort(24, 47, (471223, 4000, 200, 600.0), MANIFEST_SHA256)
_COHORTS = {"heldout": _LEGACY, "visible": _VISIBLE}


def _physical_directory(directory: Path) -> Path:
    try:
        directory = directory.absolute()
    except OSError:
        raise packet._Invalid("cannot locate source directory") from None
    if ".." in directory.parts or "\0" in str(directory):
        raise packet._Invalid("unsafe directory path")
    physical = Path(directory.anchor)
    packet._directory(physical)
    for part in directory.parts[1:]:
        physical /= part
        packet._directory(physical)
    return directory


def _check_entries(directory: Path, names: set[str]) -> None:
    packet._directory(directory)
    remaining = names.copy()
    try:
        with scandir(directory) as entries:
            for entry in entries:
                if entry.name not in remaining:
                    raise packet._Invalid("unexpected entry in stock directory")
                remaining.remove(entry.name)
    except OSError:
        raise packet._Invalid("cannot list stock directory") from None
    if remaining:
        raise packet._Invalid("stock directory is incomplete")


def _check_layout(task: Path) -> None:
    children: dict[Path, set[str]] = {}
    for name in packet._SOURCE_DIRS | packet._SOURCE_FILES:
        path = task / name
        children.setdefault(path.parent, set()).add(path.name)
    for directory in sorted(children):
        _check_entries(directory, children[directory])


def _wire(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _calendar_digest(tables: tuple[packet.Table, packet.Table]) -> str:
    normalized = sorted(tuple(sorted(table)) for table in tables)
    return packet._digest(_wire(normalized).encode("ascii"))


def _manifest_fields(cohort: _Cohort) -> dict[str, object]:
    fields: dict[str, object] = {
        "schema": SCHEMA,
        "generator_commit": GENERATOR_COMMIT,
        "seed": cohort.stock_limits[0],
        "instance_range": [cohort.first, cohort.last],
        "prior_packet_sha256": packet.PACKET_SHA256,
    }
    if cohort.prior_heldout_manifest_sha256 is not None:
        fields["prior_heldout_manifest_sha256"] = cohort.prior_heldout_manifest_sha256
    return fields


def _pinned_manifest_rows(
    path: Path, expected_sha256: str, cohort: _Cohort
) -> tuple[tuple[str, str], ...]:
    expected_sha256 = packet._check_digest(expected_sha256)
    path = _physical_directory(path.parent) / path.name
    raw = packet._read(path, _MAX_MANIFEST_BYTES)
    if packet._digest(raw) != expected_sha256:
        raise packet._Invalid("heldout manifest checksum mismatch")
    fields = _manifest_fields(cohort)
    manifest = packet._object(packet._json(raw), *fields, "worlds")
    if any(manifest[key] != value for key, value in fields.items()):
        raise packet._Invalid("heldout manifest metadata mismatch")
    rows = packet._list(manifest["worlds"])
    if len(rows) != cohort.last - cohort.first + 1:
        raise packet._Invalid("heldout manifest world count mismatch")
    digests = []
    for index, row in enumerate(rows, start=cohort.first):
        row = packet._object(
            row, "instance", "calendar_sha256", "stock_semantics_sha256"
        )
        if type(row["instance"]) is not int or row["instance"] != index:
            raise packet._Invalid("heldout manifest instance mismatch")
        digests.append(
            (
                packet._check_digest(row["calendar_sha256"]),
                packet._check_digest(row["stock_semantics_sha256"]),
            )
        )
    return tuple(digests)


def _check_schedule(schedule_root: Path, cohort: _Cohort) -> Path:
    schedule_root = _physical_directory(schedule_root)
    _check_entries(schedule_root, set(cohort.names().values()))
    return schedule_root


def _checked_stock(
    task: Path,
    index: int,
    name: str,
    cohort: _Cohort,
    *,
    expected_semantics_sha256: str | None = None,
) -> tuple[replica.Identity, tuple[packet.Table, packet.Table]]:
    _check_layout(task)
    source = replica._fingerprint(task)
    if (
        expected_semantics_sha256 is not None
        and source.semantics_sha256 != expected_semantics_sha256
    ):
        raise packet._Invalid("heldout task differs from the pinned stock semantics")
    tables = packet._checked_source_tables(
        task, index, name, stock_limits=cohort.stock_limits
    )
    return source, tables


def _prior_calendar_digests(cohort: _Cohort) -> set[str]:
    seen = {
        _calendar_digest(world.tables)
        for world in packet.load(packet.PACKET, packet.PACKET_SHA256)
    }
    if cohort.prior_heldout_manifest_sha256 is not None:
        heldout_rows = _pinned_manifest_rows(
            MANIFEST, cohort.prior_heldout_manifest_sha256, _LEGACY
        )
        for calendar, _ in heldout_rows:
            if calendar in seen:
                raise packet._Invalid("calendar repeats a legacy or heldout identity")
            seen.add(calendar)
    return seen


def build_manifest(
    schedule_root: Path, *, cohort: Literal["heldout", "visible"] = "heldout"
) -> dict[str, object]:
    study = _COHORTS[cohort]
    schedule_root = _check_schedule(schedule_root, study)
    seen = _prior_calendar_digests(study)
    worlds = []
    for index, name in study.names().items():
        task = schedule_root / name
        try:
            source, tables = _checked_stock(task, index, name, study)
            calendar = _calendar_digest(tables)
            if calendar in seen:
                raise packet._Invalid("calendar repeats a legacy or heldout identity")
        except packet._Invalid as exc:
            raise packet._Invalid(f"{name}: {exc}") from None
        seen.add(calendar)
        worlds.append(
            {
                "instance": index,
                "calendar_sha256": calendar,
                "stock_semantics_sha256": source.semantics_sha256,
            }
        )
    return {**_manifest_fields(study), "worlds": worlds}


def _render_manifest(manifest: object) -> bytes:
    return (_wire(manifest) + "\n").encode("ascii")


def _check_expected(path: Path, regenerated: bytes, *, exact: bool) -> None:
    path = _physical_directory(path.parent) / path.name
    try:
        raw = packet._read(path, _MAX_MANIFEST_BYTES)
        expected = raw if exact else _render_manifest(packet._json(raw))
    except (packet._Invalid, RecursionError):
        raise packet._Invalid("invalid or unreadable public manifest") from None
    if expected != regenerated:
        raise packet._Invalid("regenerated manifest differs from public manifest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a stock held-out calendar panel."
    )
    parser.add_argument("schedule_root", type=Path)
    parser.add_argument("--cohort", choices=tuple(_COHORTS), default="heldout")
    parser.add_argument("--expect", type=Path, help="compare with a public manifest")
    args = parser.parse_args(argv)
    try:
        regenerated = _render_manifest(
            build_manifest(args.schedule_root, cohort=args.cohort)
        )
        if args.expect is not None:
            _check_expected(args.expect, regenerated, exact=args.cohort == "visible")
    except packet._Invalid as exc:
        print(f"heldout: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(regenerated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
