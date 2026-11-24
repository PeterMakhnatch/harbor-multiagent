import hashlib
import json
from typing import Any

from .input_evidence import COMMIT, GRADES, MAX_TRIALS, Evidence, Final, JobHeader
from .input_keys import Cell
from .input_witness import HEX, MAX_ROWS, PIN, Witness, _valid
from .native_account import MAX_RETRIES
from .seat_stream import UUID, Record

SCHEMA = "seatlab.workbench.worker-result/v1"
MAX_BYTES = 8 << 20
MAX_CLAIM = (1 << 31) - 1
WIRE = "worker result is not the projected wire object"
KEYS = (
    "schema",
    "scope",
    "job",
    "job_lock",
    "closed",
    "retries",
    "registered",
    "finals",
    "locks_without_result",
    "header",
    "starts",
)
FIELDS = (
    "job",
    "execution",
    "slot",
    "binding",
    "view",
    "retry-policy",
    "harbor-version",
    "harbor-commit",
)

PINS = "job locks are not two distinct sha256:<64 hex> pins"
LOCK_OUTSIDE = "job lock is neither held pin"
EVIDENCE = "evidence is not a native job read"
REGISTERED = "registered inputs are not 1 to 1024 native cells"
FINALS = "finals are not native finals within the registration"
VIEWS = "a native input key names two result views"
HEADER = "native job header is missing or malformed"
STARTS = "start witnesses are not a native START map of at most 8192 rows"
TOO_LARGE = "worker result exceeds 8 MiB"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _pin(value: object) -> bool:
    return type(value) is str and PIN.fullmatch(value) is not None


def _shaped(value: object, pattern: Any, optional: bool) -> bool:
    if value is None:
        return optional
    return type(value) is str and pattern.fullmatch(value) is not None


def _count(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def scope(pins: tuple[str, str]) -> str:
    _require(type(pins) is tuple and len(pins) == 2, PINS)
    left, right = pins
    _require(_pin(left) and _pin(right) and left != right, PINS)
    hasher = hashlib.sha256(f"{SCHEMA}\0scope\0{left}\0{right}".encode("ascii"))
    found = f"sha256:{hasher.hexdigest()}"
    assert PIN.fullmatch(found) is not None
    return found


def tag(field: str, scoped: str, value: str) -> str:
    assert field in FIELDS and _pin(scoped)
    assert type(value) is str and value
    hasher = hashlib.sha256(f"{SCHEMA}\0{field}\0{scoped}\0".encode("ascii"))
    hasher.update(value.encode("ascii"))
    return f"sha256:{hasher.hexdigest()}"


def _optional_tag(field: str, scoped: str, value: str | None) -> str | None:
    return None if value is None else tag(field, scoped, value)


def _cell_shape(cell: object) -> bool:
    return type(cell) is Cell and _pin(cell.key) and _pin(cell.view)


def _final_shape(final: object) -> bool:
    return (
        type(final) is Final
        and _shaped(final.execution, UUID, False)
        and (final.cell is None or _cell_shape(final.cell))
        and type(final.agrees) in (bool, type(None))
        and (final.cell is not None or final.agrees is None)
        and type(final.grade) is str
        and final.grade in GRADES
        and _shaped(final.slot, HEX, True)
        and _shaped(final.binding, PIN, True)
    )


def _header_shape(header: object) -> bool:
    return (
        type(header) is JobHeader
        and _count(header.concurrency, 1, MAX_CLAIM)
        and _count(header.max_retries, 0, MAX_CLAIM)
        and _pin(header.retry_policy)
        and _shaped(header.version, PIN, True)
        and _shaped(header.commit, COMMIT, True)
        and (header.commit is None or header.commit == header.commit.lower())
        and type(header.editable) in (bool, type(None))
    )


def _check(evidence: object, pins: tuple[str, str]) -> Evidence:
    _require(type(evidence) is Evidence, EVIDENCE)
    assert type(evidence) is Evidence
    _require(_shaped(evidence.job, UUID, False), EVIDENCE)
    _require(_pin(evidence.lock_digest), EVIDENCE)
    _require(evidence.lock_digest in pins, LOCK_OUTSIDE)
    _require(type(evidence.closed) in (bool, type(None)), EVIDENCE)
    retries = evidence.retries
    _require(retries is None or _count(retries, 0, MAX_RETRIES), EVIDENCE)
    registered = evidence.registered
    bounded = type(registered) is tuple and 0 < len(registered) <= MAX_TRIALS
    _require(bounded, REGISTERED)
    _require(all(_cell_shape(item) for item in registered), REGISTERED)
    finals, pending = evidence.finals, evidence.lock_without_result
    _require(type(finals) is tuple and _count(pending, 0, len(registered)), FINALS)
    _require(len(finals) + pending <= len(registered), FINALS)
    _require(all(_final_shape(final) for final in finals), FINALS)
    executions = {final.execution for final in finals}
    _require(len(executions) == len(finals), FINALS)
    _require(_header_shape(evidence.header), HEADER)
    return evidence


def _views(evidence: Evidence) -> None:
    views: dict[str, str] = {}
    for cell in evidence.registered:
        _require(views.setdefault(cell.key, cell.view) == cell.view, VIEWS)
    for final in evidence.finals:
        cell = final.cell
        if cell is not None:
            _require(views.setdefault(cell.key, cell.view) == cell.view, VIEWS)


def _starts(starts: dict[str, Witness] | None) -> list[Witness] | None:
    if starts is None:
        return None
    _require(type(starts) is dict and len(starts) <= MAX_ROWS, STARTS)
    for trial, witness in starts.items():
        _require(
            type(witness) is Witness and _valid(witness) and trial == witness.trial,
            STARTS,
        )
    return list(starts.values())


def _header(header: JobHeader, scoped: str) -> list[Any]:
    return [
        header.concurrency,
        header.max_retries,
        tag("retry-policy", scoped, header.retry_policy),
        _optional_tag("harbor-version", scoped, header.version),
        _optional_tag("harbor-commit", scoped, header.commit),
        header.editable,
    ]


def _cell(cell: Cell, scoped: str) -> list[str]:
    return [cell.key, tag("view", scoped, cell.view)]


def _final(final: Final, scoped: str) -> list[Any]:
    return [
        tag("execution", scoped, final.execution),
        None if final.cell is None else _cell(final.cell, scoped),
        final.agrees,
        final.grade,
        _optional_tag("slot", scoped, final.slot),
        _optional_tag("binding", scoped, final.binding),
    ]


def _witness(witness: Witness, scoped: str) -> list[str]:
    return [
        tag("job", scoped, witness.job),
        tag("slot", scoped, witness.slot),
        tag("execution", scoped, witness.trial),
        witness.input,
        tag("binding", scoped, witness.binding),
    ]


def project(
    evidence: Evidence, starts: dict[str, Witness] | None, pins: tuple[str, str]
) -> Record:
    scoped = scope(pins)
    native = _check(evidence, pins)
    _views(native)
    witnesses = _starts(starts)
    assert native.header is not None
    registered = sorted(_cell(item, scoped) for item in native.registered)
    finals = sorted(
        (_final(final, scoped) for final in native.finals), key=lambda row: row[0]
    )
    found = None
    if witnesses is not None:
        found = sorted((_witness(w, scoped) for w in witnesses), key=lambda row: row[2])
    wire = {
        "schema": SCHEMA,
        "scope": scoped,
        "job": tag("job", scoped, native.job),
        "job_lock": native.lock_digest,
        "closed": native.closed,
        "retries": native.retries,
        "registered": registered,
        "finals": finals,
        "locks_without_result": native.lock_without_result,
        "header": _header(native.header, scoped),
        "starts": found,
    }
    assert tuple(wire) == KEYS and len(registered) == len(native.registered)
    assert len(finals) == len(native.finals)
    assert found is None or len({row[2] for row in found}) == len(found)
    return wire


def dump(wire: Record) -> bytes:
    _require(type(wire) is dict and tuple(wire) == KEYS, WIRE)
    data = json.dumps(
        wire,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    _require(len(data) <= MAX_BYTES, TOO_LARGE)
    return data
