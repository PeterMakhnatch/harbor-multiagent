from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from statistics import fmean
from typing import TYPE_CHECKING, Any

from ..coverage.analysis import _ROW_FIELDS, _check_receipt, _fields
from ..handoff import packet
from ..handoff.native_stock import _grade
from ..visibility.clarify_run import _encode, _is_digest

if TYPE_CHECKING:
    from ..handoff.native_input import Corpus
    from ..handoff.packet import Block

_SOURCE_SCHEMA = "seatlab.exchange-source/v1"
_REPORT_SCHEMA = "seatlab.exchange-report/v1"
_SOURCE_FIELDS = {
    "schema",
    "packet_sha256",
    "sources",
    "readers",
    "slots",
    "source_sha256",
}
_SLOT_FIELDS = {"index", "block", "case", "cluster", "rep", "opened", "notes"}
_NOTE_FIELDS = {"status", "note_sha256"}
_REPORT_FIELDS = {"schema", "source", "rows", "summary"}
_WRITERS = ("W0", "W1")
_READERS = ("R0", "R1")

type _Row = dict[str, Any]
type _Grader = Callable[[Block, dict[str, str]], dict[str, bool]]


@dataclass(frozen=True, slots=True)
class _Slot:
    block: Block
    opened: str
    ready: tuple[bool, bool]

    @property
    def unopened(self) -> int:
        return len(self.block.case.records) - 1


@dataclass(frozen=True, slots=True)
class _Score:
    opened: bool
    unopened: int


def _note_ready(note: object) -> bool:
    if not _fields(note, _NOTE_FIELDS):
        raise ValueError("invalid exchange note fields")
    status, digest = note["status"], note["note_sha256"]
    if status == "valid" and _is_digest(digest):
        return True
    if status in ("invalid", "missing") and digest is None:
        return False
    raise ValueError("invalid exchange note status or fingerprint")


def _selection(blocks: tuple[Block, ...], registry: object) -> list[_Slot]:
    if (
        not _fields(registry, _SOURCE_FIELDS)
        or registry["schema"] != _SOURCE_SCHEMA
        or registry["sources"] != list(_WRITERS)
        or registry["readers"] != list(_READERS)
        or not blocks
        or blocks != packet.grid(blocks[0].packet)
        or not _is_digest(blocks[0].fingerprint)
        or registry["packet_sha256"] != blocks[0].fingerprint
        or not _is_digest(registry["source_sha256"])
    ):
        raise ValueError("invalid exchange source register")
    slots = registry["slots"]
    if not isinstance(slots, list) or not 1 <= len(slots) <= 128:
        raise ValueError("invalid exchange selection size")
    previous = -1
    selected: list[_Slot] = []
    for slot in slots:
        if not _fields(slot, _SLOT_FIELDS):
            raise ValueError("invalid exchange slot fields")
        index = slot["index"]
        if type(index) is not int or not previous < index < len(blocks):
            raise ValueError("invalid exchange grid index or order")
        previous = index
        block = blocks[index]
        records = block.case.records
        opened = next(
            (record for record in records if record.old_version != record.new_version),
            records[0],
        ).id
        if (
            (slot["block"], slot["case"], slot["cluster"], slot["opened"])
            != (block.key, block.case.id, block.case.cluster, opened)
            or type(slot["rep"]) is not int
            or slot["rep"] != block.rep
        ):
            raise ValueError("invalid exchange slot binding or opened record")
        notes = slot["notes"]
        if not isinstance(notes, list) or len(notes) != 2:
            raise ValueError("invalid exchange note count")
        ready = (_note_ready(notes[0]), _note_ready(notes[1]))
        selected.append(_Slot(block, opened, ready))
    expected = sha256(
        _encode(
            {key: value for key, value in registry.items() if key != "source_sha256"}
        )
    ).hexdigest()
    if registry["source_sha256"] != expected:
        raise ValueError("exchange source fingerprint mismatch")
    return selected


def _evidence(slots: list[_Slot], source: str, rows: object) -> dict[int, _Row]:
    expected = {
        4 * position + 2 * writer + reader
        for position, slot in enumerate(slots)
        for writer, ready in enumerate(slot.ready)
        if ready
        for reader in range(2)
    }
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("exchange row count mismatch")
    evidence: dict[int, _Row] = {}
    for row in rows:
        if not _fields(row, _ROW_FIELDS):
            raise ValueError("invalid exchange row fields")
        number = row["slot"]
        if type(number) is not int or number not in expected:
            raise ValueError("unregistered or duplicate exchange row")
        expected.remove(number)
        if (
            row["run"] != f"cv-{source[:12]}-{number:03d}-checklist"
            or row["role"] != "route_decide"
            or row["policy"] != "checklist"
            or row["target"] is not None
        ):
            raise ValueError("invalid exchange row binding")
        block = slots[number // 4].block
        ids = tuple(record.id for record in block.case.records)
        _check_receipt(row, ids, block.packet.choices)
        peer = evidence.get(number ^ 1)
        if peer is not None and peer["prompt_sha256"] != row["prompt_sha256"]:
            raise ValueError("exchange readers do not share the same prompt")
        evidence[number] = row
    return evidence


def _packet_grade(block: Block, choices: dict[str, str]) -> dict[str, bool]:
    return {
        record.id: choices[record.id] == record.best for record in block.case.records
    }


def _scores(
    slots: list[_Slot], rows: dict[int, _Row], grade: _Grader
) -> dict[int, _Score]:
    results: dict[int, _Score] = {}
    for number, row in rows.items():
        if row["choices"] is None:
            continue
        slot = slots[number // 4]
        correctness = grade(slot.block, row["choices"])
        opened = correctness[slot.opened]
        results[number] = _Score(opened, sum(correctness.values()) - opened)
    return results


def _cell(
    slots: list[_Slot], rows: dict[int, _Row], scores: dict[int, _Score], cell: int
) -> _Row:
    calls: Counter[str] = Counter()
    delivered = opened_correct = unopened_correct = unopened_total = opportunities = 0
    for position, slot in enumerate(slots):
        number = 4 * position + cell
        opportunities += slot.unopened
        if number in rows:
            calls[rows[number]["status"]] += 1
        if number in scores:
            score = scores[number]
            delivered += 1
            opened_correct += score.opened
            unopened_correct += score.unopened
            unopened_total += slot.unopened
    return {
        "enrolled_blocks": len(slots),
        "valid_writer_notes": calls.total(),
        "received": calls["received"],
        "invalid": calls["received"] - delivered,
        "missing": calls["missing"],
        "callback_errors": calls["callback_error"],
        "delivered": delivered,
        "opened": {"correct": opened_correct, "total": delivered},
        "unopened": {"correct": unopened_correct, "total": unopened_total},
        "delivered_correct_unopened_per_enrolled": {
            "correct": unopened_correct,
            "total": opportunities,
            "fraction": unopened_correct / opportunities,
        },
    }


def _interval(value: float | None) -> tuple[float, float]:
    return (0.0, 1.0) if value is None else (value, value)


def _comparison(
    fractions: dict[int, float], worlds: dict[str, list[int]], left: int, right: int
) -> _Row:
    paired_worlds: dict[str, float] = {}
    lower: list[float] = []
    upper: list[float] = []
    gains = losses = ties = 0
    for world, positions in worlds.items():
        deltas: list[float] = []
        lows: list[float] = []
        highs: list[float] = []
        for position in positions:
            a = fractions.get(4 * position + left)
            b = fractions.get(4 * position + right)
            if a is not None and b is not None:
                delta = b - a
                deltas.append(delta)
                gains += delta > 0
                losses += delta < 0
                ties += delta == 0
            a_low, a_high = _interval(a)
            b_low, b_high = _interval(b)
            lows.append(b_low - a_high)
            highs.append(b_high - a_low)
        if deltas:
            paired_worlds[world] = fmean(deltas)
        lower.append(fmean(lows))
        upper.append(fmean(highs))
    return {
        "enrolled_pairs": sum(map(len, worlds.values())),
        "paired_worlds": len(paired_worlds),
        "contributing_worlds": sorted(paired_worlds),
        "complete_pairs": gains + losses + ties,
        "gains": gains,
        "losses": losses,
        "ties": ties,
        "paired_unopened_accuracy_delta": fmean(paired_worlds.values())
        if paired_worlds
        else None,
        "finite_missing_data_bounds": {"lower": fmean(lower), "upper": fmean(upper)},
    }


def _summary(
    slots: list[_Slot], rows: dict[int, _Row], scores: dict[int, _Score]
) -> _Row:
    worlds: dict[str, list[int]] = {}
    for position, slot in enumerate(slots):
        worlds.setdefault(slot.block.case.cluster, []).append(position)
    fractions = {
        number: score.unopened / slots[number // 4].unopened
        for number, score in scores.items()
    }
    return {
        "enrolled_blocks": len(slots),
        "enrolled_worlds": len(worlds),
        "cells": {
            f"{writer}:{reader}": _cell(slots, rows, scores, 2 * w + r)
            for w, writer in enumerate(_WRITERS)
            for r, reader in enumerate(_READERS)
        },
        "comparisons": {
            "W1_minus_W0": {
                reader: _comparison(fractions, worlds, r, r + 2)
                for r, reader in enumerate(_READERS)
            },
            "R1_minus_R0": {
                writer: _comparison(fractions, worlds, 2 * w, 2 * w + 1)
                for w, writer in enumerate(_WRITERS)
            },
        },
    }


def summarize(blocks: tuple[Block, ...], registry: _Row, rows: list[_Row]) -> _Row:
    slots = _selection(blocks, registry)
    evidence = _evidence(slots, registry["source_sha256"], rows)
    return _summary(slots, evidence, _scores(slots, evidence, _packet_grade))


def stock(corpus: Corpus, report: _Row) -> _Row:
    if (
        not isinstance(report, dict)
        or report.keys() - {"meter"} != _REPORT_FIELDS
        or report["schema"] != _REPORT_SCHEMA
    ):
        raise ValueError("invalid exchange report fields or schema")
    native = packet.parse(corpus.packet)
    ids = corpus.ids
    if (
        not 2 <= len(ids) <= 8
        or any(type(ship) is not int or ship < 0 for ship in ids)
        or len(ids) != len(set(ids))
        or not set("ABCD").issuperset(native.choices)
    ):
        raise ValueError("invalid native exchange records or choices")
    labels = tuple(f"S{ship}" for ship in ids)
    if corpus.worlds.keys() != {case.id for case in native.cases} or any(
        tuple(record.id for record in case.records) != labels for case in native.cases
    ):
        raise ValueError("packet records do not match the native exchange corpus")
    slots = _selection(packet.grid(native), report["source"])
    source = report["source"]["source_sha256"]
    rows = _evidence(slots, source, report["rows"])

    def grade(block: Block, choices: dict[str, str]) -> dict[str, bool]:
        return _grade(corpus.worlds[block.case.id], block.case, ids, choices)

    scores = _scores(slots, rows, grade)
    summary = _summary(slots, rows, scores)
    try:
        matches = _encode(report["summary"]) == _encode(summary)
    except (TypeError, ValueError, RecursionError):
        matches = False
    if not matches:
        raise ValueError("published exchange summary does not match native score")
    return {
        "packet_sha256": native.sha256,
        "source_sha256": source,
        "observed_worlds": len(
            {slots[number // 4].block.case.cluster for number in scores}
        ),
        "observed_dossiers": len(scores),
        "observed_native_choices": sum(
            slots[number // 4].unopened + 1 for number in scores
        ),
        "correct_native_choices": sum(
            score.opened + score.unopened for score in scores.values()
        ),
        "mismatches": 0,
        "summary": summary,
    }
