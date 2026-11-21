import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.coverage.jobs import _BATCH, _DATA
from examples.seatlab.exchange.source import build
from examples.seatlab.handoff import journal
from examples.seatlab.handoff.packet import Block, grid, parse
from examples.seatlab.visibility.clarify_run import StudyError, _encode
from examples.seatlab.visibility.route_run import Answer


def _blocks() -> tuple[Block, ...]:
    packet = {
        "schema": "seatlab.handoff-input/v1",
        "brief": "Read the stock records.",
        "choices": ["A", "B"],
        "cases": [
            {
                "id": case,
                "cluster": "north",
                "records": [
                    {
                        "id": key,
                        "old_version": "1",
                        "new_version": "2" if changed else "1",
                        "before": f"Old stock {key}",
                        "after": f"New stock {key}" if changed else f"Old stock {key}",
                        "question": f"Which bay for {key}?",
                        "old_best": "A",
                        "best": "B" if changed else "A",
                    }
                    for key, changed in (("P", False), ("Q", case == "revised"))
                ],
            }
            for case in ("revised", "unchanged")
        ],
    }
    return grid(parse(json.dumps(packet).encode()))


@pytest.fixture
def writers(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path.resolve() / "first"
    second = tmp_path.resolve() / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    return first, second


def _record(study: Path, block: Block, raw: str, selected: str) -> dict[str, Any]:
    def respond(_prompt: str, run: str, _role: str) -> Answer:
        stage = run.removeprefix(f"{block.key}-")
        if stage == "note":
            return Answer(raw)
        if stage.endswith("-select"):
            return Answer(json.dumps({"read": selected}))
        return Answer('{"choices":{"P":"B","Q":"B"}}')

    return journal.play(study, block, respond)


def _valid(note: str) -> dict[str, str]:
    return {"status": "valid", "note_sha256": sha256(note.encode()).hexdigest()}


def test_exchange_uses_verified_notes_and_only_the_fixed_visible_record(
    writers: tuple[Path, Path],
) -> None:
    blocks = _blocks()
    first, second = writers
    note0, note1 = "Mémo from the first writer.", "Different note from the second."
    own = _record(first, blocks[1], json.dumps({"note": note0}), "P")
    other = _record(second, blocks[1], json.dumps({"note": note1}), "unknown")
    assert own["arms"]["bound"]["lookup"] == "P"
    assert other["arms"]["bound"]["status"] == "invalid_select"
    _record(first, blocks[2], '{"note":""}', "P")
    _record(second, blocks[2], json.dumps({"note": note1}), "unknown")
    _record(first, blocks[3], json.dumps({"note": note0}), "unknown")

    registry, requests = build(blocks, writers, (1, 2, 3))
    invalid = {"status": "invalid", "note_sha256": None}
    missing = {"status": "missing", "note_sha256": None}
    expected = {
        "schema": "seatlab.exchange-source/v1",
        "packet_sha256": blocks[0].fingerprint,
        "sources": ["W0", "W1"],
        "readers": ["R0", "R1"],
        "slots": [
            {
                "index": index,
                "block": block.key,
                "case": block.case.id,
                "cluster": block.case.cluster,
                "rep": block.rep,
                "opened": opened,
                "notes": notes,
            }
            for index, block, opened, notes in (
                (1, blocks[1], "Q", [_valid(note0), _valid(note1)]),
                (2, blocks[2], "P", [invalid, _valid(note1)]),
                (3, blocks[3], "P", [_valid(note0), missing]),
            )
        ],
    }
    digest = sha256(_encode(expected)).hexdigest()
    assert registry == expected | {"source_sha256": digest}
    assert [request.slot for request in requests] == [0, 1, 2, 3, 7, 6, 8, 9]
    assert requests[0].prompt == requests[1].prompt
    assert requests[2].prompt == requests[3].prompt
    assert requests[0].prompt != requests[2].prompt
    for request in requests:
        assert request.key == f"cv-{digest[:12]}-{request.slot:03d}-checklist"
        assert request.policy == "checklist"
        assert request.target is None
        assert request.ids == ("P", "Q")
        assert request.options == ("A", "B")
        instruction, payload = request.prompt.split(_DATA, 1)
        assert instruction == (
            f'{_BATCH} Required answer IDs: ["P", "Q"]. '
            "Opening one record does not change the required answer IDs."
        )
        assert json.loads(payload) == {
            "brief": "Read the stock records.",
            "note": note0 if request.slot % 4 < 2 else note1,
            "questions": [
                {"id": "P", "question": "Which bay for P?"},
                {"id": "Q", "question": "Which bay for Q?"},
            ],
            "options": ["A", "B"],
            "record": {"id": "Q", "body": "New stock Q"}
            if request.slot < 4
            else {"id": "P", "body": "Old stock P"},
        }


def test_exchange_rejects_the_same_study_twice(writers: tuple[Path, Path]) -> None:
    first, _ = writers
    with pytest.raises(StudyError, match="^duplicate_source$"):
        build(_blocks(), (first, first / "."), (0,))


def test_exchange_rejects_raw_note_drift_even_if_the_decoded_note_is_unchanged(
    writers: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = _blocks()
    first, _ = writers
    _record(first, blocks[0], '{"note":"Original note"}', "P")
    path = first / blocks[0].key / "note.json"
    collect = journal.collect

    def collect_then_change(
        study: Path, register: tuple[Block, ...]
    ) -> list[dict[str, Any]]:
        rows = collect(study, register)
        if study == first:
            receipt = json.loads(path.read_bytes())
            text = receipt["text"] + " "
            receipt.update(text=text, response_sha256=sha256(text.encode()).hexdigest())
            path.write_bytes(_encode(receipt))
        return rows

    monkeypatch.setattr(journal, "collect", collect_then_change)
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        build(blocks, writers, (0,))


def test_exchange_checks_unselected_turns(writers: tuple[Path, Path]) -> None:
    blocks = _blocks()
    _, second = writers
    _record(second, blocks[1], '{"note":"Original note"}', "P")
    path = second / blocks[1].key / "both-decide.json"
    receipt = json.loads(path.read_bytes())
    receipt["response_sha256"] = "0" * 64
    path.write_bytes(_encode(receipt))
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        build(blocks, writers, (0,))
