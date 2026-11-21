from dataclasses import dataclass, field
from hashlib import sha256
from itertools import islice
from os import scandir
from pathlib import Path
from typing import Any, cast

from ..handoff import journal
from ..handoff.episode import Turn, episode
from ..handoff.packet import Block
from ..visibility.clarify_run import StudyError, _encode
from ..visibility.route_audit import _block


@dataclass(frozen=True, slots=True)
class Entry:
    block: Block = field(repr=False)
    status: str
    prompt: str | None = field(repr=False)
    prompt_sha256: str | None


@dataclass(frozen=True, slots=True)
class Frozen:
    packet_sha256: str
    sha256: str
    entries: tuple[Entry, ...] = field(repr=False)


def _replay(fd: int, block: Block, remaining: set[str], row: dict[str, Any]) -> Entry:
    expected = iter(cast(list[dict[str, Any]], row["turns"]))
    halted = False
    finals: dict[str, tuple[str, str]] = {}

    def ask(turn: Turn) -> str | None:
        nonlocal halted
        name = f"{turn.stage}.json"
        if halted or name not in remaining:
            halted = True
            return None
        receipt = journal._evidence(fd, name)
        journal._verify(block, turn, receipt)
        if {key: receipt[key] for key in journal._PUBLIC_FIELDS} != next(
            expected, None
        ):
            raise StudyError("invalid_evidence")
        remaining.remove(name)
        if turn.stage in ("bound-decide", "both-decide"):
            finals[turn.stage] = (turn.prompt, cast(str, receipt["prompt_sha256"]))
        halted = receipt["status"] == "callback_error"
        return cast(str | None, receipt["text"])

    outcome = episode(block, ask)
    if remaining or next(expected, None) is not None:
        raise StudyError("invalid_evidence")
    bound = outcome["arms"]["bound"]
    lookup, stage = bound["lookup"], bound["decision_stage"]
    original = row["outcome"]["arms"]["bound"]
    if lookup != original["lookup"] or stage != original["decision_stage"]:
        raise StudyError("invalid_evidence")
    if outcome["note_status"] != "valid":
        return Entry(block, "no_note", None, None)
    if lookup is None:
        return Entry(block, "no_lookup", None, None)
    if stage not in finals:
        return Entry(block, "no_request", None, None)
    prompt, digest = finals[stage]
    return Entry(block, "ready", prompt, digest)


def _entry(root: int, block: Block, row: dict[str, Any]) -> Entry:
    with _block(root, block.key) as (fd, disk_state):
        if fd is None:
            if disk_state == "missing" and row["state"] == "unclaimed":
                return Entry(block, "no_note", None, None)
            raise StudyError("invalid_evidence")
        with scandir(fd) as files:
            remaining = {file.name for file in islice(files, 7)}
        if not remaining and row["state"] == "unbound":
            return Entry(block, "no_note", None, None)
        if (
            row["state"] != "claimed"
            or "claim.json" not in remaining
            or journal._evidence(fd, "claim.json") != journal._claim(block)
        ):
            raise StudyError("invalid_evidence")
        remaining.remove("claim.json")
        return _replay(fd, block, remaining, row)


def load(study: Path, blocks: tuple[Block, ...]) -> Frozen:
    rows = journal.collect(study, blocks)
    try:
        with journal._study(study) as root:
            entries = tuple(
                _entry(root, block, row)
                for block, row in zip(blocks, rows, strict=True)
            )
    except OSError:
        raise StudyError("study_io_failed") from None
    packet_sha256 = blocks[0].fingerprint
    slots = [
        {
            "block": entry.block.key,
            "case": entry.block.case.id,
            "cluster": entry.block.case.cluster,
            "rep": entry.block.rep,
            "status": entry.status,
            "prompt_sha256": entry.prompt_sha256,
        }
        for entry in entries
    ]
    digest = sha256(
        _encode(
            {
                "schema": "seatlab.coverage-source/v1",
                "packet_sha256": packet_sha256,
                "slots": slots,
            }
        )
    ).hexdigest()
    return Frozen(packet_sha256, digest, entries)
