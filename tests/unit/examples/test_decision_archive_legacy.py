import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import archive_legacy as archive

SAMPLES = archive.SAMPLES
LOST = "sh-abc9880d5d22d2ca"
DROPPED_DELIVER = "sh-64b2edcd04cca161"

OBS = "observed"
EXPECTED: dict[str, list[tuple[Any, ...]]] = {
    "stock-controls": [
        ("stock", "dropped", "accepted", "absent", 1, OBS),
        ("stock", "committed", "accepted", "empty", 1, OBS),
        ("stock", "committed", "accepted", "empty", 1, OBS),
        ("stock", "committed", "accepted", "original", 0, OBS),
        ("stock", "committed", "absent", "absent", 1, OBS),
        ("stock", "dropped", "accepted", "empty", 0, OBS),
        ("stock", "dropped", "accepted", "original", 1, OBS),
        ("stock", "committed", "absent", "absent", 1, OBS),
        ("stock", "committed", "accepted", "absent", 1, OBS),
        ("stock", "dropped", "accepted", "absent", 0, OBS),
        ("stock", "dropped", "accepted", "absent", 0, OBS),
        ("stock", "committed", "accepted", "original", 1, OBS),
        ("stock", "dropped", "accepted", "absent", 0, OBS),
        ("stock", "committed", "accepted", "absent", 1, OBS),
        ("stock", "dropped", "accepted", "empty", 0, OBS),
        ("stock", "committed", "accepted", "empty", 1, OBS),
        ("stock", "dropped", "accepted", "empty", 0, OBS),
        ("stock", "committed", "accepted", "original", 1, OBS),
        ("stock", "dropped", "accepted", "original", 0, OBS),
        ("stock", "dropped", "accepted", "original", 1, OBS),
        ("stock", "committed", "accepted", "absent", 1, OBS),
        ("stock", "committed", "accepted", "absent", 0, OBS),
        ("stock", "dropped", "absent", "absent", 0, OBS),
        ("stock", "dropped", "accepted", "draft", 1, OBS),
        ("stock", "committed", "accepted", "draft", 0, OBS),
        ("stock", "dropped", "absent", "absent", 0, OBS),
    ],
    "two-worlds": [
        ("stock", "committed", "accepted", "original", 1, OBS),
        ("stock", "committed", "accepted", "empty", 1, OBS),
        ("stock", "dropped", "accepted", "original", 1, OBS),
        ("stock", "dropped", "accepted", "empty", 0, OBS),
        ("stock", "committed", "unrecorded", "unrecorded", 1, OBS),
        ("stock", "committed", "unrecorded", "unrecorded", 1, OBS),
        ("stock", "dropped", "unrecorded", "unrecorded", 0, OBS),
        ("stock", "dropped", "unrecorded", "unrecorded", 0, OBS),
        ("inflight", "settles", "accepted", "empty", 1, OBS),
        ("inflight", "fails", "accepted", "empty", 0, OBS),
        ("inflight", "fails", "accepted", "original", 0, OBS),
        ("inflight", "settles", "accepted", "original", 1, OBS),
        ("inflight", "settles", "unrecorded", "unrecorded", 1, OBS),
        ("inflight", "settles", "unrecorded", "unrecorded", 1, OBS),
        ("inflight", "fails", "unrecorded", "unrecorded", 0, OBS),
        ("inflight", "fails", "unrecorded", "unrecorded", 0, OBS),
    ],
    "disconnected": [
        ("stock", "committed", "accepted", "draft", 0, OBS),
        ("stock", "dropped", "unrecorded", "unrecorded", None, "missing"),
        ("stock", "dropped", "accepted", "draft", 1, OBS),
        ("stock", "committed", "accepted", "unrecorded", 1, OBS),
        ("inflight", "settles", "accepted", "draft", 0, OBS),
        ("inflight", "fails", "accepted", "unrecorded", 0, OBS),
        ("inflight", "fails", "accepted", "draft", 1, OBS),
        ("inflight", "settles", "accepted", "unrecorded", 1, OBS),
    ],
}
MISSING = ("stock", "dropped", "unrecorded", "unrecorded", None, "missing")


def entry(*values: object) -> dict[str, object]:
    world, state, decision, inbox, reward, status = values
    row = (world, state, decision, inbox, "unrecorded", reward, status)
    return dict(zip(archive.ROW_FIELDS, row, strict=True))


def expected(release: str) -> list[dict[str, object]]:
    return [entry(*row) for row in EXPECTED[release]]


def load(*parts: str) -> dict[str, Any]:
    return json.loads(SAMPLES.joinpath(*parts).read_text(encoding="utf-8"))


def disconnected() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    folder = "disconnected-workers"
    return (
        load(folder, "qualification.json"),
        load(folder, "stock.json"),
        load(folder, "inflight.json"),
    )


def native_row(qualification: dict[str, Any], ticket: str) -> dict[str, Any]:
    found = [row for row in qualification["native_rows"] if row["ticket"] == ticket]
    assert len(found) == 1
    return found[0]


def test_collect_projects_each_pinned_release_separately() -> None:
    found = archive.collect()

    assert found == {release: expected(release) for release in EXPECTED}
    assert list(found) == ["stock-controls", "two-worlds", "disconnected"]


def test_missing_zero_and_unrecorded_stay_distinct() -> None:
    found = archive.collect()
    rows = [row for release in found.values() for row in release]

    assert [row for row in rows if row["status"] == "missing"] == [entry(*MISSING)]
    assert sum(row["reward"] == 0 for row in found["disconnected"]) == 3
    silent = [row for row in found["two-worlds"] if row["decision"] == "unrecorded"]
    assert len(silent) == 8
    assert [row["reward"] for row in silent] == [1, 1, 0, 0, 1, 1, 0, 0]


def test_stock_return_after_decision_is_not_a_pre_decision_inbox() -> None:
    outcomes = load("control-outcomes.json")
    registration = load("control-registration.json")
    late = {
        cell["ticket"]
        for cell in outcomes["cells"]
        if cell["observation"]["timeline"] == ["resolve", "handoff", "inbox"]
    }
    rows = archive.stock_rows(outcomes, registration)
    order = [cell["ticket"] for cell in registration["cells"]]
    projected = {
        (row["decision"], row["inbox"])
        for ticket, row in zip(order, rows, strict=True)
        if ticket in late
    }

    assert len(late) == 4
    assert projected == {("accepted", "absent")}


def test_dropped_disconnected_row_becomes_missing_not_zero() -> None:
    qualification, stock, inflight = disconnected()
    qualification["native_rows"].remove(native_row(qualification, DROPPED_DELIVER))
    qualification["native_firsts"]["no_known_grade"].append(DROPPED_DELIVER)

    rows = archive.disconnected_rows(qualification, stock, inflight)

    assert rows[2] == entry(*MISSING)
    unchanged = expected("disconnected")
    assert rows[:2] + rows[3:] == unchanged[:2] + unchanged[3:]


def forged_lost_grade(qualification: dict[str, Any]) -> None:
    forged = {**native_row(qualification, DROPPED_DELIVER), "ticket": LOST}
    forged.update(channel="withhold", grade=0, physical_outcome="omission")
    qualification["native_rows"].append(forged)


def repeated_ticket(qualification: dict[str, Any]) -> None:
    qualification["native_rows"].append(dict(qualification["native_rows"][0]))


def foreign_ticket(qualification: dict[str, Any]) -> None:
    qualification["native_rows"][0]["ticket"] = "sh-0000000000000000"


def moved_world(qualification: dict[str, Any]) -> None:
    qualification["native_rows"][0]["prior"] = "dropped"


def unnamed_receipt(qualification: dict[str, Any]) -> None:
    qualification["native_rows"][0]["receipt_before_decision"] = None


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (forged_lost_grade, "missing tickets differ from the release"),
        (repeated_ticket, "row repeats a ticket"),
        (foreign_ticket, "row ticket is not registered"),
        (moved_world, "row differs from its registration"),
        (unnamed_receipt, "returned symbol is unknown"),
    ],
)
def test_disconnected_release_edges_are_refused(edit: Any, message: str) -> None:
    qualification, stock, inflight = disconnected()
    edit(qualification)

    with pytest.raises(archive.ArchiveError) as error:
        archive.disconnected_rows(qualification, stock, inflight)

    assert str(error.value) == message


def test_registrations_sharing_a_ticket_are_refused() -> None:
    _, stock, inflight = disconnected()

    with pytest.raises(archive.ArchiveError) as error:
        archive.registered(stock, inflight, stock)

    assert str(error.value) == "registrations repeat a ticket"


def copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy)
    monkeypatch.setattr(archive, "SAMPLES", copy)
    return copy


def test_hash_drift_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = copied(tmp_path, monkeypatch) / "two-worlds" / "native-qualification.json"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace('"reward": 0', '"reward": 1', 1), encoding="utf-8")

    with pytest.raises(archive.ArchiveError) as error:
        archive.collect()

    assert str(error.value) == "sample digest differs from its pin"


@pytest.mark.parametrize(
    "parts", [("disconnected-workers", "stock.json"), ("two-worlds",)]
)
def test_linked_sample_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts: tuple[str, ...]
) -> None:
    target = copied(tmp_path, monkeypatch).joinpath(*parts)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    target.symlink_to(SAMPLES.joinpath(*parts), target_is_directory=len(parts) == 1)

    with pytest.raises(archive.ArchiveError) as error:
        archive.collect()

    assert str(error.value) == "sample is not a readable plain file"
