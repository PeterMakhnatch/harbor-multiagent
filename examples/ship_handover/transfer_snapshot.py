import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import NoReturn, cast, override

from ..native_evidence import Incomplete, Invalid, Record, same
from .collect import (
    INFLIGHT_SCHEMA,
    INFLIGHT_STATUSES,
    RETURNING_SCHEMA,
    collect_returning,
)
from .study import INFLIGHT, MAX_REGISTRATION_BYTES, load_registration, spec_for
from .transfer_contract import (
    GRADE_OF,
    GRADE_SOURCES,
    GRADED,
    MAX_PACKET_BYTES,
    NATIVE_AGREED,
    OMITTED,
    OPTIONALLY_GRADED,
    PACKET_SCHEMA,
    PACKET_STATUSES,
    bindings,
    check_packet,
    registration_digest,
)

PROG = "python3.12 -BS -m examples.ship_handover.transfer_snapshot"
ARGUMENTS = "REG ROOT [ROOT ...] [--task T]"
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
COPY_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
REGISTRATION = "registration is unreadable or invalid"
UNBOUND = "registration does not bind a transfer world"
CHANGED = "registration changed during collection"
REFUSED = "collector refused the roots or task"
UNUSABLE = "collector met unusable evidence"
STORAGE = "storage read failed"
COLLECTOR = "collector output breaks the returning contract"
PACKET = "packet breaks the transfer contract"
OVERSIZE = "packet exceeds the transfer bound"
EVIDENCE_FIELDS = frozenset({"grade", "grade_source", "observation"})
ROW_EXTRAS = frozenset(
    {"status", "grade", "observation", "grade_source", "execution", "world_evidence"}
)
assert set(GRADED) | set(OPTIONALLY_GRADED) <= set(INFLIGHT_STATUSES)
assert OMITTED in INFLIGHT_STATUSES
assert all(OMITTED not in found for found in PACKET_STATUSES.values())
assert all(set(found) <= set(INFLIGHT_STATUSES) for found in PACKET_STATUSES.values())


class Refusal(Exception):
    pass


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        print(f"transfer_snapshot: usage: {ARGUMENTS}", file=sys.stderr)
        raise SystemExit(2)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Refusal(reason)


def _registration_bytes(path: Path) -> bytes:
    try:
        descriptor = os.open(path, PLAIN_FILE)
        with open(descriptor, "rb") as handle:
            regular = stat.S_ISREG(os.fstat(handle.fileno()).st_mode)
            data = handle.read(MAX_REGISTRATION_BYTES + 1) if regular else b""
    except OSError:
        raise Refusal(REGISTRATION) from None
    _require(regular and 0 < len(data) <= MAX_REGISTRATION_BYTES, REGISTRATION)
    return data


def _write_copy(path: Path, data: bytes) -> None:
    assert not path.exists()
    descriptor = os.open(path, COPY_FILE, 0o600)
    with open(descriptor, "wb") as handle:
        written = handle.write(data)
    assert written == len(data)


def _load(path: Path) -> tuple[Record, str]:
    try:
        registration = load_registration(path)
        digest = registration_digest(registration)
    except (ValueError, Invalid, OSError):
        raise Refusal(REGISTRATION) from None
    return registration, digest


def _bind(registration: Record) -> list[str]:
    try:
        bound = bindings(registration)
    except (ValueError, Invalid):
        raise Refusal(UNBOUND) from None
    order = [cell["ticket"] for cell in registration["cells"]]
    assert list(bound) == order and len(set(order)) == len(order)
    return order


def _collect(copy: Path, roots: list[Path], task: Path | None) -> Record:
    try:
        return collect_returning(copy, roots, task)
    except ValueError:
        raise Refusal(REFUSED) from None
    except (Invalid, Incomplete):
        raise Refusal(UNUSABLE) from None
    except OSError:
        raise Refusal(STORAGE) from None


def _packet_row(
    ticket: str, status: str, grade: int | None, source: str | None
) -> Record:
    assert (grade is None) == (source is None)
    return {
        "ticket": ticket,
        "status": status,
        "world_grade": grade,
        "grade_source": source,
    }


def _world_grade(world: object) -> tuple[int | None, str | None]:
    if world is None:
        return None, None
    _require(type(world) is dict and set(world) == EVIDENCE_FIELDS, COLLECTOR)
    assert type(world) is dict
    evidence = cast(Record, world)
    grade, source = evidence["grade"], evidence["grade_source"]
    _require(type(grade) is int and grade in (0, 1), COLLECTOR)
    _require(source in GRADE_SOURCES, COLLECTOR)
    return grade, source


def _project(
    row: Record, cell: Record, fields: frozenset[str], allowed: tuple[str, ...]
) -> Record | None:
    _require(set(row) == fields | ROW_EXTRAS, COLLECTOR)
    _require(all(same(row[field], cell[field]) for field in fields), COLLECTOR)
    status, grade, world = row["status"], row["grade"], row["world_evidence"]
    _require(status == OMITTED or status in allowed, COLLECTOR)
    if status in GRADED:
        _require(type(grade) is int and grade == GRADE_OF[status], COLLECTOR)
        _require(row["grade_source"] == NATIVE_AGREED, COLLECTOR)
        _require(row["observation"] is not None and world is None, COLLECTOR)
        return _packet_row(cell["ticket"], status, grade, NATIVE_AGREED)
    _require(grade is None and row["grade_source"] is None, COLLECTOR)
    _require(row["observation"] is None, COLLECTOR)
    _require(status in OPTIONALLY_GRADED or world is None, COLLECTOR)
    if status == OMITTED:
        return None
    world_grade, source = _world_grade(world)
    return _packet_row(cell["ticket"], status, world_grade, source)


def _rows(registration: Record, order: list[str], report: object) -> list[Record]:
    spec = spec_for(registration)
    schema = INFLIGHT_SCHEMA if spec is INFLIGHT else RETURNING_SCHEMA
    _require(type(report) is dict and report.get("schema") == schema, COLLECTOR)
    assert type(report) is dict
    found = report.get("cells")
    _require(type(found) is list and len(found) == len(order), COLLECTOR)
    assert type(found) is list
    tickets = [row.get("ticket") if type(row) is dict else None for row in found]
    _require(tickets == order, COLLECTOR)
    allowed = PACKET_STATUSES[spec.world]
    rows: list[Record] = []
    for row, cell in zip(found, registration["cells"], strict=True):
        assert type(row) is dict
        projected = _project(cast(Record, row), cell, spec.cell_fields(), allowed)
        if projected is not None:
            rows.append(projected)
    assert len(rows) <= len(order)
    return rows


def snapshot(registration_path: Path, roots: list[Path], task: Path | None) -> Record:
    data = _registration_bytes(registration_path)
    with tempfile.TemporaryDirectory(prefix="transfer-snapshot-") as scratch:
        copy = Path(scratch) / "registration.json"
        _write_copy(copy, data)
        registration, digest = _load(copy)
        order = _bind(registration)
        report = _collect(copy, roots, task)
        _require(_load(copy)[1] == digest, CHANGED)
    _require(_registration_bytes(registration_path) == data, CHANGED)
    rows = _rows(registration, order, report)
    packet = {"schema": PACKET_SCHEMA, "registration_sha256": digest, "rows": rows}
    try:
        checked = check_packet(registration, packet)
    except ValueError:
        raise Refusal(PACKET) from None
    _require(same(checked, rows), PACKET)
    return packet


def render(packet: Record) -> str:
    text = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    _require(len(text) + 1 <= MAX_PACKET_BYTES, OVERSIZE)
    return text + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = Parser(prog=PROG, usage=f"%(prog)s {ARGUMENTS}")
    parser.add_argument("registration", type=Path, metavar="REG")
    parser.add_argument("roots", type=Path, nargs="+", metavar="ROOT")
    parser.add_argument("--task", type=Path, metavar="T")
    args = parser.parse_args(argv)
    try:
        text = render(snapshot(args.registration, args.roots, args.task))
    except Refusal as error:
        print(f"transfer_snapshot: {error}", file=sys.stderr)
        return 2
    except OSError:
        print(f"transfer_snapshot: {STORAGE}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
