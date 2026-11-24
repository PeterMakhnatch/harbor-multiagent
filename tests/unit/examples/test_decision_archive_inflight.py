import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import archive_inflight
from examples.ship_inflight import inflight_verify
from examples.ship_inflight.replay import EXPECTED as GRADED
from examples.ship_inflight.replay import PLACEHOLDER_RUN

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "examples" / "ship_inflight" / "samples"
PACK_BYTES = (SAMPLES / "public-cuts.json").read_bytes()
PUBLIC = json.loads(PACK_BYTES.decode("utf-8"))
QUESTION = json.loads((SAMPLES / "question.json").read_text(encoding="utf-8"))
KEYS = ["world", "axis_state", "decision", "inbox", "citation", "reward", "status"]
BLIND = "si-8b457c4e64fa243d"
SETTLES_BLIND = "si-c7aa9cf61cbbb83e"
REPLACE = "si-1c1135b3a2a365be"
DRAFT_REPLACE = "si-d6a136ca7e4bf8d1"
DRAFT_SETTLED = "si-24618ead1522e201"
IDLE = "si-42d32f515443a2d3"
SETTLES_IDLE = "si-93b4d29020345eb5"
WITHHELD = "si-3c710acdaa429092"
JOIN_ORIGINAL = "si-56eb464e42a3b592"
TABLE = [
    (BLIND, "fails", "accepted", "absent", 1),
    (SETTLES_BLIND, "settles", "accepted", "absent", 0),
    (REPLACE, "settles", "accepted", "original", 1),
    (DRAFT_REPLACE, "settles", "accepted", "draft", 0),
    (DRAFT_SETTLED, "fails", "accepted", "draft", 1),
    (IDLE, "fails", "absent", "unrecorded", 0),
    (WITHHELD, "settles", "accepted", "empty", 1),
    ("si-8d37d0777f34c365", "fails", "accepted", "original", 1),
    ("si-7570d87c985c30ea", "fails", "accepted", "empty", 0),
    ("si-16628251379828d3", "fails", "accepted", "empty", 0),
    (SETTLES_IDLE, "settles", "absent", "unrecorded", 1),
    ("si-58eb44feac2e10ee", "settles", "accepted", "draft", 0),
    (JOIN_ORIGINAL, "fails", "accepted", "original", 0),
    ("si-5b7b9dbc720d7132", "settles", "accepted", "original", 1),
    ("si-3c1dd43fb19073e1", "fails", "accepted", "draft", 1),
    ("si-a05885b6627797a5", "settles", "accepted", "empty", 1),
]
TICKETS = [entry[0] for entry in TABLE]
Json = dict[str, Any]
Edit = Callable[[Json, dict[str, Json]], None]


def observed(fate: str, decision: str, inbox: str, reward: int) -> Json:
    return {
        "world": "inflight",
        "axis_state": fate,
        "decision": decision,
        "inbox": inbox,
        "citation": "unrecorded",
        "reward": reward,
        "status": "observed",
    }


def missing(fate: str) -> Json:
    return {
        "world": "inflight",
        "axis_state": fate,
        "decision": "unrecorded",
        "inbox": "unrecorded",
        "citation": "unrecorded",
        "reward": None,
        "status": "missing",
    }


EXPECTED = [observed(*entry[1:]) for entry in TABLE]


def project(edit: Edit) -> list[Json]:
    data = copy.deepcopy(PUBLIC)
    edit(data, {row["ticket"]: row for row in data["rows"]})
    return archive_inflight._project(json.dumps(data).encode("utf-8"))


def at(ticket: str) -> int:
    return TICKETS.index(ticket)


def tally(rows: list[Json]) -> tuple[int, int, int]:
    decided = [row for row in rows if row["decision"] == "accepted"]
    returned = sum(row["inbox"] in ("original", "draft") for row in decided)
    undecided = sum(row["decision"] == "absent" for row in rows)
    return returned, len(decided) - returned, undecided


def test_bundled_release_covers_all_sixteen() -> None:
    assert TICKETS == [case["ticket"] for case in QUESTION["cases"]]
    assert TICKETS == [row["ticket"] for row in PUBLIC["rows"]]
    rows: list[Json] = archive_inflight.collect()
    assert rows == EXPECTED and len(rows) == 16
    assert all(list(row) == KEYS for row in rows)
    assert tally(rows) == (8, 6, 2)
    assert {row["citation"] for row in rows} == {"unrecorded"}
    assert {row["status"] for row in rows} == {"observed"}
    assert sum(row["reward"] for row in rows) == 9
    inboxes = [row["inbox"] for row in rows]
    names = ("absent", "empty", "original", "draft", "unrecorded")
    assert [inboxes.count(name) for name in names] == [2, 4, 4, 4, 2]


def test_collect_binds_the_frozen_release_bytes() -> None:
    digest = "sha256:" + hashlib.sha256(PACK_BYTES).hexdigest()
    assert archive_inflight.PACK_SHA256 == digest
    assert archive_inflight.PACK == SAMPLES / "public-cuts.json"


def test_changed_release_bytes_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_content = json.dumps(PUBLIC).encode("utf-8")
    assert same_content != PACK_BYTES
    assert archive_inflight._project(same_content) == EXPECTED
    path = tmp_path / "public-cuts.json"
    path.write_bytes(same_content)
    monkeypatch.setattr(archive_inflight, "PACK", path)
    with pytest.raises(ValueError, match="frozen public release"):
        archive_inflight.collect()


def test_results_carry_no_raw_ids() -> None:
    raw = json.dumps(archive_inflight.collect())
    assert PLACEHOLDER_RUN not in raw and "si-" not in raw
    assert not any(ticket in raw for ticket in TICKETS)


def test_pre_decision_empty_is_distinct_from_absent() -> None:
    rows = archive_inflight.collect()
    assert rows[at(WITHHELD)] == observed("settles", "accepted", "empty", 1)
    assert rows[at(BLIND)] == observed("fails", "accepted", "absent", 1)


def test_no_decision_leaves_inbox_unrecorded() -> None:
    rows = archive_inflight.collect()
    assert rows[at(IDLE)] == observed("fails", "absent", "unrecorded", 0)
    assert rows[at(SETTLES_IDLE)] == observed("settles", "absent", "unrecorded", 1)


def test_received_token_without_decision_is_not_an_exposure() -> None:
    data = copy.deepcopy(PUBLIC)
    row = next(row for row in data["rows"] if row["ticket"] == IDLE)
    row["cut"]["events"] = [
        {"seq": 1, "kind": "handoff", "choice": "original"},
        {"seq": 2, "kind": "inbox", "choice": "original"},
    ]
    world = inflight_verify.evaluate({**row["cut"], "run": PLACEHOLDER_RUN})
    case = next(case for case in QUESTION["cases"] if case["ticket"] == IDLE)
    case = {**case, "expected": {key: world[key] for key in GRADED}}
    assert case["expected"]["outgoing_handoff"] == "original"
    assert archive_inflight.classify(case, row) == observed(
        "fails", "absent", "unrecorded", 0
    )


def test_returned_draft_is_kept_apart_from_original() -> None:
    rows = archive_inflight.collect()
    assert rows[at(DRAFT_SETTLED)] == observed("fails", "accepted", "draft", 1)
    assert rows[at(DRAFT_REPLACE)] == observed("settles", "accepted", "draft", 0)
    assert rows[at(REPLACE)] == observed("settles", "accepted", "original", 1)


def test_late_read_is_absent_at_decision() -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        rows[JOIN_ORIGINAL]["cut"]["events"][1:] = [
            {"seq": 2, "kind": "resolve", "choice": "original"},
            {"seq": 3, "kind": "inbox", "choice": "original"},
        ]

    rows = project(edit)
    assert rows[at(JOIN_ORIGINAL)] == observed("fails", "accepted", "absent", 0)
    assert tally(rows) == (7, 7, 2)
    assert [row for i, row in enumerate(rows) if i != at(JOIN_ORIGINAL)] == [
        row for i, row in enumerate(EXPECTED) if i != at(JOIN_ORIGINAL)
    ]


def test_missing_rows_have_no_grade() -> None:
    gone = (IDLE, REPLACE, WITHHELD)

    def edit(data: Json, rows: dict[str, Json]) -> None:
        data["rows"] = [row for row in data["rows"] if row["ticket"] not in gone]

    rows = project(edit)
    assert rows[at(IDLE)] == missing("fails")
    assert rows[at(REPLACE)] == rows[at(WITHHELD)] == missing("settles")
    assert sum(row["status"] == "missing" for row in rows) == 3
    assert all(list(row) == KEYS for row in rows)
    kept = [i for i in range(16) if TICKETS[i] not in gone]
    assert [rows[i] for i in kept] == [EXPECTED[i] for i in kept]
    assert tally(rows) == (7, 5, 1)


def test_all_missing_is_sixteen_gaps() -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        data["rows"] = []

    assert project(edit) == [missing(entry[1]) for entry in TABLE]


def swap_fates(data: Json, rows: dict[str, Json]) -> None:
    rows[BLIND]["cut"], rows[SETTLES_BLIND]["cut"] = (
        rows[SETTLES_BLIND]["cut"],
        rows[BLIND]["cut"],
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda data, rows: rows[BLIND]["native"].update(reward=0),
        lambda data, rows: rows[BLIND]["native"].update(
            reward_valid=False, reward=None
        ),
        lambda data, rows: rows[REPLACE]["native"]["seats"].update(relief="raised"),
        lambda data, rows: rows[REPLACE]["native"]["seats"].update(outgoing=None),
        lambda data, rows: rows[REPLACE]["cut"]["events"][1].update(choice="none"),
        lambda data, rows: rows[WITHHELD]["cut"].update(drained=[]),
        swap_fates,
        lambda data, rows: rows[BLIND]["cut"].update(run=PLACEHOLDER_RUN),
        lambda data, rows: rows[BLIND].update(ticket="si-0000000000000000"),
        lambda data, rows: data["rows"].append(copy.deepcopy(rows[BLIND])),
        lambda data, rows: data.update(question_sha256="sha256:" + "0" * 64),
        lambda data, rows: data.update(task_files="sha256:" + "0" * 64),
        lambda data, rows: data.update(extra=1),
    ],
)
def test_discrepancies_are_rejected(edit: Edit) -> None:
    with pytest.raises(ValueError):
        project(edit)


def test_question_is_the_bundled_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = copy.deepcopy(QUESTION)
    question["cases"][0]["fate"] = "settles"
    path = tmp_path / "question.json"
    path.write_text(json.dumps(question), encoding="utf-8")
    monkeypatch.setattr(archive_inflight, "QUESTION", path)
    with pytest.raises(ValueError):
        archive_inflight.collect()
