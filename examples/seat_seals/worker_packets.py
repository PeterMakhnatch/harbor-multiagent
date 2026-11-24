import hashlib
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

from ..native_evidence import Incomplete, Invalid, canonical_uuid, same
from ..native_study import (
    DIGEST,
    as_choice,
    as_list,
    as_record,
    as_text,
    record_bytes,
    require,
)
from ..seat_checkpoints.collect import HOST_JOURNAL, WORLD_LOG
from ..seat_handoffs.native_records import read_record
from ..shift_board.collect import (
    CONFIG,
    CUT,
    IDENTITY,
    LOCK,
    MAX_ENTRIES,
    MAX_ROOT_ENTRIES,
    MAX_ROOTS,
    OBSERVATION,
    RESULT,
    REWARD,
    _identifiers,
    _registered_teams,
    load_verifier,
)
from ..shift_board.plan import TICKET
from .collect import (
    ATTEMPT,
    GATES,
    SCHEDULE_RECEIPT,
    SEAL_LOG,
    STATUSES,
    Native,
    _native,
    collect,
)
from .contract import load_registration
from .worker_layout import (
    MAX_RECORD_BYTES,
    _check_path,
    assigned,
    digest,
    make_plan,
    validate_plan,
)
from .worker_root import binding_state, open_root, root_binding

SCHEMA = "harbor.seat-seal.worker-packet/v1"
PACKET_FIELDS = frozenset({"schema", "plan", "worker", "claims", "rows"})
ROW_ORDER = ("ticket", "status", "grade", "joined", "gate", "native_tag")
ROW_FIELDS = frozenset(ROW_ORDER)
WITNESSED = ("graded", "native_error")
UNWITNESSED = ("incomplete", "invalid")
DUPLICATE = "duplicate_id"
MISSING = "missing"
GRADES = (0, 1)
UNKNOWN_GATE = "unknown"
IDENTIFIER_SOURCES = (RESULT, IDENTITY, OBSERVATION, CUT)
MAX_CLAIMS = MAX_ENTRIES * len(IDENTIFIER_SOURCES)
MAX_PACKET_BYTES = MAX_RECORD_BYTES
EVIDENCE = (
    RESULT,
    CONFIG,
    LOCK,
    ATTEMPT,
    IDENTITY,
    CUT,
    OBSERVATION,
    REWARD,
    WORLD_LOG,
    HOST_JOURNAL,
    SEAL_LOG,
    SCHEDULE_RECEIPT,
)
ALLOWED_BINDINGS = ("unbound", "same")
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
STAMP_FIELDS = (
    "st_mode",
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)

Record = dict[str, Any]
Stamp = tuple[int, ...] | str
Entry = tuple[str, Stamp, list[Stamp]]


class Snapshot(NamedTuple):
    roots: list[tuple[str, int, int]]
    claims: dict[str, list[Entry]]


assert set(WITNESSED) | set(UNWITNESSED) | {DUPLICATE, MISSING} < set(STATUSES)
assert UNKNOWN_GATE in GATES


def native_tag(registration: str, native_id: str) -> str:
    as_text(registration, "registration digest", DIGEST)
    require(canonical_uuid(native_id), "native trial id is not a canonical UUID")
    text = f"{registration}\0{native_id}".encode("ascii")
    tag = "sha256:" + hashlib.sha256(text).hexdigest()
    assert DIGEST.fullmatch(tag) is not None
    return tag


def _check_row(value: object) -> Record:
    row = as_record(value, "packet row", ROW_FIELDS)
    ticket = as_text(row["ticket"], "packet row ticket", TICKET)
    status = as_choice(row["status"], f"ticket {ticket} status", STATUSES)
    grade, joined, tag = row["grade"], row["joined"], row["native_tag"]
    require(
        grade is None or (type(grade) is int and grade in GRADES),
        f"ticket {ticket} grade is not 0, 1 or null",
    )
    require(type(joined) is bool, f"ticket {ticket} joined is not a boolean")
    gate = as_choice(row["gate"], f"ticket {ticket} gate", GATES)
    if tag is not None:
        as_text(tag, f"ticket {ticket} native tag", DIGEST)
    if status in WITNESSED:
        require(tag is not None, f"ticket {ticket} is {status} without a native tag")
        require(gate != UNKNOWN_GATE, f"ticket {ticket} is {status} with no gate read")
        require(
            status != "graded" or grade is not None,
            f"ticket {ticket} is graded without a grade",
        )
    else:
        require(
            grade is None and joined is False and gate == UNKNOWN_GATE and tag is None,
            f"ticket {ticket} is {status} but carries native evidence",
        )
    require(not joined or grade is not None, f"ticket {ticket} joins without a grade")
    return {field: row[field] for field in ROW_ORDER}


def _check_claims(value: object, tickets: list[str]) -> list[list[str]]:
    claims = as_list(value, "worker packet claims", 0, MAX_CLAIMS)
    own = frozenset(tickets)
    checked: list[list[str]] = []
    for claim in claims:
        pair = as_list(claim, "worker packet claim", 2, 2)
        ticket = as_text(pair[0], "claim ticket", TICKET)
        require(ticket in own, f"claim names ticket {ticket} of another worker")
        checked.append([ticket, as_text(pair[1], f"ticket {ticket} claim", DIGEST)])
    require(
        all(first < second for first, second in zip(checked, checked[1:])),
        "worker packet claims are not sorted unique pairs",
    )
    return checked


def _check_witnesses(rows: list[Record], claims: list[list[str]]) -> None:
    owners: dict[str, set[str]] = {}
    for ticket, tag in claims:
        owners.setdefault(tag, set()).add(ticket)
    shared = {ticket for found in owners.values() if len(found) > 1 for ticket in found}
    claimed = {ticket for ticket, _ in claims}
    for row in rows:
        ticket, status, tag = row["ticket"], row["status"], row["native_tag"]
        require(
            (ticket in shared) == (status == DUPLICATE),
            f"ticket {ticket} duplicate status disagrees with the packet claims",
        )
        require(
            status != MISSING or ticket not in claimed,
            f"ticket {ticket} is missing but holds identifier claims",
        )
        require(
            tag is None or ticket in owners.get(tag, set()),
            f"ticket {ticket} native tag is not among its claims",
        )


def validate_packet(value: object, plan: Record) -> Record:
    plan = validate_plan(plan)
    packet = as_record(value, "worker packet", PACKET_FIELDS)
    require(packet["schema"] == SCHEMA, "worker packet schema differs")
    require(packet["plan"] == digest(plan), "worker packet belongs to another plan")
    worker = packet["worker"]
    require(
        type(worker) is int and 0 <= worker < plan["workers"],
        "worker packet worker is not a planned worker",
    )
    tickets = assigned(plan, worker)
    rows = as_list(packet["rows"], "worker packet rows", len(tickets), len(tickets))
    checked = [_check_row(row) for row in rows]
    require(
        [row["ticket"] for row in checked] == tickets,
        f"worker {worker} rows are not exactly its assigned tickets in order",
    )
    claims = _check_claims(packet["claims"], tickets)
    _check_witnesses(checked, claims)
    tags = [row["native_tag"] for row in checked if row["native_tag"] is not None]
    assert len(tags) == len(set(tags))
    return {
        "schema": SCHEMA,
        "plan": packet["plan"],
        "worker": worker,
        "claims": claims,
        "rows": checked,
    }


def load_packet(path: Path, plan: Record, expected_digest: str | None = None) -> Record:
    path = _check_path(path, "worker packet")
    if expected_digest is not None:
        as_text(expected_digest, "expected packet digest", DIGEST)
    try:
        record = read_record(path, MAX_PACKET_BYTES)
    except OSError as error:
        raise ValueError(f"worker packet {path} is not a plain file") from error
    require(record is not None, f"worker packet {path} does not exist")
    assert record is not None
    require(
        expected_digest is None or digest(record) == expected_digest,
        f"worker packet {path} digest differs from the expected digest",
    )
    packet = validate_packet(record, plan)
    assert expected_digest is None or digest(packet) == expected_digest
    return packet


def _stamp(info: os.stat_result) -> Stamp:
    return tuple(getattr(info, field) for field in STAMP_FIELDS)


def _relative(trial: int, parts: tuple[str, ...]) -> Stamp:
    assert 1 <= len(parts) <= 4
    opened: list[int] = []
    try:
        for name in parts[:-1]:
            opened.append(
                os.open(name, DIRECTORY, dir_fd=opened[-1] if opened else trial)
            )
        return _stamp(os.lstat(parts[-1], dir_fd=opened[-1] if opened else trial))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreachable"
    finally:
        for descriptor in opened:
            os.close(descriptor)


def _entry(root: int, name: str, where: str) -> tuple[Stamp, list[Stamp]]:
    refusal = f"ticket {name} entry in {where} is not a readable plain directory"
    try:
        info = os.lstat(name, dir_fd=root)
        require(stat.S_ISDIR(info.st_mode), refusal)
        trial = os.open(name, DIRECTORY, dir_fd=root)
    except OSError:
        raise ValueError(refusal) from None
    try:
        opened = os.fstat(trial)
        require(
            (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
            f"ticket {name} entry in {where} changed while it was opened",
        )
        return _stamp(opened), [_relative(trial, parts) for parts in EVIDENCE]
    finally:
        os.close(trial)


def _names(root: int, where: str) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(root) as listing:
            for entry in listing:
                require(
                    len(names) < MAX_ROOT_ENTRIES,
                    f"{where} has over {MAX_ROOT_ENTRIES} entries",
                )
                names.append(entry.name)
    except OSError:
        raise ValueError(f"{where} cannot be listed") from None
    return sorted(names)


def _root(path: Path, where: str, plan_digest: str, worker: int) -> str:
    binding = root_binding(path, plan_digest, worker)
    require(
        binding in ALLOWED_BINDINGS,
        f"{where} is bound to another plan or worker, or its binding is unreadable",
    )
    return binding


def _snapshot(
    roots: list[Path],
    tickets: frozenset[str],
    own: list[str],
    plan_digest: str,
    worker: int,
) -> Snapshot:
    require(1 <= len(roots) <= MAX_ROOTS, f"give 1 to {MAX_ROOTS} worker roots")
    snapshot = Snapshot([], {})
    total = 0
    with ExitStack() as stack:
        for index, path in enumerate(roots):
            where = f"worker root {index}"
            binding = _root(path, where, plan_digest, worker)
            root = open_root(stack, path)
            require(
                binding_state(root, plan_digest, worker) == binding,
                f"{where} binding changed while it was opened",
            )
            info = os.fstat(root)
            identity = (info.st_dev, info.st_ino)
            require(
                all(found[1:] != identity for found in snapshot.roots),
                f"{where} repeats another worker root",
            )
            snapshot.roots.append((binding, *identity))
            names = _names(root, where)
            total += len(names)
            require(total <= MAX_ENTRIES, f"roots hold over {MAX_ENTRIES} entries")
            for name in (name for name in names if name in tickets):
                require(name in own, f"{where} holds ticket {name} of another worker")
                stamp, evidence = _entry(root, name, where)
                entry: Entry = (str(path / name), stamp, evidence)
                snapshot.claims.setdefault(name, []).append(entry)
            require(
                _root(path, where, plan_digest, worker) == binding,
                f"{where} binding changed while it was read",
            )
    assert len(snapshot.claims) <= len(own)
    return snapshot


def _rebind(
    snapshot: Snapshot,
    cells: dict[str, Record],
    registration: Record,
    verifier: ModuleType,
) -> dict[str, Native | None]:
    teams = _registered_teams(registration)
    task = {**registration["task"], "files": registration["task_files"]}
    bound: dict[str, Native | None] = {}
    for ticket, entries in snapshot.claims.items():
        cell = cells[ticket]
        bound[ticket] = None
        if len(entries) != 1:
            continue
        try:
            trial = Path(entries[0][0])
            bound[ticket] = _native(trial, cell, teams[cell["team"]], task, verifier)
        except (Incomplete, Invalid):
            continue
    assert set(bound) == set(snapshot.claims)
    return bound


def _claims(snapshot: Snapshot, verifier: ModuleType, study: str) -> list[list[str]]:
    pairs: set[tuple[str, str]] = set()
    for ticket, entries in snapshot.claims.items():
        for entry in entries:
            found = _identifiers(Path(entry[0]), verifier)
            require(
                len(found) <= len(IDENTIFIER_SOURCES),
                f"ticket {ticket} yields more identifiers than its sources",
            )
            pairs.update((ticket, native_tag(study, value)) for value in found)
    require(len(pairs) <= MAX_CLAIMS, f"identifier claims exceed {MAX_CLAIMS}")
    return [[ticket, tag] for ticket, tag in sorted(pairs)]


def _packet_row(
    row: Record, claims: int, native: Native | None, registration: str
) -> Record:
    ticket, status, grade = row["ticket"], row["status"], row["native_grade"]
    drift = f"ticket {ticket} evidence changed during export; retry when stationary"
    require(grade is None or grade in GRADES, f"ticket {ticket} grade is not binary")
    require((status == "missing") == (claims == 0), drift)
    require(status != "ambiguous" or claims > 1, drift)
    require(claims <= 1 or status in ("ambiguous", "duplicate_id"), drift)
    tag = None
    if status in WITNESSED:
        require(native is not None, drift)
        assert native is not None
        require(native.grade == grade, drift)
        require((native.error is None) == (status == "graded"), drift)
        tag = native_tag(registration, native.native_id)
    elif status in UNWITNESSED:
        require(native is None, drift)
    decision, gate = row["decision"], row["gate"]["status"]
    joined = decision["intervention"] is True
    assert not joined or decision["joined"] is True
    return {
        "ticket": ticket,
        "status": status,
        "grade": grade if status in WITNESSED else None,
        "joined": joined,
        "gate": gate,
        "native_tag": tag,
    }


def _registration(path: Path, plan: Record) -> Record:
    registration = load_registration(path)
    require(
        digest(registration) == plan["registration"],
        "registration differs from the worker plan registration",
    )
    return registration


def capture(
    registration_path: Path, plan: Record, worker: int, roots: list[Path]
) -> Record:
    plan = validate_plan(plan)
    registration = _registration(registration_path, plan)
    expected = make_plan(registration, plan["workers"], plan["layout"])
    require(same(expected, plan), "worker plan is not the exact registration plan")
    own = assigned(plan, worker)
    cells = {cell["ticket"]: cell for cell in registration["cells"]}
    tickets = frozenset(cells)
    verifier = load_verifier()
    plan_digest = digest(plan)
    before = _snapshot(roots, tickets, own, plan_digest, worker)
    report = collect(registration_path, roots)
    bound = _rebind(before, cells, registration, verifier)
    claims = _claims(before, verifier, plan["registration"])
    after = _snapshot(roots, tickets, own, plan_digest, worker)
    require(before == after, "worker roots changed during export; retry later")
    require(
        same(_registration(registration_path, plan), registration),
        "registration changed during export",
    )
    rows = {row["ticket"]: row for row in report["cells"]}
    require(set(rows) == set(tickets), "collection rows differ from the registration")
    packet_rows = [
        _packet_row(
            rows[ticket],
            len(before.claims.get(ticket, [])),
            bound.get(ticket),
            plan["registration"],
        )
        for ticket in own
    ]
    packet = {
        "schema": SCHEMA,
        "plan": plan_digest,
        "worker": worker,
        "claims": claims,
        "rows": packet_rows,
    }
    checked = validate_packet(packet, plan)
    require(
        len(record_bytes(checked)) <= MAX_PACKET_BYTES,
        f"worker packet exceeds {MAX_PACKET_BYTES} bytes; refusing to truncate claims",
    )
    return checked
