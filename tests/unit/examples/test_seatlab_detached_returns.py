import hashlib
import json
from collections.abc import Callable
from typing import Any, NamedTuple
from uuid import NAMESPACE_URL, uuid5

import pytest

from examples.seatlab.workbench import paired_cohort, return_decode, return_encode
from examples.seatlab.workbench.input_evidence import Evidence, Final, JobHeader
from examples.seatlab.workbench.input_keys import Cell
from examples.seatlab.workbench.input_witness import Witness
from examples.seatlab.workbench.seat_stream import UUID

Json = dict[str, Any]
Starts = dict[str, Witness] | None
SIDES = ("left", "right")


def pin(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


class Identity(NamedTuple):
    job: str
    lock: str
    key: str
    view: str


IDENTITY = {
    side: Identity(
        str(uuid5(NAMESPACE_URL, f"job/{side}")),
        pin(f"lock/{side}"),
        pin(f"key/{side}"),
        pin(f"view/{side}"),
    )
    for side in SIDES
}
PINS = (IDENTITY["left"].lock, IDENTITY["right"].lock)
KEYS = (IDENTITY["left"].key, IDENTITY["right"].key)
OTHER_LOCK = pin("lock/other")
OTHER_KEY = pin("key/other")
SHARED_VIEW = pin("view/shared")
COPIES = 3
SCHEDULE = (2, 0)
GRADED = ("one", "zero", "one")
HEADER = JobHeader(
    concurrency=2,
    max_retries=0,
    retry_policy=pin("retry-policy"),
    version=pin("harbor-version"),
    commit=hashlib.sha1(b"harbor-commit").hexdigest(),
    editable=False,
)


def final(job: str, cell: Cell, index: int, grade: str, *, bound: bool) -> Final:
    return Final(
        execution=str(uuid5(NAMESPACE_URL, f"{job}/trial/{index}")),
        cell=cell,
        agrees=True,
        grade=grade,
        slot=hashlib.sha256(f"{job}/slot/{index}".encode("ascii")).hexdigest()
        if bound
        else None,
        binding=pin(f"{job}/binding/{index}") if bound else None,
    )


def arm(
    side: str,
    *,
    view: str | None = None,
    grades: tuple[str, ...] = GRADED,
    bound: bool = False,
    closed: bool | None = True,
    pending: int = 0,
    header: JobHeader = HEADER,
) -> Evidence:
    held = IDENTITY[side]
    cell = Cell(held.key, held.view if view is None else view)
    return Evidence(
        job=held.job,
        registered=(cell,) * COPIES,
        closed=closed,
        retries=0,
        lock_digest=held.lock,
        finals=tuple(
            final(held.job, cell, index, grade, bound=bound)
            for index, grade in enumerate(grades)
        ),
        lock_without_result=pending,
        header=header,
    )


def witness(evidence: Evidence, found: Final) -> Witness:
    assert (
        found.cell is not None and found.slot is not None and found.binding is not None
    )
    return Witness(
        evidence.job, found.slot, found.execution, found.cell.key, found.binding
    )


def starts(evidence: Evidence, skipped: int = 0) -> dict[str, Witness]:
    return {f.execution: witness(evidence, f) for f in evidence.finals[skipped:]}


def wire(evidence: Evidence, found: Starts, pins: tuple[str, str] = PINS) -> Json:
    return return_encode.project(evidence, found, pins)


def private(pair: tuple[Evidence, Evidence], found: tuple[Starts, Starts]) -> set[str]:
    values: set[str] = set()
    for evidence in pair:
        assert evidence.header is not None
        header = evidence.header
        values.add(evidence.job)
        values.update(
            v for v in (header.retry_policy, header.version, header.commit) if v
        )
        values.update(item.view for item in evidence.registered)
        for f in evidence.finals:
            values.update(v for v in (f.execution, f.slot, f.binding) if v)
            values.update(() if f.cell is None else (f.cell.view,))
    for side in found:
        for w in (side or {}).values():
            values.update((w.job, w.slot, w.trial, w.binding))
    return values


def anonymous(report: Json) -> Json:
    arms = {
        side: {name: value for name, value in body.items() if name != "job"}
        for side, body in report["arms"].items()
    }
    return {**report, "arms": arms}


def compare(
    pair: tuple[Evidence, Evidence],
    found: tuple[Starts, Starts] = (None, None),
    *,
    keys: tuple[str, str] = KEYS,
    schedule: tuple[int, int] = SCHEDULE,
    require_starts: bool = False,
) -> Json:
    left, right = found
    supplied = None if left is None or right is None else (left, right)
    assert (left is None) == (right is None)
    direct = paired_cohort.combine(pair, keys, COPIES, supplied, schedule=schedule)
    wires = [
        return_encode.dump(wire(evidence, side))
        for evidence, side in zip(pair, found, strict=True)
    ]
    outer = return_decode.check(
        wires[0],
        wires[1],
        pins=PINS,
        keys=keys,
        copies=COPIES,
        schedule=schedule,
        require_starts=require_starts,
    )
    comparison = outer["comparison"]
    assert outer == {
        "schema": "seatlab.workbench.worker-result-check/v1",
        "scope": return_encode.scope(PINS),
        "method": "worker-claims",
        "comparison": comparison,
        "caveat": return_decode.CAVEAT,
    }
    assert anonymous(comparison) == anonymous(direct)
    scoped = return_encode.scope(PINS)
    assert [comparison["arms"][side]["job"] for side in SIDES] == [
        return_encode.tag("job", scoped, evidence.job) for evidence in pair
    ]
    text = b"".join(wires).decode("ascii") + json.dumps(outer)
    assert UUID.search(text) is None
    assert not [value for value in private(pair, found) if value in text]
    return comparison


def test_covered_pair_keeps_a_valid_zero_as_a_grade() -> None:
    comparison = compare((arm("left"), arm("right", grades=("zero", "zero", "one"))))
    assert (comparison["status"], comparison["reasons"]) == ("covered", [])
    assert comparison["arms"]["left"]["grades"]["zero"] == 1
    assert comparison["arms"]["right"]["grades"]["zero"] == 2
    assert [comparison["arms"][side]["input"] for side in SIDES] == list(KEYS)
    assert [comparison["arms"][side]["job_lock"] for side in SIDES] == list(PINS)


def test_wire_bytes_are_canonical_and_independent_of_read_order() -> None:
    native = arm("left", bound=True)
    found = starts(native)
    data = return_encode.dump(wire(native, found))
    shuffled = native._replace(finals=native.finals[::-1])
    assert data == return_encode.dump(wire(shuffled, dict(reversed(found.items()))))
    document = json.loads(data)
    assert data == json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    assert document["job_lock"] == PINS[0]
    assert [row[0] for row in document["registered"]] == [KEYS[0]] * COPIES
    assert [row[3] for row in document["starts"]] == [KEYS[0]] * len(GRADED)
    assert sorted(row[3] for row in document["finals"]) == sorted(GRADED)


def unlocked_final() -> Evidence:
    native = arm("right")
    first = native.finals[0]._replace(cell=None, agrees=None)
    return native._replace(finals=(first, *native.finals[1:]))


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: arm("right", grades=("one", "zero")), "right_finals_missing"),
        (lambda: arm("right", closed=False), "right_job_not_closed"),
        (lambda: arm("right", closed=None), "right_job_not_closed"),
        (
            lambda: arm("right", grades=("zero",), pending=2),
            "right_locks_without_result",
        ),
        (unlocked_final, "right_finals_without_lock"),
    ],
    ids=[
        "missing-final",
        "open-job",
        "unknown-state",
        "lock-without-result",
        "final-without-lock",
    ],
)
def test_partial_arm_stays_incomplete_after_the_return(
    build: Callable[[], Evidence], reason: str
) -> None:
    comparison = compare((arm("left"), build()))
    assert comparison["status"] == "incomplete"
    assert reason in comparison["reasons"]


@pytest.mark.parametrize(
    ("witnessed", "skipped", "status", "reason"),
    [
        (False, 0, "incomplete", paired_cohort.SHARED_VIEW),
        (True, 0, "covered", None),
        (True, 1, "incomplete", "left_finals_unattributed"),
    ],
    ids=["no-starts", "matched-starts", "missing-start"],
)
def test_same_view_under_two_keys_needs_a_matching_start(
    witnessed: bool, skipped: int, status: str, reason: str | None
) -> None:
    pair = (
        arm("left", view=SHARED_VIEW, bound=witnessed),
        arm("right", view=SHARED_VIEW, bound=witnessed),
    )
    found = (starts(pair[0], skipped), starts(pair[1])) if witnessed else (None, None)
    comparison = compare(pair, found, require_starts=witnessed)
    assert comparison["status"] == status
    assert (reason is None) == (comparison["reasons"] == [])
    assert reason is None or reason in comparison["reasons"]
    assert all(comparison["arms"][side]["shared_result_view"] for side in SIDES)


def shared_final() -> tuple[tuple[Evidence, Evidence], tuple[Starts, Starts]]:
    left, right = arm("left"), arm("right")
    reused = right.finals[0]._replace(execution=left.finals[0].execution)
    return (left, right._replace(finals=(reused, *right.finals[1:]))), (None, None)


def start_without_final() -> tuple[tuple[Evidence, Evidence], tuple[Starts, Starts]]:
    left, right = arm("left", bound=True), arm("right", bound=True)
    stray = Witness(
        right.job,
        hashlib.sha256(b"right/slot/stray").hexdigest(),
        left.finals[0].execution,
        KEYS[1],
        pin("right/binding/stray"),
    )
    return (left, right), (starts(left), {**starts(right), stray.trial: stray})


@pytest.mark.parametrize(
    "build", [shared_final, start_without_final], ids=["final", "start"]
)
def test_execution_shared_across_jobs_withholds_both_arms(
    build: Callable[[], tuple[tuple[Evidence, Evidence], tuple[Starts, Starts]]],
) -> None:
    pair, found = build()
    comparison = compare(pair, found, require_starts=found[0] is not None)
    assert comparison["status"] == "inconsistent"
    assert paired_cohort.SHARED_EXECUTION in comparison["reasons"]
    assert all(comparison["arms"][side]["grades"] is None for side in SIDES)


@pytest.mark.parametrize(
    ("header", "schedule", "status", "reasons"),
    [
        (
            HEADER,
            (3, 0),
            "inconsistent",
            {"left_concurrency_not_scheduled", "right_concurrency_not_scheduled"},
        ),
        (
            HEADER,
            (2, 1),
            "inconsistent",
            {"left_max_retries_not_scheduled", "right_max_retries_not_scheduled"},
        ),
        (
            HEADER._replace(concurrency=3),
            SCHEDULE,
            "inconsistent",
            {"concurrency_differs", "right_concurrency_not_scheduled"},
        ),
        (
            HEADER._replace(version=pin("harbor-version/other")),
            SCHEDULE,
            "inconsistent",
            {"harbor_version_differs"},
        ),
        (HEADER._replace(commit=None), SCHEDULE, "covered", set()),
    ],
    ids=[
        "joint-concurrency",
        "joint-retries",
        "one-concurrency",
        "other-version",
        "unrecorded-commit",
    ],
)
def test_registration_and_schedule_drift_survive_pseudonyms(
    header: JobHeader, schedule: tuple[int, int], status: str, reasons: set[str]
) -> None:
    comparison = compare((arm("left"), arm("right", header=header)), schedule=schedule)
    assert (comparison["status"], set(comparison["reasons"])) == (status, reasons)
    fields = comparison["registration"]["fields"]
    assert fields["harbor_commit"] == (
        "unrecorded" if header.commit is None else "same"
    )


@pytest.mark.parametrize(
    ("keys", "reason"),
    [
        ((KEYS[0], OTHER_KEY), "right_registers_other_input"),
        ((KEYS[1], KEYS[0]), "left_registers_other_input"),
        ((KEYS[0], KEYS[0]), paired_cohort.SAME_KEY),
        ((KEYS[0], "sha256:" + KEYS[1][7:].upper()), paired_cohort.KEY_SHAPE),
    ],
    ids=["other-key", "swapped-keys", "same-key", "uppercase-key"],
)
def test_held_input_keys_are_still_checked(keys: tuple[str, str], reason: str) -> None:
    comparison = compare((arm("left"), arm("right")), keys=keys)
    assert comparison["status"] == "inconsistent"
    assert reason in comparison["reasons"]
    assert all(comparison["arms"][side]["grades"] is None for side in SIDES)


def checked(
    left: object, right: object, pins: tuple[str, str] = PINS, **options: Any
) -> Json:
    return return_decode.check(
        left, right, pins=pins, keys=KEYS, copies=COPIES, schedule=SCHEDULE, **options
    )


def test_held_pins_and_sides_bind_each_returned_job() -> None:
    left, right = wire(arm("left"), None), wire(arm("right"), None)
    for sides, pins in (
        ((right, left), PINS),
        ((left, right), (PINS[1], PINS[0])),
        ((left, right), (PINS[0], OTHER_LOCK)),
        ((left, right), (PINS[0], PINS[0])),
    ):
        with pytest.raises(ValueError):
            checked(*sides, pins)
    with pytest.raises(ValueError, match=return_encode.LOCK_OUTSIDE):
        return_encode.project(arm("left"), None, (OTHER_LOCK, PINS[1]))
    with pytest.raises(ValueError):
        return_decode.unpack(left, PINS, "right")
    evidence, found = return_decode.unpack(left, PINS, "left")
    assert found is None and evidence.lock_digest == PINS[0]
    assert {item.key for item in evidence.registered} == {KEYS[0]}


def test_scope_is_bound_to_the_ordered_pin_pair() -> None:
    pairs = (PINS, (PINS[1], PINS[0]), (PINS[0], OTHER_LOCK))
    assert len({return_encode.scope(pins) for pins in pairs}) == len(pairs)
    native = arm("left", bound=True)
    here = wire(native, starts(native))
    there = wire(native, starts(native), (PINS[0], OTHER_LOCK))
    assert here["job_lock"] == there["job_lock"] == PINS[0]
    assert here["registered"][0][0] == there["registered"][0][0] == KEYS[0]
    assert here["scope"] != there["scope"] and here["job"] != there["job"]
    assert here["registered"][0][1] != there["registered"][0][1]
    assert {row[0] for row in here["finals"]}.isdisjoint(
        row[0] for row in there["finals"]
    )
    other = arm("right", bound=True)
    with pytest.raises(ValueError):
        checked(there, wire(other, starts(other)), require_starts=True)


def test_require_starts_cannot_be_met_by_dropping_an_archive() -> None:
    left, right = arm("left", bound=True), arm("right", bound=True)
    full = (wire(left, starts(left)), wire(right, starts(right)))
    bare = (wire(left, None), wire(right, None))
    for sides, required in (
        (bare, True),
        ((full[0], bare[1]), True),
        ((bare[0], full[1]), True),
        ((full[0], bare[1]), False),
    ):
        with pytest.raises(ValueError):
            checked(*sides, require_starts=required)
    empty = compare((left, right), ({}, {}), require_starts=True)
    assert empty["status"] == "incomplete"
    assert all(empty["arms"][side]["grades"] is None for side in SIDES)
    assert (
        compare((left, right), (starts(left), starts(right)), require_starts=True)[
            "status"
        ]
        == "covered"
    )


def boolean_lock_count(document: Json) -> None:
    document["locks_without_result"] = False


def surplus_lock(document: Json) -> None:
    document["locks_without_result"] = 1


def repeated_execution(document: Json) -> None:
    document["finals"][1][0] = document["finals"][0][0]


def numeric_grade(document: Json) -> None:
    document["finals"][0][3] = 0


def numeric_agreement(document: Json) -> None:
    document["finals"][0][2] = 1


def unlocked_agreement(document: Json) -> None:
    document["finals"][0][1] = None


def uppercase_key(document: Json) -> None:
    key = document["registered"][0][0]
    document["registered"][0][0] = "sha256:" + key[7:].upper()


def added_tally(document: Json) -> None:
    document["grades"] = {"zero": COPIES}


@pytest.mark.parametrize(
    "mutate",
    [
        boolean_lock_count,
        surplus_lock,
        repeated_execution,
        numeric_grade,
        numeric_agreement,
        unlocked_agreement,
        uppercase_key,
        added_tally,
    ],
)
def test_malformed_wire_field_is_refused_not_counted(
    mutate: Callable[[Json], None],
) -> None:
    left = json.loads(return_encode.dump(wire(arm("left"), None)))
    right = wire(arm("right"), None)
    assert checked(left, right)["comparison"]["status"] == "covered"
    mutate(left)
    with pytest.raises(ValueError):
        checked(left, right)
    with pytest.raises(ValueError):
        checked(json.dumps(left).encode("ascii"), right)


def test_duplicate_member_in_raw_bytes_is_refused() -> None:
    data = return_encode.dump(wire(arm("left"), None))
    forged = data.replace(b'"closed":true', b'"closed":false,"closed":true', 1)
    assert forged != data
    with pytest.raises(ValueError):
        checked(forged, wire(arm("right"), None))
