import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.seat_stream import Malformed
from examples.seatlab.workbench.team_timing_math import measure
from examples.seatlab.workbench.team_timing_source import windows

Json = dict[str, Any]
STREAM = Path(__file__).parents[3] / "examples/seatlab/results/seat-event-stream"
RETURN = STREAM / "native-jobs/job-return/hello-seats__MMUm7dq"
TRIAL = "fab6f592-0000-4000-8000-000000000001"
S = 1_000_000_000


def _seat(name: str, start: str | None, end: str | None, tested: bool = True) -> Json:
    return {
        "seat": name,
        "under_test": tested,
        "agent_execution": {"started_at": start, "finished_at": end},
    }


def _team(*seats: Json, frame: Json | None = None) -> Json:
    document: Json = {"schema_version": 1, "id": TRIAL, "seat_results": list(seats)}
    if frame is not None:
        document["agent_execution"] = frame
    return document


def _wall(document: Json, under_test: bool = True, declared: list[str] | None = None):
    journal = Path("/nonexistent-slot") / "seat-events.jsonl"
    return windows(
        document,
        document["id"],
        declared,
        journal,
        clock="result",
        under_test=under_test,
    )


def _slot(tmp_path: Path, data: bytes | None) -> Path:
    journal = tmp_path / "seat-events.jsonl"
    if data is not None:
        journal.write_bytes(data)
    return journal


def _fixture(name: str) -> tuple[Json, bytes]:
    result = json.loads((STREAM / "results" / f"{name}.json").read_text())
    return result, (STREAM / "journals" / f"{name}.jsonl").read_bytes()


def _monotonic(document: Json, journal: Path, under_test: bool = False):
    return windows(
        document, document["id"], None, journal, clock="journal", under_test=under_test
    )


TEAM = _team(
    _seat("p1", "2026-11-23T19:33:44.256000Z", "2026-11-23T19:34:15.556123Z"),
    _seat("p2", "2026-11-23T14:33:44.2591-05:00", "2026-11-23T19:34:14.759+00:00"),
    _seat("p3", "2026-11-24T01:03:44.265000000+05:30", "2026-11-23T19:35:24.925Z"),
    frame={
        "started_at": "2026-11-23T14:33:44.256-05:00",
        "finished_at": "2026-11-23T19:35:24.925000Z",
    },
)


def test_offsets_normalize_to_exact_integer_nanoseconds() -> None:
    code, found = _wall(TEAM, declared=["p3", "p1", "p2"])
    base = 1_795_462_424 * S
    assert code == "measured"
    assert found == [
        ("p1", base + 256_000_000, base + 31 * S + 556_123_000),
        ("p2", base + 259_100_000, base + 30 * S + 759_000_000),
        ("p3", base + 265_000_000, base + 100 * S + 925_000_000),
    ]
    assert all(type(point) is int for _, start, end in found for point in (start, end))
    assert measure(found)["last_seat"] == "p3"


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-11-23T14:35:25.929594",
        "2026-11-23T19:34:15.5561231Z",
        "2026-11-23T19:34:15.5561230001Z",
        "2026-11-23 19:34:15Z",
        "2026-11-23T19:34:15z",
        "2026-11-23T19:34:15+0000",
        "2026-11-23T19:34:15+05:60",
        "2026-11-23T19:34:15+24:00",
        "2026-02-30T19:34:15Z",
        "2026-11-23T19:34:15.Z",
        "2026-11-23T19:34:\u0661\u0665Z",
        "NaN",
        "",
        1795462455.5,
        1795462455556123,
        float("nan"),
        {"at": "2026-11-23T19:34:15Z"},
    ],
)
def test_unsafe_selected_stamp_is_invalid_without_detail(stamp: object) -> None:
    document = copy.deepcopy(TEAM)
    document["seat_results"][1]["agent_execution"]["finished_at"] = stamp
    assert _wall(document) == ("invalid_clock", [])


def test_nonzero_sub_microsecond_never_truncates() -> None:
    document = copy.deepcopy(TEAM)
    document["seat_results"][0]["agent_execution"]["finished_at"] = (
        "2026-11-23T19:34:15.556123000Z"
    )
    assert _wall(document) == _wall(TEAM)
    document["seat_results"][0]["agent_execution"]["finished_at"] = (
        "2026-11-23T19:34:15.556123009Z"
    )
    assert _wall(document) == ("invalid_clock", [])


def test_reversed_window_and_parent_disagreement_are_invalid() -> None:
    reversed_seat = copy.deepcopy(TEAM)
    reversed_seat["seat_results"][2]["agent_execution"]["finished_at"] = (
        "2026-11-23T19:33:44.264999Z"
    )
    escaped = copy.deepcopy(TEAM)
    escaped["seat_results"][2]["agent_execution"]["finished_at"] = (
        "2026-11-23T19:35:24.925001Z"
    )
    naive_parent = copy.deepcopy(TEAM)
    naive_parent["agent_execution"]["started_at"] = "2026-11-23T14:33:44.256"
    reversed_parent = copy.deepcopy(TEAM)
    reversed_parent["agent_execution"]["finished_at"] = "2026-11-23T19:33:44.255Z"
    for document in (reversed_seat, escaped, naive_parent, reversed_parent):
        assert _wall(document) == ("invalid_clock", [])


def test_absent_or_partial_parent_is_never_fabricated() -> None:
    expected = _wall(TEAM)
    no_parent = copy.deepcopy(TEAM)
    del no_parent["agent_execution"]
    open_parent = copy.deepcopy(TEAM)
    open_parent["agent_execution"]["finished_at"] = None
    assert _wall(no_parent) == _wall(open_parent) == expected
    late_start = copy.deepcopy(open_parent)
    late_start["agent_execution"]["started_at"] = "2026-11-23T19:33:44.257Z"
    assert _wall(late_start) == ("invalid_clock", [])


def test_missing_one_role_refuses_and_invalid_wins_in_any_order() -> None:
    missing = copy.deepcopy(TEAM)
    missing["seat_results"][1]["agent_execution"] = None
    assert _wall(missing) == ("missing_intervals", [])
    open_end = copy.deepcopy(TEAM)
    open_end["seat_results"][2]["agent_execution"]["finished_at"] = None
    assert _wall(open_end) == ("missing_intervals", [])
    for bad, gone in ((0, 2), (2, 0)):
        document = copy.deepcopy(TEAM)
        document["seat_results"][bad]["agent_execution"]["started_at"] = "garbage"
        del document["seat_results"][gone]["agent_execution"]
        assert _wall(document) == ("invalid_clock", [])


def test_unselected_support_clock_is_ignored_only_for_under_test_view() -> None:
    document = copy.deepcopy(TEAM)
    document["seat_results"].append(_seat("helper", "yesterday", None, tested=False))
    assert _wall(document, under_test=True) == _wall(TEAM)
    assert _wall(document, under_test=False) == ("invalid_clock", [])
    document["seat_results"][-1]["agent_execution"] = None
    assert _wall(document, under_test=False) == ("missing_intervals", [])


def test_roster_codes_and_unsafe_identity() -> None:
    absent = {"schema_version": 1, "id": TRIAL}
    assert _wall(absent, declared=["p1", "p2"]) == ("no_roster", [])
    assert _wall({**absent, "seat_results": None}) == ("no_roster", [])
    support = copy.deepcopy(TEAM)
    support["seat_results"][1]["under_test"] = False
    support["seat_results"][2]["under_test"] = False
    assert _wall(support) == ("fewer_than_two", [])
    assert _wall(support, under_test=False) == _wall(TEAM)
    duplicate = copy.deepcopy(TEAM)
    duplicate["seat_results"][2]["seat"] = "p1"
    unsafe = copy.deepcopy(TEAM)
    unsafe["seat_results"][0]["seat"] = "../p1"
    for document, declared in (
        (TEAM, ["p1", "p2"]),
        (TEAM, ["p1", "p2", "p3", "p1"]),
        (duplicate, None),
        (unsafe, None),
        ({**TEAM, "seat_results": []}, None),
    ):
        with pytest.raises(Malformed):
            _wall(document, declared=declared)
    with pytest.raises(Malformed):
        windows(
            TEAM,
            "fab6f592-0000-4000-8000-000000000002",
            None,
            Path("/nonexistent-slot/seat-events.jsonl"),
            clock="result",
            under_test=True,
        )


def test_closed_native_journal_measures_both_roles(tmp_path: Path) -> None:
    result = json.loads((RETURN / "result.json").read_text())
    journal = _slot(tmp_path, (RETURN / "seat-events.jsonl").read_bytes())
    assert _monotonic(result, journal, under_test=True) == ("fewer_than_two", [])
    assert _monotonic(result, journal) == (
        "measured",
        [("p1", 6_112_855_583, 11_356_005_000), ("p2", 6_116_307_500, 6_353_867_291)],
    )
    for row in result["seat_results"]:
        del row["execution_disposition"]
    assert _monotonic(result, journal)[0] == "measured"


def test_absent_journal_has_no_wall_fallback(tmp_path: Path) -> None:
    result, _ = _fixture("support-return")
    for index, row in enumerate(TEAM["seat_results"][:2]):
        result["seat_results"][index]["agent_execution"] = row["agent_execution"]
    assert _monotonic(result, _slot(tmp_path, None)) == ("no_journal", [])
    assert (
        windows(
            result,
            result["id"],
            None,
            _slot(tmp_path, None),
            clock="result",
            under_test=False,
        )[0]
        == "measured"
    )


@pytest.mark.parametrize("name", ["support-return", "setup-refusal", "team-deadline"])
def test_open_partial_or_never_entered_journal_is_incomplete(
    tmp_path: Path, name: str
) -> None:
    result, data = _fixture(name)
    if name == "support-return":
        data = (STREAM / "journals/support-return.prefix.jsonl").read_bytes()
    elif name == "team-deadline":
        data = data[: data.rindex(b'{"kind":"closed"') + 9]
    assert _monotonic(result, _slot(tmp_path, data)) == ("incomplete_journal", [])
    assert _monotonic(result, _slot(tmp_path, b"")) == ("incomplete_journal", [])
    assert _monotonic(result, _slot(tmp_path, data[:40])) == ("incomplete_journal", [])


def test_known_terminal_contradiction_is_conflict_even_when_open(
    tmp_path: Path,
) -> None:
    deadline, data = _fixture("team-deadline")
    deadline["seat_results"][1]["execution_disposition"] = "returned"
    assert _monotonic(deadline, _slot(tmp_path, data)) == ("journal_conflict", [])
    support, _ = _fixture("support-return")
    support["seat_results"][1]["execution_disposition"] = "cancelled"
    prefix = (STREAM / "journals/support-return.prefix.jsonl").read_bytes()
    assert _monotonic(support, _slot(tmp_path, prefix)) == ("journal_conflict", [])


def test_unbound_or_unsafe_journal_is_malformed(tmp_path: Path) -> None:
    result, data = _fixture("team-deadline")
    other = copy.deepcopy(result)
    other["id"] = "61a751fa-0d84-4e34-892b-2a264c54bd33"
    flipped = copy.deepcopy(result)
    flipped["seat_results"][1]["under_test"] = True
    renamed = copy.deepcopy(result)
    renamed["seat_results"][1]["seat"] = "p3"
    for document in (other, flipped, renamed):
        with pytest.raises(Malformed):
            _monotonic(document, _slot(tmp_path, data))
    with pytest.raises(Malformed):
        _monotonic(result, _slot(tmp_path, data.replace(b'"seq":3', b'"seq":7')))
    real = tmp_path / "real.jsonl"
    real.write_bytes(data)
    link = tmp_path / "linked" / "seat-events.jsonl"
    link.parent.mkdir()
    os.symlink(real, link)
    with pytest.raises(Malformed):
        _monotonic(result, link)


def test_each_clock_reads_only_its_own_source(tmp_path: Path) -> None:
    result, data = _fixture("team-deadline")
    for row in result["seat_results"]:
        row["agent_execution"] = {"started_at": "noon", "finished_at": 3.5}
    assert _monotonic(result, _slot(tmp_path, data)) == (
        "measured",
        [("p1", 6_150_792_375, 27_154_664_667), ("p2", 6_154_451_417, 27_155_385_875)],
    )
    slot = tmp_path / "other"
    slot.mkdir()
    journal = _slot(slot, b"\xff not a journal\n")
    assert windows(
        TEAM, TRIAL, None, journal, clock="result", under_test=True
    ) == _wall(TEAM)
