import json
import os
from pathlib import Path
from typing import Any

import pytest

from examples.native_evidence import Invalid
from examples.seatlab.decision_receipt import archive_current as archive
from examples.seatlab.decision_receipt import decision_verify as verify

RUN_A = "0b6c3a52-6f0e-4b1d-9a8e-2f4c5d6e7a81"
RUN_B = "5d2e7f10-3a4b-4c6d-8e9f-0a1b2c3d4e5f"
RUN_C = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"
RUN_D = "c4d5e6f7-0819-4a2b-b3c4-d5e6f708192a"
RUN_E = "e1f2a3b4-c5d6-4e7f-8091-a2b3c4d5e6f7"
REASONS = (
    archive.MISSING,
    archive.UNREADABLE,
    archive.NOT_A_PATH,
    archive.OVER_TOTAL,
    archive.FOREIGN,
    archive.NOT_A_CUT,
    archive.INCONSISTENT,
    archive.COPIED,
    archive.CONFLICT,
)


def cut(
    run: str,
    fate: str,
    channel: str,
    events: list[tuple[str, str]],
    drained: list[str],
    cited: str | None,
) -> dict[str, Any]:
    return {
        "schema": verify.CUT_SCHEMA,
        "run": run,
        "fate": fate,
        "channel": channel,
        "events": [
            {"seq": seq, "kind": kind, "choice": choice}
            for seq, (kind, choice) in enumerate(events, 1)
        ],
        "replace": None,
        "settled": [],
        "drained": drained,
        verify.CITATION: cited,
    }


RETURNED = [("handoff", "original"), ("inbox", "original"), ("resolve", "close")]
NO_DECISION = cut(
    RUN_A,
    "settles",
    "deliver",
    [("handoff", "original"), ("inbox", "original")],
    ["original"],
    None,
)
LATE_READ = cut(
    RUN_B,
    "fails",
    "deliver",
    [("handoff", "original"), ("resolve", "close"), ("inbox", "original")],
    [],
    None,
)
EMPTY_INBOX = cut(
    RUN_C,
    "settles",
    "withhold",
    [("handoff", "original"), ("inbox", "none"), ("resolve", "close")],
    ["original"],
    None,
)
NOT_CITED = cut(RUN_D, "settles", "deliver", RETURNED, ["original"], None)
CITED_SETTLES = cut(RUN_D, "settles", "deliver", RETURNED, ["original"], "original")
CITED_FAILS = cut(RUN_E, "fails", "deliver", RETURNED, [], "original")
DRAFT_CITED = cut(
    RUN_E,
    "settles",
    "deliver",
    [("handoff", "draft"), ("inbox", "draft"), ("resolve", "close")],
    ["original"],
    "draft",
)


def row(
    axis: str, decision: str, inbox: str, citation: str, reward: int
) -> dict[str, object]:
    return {
        "world": "inflight",
        "axis_state": axis,
        "decision": decision,
        "inbox": inbox,
        "citation": citation,
        "reward": reward,
        "status": "observed",
    }


def saved(directory: Path, name: str, value: object) -> Path:
    path = directory / name
    path.write_bytes(json.dumps(value).encode("utf-8"))
    return path


def refused(paths: list[Any], reason: str, private: Path) -> None:
    with pytest.raises(Invalid) as error:
        archive.collect(paths)
    message = str(error.value)
    assert message == reason and message in REASONS
    assert str(private) not in message and private.name not in message
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (NO_DECISION, row("settles", "absent", "unrecorded", "not_applicable", 1)),
        (LATE_READ, row("fails", "accepted", "absent", "not_applicable", 0)),
        (EMPTY_INBOX, row("settles", "accepted", "empty", "not_applicable", 1)),
        (NOT_CITED, row("settles", "accepted", "original", "not_cited", 1)),
        (CITED_SETTLES, row("settles", "accepted", "original", "at_decision", 1)),
        (CITED_FAILS, row("fails", "accepted", "original", "at_decision", 0)),
        (DRAFT_CITED, row("settles", "accepted", "draft", "at_decision", 1)),
    ],
)
def test_one_saved_cut_projects_its_verified_row(
    tmp_path: Path, fixture: dict[str, Any], expected: dict[str, object]
) -> None:
    assert archive.collect([saved(tmp_path, verify.CUT_NAME, fixture)]) == [expected]


def test_read_without_decision_is_not_a_predecision_read(tmp_path: Path) -> None:
    observation = verify.evaluate(NO_DECISION)
    assert observation["citation_status"] == "no_decision"
    assert verify.returned_before_decision(verify._replayed_events(observation)) is None
    unread = {**NO_DECISION, "events": NO_DECISION["events"][:1]}
    rows = archive.collect([saved(tmp_path, "unread.json", unread)])
    assert rows == [row("settles", "absent", "unrecorded", "not_applicable", 1)]


def test_late_read_is_absent_and_cannot_be_cited(tmp_path: Path) -> None:
    assert verify.evaluate(LATE_READ)["citation_status"] == (
        "decided_without_returned_reference"
    )
    late = {**LATE_READ, verify.CITATION: "original"}
    late_cited = saved(tmp_path, "late.json", late)
    refused([late_cited], archive.NOT_A_CUT, tmp_path)


def test_returned_plain_close_is_not_cited_but_withheld_is_not_applicable(
    tmp_path: Path,
) -> None:
    rows = archive.collect(
        [
            saved(tmp_path, "plain.json", NOT_CITED),
            saved(tmp_path, "empty.json", EMPTY_INBOX),
        ]
    )
    assert [(item["inbox"], item["citation"]) for item in rows] == [
        ("original", "not_cited"),
        ("empty", "not_applicable"),
    ]


def test_atomic_close_citation_separates_from_reward_across_fates(
    tmp_path: Path,
) -> None:
    rows = archive.collect(
        [
            saved(tmp_path, "settles.json", CITED_SETTLES),
            saved(tmp_path, "fails.json", CITED_FAILS),
        ]
    )
    assert [item["citation"] for item in rows] == ["at_decision", "at_decision"]
    assert [item["reward"] for item in rows] == [1, 0]
    assert [item["axis_state"] for item in rows] == ["settles", "fails"]


@pytest.mark.parametrize("cited", [None, "original"])
def test_earlier_inflight_schema_is_refused_not_downgraded(
    tmp_path: Path, cited: str | None
) -> None:
    earlier = {key: value for key, value in NOT_CITED.items() if key != verify.CITATION}
    earlier["schema"] = verify.inflight.CUT_SCHEMA
    assert verify.inflight.evaluate(earlier)["reward"] == 1
    if cited is not None:
        earlier[verify.CITATION] = cited
    refused([saved(tmp_path, "v1.json", earlier)], archive.FOREIGN, tmp_path)


def test_stock_handover_schema_is_refused(tmp_path: Path) -> None:
    stock = {**NOT_CITED, "schema": verify.stock.CUT_SCHEMA}
    refused([saved(tmp_path, "stock.json", stock)], archive.FOREIGN, tmp_path)


def test_one_foreign_cut_rejects_the_whole_archive(tmp_path: Path) -> None:
    earlier = {**NOT_CITED, "schema": verify.inflight.CUT_SCHEMA, "run": RUN_A}
    paths = [
        saved(tmp_path, "new.json", CITED_FAILS),
        saved(tmp_path, "old.json", earlier),
    ]
    refused(paths, archive.FOREIGN, tmp_path)


def test_distinct_runs_are_kept_in_input_order(tmp_path: Path) -> None:
    paths = [
        saved(tmp_path, "b.json", LATE_READ),
        saved(tmp_path, "a.json", NO_DECISION),
    ]
    rows = archive.collect(paths)
    assert [item["inbox"] for item in rows] == ["absent", "unrecorded"]


def test_copied_cut_rejects_the_whole_archive(tmp_path: Path) -> None:
    first = saved(tmp_path, "first.json", NOT_CITED)
    copy = tmp_path / "copy.json"
    copy.write_bytes(json.dumps(NOT_CITED, indent=1).encode("utf-8"))
    other = saved(tmp_path, "other.json", LATE_READ)
    refused([first, other, copy], archive.COPIED, tmp_path)
    refused([first, first], archive.COPIED, tmp_path)


def test_same_run_with_different_contents_rejects_the_whole_archive(
    tmp_path: Path,
) -> None:
    assert NOT_CITED["run"] == CITED_SETTLES["run"]
    paths = [
        saved(tmp_path, "a.json", NOT_CITED),
        saved(tmp_path, "b.json", CITED_SETTLES),
    ]
    refused(paths, archive.CONFLICT, tmp_path)


def test_missing_path_is_a_hard_failure(tmp_path: Path) -> None:
    present = saved(tmp_path, "present.json", NOT_CITED)
    refused([present, tmp_path / "gone.json"], archive.MISSING, tmp_path)
    refused([tmp_path / "gone" / "cut.json"], archive.MISSING, tmp_path)


def test_symlink_is_refused_even_to_a_valid_cut(tmp_path: Path) -> None:
    target = saved(tmp_path, "target.json", NOT_CITED)
    link = tmp_path / "link.json"
    os.symlink(target, link)
    refused([link], archive.UNREADABLE, tmp_path)
    dangling = tmp_path / "dangling.json"
    os.symlink(tmp_path / "nowhere.json", dangling)
    refused([dangling], archive.UNREADABLE, tmp_path)


def test_fifo_and_directory_are_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.json"
    os.mkfifo(fifo)
    refused([fifo], archive.UNREADABLE, tmp_path)
    folder = tmp_path / "folder.json"
    folder.mkdir()
    refused([folder], archive.UNREADABLE, tmp_path)
    refused([tmp_path / ".."], archive.UNREADABLE, tmp_path)


def test_file_byte_bound_and_content_are_checked(tmp_path: Path) -> None:
    large = tmp_path / "large.json"
    large.write_bytes(b" " * verify.stock.MAX_CUT_BYTES + b"{}")
    refused([large], archive.UNREADABLE, tmp_path)
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    refused([empty], archive.NOT_A_CUT, tmp_path)
    garbled = tmp_path / "garbled.json"
    garbled.write_bytes(b'{"schema": 1, "schema": 2}')
    refused([garbled], archive.NOT_A_CUT, tmp_path)


def test_archive_size_and_path_types_are_bounded(tmp_path: Path) -> None:
    one = saved(tmp_path, "one.json", NOT_CITED)
    refused([], archive.NOT_A_PATH, tmp_path)
    refused([one] * (archive.MAX_FILES + 1), archive.NOT_A_PATH, tmp_path)
    refused([str(one)], archive.NOT_A_PATH, tmp_path)
    with pytest.raises(Invalid):
        invalid: Any = (one,)
        archive.collect(invalid)
