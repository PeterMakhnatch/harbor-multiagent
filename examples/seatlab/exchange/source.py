import json
import os
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any

from ..coverage.jobs import _DATA, Request, checklist_instruction
from ..handoff import journal
from ..handoff.episode import _field, reader_context, writer_turn
from ..handoff.packet import Block, Record
from ..visibility.clarify_run import StudyError, _encode
from ..visibility.route_audit import _block


def _note(
    root: int, block: Block, row: dict[str, Any]
) -> tuple[dict[str, str | None], str | None]:
    outcome = row["outcome"]
    status = outcome["note_status"]
    if status in ("invalid", "missing"):
        return {"status": status, "note_sha256": None}, None
    if status != "valid" or row["state"] != "claimed" or not row["turns"]:
        raise StudyError("invalid_evidence")

    with _block(root, block.key) as (fd, _):
        if fd is None or journal._evidence(fd, "claim.json") != journal._claim(block):
            raise StudyError("invalid_evidence")
        receipt = journal._evidence(fd, "note.json")
    journal._verify(block, writer_turn(block), receipt)
    if (
        receipt["status"] != "received"
        or {key: receipt[key] for key in journal._PUBLIC_FIELDS} != row["turns"][0]
    ):
        raise StudyError("invalid_evidence")
    note = _field(receipt["text"], "note")
    if not isinstance(note, str) or not note or not note.isprintable():
        raise StudyError("invalid_evidence")
    raw = note.encode("utf-8")
    digest = sha256(raw).hexdigest()
    if len(raw) > 256 or digest != outcome["note_sha256"]:
        raise StudyError("invalid_evidence")
    return {"status": "valid", "note_sha256": digest}, note


def _checklist(block: Block, opened: Record, note: str, ids: tuple[str, ...]) -> str:
    data = reader_context(block, note)
    data["record"] = {"id": opened.id, "body": opened.after}
    return checklist_instruction(ids) + _DATA + json.dumps(data, ensure_ascii=False)


def _requests(
    prefix: str, selections: list[tuple[Block, Record, list[str | None]]]
) -> tuple[Request, ...]:
    requests: list[Request] = []
    for position, (block, opened, notes) in enumerate(selections):
        ids = tuple(r.id for r in block.case.records)
        prompts = tuple(
            _checklist(block, opened, note, ids) if note is not None else None
            for note in notes
        )
        cells = range(4) if position % 2 == 0 else range(3, -1, -1)
        for cell in cells:
            prompt = prompts[cell // 2]
            if prompt is not None:
                slot = 4 * position + cell
                requests.append(
                    Request(
                        f"cv-{prefix}-{slot:03d}-checklist",
                        slot,
                        "checklist",
                        None,
                        prompt,
                        ids,
                        block.packet.choices,
                    )
                )
    return tuple(requests)


def build(
    blocks: tuple[Block, ...], writers: tuple[Path, Path], indices: tuple[int, ...]
) -> tuple[dict[str, Any], tuple[Request, ...]]:
    if (
        type(indices) is not tuple
        or not 1 <= len(indices) <= 128
        or any(
            type(index) is not int or not 0 <= index < len(blocks) for index in indices
        )
        or any(a >= b for a, b in pairwise(indices))
    ):
        raise StudyError("invalid_indices")
    first, second = writers
    slots: list[dict[str, Any]] = []
    selections: list[tuple[Block, Record, list[str | None]]] = []
    try:
        with journal._study(first) as left, journal._study(second) as right:
            if os.path.samestat(os.fstat(left), os.fstat(right)):
                raise StudyError("duplicate_source")
            sources = (
                (left, journal.collect(first, blocks)),
                (right, journal.collect(second, blocks)),
            )
            for index in indices:
                block = blocks[index]
                records = block.case.records
                opened = next(
                    (r for r in records if r.old_version != r.new_version), records[0]
                )
                notes = [_note(root, block, rows[index]) for root, rows in sources]
                slots.append(
                    {
                        "index": index,
                        "block": block.key,
                        "case": block.case.id,
                        "cluster": block.case.cluster,
                        "rep": block.rep,
                        "opened": opened.id,
                        "notes": [metadata for metadata, _ in notes],
                    }
                )
                selections.append((block, opened, [text for _, text in notes]))
    except OSError:
        raise StudyError("study_io_failed") from None

    registry = {
        "schema": "seatlab.exchange-source/v1",
        "packet_sha256": blocks[0].fingerprint,
        "sources": ["W0", "W1"],
        "readers": ["R0", "R1"],
        "slots": slots,
    }
    digest = sha256(_encode(registry)).hexdigest()
    return registry | {"source_sha256": digest}, _requests(digest[:12], selections)
