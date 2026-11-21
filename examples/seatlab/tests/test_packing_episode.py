import json
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.handoff.episode import Turn
from examples.seatlab.handoff.packet import Block, Case, Packet, Record
from examples.seatlab.packing.episode import episode
from examples.seatlab.packing.source import Entry

_ANSWER = '{"choices":{"a":"L","b":"R"}}'


def _entry(
    note: str | None,
    *,
    status: str = "usable",
    index: int = 0,
    rep: int = 0,
    revised: bool = False,
) -> Entry:
    unchanged = Record(
        "a",
        "1",
        "1",
        "UNCHANGED_BODY_MARKER",
        "UNCHANGED_BODY_MARKER",
        "Pick for a",
        "L",
        "L",
    )
    stable_body = "STABLE_BODY_MARKER"
    second = (
        Record("b", "1", "2", "OLD_BODY_MARKER", "CURRENT_BODY", "Pick for b", "L", "R")
        if revised
        else Record("b", "1", "1", stable_body, stable_body, "Pick for b", "R", "R")
    )
    packet = Packet(
        "BRIEF_MARKER",
        ("L", "R"),
        (
            Case("first", "world", (unchanged, second)),
            Case("second", "world", (unchanged, second)),
        ),
        "f" * 64,
    )
    response_sha256 = None if status == "missing" else "e" * 64
    return Entry(Block(packet, index, rep), status, response_sha256, note)


def _data(turn: Turn) -> dict[str, Any]:
    return json.loads(turn.prompt.split("\n\nData:\n", 1)[1])


def test_packer_is_blind_and_both_arms_use_the_same_utf8_boundary() -> None:
    original = "a" * 95 + "é" + "SOURCE_TAIL_MARKER"
    rewrite = "b" * 94 + "é" + "🦉" + "REWRITE_TAIL_MARKER"
    turns: list[Turn] = []

    def ask(turn: Turn) -> str:
        turns.append(turn)
        return json.dumps({"note": rewrite}) if turn.stage == "rewrite" else _ANSWER

    result = episode(_entry(original, revised=True), ask)
    assert [(turn.stage, turn.role) for turn in turns] == [
        ("rewrite", "route_review"),
        ("prefix-decide", "route_decide"),
        ("rewrite-decide", "route_decide"),
    ]
    assert _data(turns[0]) == {"note": original}
    assert "96 UTF-8 bytes" in turns[0].prompt
    assert all(
        marker not in turns[0].prompt
        for marker in ("BRIEF_MARKER", "CURRENT_BODY", "Pick for a", "Pick for b")
    )
    assert result["source_note_bytes"] == len(original.encode())
    assert result["source_note_sha256"] == sha256(original.encode()).hexdigest()
    for name, turn, note in zip(
        ("prefix", "rewrite"), turns[1:], ("a" * 95, "b" * 94 + "é"), strict=True
    ):
        assert _data(turn) == {
            "brief": "BRIEF_MARKER",
            "note": note,
            "questions": [
                {"id": "a", "question": "Pick for a"},
                {"id": "b", "question": "Pick for b"},
            ],
            "options": ["L", "R"],
            "record": {"id": "b", "body": "CURRENT_BODY"},
        }
        assert all(
            marker not in turn.prompt
            for marker in (
                "UNCHANGED_BODY_MARKER",
                "OLD_BODY_MARKER",
                "SOURCE_TAIL_MARKER",
                "REWRITE_TAIL_MARKER",
                "prefix",
                "rewrite",
                "96 UTF-8 bytes",
            )
        )
        assert result["arms"][name] == {
            "status": "complete",
            "packed_bytes": len(note.encode()),
            "packed_sha256": sha256(note.encode()).hexdigest(),
            "decision_stage": turn.stage,
            "choices": {"a": "L", "b": "R"},
        }


@pytest.mark.parametrize("suffix", ["", "🦉"])
def test_identical_packed_bytes_share_one_decision(suffix: str) -> None:
    note = "é" * 48
    stages: list[str] = []

    def ask(turn: Turn) -> str:
        stages.append(turn.stage)
        if turn.stage == "rewrite":
            return json.dumps({"note": note + "more"})
        assert _data(turn)["note"] == note
        assert _data(turn)["record"] is None
        assert "STABLE_BODY_MARKER" not in turn.prompt
        return _ANSWER

    arms = episode(_entry(note + suffix), ask)["arms"]
    assert stages == (["rewrite", "both-decide"] if suffix else ["both-decide"])
    assert arms["prefix"] == arms["rewrite"]
    assert arms["prefix"]["choices"] == {"a": "L", "b": "R"}


@pytest.mark.parametrize("status", ["invalid", "missing"])
def test_unusable_source_starts_no_turn(status: str) -> None:
    def ask(_turn: Turn) -> str:
        pytest.fail("an unusable source started a turn")

    result = episode(_entry(None, status=status), ask)
    no_source = {
        "status": "no_source",
        "packed_bytes": None,
        "packed_sha256": None,
        "decision_stage": None,
        "choices": None,
    }
    assert result == {
        "source_status": status,
        "source_note_bytes": None,
        "source_note_sha256": None,
        "arms": {"prefix": no_source, "rewrite": no_source},
    }


@pytest.mark.parametrize(
    ("index", "rep", "first"), [(0, 0, "prefix"), (0, 1, "rewrite"), (1, 0, "rewrite")]
)
def test_counterbalance_preserves_the_other_arm_when_a_decision_is_missing(
    index: int, rep: int, first: str
) -> None:
    stages: list[str] = []

    def ask(turn: Turn) -> str | None:
        stages.append(turn.stage)
        if turn.stage == "rewrite":
            return '{"note":"different"}'
        return None if turn.stage == "prefix-decide" else _ANSWER

    arms = episode(_entry("x" * 97, index=index, rep=rep), ask)["arms"]
    second = "rewrite" if first == "prefix" else "prefix"
    assert stages == ["rewrite", f"{first}-decide", f"{second}-decide"]
    assert arms["prefix"]["status"] == "missing_decide"
    assert arms["prefix"]["choices"] is None
    assert arms["rewrite"]["status"] == "complete"
    assert arms["rewrite"]["choices"] == {"a": "L", "b": "R"}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        '{"note":""}',
        '{"note":"first","note":"second"}',
        '{"note":"\\ud800"}',
        json.dumps({"note": "a" * 97 + "\t"}),
    ],
)
def test_missing_or_invalid_rewrite_skips_its_decision(raw: str | None) -> None:
    stages: list[str] = []

    def ask(turn: Turn) -> str | None:
        stages.append(turn.stage)
        return raw if turn.stage == "rewrite" else _ANSWER

    arms = episode(_entry("x" * 97, rep=1), ask)["arms"]
    assert stages == ["rewrite", "prefix-decide"]
    assert arms["prefix"]["status"] == "complete"
    assert arms["rewrite"] == {
        "status": "missing_rewrite" if raw is None else "invalid_rewrite",
        "packed_bytes": None,
        "packed_sha256": None,
        "decision_stage": None,
        "choices": None,
    }


@pytest.mark.parametrize(
    "raw",
    [
        '{"choices":{"a":"L","a":"R","b":"R"}}',
        '{"choices":{"a":"L"}}',
        '{"choices":{"a":"L","b":"other"}}',
        '{"choices":{"a":"L","b":"R"},"extra":true}',
    ],
)
def test_decision_requires_exact_fields_ids_and_options(raw: str) -> None:
    arms = episode(_entry("note"), lambda _: raw)["arms"]
    for arm in arms.values():
        assert arm["status"] == "invalid_decide"
        assert arm["choices"] is None
