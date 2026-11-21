import argparse
import json
import sys
from os import scandir
from pathlib import Path

from ..visibility import calendar_packet as packet
from ..visibility import calendar_replica_source as replica

SCHEMA = "seatlab.flotilla-heldout/v1"
GENERATOR_COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
_FIRST = 12
_LAST = 23
_SEED = 1
_STOCK_LIMITS = (_SEED, 4000, 200, 600.0)
_MAX_MANIFEST_BYTES = 8192


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


def build_manifest(schedule_root: Path) -> dict[str, object]:
    schedule_root = _physical_directory(schedule_root)
    names = {i: f"schedule-{i:03d}-talk" for i in range(_FIRST, _LAST + 1)}
    _check_entries(schedule_root, set(names.values()))
    seen = {
        _calendar_digest(world.tables)
        for world in packet.load(packet.PACKET, packet.PACKET_SHA256)
    }
    worlds = []
    for index, name in names.items():
        task = schedule_root / name
        try:
            _check_layout(task)
            tables = packet._checked_source_tables(
                task, index, name, stock_limits=_STOCK_LIMITS
            )
            calendar = _calendar_digest(tables)
            if calendar in seen:
                raise packet._Invalid("calendar repeats a legacy or heldout identity")
            semantics = replica._fingerprint(task).semantics_sha256
        except packet._Invalid as exc:
            raise packet._Invalid(f"{name}: {exc}") from None
        seen.add(calendar)
        worlds.append(
            {
                "instance": index,
                "calendar_sha256": calendar,
                "stock_semantics_sha256": semantics,
            }
        )
    return {
        "schema": SCHEMA,
        "generator_commit": GENERATOR_COMMIT,
        "seed": _SEED,
        "instance_range": [_FIRST, _LAST],
        "prior_packet_sha256": packet.PACKET_SHA256,
        "worlds": worlds,
    }


def _check_expected(path: Path, regenerated: str) -> None:
    path = _physical_directory(path.parent) / path.name
    try:
        expected = _wire(packet._json(packet._read(path, _MAX_MANIFEST_BYTES)))
    except (packet._Invalid, RecursionError):
        raise packet._Invalid("invalid or unreadable public manifest") from None
    if expected != regenerated:
        raise packet._Invalid("regenerated manifest differs from public manifest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a stock held-out calendar panel."
    )
    parser.add_argument("schedule_root", type=Path)
    parser.add_argument("--expect", type=Path, help="compare with a public manifest")
    args = parser.parse_args(argv)
    try:
        regenerated = _wire(build_manifest(args.schedule_root))
        if args.expect is not None:
            _check_expected(args.expect, regenerated)
    except packet._Invalid as exc:
        print(f"heldout: {exc}", file=sys.stderr)
        return 1
    print(regenerated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
