"""Identify a regenerated stock calendar task without exposing its contents."""

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import calendar_packet as packet

# Flotilla 71ddf40b, schedule 0–11, 2 seats, easy, seed 1, talk, 600 s.
# Each digest covers the full stock tree after the four Compose bindings below.
_SEMANTICS_SHA256 = (
    "b3cc8a3a615d7a6b7255e2e62565c5c30652dac9d91ad249bf75c67ed080807b",
    "079c4d1b306f36d3c483daa5bdb195b16f0bc7ea0fdbff3ed57ebd71691ddff0",
    "e29353d57ea67d24b03d7459f47f6ed5bb0ea2d05b5da9bad33765359536f673",
    "105cb41458851d5075c2de88a5baa40a19c0d33f11de884b4f241f7b37ad25c4",
    "2db5c269f7731b40bcf680cf0b2ee6baae9bca9aa9ea9a9e430099758302f986",
    "9397bbb166486b39169f9b00eee31b1e97d440e1d88d9281c12b76ee8cde57e4",
    "cf4c6effe3b5e4d9f4fff51383ff0e35b36bfb076040f6e0edc0385d33c0b1c4",
    "59d7a1443f09b0f3716e2bc163850a7adcf349c508920baac90fd1073bd99498",
    "46d33dfaf5dc1507f4d5b5443bfc35a5e329fddc1f00fb7cd15f965fc06f7636",
    "1966ecbabcdee0a564782bdf4a998ebbbd13965da134f9e71c77d4415da4ef6f",
    "e41431006c17b57d439961414ff07f90b55a66c70eb7cdefc69cbd6f6d12e474",
    "97e926fff7f4f18882aa9aad246134c1ca324b0e115b55ed1e1e7593597cd745",
)
_COMPOSE = "environment/docker-compose.yaml"
_MAIN_BINDINGS = re.compile(
    rb"^  main:\n"
    rb"    command: [^\r\n]+\n"
    rb"    environment:\n"
    rb"      FLOTILLA_CONDITION: [^\r\n]+\n"
    rb"      FLOTILLA_SEATS: [^\r\n]+\n"
    rb'      FLOTILLA_TOKENS: "p1:([0-9a-f]{32}),p2:([0-9a-f]{32})"\n'
    rb'      FLOTILLA_ADMIN: "([0-9a-f]{32})"$',
    re.MULTILINE,
)
_SEAT_BINDING = re.compile(
    rb"^  (p[12]):\n"
    rb"    <<: \*player\n"
    rb"    networks: \[seat-\1\]\n"
    rb"    environment:\n"
    rb'      SEAT: "\1"\n'
    rb'      SEAT_TOKEN: "([0-9a-f]{32})"$',
    re.MULTILINE,
)


@dataclass(frozen=True)
class Identity:
    native_sha256: str
    semantics_sha256: str


def _project_compose(raw: bytes) -> bytes:
    main = list(_MAIN_BINDINGS.finditer(raw))
    seats = list(_SEAT_BINDING.finditer(raw))
    by_role = {match[1]: match for match in seats}
    if len(main) != 1 or len(seats) != 2 or by_role.keys() != {b"p1", b"p2"}:
        raise packet._Invalid("missing or malformed calendar auth binding")
    bindings = main[0]
    p1, p2, admin = bindings.groups()
    if len({p1, p2, admin}) != 3 or (by_role[b"p1"][2], by_role[b"p2"][2]) != (p1, p2):
        raise packet._Invalid("inconsistent calendar auth binding")

    # Replace only credential spans. All other bytes, including line ends, stay fixed.
    projected = bytearray(raw)
    for match, group, marker in (
        (bindings, 1, b"1"),
        (bindings, 2, b"2"),
        (bindings, 3, b"0"),
        (by_role[b"p1"], 2, b"1"),
        (by_role[b"p2"], 2, b"2"),
    ):
        start, end = match.span(group)
        projected[start:end] = marker * 32
    return bytes(projected)


def _fingerprint(task: Path) -> Identity:
    files: set[str] = set()

    def walk(directory: Path) -> Identity:
        packet._directory(directory)
        native, semantics = [], []
        for path in packet._files(directory):
            relative = path.relative_to(task).as_posix()
            if relative in packet._SOURCE_DIRS:
                kind, identity = "dirhash", walk(path)
            elif relative in packet._SOURCE_FILES:
                files.add(relative)
                raw = packet._read(path)
                digest = packet._digest(raw)
                projected = (
                    packet._digest(_project_compose(raw))
                    if relative == _COMPOSE
                    else digest
                )
                kind, identity = "data", Identity(digest, projected)
            else:
                raise packet._Invalid("unexpected file in replica task tree")
            native.append(f"{kind}:{identity.native_sha256}\0name:{path.name}")
            semantics.append(f"{kind}:{identity.semantics_sha256}\0name:{path.name}")
        # Use Harbor's legacy dirhash protocol for both views of this regular tree.
        return Identity(
            packet._digest("\0\0".join(sorted(native)).encode("ascii")),
            packet._digest("\0\0".join(sorted(semantics)).encode("ascii")),
        )

    identity = walk(task)
    if files != packet._SOURCE_FILES:
        raise packet._Invalid("replica task tree is incomplete")
    return identity


def verify(task: Path, world: int) -> Identity:
    """Check the exact stock tree and role tables, and return only its two digests."""
    if task.name != packet._task_name(world):
        raise packet._Invalid("replica task name differs from the calendar world")
    try:
        task = task.absolute()
        physical = task.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise packet._Invalid("missing or unsafe replica task path") from None
    if task != physical:
        raise packet._Invalid("missing or unsafe replica task path")
    identity = _fingerprint(task)
    if identity.semantics_sha256 != _SEMANTICS_SHA256[world]:
        raise packet._Invalid("replica task differs from the pinned stock semantics")
    original = packet.load(packet.PACKET, packet.PACKET_SHA256)[world]
    if identity.native_sha256 == original.task_checksum:
        raise packet._Invalid("replica task repeats the original native checksum")
    if packet.source_tables(task, world) != original.tables:
        raise packet._Invalid("replica role tables differ from the calendar packet")
    return identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--world", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        identity = verify(args.tasks / packet._task_name(args.world), args.world)
    except packet._Invalid as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(identity), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
