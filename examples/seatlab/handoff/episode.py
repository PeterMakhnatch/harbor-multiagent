import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from .packet import Block


@dataclass(frozen=True)
class Turn:
    stage: str
    role: str
    prompt: str


class _Arm(TypedDict):
    status: str
    lookup: str | None
    decision_stage: str | None
    choices: dict[str, str] | None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate answer key")
        result[key] = value
    return result


def _field(text: str | None, key: str) -> object:
    if text is None:
        return None
    try:
        value = json.loads(text, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        return None
    return value[key] if isinstance(value, dict) and value.keys() == {key} else None


def _turn(stage: str, role: str, instruction: str, data: dict[str, object]) -> Turn:
    return Turn(
        stage, role, instruction + "\n\nData:\n" + json.dumps(data, ensure_ascii=False)
    )


def writer_turn(block: Block) -> Turn:
    return _turn(
        "note",
        "route_review",
        'Write a handoff note for a replacement agent. Return only {"note":"..."}. '
        "Use a nonempty printable string of at most 256 UTF-8 bytes.",
        {
            "brief": block.packet.brief,
            "records": [{"id": r.id, "body": r.before} for r in block.case.records],
        },
    )


def reader_context(block: Block, note: str) -> dict[str, object]:
    return {
        "brief": block.packet.brief,
        "note": note,
        "questions": [{"id": r.id, "question": r.question} for r in block.case.records],
        "options": block.packet.choices,
    }


def episode(block: Block, ask: Callable[[Turn], str | None]) -> dict[str, Any]:
    packet = block.packet
    records = {record.id: record for record in block.case.records}
    arms: dict[str, _Arm] = {
        name: {
            "status": "no_note",
            "lookup": None,
            "decision_stage": None,
            "choices": None,
        }
        for name in ("bound", "anonymous")
    }
    state = {
        "note_status": "missing",
        "note_bytes": None,
        "note_sha256": None,
        "arms": arms,
    }
    raw = ask(writer_turn(block))
    if raw is None:
        return state
    state["note_status"] = "invalid"
    note = _field(raw, "note")
    if not isinstance(note, str) or not note or not note.isprintable():
        return state
    note_bytes = note.encode("utf-8")
    if len(note_bytes) > 256:
        return state
    state.update(
        note_status="valid",
        note_bytes=len(note_bytes),
        note_sha256=hashlib.sha256(note_bytes).hexdigest(),
    )

    context = reader_context(block, note)
    statuses = {
        r.id: "same" if r.old_version == r.new_version else "revised"
        for r in records.values()
    }
    order = (
        ("bound", "anonymous")
        if (block.index + block.rep) % 2 == 0
        else ("anonymous", "bound")
    )
    selected: dict[str, list[str]] = {}
    for name in order:
        receipts = (
            [{"id": key, "status": status} for key, status in statuses.items()]
            if name == "bound"
            else sorted(statuses.values())
        )
        raw = ask(
            _turn(
                name + "-select",
                "route_decide",
                "Choose one record to read before answering the questions. Receipts show whether a "
                'record was revised since the note. Return only {"read":"record ID"}.',
                dict(context, receipts=receipts),
            )
        )
        lookup = _field(raw, "read")
        if isinstance(lookup, str) and lookup in records:
            arms[name]["lookup"] = lookup
            selected.setdefault(lookup, []).append(name)
        else:
            arms[name]["status"] = "missing_select" if raw is None else "invalid_select"

    for lookup, names in selected.items():
        stage = "both-decide" if len(names) == 2 else names[0] + "-decide"
        raw = ask(
            _turn(
                stage,
                "route_decide",
                "Use the note and the current record to answer all the questions. Return only a JSON "
                'object with one field, "choices", mapping each record ID to one of the options.',
                dict(context, record={"id": lookup, "body": records[lookup].after}),
            )
        )
        choices = _field(raw, "choices")
        if (
            not isinstance(choices, dict)
            or choices.keys() != records.keys()
            or not all(
                isinstance(choice, str) and choice in packet.choices
                for choice in choices.values()
            )
        ):
            choices = None
        status = (
            "complete"
            if choices is not None
            else "missing_decide"
            if raw is None
            else "invalid_decide"
        )
        for name in names:
            arms[name]["decision_stage"] = stage
            arms[name]["status"] = status
            arms[name]["choices"] = choices
    return state
