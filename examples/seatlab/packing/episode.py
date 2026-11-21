from collections.abc import Callable
from hashlib import sha256
from typing import Any, TypedDict, cast

from ..handoff.episode import Turn, _field, _turn
from ..handoff.packet import Block
from .source import Entry

_LIMIT = 96


class _Arm(TypedDict):
    status: str
    packed_bytes: int | None
    packed_sha256: str | None
    decision_stage: str | None
    choices: dict[str, str] | None


def _empty_arm(status: str) -> _Arm:
    return {
        "status": status,
        "packed_bytes": None,
        "packed_sha256": None,
        "decision_stage": None,
        "choices": None,
    }


def _note_bytes(value: object) -> bytes | None:
    if not isinstance(value, str) or not value or not value.isprintable():
        return None
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _prefix(note: bytes) -> bytes:
    if len(note) <= _LIMIT:
        return note
    end = _LIMIT
    while note[end] & 0xC0 == 0x80:
        end -= 1
    return note[:end]


def _decide(
    block: Block, ask: Callable[[Turn], str | None], stage: str, packed: bytes
) -> _Arm:
    records = block.case.records
    options = block.packet.choices
    revised = next((r for r in records if r.old_version != r.new_version), None)
    raw = ask(
        _turn(
            stage,
            "route_decide",
            "Use the note and the current record, if present, to answer all questions. "
            'Return only a JSON object with one field, "choices", mapping each record ID '
            "to one of the options.",
            {
                "brief": block.packet.brief,
                "note": packed.decode("utf-8"),
                "questions": [{"id": r.id, "question": r.question} for r in records],
                "options": options,
                "record": (
                    {"id": revised.id, "body": revised.after}
                    if revised is not None
                    else None
                ),
            },
        )
    )
    value = _field(raw, "choices")
    choices = (
        cast(dict[str, str], value)
        if isinstance(value, dict)
        and value.keys() == {r.id for r in records}
        and all(
            isinstance(choice, str) and choice in options for choice in value.values()
        )
        else None
    )
    status = (
        "complete"
        if choices is not None
        else "missing_decide"
        if raw is None
        else "invalid_decide"
    )
    return {
        "status": status,
        "packed_bytes": len(packed),
        "packed_sha256": sha256(packed).hexdigest(),
        "decision_stage": stage,
        "choices": choices,
    }


def episode(entry: Entry, ask: Callable[[Turn], str | None]) -> dict[str, Any]:
    block = entry.block
    note = _note_bytes(entry.note) if entry.status == "usable" else None
    arms = {name: _empty_arm("no_source") for name in ("prefix", "rewrite")}
    outcome = {
        "source_status": entry.status,
        "source_note_bytes": len(note) if note is not None else None,
        "source_note_sha256": sha256(note).hexdigest() if note is not None else None,
        "arms": arms,
    }
    if note is None:
        return outcome

    prefix = _prefix(note)
    rewritten: bytes | None = note
    if len(note) > _LIMIT:
        raw = ask(
            _turn(
                "rewrite",
                "route_review",
                f"Pack the note into at most {_LIMIT} UTF-8 bytes. Preserve identifiers "
                'and values. Return only {"note":"..."} with a nonempty printable note.',
                {"note": entry.note},
            )
        )
        rewritten = _note_bytes(_field(raw, "note"))
        if rewritten is None:
            arms["rewrite"] = _empty_arm(
                "missing_rewrite" if raw is None else "invalid_rewrite"
            )
        else:
            rewritten = _prefix(rewritten)

    if prefix == rewritten:
        shared = _decide(block, ask, "both-decide", prefix)
        arms["prefix"] = shared
        arms["rewrite"] = shared.copy()
    else:
        order = (
            ("prefix", "rewrite")
            if (block.index + block.rep) % 2 == 0
            else ("rewrite", "prefix")
        )
        for name in order:
            packed = prefix if name == "prefix" else rewritten
            if packed is not None:
                arms[name] = _decide(block, ask, name + "-decide", packed)
    return outcome
