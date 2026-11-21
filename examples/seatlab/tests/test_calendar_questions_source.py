import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.calendar_questions import source
from examples.seatlab.rolepack import calendar_source, protocol

_PACKET = Path("examples/seatlab/results/flotilla-calendar-packet.json")


@pytest.fixture(scope="module")
def calendar_root() -> Path:
    if not (root := os.environ.get("SEATLAB_CALENDAR_ROOT")):
        pytest.skip("set SEATLAB_CALENDAR_ROOT to the trusted calendar-list checkout")
    return Path(root).absolute()


@pytest.fixture(scope="module")
def mixed_pack(calendar_root: Path) -> protocol.Document:
    records = calendar_source.export(calendar_root)
    other = {**records[0], "id": "other-task", "task": "other"}
    return {"schema": protocol.SCHEMA, "records": [other, *reversed(records)]}


@pytest.fixture(scope="module")
def board(calendar_root: Path, mixed_pack: protocol.Document) -> protocol.Document:
    return source.build(calendar_root, mixed_pack)


def test_native_calendar_board(
    board: protocol.Document, mixed_pack: protocol.Document
) -> None:
    originals = {
        row["id"]: row for row in mixed_pack["records"] if row["task"] == "calendar"
    }
    rows = board["records"]
    assert [row["id"] for row in rows] == sorted(originals)
    for row in rows:
        record_id = row["id"]
        original = originals[record_id]
        direction = record_id.split("-")[3]
        opposite_id = record_id.replace(f"-{direction}-", f"-d{1 - int(direction[1])}-")
        opposite = originals[opposite_id]
        expected_view = opposite["prompt"].split("Task:\n", 1)[0] + "Task:\n"
        assert row["receiver_prompt"] == original["prompt"]
        assert row["receiver_sha256"] == original["prompt_sha256"]
        assert row["sender_view"] == expected_view
        assert row["sender_sha256"] == sha256(expected_view.encode("utf-8")).hexdigest()
        assert [row["optimum"]] == original["rubric"]["items"][0]["optimal"]
    assert rows[0]["receiver_starts"] == [16, 26, 28, 29]
    assert rows[0]["sender_starts"] == [7, 8, 9, 26]
    assert rows[0]["optimum"] == 26
    assert source.validate(protocol.loads(protocol.canonical_bytes(board))) == board


def test_altered_native_packet_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    packet = root / _PACKET
    packet.parent.mkdir(parents=True)
    packet.write_text('{"schema":"seatlab.calendar-packet/v1","worlds":[]}\n')
    prompt = "Unrelated valid RolePack record."
    pack = {
        "schema": protocol.SCHEMA,
        "records": [
            {
                "id": "other-task",
                "task": "other",
                "world": "w00",
                "prompt": prompt,
                "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
                "peer": None,
                "rubric": {
                    "field": "slot",
                    "scalar": True,
                    "items": [{"key": "slot", "valid": [0], "optimal": [0]}],
                },
            }
        ],
    }
    with pytest.raises(ValueError, match="altered.*native calendar packet"):
        source.build(root, pack)


@pytest.mark.parametrize("alteration", ["prompt", "grade", "id", "role"])
def test_calendar_source_rejects_recommitted_rolepack_changes(
    calendar_root: Path, mixed_pack: protocol.Document, alteration: str
) -> None:
    pack = deepcopy(mixed_pack)
    rows = {row["id"]: row for row in pack["records"]}
    row = rows["cal-rx-w00-d0-r0-blind"]
    if alteration == "prompt":
        row["prompt"] += "Choose a different slot.\n"
        row["prompt_sha256"] = sha256(row["prompt"].encode("utf-8")).hexdigest()
    elif alteration == "grade":
        row["rubric"]["items"][0]["optimal"] = [25]
    elif alteration == "id":
        row["id"] = "cal-rx-w00-d0-r2-blind"
    else:
        opposite = rows["cal-rx-w00-d1-r0-blind"]
        for key in ("prompt", "prompt_sha256"):
            row[key], opposite[key] = opposite[key], row[key]
    with pytest.raises(ValueError, match="records differ from the published source"):
        source.build(calendar_root, pack)


@pytest.mark.parametrize("alteration", ["digest", "order", "type", "view", "starts"])
def test_offline_calendar_source_rejects_inconsistent_boards(
    board: protocol.Document, alteration: str
) -> None:
    changed = deepcopy(board)
    rows = changed["records"]
    row = rows[0]
    match = {
        "digest": "hash mismatch",
        "order": "coordinates or order",
        "type": "optimum differs",
        "view": "opposite receiver role",
        "starts": "starts differ from the receiver view",
    }[alteration]
    if alteration == "digest":
        row["sender_sha256"] = "0" * 64
    elif alteration == "order":
        rows[0], rows[1] = rows[1], rows[0]
    elif alteration == "type":
        row["optimum"] = float(row["optimum"])
    elif alteration == "view":
        row["sender_view"] = row["receiver_prompt"].split("Task:\n", 1)[0] + "Task:\n"
        row["sender_sha256"] = sha256(row["sender_view"].encode("utf-8")).hexdigest()
    else:
        row["receiver_starts"].remove(16)
    with pytest.raises(ValueError, match=match):
        source.validate(changed)
