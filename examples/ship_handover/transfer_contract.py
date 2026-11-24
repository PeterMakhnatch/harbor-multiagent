from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..native_study import DIGEST, as_choice, as_list, as_record, as_text, require
from ..seat_handoffs.native_records import read_record, record_digest
from ..ship_inflight.materialize import BASE as STOCK_TASK
from .arm_inputs import ROLE_PACKAGE_DIGEST
from .collect import INFLIGHT_STATUSES
from .study import (
    CHANNELS,
    INFLIGHT,
    INFLIGHT_ROLE_DIGEST,
    MAX_CELLS,
    ROLES,
    SPECS,
    STOCK,
    Spec,
    load_registration,
    spec_for,
    validate_registration,
)

PACKET_SCHEMA = "harbor.shipping.returns-packet/v1"
PACKET_FIELDS = frozenset({"schema", "registration_sha256", "rows"})
ROW_FIELDS = frozenset({"ticket", "status", "world_grade", "grade_source"})
DIGEST_RULE = (
    "sha256 of json.dumps(parsed registration, sort_keys=True, "
    "separators=(',', ':'), ensure_ascii=True, allow_nan=False) encoded ascii, "
    "no trailing newline; names content only, not a credential or signature"
)
OMITTED = "no_result"
GRADED = ("zero", "positive")
GRADE_OF = {"zero": 0, "positive": 1}
GRADE_SOURCES = ("native_agreed", "world_only")
NATIVE_AGREED = "native_agreed"
OPTIONALLY_GRADED = ("unresolved", "native_error")
RETURNING_STATUSES = tuple(status for status in INFLIGHT_STATUSES if status != OMITTED)
PACKET_STATUSES = {spec.world: RETURNING_STATUSES for spec in SPECS.values()}
MAX_STRATUM = 512
MAX_PACKET_BYTES = 1 << 20
assert set(PACKET_STATUSES) == set(SPECS)
assert all(OMITTED not in found for found in PACKET_STATUSES.values())
assert set(GRADED) | set(OPTIONALLY_GRADED) <= set(RETURNING_STATUSES)
assert set(GRADE_OF) == set(GRADED) and sorted(GRADE_OF.values()) == [0, 1]
assert NATIVE_AGREED in GRADE_SOURCES
assert all(
    len(spec.states) * len(CHANNELS) * MAX_STRATUM == MAX_CELLS
    for spec in SPECS.values()
)
assert STOCK_TASK["name"] == STOCK.task_name
STOCK_ROLE_TASK = {**STOCK_TASK, "digest": ROLE_PACKAGE_DIGEST}
STOCK_TASKS = (STOCK_TASK, STOCK_ROLE_TASK)
assert all(set(task) == {"name", "digest", "checksum"} for task in STOCK_TASKS)
assert STOCK_ROLE_TASK["name"] == STOCK_TASK["name"]
assert STOCK_ROLE_TASK["checksum"] == STOCK_TASK["checksum"]
assert STOCK_ROLE_TASK["digest"] != STOCK_TASK["digest"]


def _guarded(
    path: Path, label: str, load: Callable[[Path], dict[str, Any] | None]
) -> dict[str, Any]:
    require(isinstance(path, Path), f"{label} path must be a Path")
    try:
        record = load(path)
    except OSError as error:
        raise ValueError(
            f"{label} {path} is not a readable regular non-symlink file "
            f"({error.strerror})"
        ) from None
    except ValueError as error:
        raise ValueError(f"{label} {path}: {error}") from None
    require(record is not None, f"{label} {path} does not exist")
    assert isinstance(record, dict)
    return record


def _packet_record(path: Path) -> dict[str, Any] | None:
    return read_record(path, MAX_PACKET_BYTES)


def registration_digest(reg: dict[str, Any]) -> str:
    require(isinstance(reg, dict), "registration is not a JSON object")
    digest = "sha256:" + record_digest(reg)
    return as_text(digest, "registration digest", DIGEST)


def team_label(reg: dict[str, Any]) -> str:
    teams = as_record(reg.get("teams"), "registration teams")
    require(len(teams) == 1, "transfer registration must name exactly one team")
    return next(iter(teams))


def _world(reg: dict[str, Any]) -> Spec:
    spec = spec_for(validate_registration(reg))
    if spec.world == STOCK.world:
        require(
            reg["task"] in STOCK_TASKS,
            f"stock registration does not bind the pinned {STOCK.task_name} fixture",
        )
    team_label(reg)
    return spec


def bindings(reg: dict[str, Any]) -> dict[str, dict[str, str]]:
    spec = _world(reg)
    bound: dict[str, dict[str, str]] = {}
    for cell in reg["cells"]:
        assert cell["ticket"] not in bound
        bound[cell["ticket"]] = {
            "world": spec.world,
            "state": cell[spec.axis],
            "channel": cell["channel"],
        }
    assert 0 < len(bound) <= MAX_CELLS
    return bound


def _stratum_size(reg: dict[str, Any], spec: Spec) -> int:
    counts = Counter((cell[spec.axis], cell["channel"]) for cell in reg["cells"])
    strata = [(state, channel) for state in spec.states for channel in CHANNELS]
    assert set(counts) <= set(strata)
    sizes = {counts[stratum] for stratum in strata}
    require(
        len(sizes) == 1,
        f"{spec.world} registration has unequal {spec.axis} x channel counts",
    )
    size = sizes.pop()
    require(
        1 <= size <= MAX_STRATUM,
        f"{spec.world} registration needs 1..{MAX_STRATUM} cells per stratum",
    )
    assert size * len(strata) == len(reg["cells"])
    return size


def _roster(reg: dict[str, Any]) -> dict[str, str]:
    members = reg["teams"][team_label(reg)]
    by_seat = {member["seat"]: record_digest(member) for member in members}
    require(set(by_seat) == set(ROLES), "transfer team needs one member per seat")
    assert len(by_seat) == len(members) == len(ROLES)
    return by_seat


def check_pair(stock: dict[str, Any], inflight: dict[str, Any]) -> None:
    require(_world(stock) is STOCK, f"stock registration must be {STOCK.schema}")
    require(
        _world(inflight) is INFLIGHT,
        f"inflight registration must be {INFLIGHT.schema}",
    )
    stock_role = stock["task"]["digest"] == ROLE_PACKAGE_DIGEST
    inflight_role = inflight["task"]["digest"] == INFLIGHT_ROLE_DIGEST
    require(
        stock_role == inflight_role,
        "stock and inflight registrations bind different task package generations",
    )
    require(
        team_label(stock) == team_label(inflight),
        "stock and inflight registrations name different teams",
    )
    require(
        _roster(stock) == _roster(inflight),
        "stock and inflight teams differ in a seat configuration",
    )
    require(
        _stratum_size(stock, STOCK) == _stratum_size(inflight, INFLIGHT),
        "stock and inflight registrations have different stratum sizes",
    )
    stock_tickets = {cell["ticket"] for cell in stock["cells"]}
    inflight_tickets = {cell["ticket"] for cell in inflight["cells"]}
    require(
        not stock_tickets & inflight_tickets,
        "stock and inflight registrations share a ticket",
    )


def load_pair(stock: Path, inflight: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    pair = (
        _guarded(stock, "stock registration", load_registration),
        _guarded(inflight, "inflight registration", load_registration),
    )
    check_pair(*pair)
    return pair


def _packet_shape(packet: object) -> dict[str, Any]:
    record = as_record(packet, "returns packet", PACKET_FIELDS)
    require(
        record["schema"] == PACKET_SCHEMA,
        f"returns packet schema must be {PACKET_SCHEMA}",
    )
    as_text(record["registration_sha256"], "returns packet registration_sha256", DIGEST)
    as_list(record["rows"], "returns packet rows", 0, MAX_CELLS)
    return record


def read_packet(path: Path) -> dict[str, Any]:
    return _packet_shape(_guarded(path, "returns packet", _packet_record))


def _grade(row: dict[str, Any], where: str) -> tuple[int | None, str | None]:
    grade, source = row["world_grade"], row["grade_source"]
    require(
        (grade is None) == (source is None),
        f"{where} world_grade and grade_source must both be null or both set",
    )
    if grade is None:
        return None, None
    require(
        type(grade) is int and grade in (0, 1),
        f"{where} world_grade is not the integer 0 or 1",
    )
    return grade, as_choice(source, f"{where} grade_source", GRADE_SOURCES)


def _row(item: object, where: str, spec: Spec) -> dict[str, Any]:
    row = as_record(item, where, ROW_FIELDS)
    status = as_choice(row["status"], f"{where} status", PACKET_STATUSES[spec.world])
    grade, source = _grade(row, where)
    if status in GRADED:
        require(
            grade == GRADE_OF[status] and source == NATIVE_AGREED,
            f"{where} {status} needs world_grade {GRADE_OF[status]} "
            f"and grade_source {NATIVE_AGREED}",
        )
    elif status not in OPTIONALLY_GRADED:
        require(grade is None, f"{where} {status} must carry null grade and source")
    return {
        "ticket": row["ticket"],
        "status": status,
        "world_grade": grade,
        "grade_source": source,
    }


def check_packet(reg: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    bound = bindings(reg)
    spec = spec_for(reg)
    _packet_shape(packet)
    require(
        packet["registration_sha256"] == registration_digest(reg),
        "returns packet names a different registration",
    )
    order = {ticket: position for position, ticket in enumerate(bound)}
    items = as_list(packet["rows"], "returns packet rows", 0, len(bound))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    last = -1
    for position, item in enumerate(items):
        where = f"returns packet row {position}"
        found = as_record(item, where).get("ticket")
        ticket = as_text(found, f"{where} ticket", spec.ticket)
        require(ticket in order, f"{where} ticket {ticket} is not registered")
        require(ticket not in seen, f"{where} repeats ticket {ticket}")
        require(order[ticket] > last, f"{where} is out of registration order")
        seen.add(ticket)
        last = order[ticket]
        rows.append(_row(item, where, spec))
    assert len(rows) == len(seen) <= len(bound)
    return rows
