import hashlib
import json
from collections.abc import Mapping
from typing import Any

from examples.seatlab.handoff.episode import Turn, episode
from examples.seatlab.handoff.packet import Block, Case, Packet, Record


_NOTE = "Mémo: stock may change."


def _block(changed: str, rep: int) -> Block:
    records = tuple(
        Record(
            id=ident,
            old_version="old",
            new_version="new" if ident == changed else "old",
            before=f"old stock {ident}",
            after=f"new stock {ident}" if ident == changed else f"old stock {ident}",
            question=f"question {ident}",
            old_best="A",
            best="B" if ident == changed else "A",
        )
        for ident in ("P", "Q")
    )
    case = Case(id="sample", cluster="seed", records=records)
    packet = Packet(
        brief="Dispatch brief", choices=("A", "B"), cases=(case,), sha256="1" * 64
    )
    return Block(packet=packet, index=0, rep=rep)


def _replay(
    block: Block, responses: Mapping[str, str | None]
) -> tuple[dict[str, Any], dict[str, Turn]]:
    seen: dict[str, Turn] = {}

    def ask(turn: Turn) -> str | None:
        assert turn.stage not in seen
        assert turn.role == ("route_review" if turn.stage == "note" else "route_decide")
        seen[turn.stage] = turn
        return responses[turn.stage]

    outcome = episode(block, ask)
    assert tuple(seen) == tuple(responses)
    return outcome, seen


def _data(turn: Turn) -> dict[str, Any]:
    return json.loads(turn.prompt.split("\n\nData:\n", 1)[1])


def test_handoff_prompt_views_and_shared_read() -> None:
    responses = {
        "note": json.dumps({"note": _NOTE}),
        "bound-select": '{"read":"P"}',
        "anonymous-select": '{"read":"P"}',
        "both-decide": '{"choices":{"P":"B","Q":"A"}}',
    }
    outcome, seen = _replay(_block("P", 0), responses)
    _, swapped = _replay(_block("Q", 0), responses)
    lookup_context = {
        "brief": "Dispatch brief",
        "note": _NOTE,
        "options": ["A", "B"],
        "questions": [
            {"id": "P", "question": "question P"},
            {"id": "Q", "question": "question Q"},
        ],
    }
    assert _data(seen["note"]) == {
        "brief": "Dispatch brief",
        "records": [
            {"id": "P", "body": "old stock P"},
            {"id": "Q", "body": "old stock Q"},
        ],
    }
    assert seen["note"].prompt == swapped["note"].prompt
    assert _data(seen["bound-select"]) == dict(
        lookup_context,
        receipts=[{"id": "P", "status": "revised"}, {"id": "Q", "status": "same"}],
    )
    assert _data(swapped["bound-select"])["receipts"] == [
        {"id": "P", "status": "same"},
        {"id": "Q", "status": "revised"},
    ]
    assert _data(seen["anonymous-select"]) == dict(
        lookup_context, receipts=["revised", "same"]
    )
    assert seen["anonymous-select"].prompt == swapped["anonymous-select"].prompt
    assert _data(seen["both-decide"]) == dict(
        lookup_context, record={"id": "P", "body": "new stock P"}
    )
    assert _data(swapped["both-decide"]) == dict(
        lookup_context, record={"id": "P", "body": "old stock P"}
    )
    assert outcome == {
        "note_status": "valid",
        "note_bytes": len(_NOTE.encode()),
        "note_sha256": hashlib.sha256(_NOTE.encode()).hexdigest(),
        "arms": {
            name: {
                "status": "complete",
                "lookup": "P",
                "decision_stage": "both-decide",
                "choices": {"P": "B", "Q": "A"},
            }
            for name in ("bound", "anonymous")
        },
    }


def test_handoff_distinct_reads_and_first_responses() -> None:
    note = json.dumps({"note": _NOTE})
    outcome, seen = _replay(
        _block("P", 1),
        {
            "note": note,
            "anonymous-select": '{"read":"Q"}',
            "bound-select": '{"read":"P"}',
            "anonymous-decide": '{"choices":{"Q":"A"}}',
            "bound-decide": None,
        },
    )
    assert _data(seen["anonymous-decide"])["record"] == {
        "id": "Q",
        "body": "old stock Q",
    }
    assert _data(seen["bound-decide"])["record"] == {"id": "P", "body": "new stock P"}
    assert outcome["arms"] == {
        "bound": {
            "status": "missing_decide",
            "lookup": "P",
            "decision_stage": "bound-decide",
            "choices": None,
        },
        "anonymous": {
            "status": "invalid_decide",
            "lookup": "Q",
            "decision_stage": "anonymous-decide",
            "choices": None,
        },
    }
    outcome, _ = _replay(
        _block("P", 0),
        {
            "note": note,
            "bound-select": '{"read":"P","read":"Q"}',
            "anonymous-select": None,
        },
    )
    assert outcome["arms"] == {
        name: {
            "status": status,
            "lookup": None,
            "decision_stage": None,
            "choices": None,
        }
        for name, status in (
            ("bound", "invalid_select"),
            ("anonymous", "missing_select"),
        )
    }
    outcome, _ = _replay(
        _block("P", 0),
        {
            "note": note,
            "bound-select": '{"read":"UNKNOWN"}',
            "anonymous-select": '{"read":"Q"}',
            "anonymous-decide": '{"choices":{"P":"A","Q":"A"}}',
        },
    )
    assert outcome["arms"] == {
        "bound": {
            "status": "invalid_select",
            "lookup": None,
            "decision_stage": None,
            "choices": None,
        },
        "anonymous": {
            "status": "complete",
            "lookup": "Q",
            "decision_stage": "anonymous-decide",
            "choices": {"P": "A", "Q": "A"},
        },
    }
    for raw, status in (
        (None, "missing"),
        ("", "invalid"),
        ('{"note":"bad\\nline"}', "invalid"),
        (json.dumps({"note": "é" * 129}), "invalid"),
    ):
        outcome, _ = _replay(_block("P", 0), {"note": raw})
        assert outcome == {
            "note_status": status,
            "note_bytes": None,
            "note_sha256": None,
            "arms": {
                name: {
                    "status": "no_note",
                    "lookup": None,
                    "decision_stage": None,
                    "choices": None,
                }
                for name in ("bound", "anonymous")
            },
        }
