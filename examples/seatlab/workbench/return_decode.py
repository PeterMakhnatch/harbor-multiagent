from typing import Any, cast

from . import paired_cohort
from .input_evidence import GRADES, MAX_TRIALS, PIN, Evidence, Final, JobHeader
from .input_keys import Cell
from .input_witness import MAX_ROWS, Witness
from .native_account import MAX_RETRIES as MAX_REPORTED_RETRIES
from .return_encode import KEYS, MAX_BYTES, MAX_CLAIM, SCHEMA, scope
from .seat_stream import Record, _strict_json

CHECK_SCHEMA = "seatlab.workbench.worker-result-check/v1"
METHOD = "worker-claims"
SIDES = ("left", "right")
FIELDS = frozenset(KEYS)
CAVEAT = (
    "Coordinator check of two unsigned worker result receipts. It trusts what each worker claims: it does not "
    "authenticate a worker, detect a worker that lies, hide inputs that can be guessed, or observe deployed bytes. "
    "Identifiers are pseudonyms scoped to this pair of job-lock pins; only full trial input keys and the held "
    "job-lock pins are public. The comparison is the native paired-cohort report over those claims. It ran 0 jobs "
    "and 0 trials and shows no causal effect."
)

BAD_SIDE = "worker result side is not left or right"
BAD_PINS = "job-lock pins are not two distinct canonical sha256 pins"
BAD_OPTIONS = "worker result check options are outside the profile"
NOT_JSON = "{} worker result is not strict UTF-8 JSON of at most 8 MiB"
NOT_RESULT = "{} worker result is not an exact worker result"
WRONG_SCOPE = "{} worker result scope does not match the held job-lock pins"
WRONG_LOCK = "{} worker result job lock does not match the held {} pin"
BAD_REGISTERED = "{} worker result registrations are not 1 to 1024 key and view tags"
BAD_FINALS = "{} worker result finals are not well-formed native finals"
DUPLICATE_FINAL = "{} worker result holds two finals for one execution"
TWO_VIEWS = "{} worker result gives one input key two result views"
SURPLUS = "{} worker result holds more finals and locks than registered trials"
BAD_HEADER = "{} worker result header is not a native job header"
BAD_STARTS = "{} worker result START witnesses are not well-formed"
DUPLICATE_START = "{} worker result holds two START witnesses for one execution"
STARTS_UNEVEN = "only one worker result holds START witnesses"
STARTS_REQUIRED = "START witnesses are required but absent"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _pin(value: Any) -> bool:
    return type(value) is str and PIN.fullmatch(value) is not None


def _optional_pin(value: Any) -> bool:
    return value is None or _pin(value)


def _count(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _rows(value: Any, width: int, high: int, reason: str) -> list[list[Any]]:
    _require(type(value) is list and len(value) <= high, reason)
    _require(all(type(row) is list and len(row) == width for row in value), reason)
    return value


def _document(raw: object, side: str) -> Record:
    if type(raw) in (bytes, bytearray):
        assert isinstance(raw, (bytes, bytearray))
        _require(len(raw) <= MAX_BYTES, NOT_JSON.format(side))
        try:
            raw = _strict_json(bytes(raw), NOT_JSON.format(side))
        except ValueError:
            raw = None
        _require(raw is not None, NOT_JSON.format(side))
    _require(type(raw) is dict and raw.keys() == FIELDS, NOT_RESULT.format(side))
    return cast(Record, raw)


def _cell(row: Any, views: dict[str, str], side: str, reason: str) -> Cell:
    _require(type(row) is list and len(row) == 2, reason)
    key, view = row
    _require(_pin(key) and _pin(view), reason)
    _require(views.setdefault(key, view) == view, TWO_VIEWS.format(side))
    return Cell(key=key, view=view)


def _final(row: list[Any], views: dict[str, str], side: str) -> Final:
    reason = BAD_FINALS.format(side)
    execution, saved, agrees, grade, slot, binding = row
    _require(_pin(execution), reason)
    found = None if saved is None else _cell(saved, views, side, reason)
    _require(agrees is None or type(agrees) is bool, reason)
    _require(found is not None or agrees is None, reason)
    _require(type(grade) is str and grade in GRADES, reason)
    _require(_optional_pin(slot) and _optional_pin(binding), reason)
    return Final(execution, found, agrees, grade, slot, binding)


def _evidence_rows(
    document: Record, side: str
) -> tuple[tuple[Cell, ...], tuple[Final, ...]]:
    views: dict[str, str] = {}
    reason = BAD_REGISTERED.format(side)
    rows = _rows(document["registered"], 2, MAX_TRIALS, reason)
    _require(len(rows) > 0, reason)
    registered = tuple(_cell(row, views, side, reason) for row in rows)
    finals = [
        _final(row, views, side)
        for row in _rows(document["finals"], 6, MAX_TRIALS, BAD_FINALS.format(side))
    ]
    executions = {final.execution for final in finals}
    _require(len(executions) == len(finals), DUPLICATE_FINAL.format(side))
    return registered, tuple(sorted(finals, key=lambda final: final.execution))


def _header(value: Any, side: str) -> JobHeader:
    reason = BAD_HEADER.format(side)
    _require(type(value) is list and len(value) == 6, reason)
    concurrency, retries, policy, version, commit, editable = value
    _require(_count(concurrency, 1, MAX_CLAIM), reason)
    _require(_count(retries, 0, MAX_CLAIM), reason)
    _require(_pin(policy) and _optional_pin(version) and _optional_pin(commit), reason)
    _require(editable is None or type(editable) is bool, reason)
    return JobHeader(concurrency, retries, policy, version, commit, editable)


def _starts(value: Any, side: str) -> dict[str, Witness] | None:
    if value is None:
        return None
    reason = BAD_STARTS.format(side)
    found: dict[str, Witness] = {}
    for row in _rows(value, 5, MAX_ROWS, reason):
        _require(all(_pin(part) for part in row), reason)
        witness = Witness(
            job=row[0], slot=row[1], trial=row[2], input=row[3], binding=row[4]
        )
        _require(witness.trial not in found, DUPLICATE_START.format(side))
        found[witness.trial] = witness
    return found


def unpack(
    raw: object, pins: tuple[str, str], side: str
) -> tuple[Evidence, dict[str, Witness] | None]:
    _require(type(side) is str and side in SIDES, BAD_SIDE)
    _require(type(pins) is tuple and len(pins) == 2, BAD_PINS)
    _require(all(_pin(pin) for pin in pins) and pins[0] != pins[1], BAD_PINS)
    expected = scope(pins)
    document = _document(raw, side)
    _require(document["schema"] == SCHEMA, NOT_RESULT.format(side))
    _require(document["scope"] == expected, WRONG_SCOPE.format(side))
    held = pins[SIDES.index(side)]
    _require(document["job_lock"] == held, WRONG_LOCK.format(side, side))
    closed, retries = document["closed"], document["retries"]
    pending = document["locks_without_result"]
    _require(_pin(document["job"]), NOT_RESULT.format(side))
    _require(closed is None or type(closed) is bool, NOT_RESULT.format(side))
    _require(
        retries is None or _count(retries, 0, MAX_REPORTED_RETRIES),
        NOT_RESULT.format(side),
    )
    _require(_count(pending, 0, MAX_TRIALS), NOT_RESULT.format(side))
    registered, finals = _evidence_rows(document, side)
    _require(len(finals) + pending <= len(registered), SURPLUS.format(side))
    evidence = Evidence(
        job=document["job"],
        registered=registered,
        closed=closed,
        retries=retries,
        lock_digest=held,
        finals=finals,
        lock_without_result=pending,
        header=_header(document["header"], side),
    )
    return evidence, _starts(document["starts"], side)


def check(
    left_raw: object,
    right_raw: object,
    *,
    pins: tuple[str, str],
    keys: tuple[str, str],
    copies: int,
    schedule: tuple[int, int],
    require_starts: bool = False,
) -> Record:
    _require(type(keys) is tuple and len(keys) == 2, BAD_OPTIONS)
    _require(all(type(key) is str for key in keys), BAD_OPTIONS)
    _require(type(copies) is int and type(require_starts) is bool, BAD_OPTIONS)
    _require(type(schedule) is tuple and len(schedule) == 2, BAD_OPTIONS)
    left, left_starts = unpack(left_raw, pins, "left")
    right, right_starts = unpack(right_raw, pins, "right")
    _require((left_starts is None) == (right_starts is None), STARTS_UNEVEN)
    _require(left_starts is not None or not require_starts, STARTS_REQUIRED)
    starts = None
    if left_starts is not None and right_starts is not None:
        starts = (left_starts, right_starts)
    comparison = paired_cohort.combine(
        (left, right), keys, copies, starts, schedule=schedule
    )
    return {
        "schema": CHECK_SCHEMA,
        "scope": scope(pins),
        "method": METHOD,
        "comparison": comparison,
        "caveat": CAVEAT,
    }
