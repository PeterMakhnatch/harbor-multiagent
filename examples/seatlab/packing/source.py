from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from ..handoff import journal
from ..handoff.episode import Turn, _field, episode
from ..handoff.packet import Block
from ..visibility.clarify_run import StudyError, _encode
from ..visibility.route_audit import _block


@dataclass(frozen=True, slots=True)
class Entry:
    block: Block = field(repr=False)
    status: str
    response_sha256: str | None
    note: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class Frozen:
    packet_sha256: str
    sha256: str
    entries: tuple[Entry, ...] = field(repr=False)


def _writer_turn(block: Block) -> Turn:
    turns: list[Turn] = []
    episode(block, turns.append)
    if len(turns) != 1 or turns[0].stage != "note":
        raise StudyError("invalid_evidence")
    return turns[0]


def _received(root: int, block: Block, first: dict[str, Any]) -> Entry:
    with _block(root, block.key) as (fd, _):
        if fd is None or journal._evidence(fd, "claim.json") != journal._claim(block):
            raise StudyError("invalid_evidence")
        receipt = journal._evidence(fd, "note.json")
        journal._verify(block, _writer_turn(block), receipt)
        if {key: receipt[key] for key in journal._PUBLIC_FIELDS} != first:
            raise StudyError("invalid_evidence")

    note = _field(cast(str, receipt["text"]), "note")
    digest = cast(str, receipt["response_sha256"])
    if not isinstance(note, str) or not note or not note.isprintable():
        return Entry(block, "invalid", digest, None)
    return Entry(block, "usable", digest, note)


def load(study: Path, blocks: tuple[Block, ...]) -> Frozen:
    rows = journal.collect(study, blocks)
    packet_sha256 = blocks[0].fingerprint
    entries: list[Entry] = []
    slots: list[dict[str, object]] = []
    try:
        with journal._study(study) as root:
            for block, row in zip(blocks, rows, strict=True):
                first = row["turns"][0] if row["turns"] else None
                if first is None or first["status"] == "callback_error":
                    entry = Entry(block, "missing", None, None)
                elif row["state"] == "claimed" and first["status"] == "received":
                    entry = _received(root, block, first)
                else:
                    raise StudyError("invalid_evidence")
                entries.append(entry)
                slots.append(
                    {
                        "block": row["block"],
                        "case": row["case"],
                        "cluster": row["cluster"],
                        "rep": row["rep"],
                        "state": row["state"],
                        "status": entry.status,
                        "writer": first,
                    }
                )
    except OSError:
        raise StudyError("study_io_failed") from None

    digest = sha256(
        _encode(
            {
                "schema": "seatlab.packing-source/v1",
                "packet_sha256": packet_sha256,
                "slots": slots,
            }
        )
    ).hexdigest()
    return Frozen(packet_sha256, digest, tuple(entries))
