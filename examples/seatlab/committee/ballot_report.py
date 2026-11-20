"""Verify and summarize the registered peer-ballot study without private text."""

import argparse
import errno
import json
import os
import stat
from collections import Counter
from pathlib import Path
from statistics import mean

from ..visibility import deepseek
from . import analysis, ballot_packet, contract
from .ballot_schedule import ARMS, Cell, grid

_RECEIPT_SCHEMA = "seatlab.committee-peer-turn/v1"
_RECEIPT_FIELDS = {
    "schema",
    "cell_id",
    "packet_sha256",
    "status",
    "prompt_sha256",
    "served_model",
    "response_sha256",
    "response_text",
    "ballot",
}
_INCOMPLETE = ("started", "transport_failed", "identity_failed", "budget_unresolved")
_WITH_RESPONSE = ("complete", "invalid_ballot")
_STATUSES = (*_INCOMPLETE, *_WITH_RESPONSE, "evidence_invalid")
_MODELS = (deepseek.MODEL, deepseek.REVISION, deepseek.REVISION.lower())
_BLOCKS = (0, 1)
_MAPS = range(len(analysis._LABELS))
_SEATS = range(len(analysis._ROLES))
_SEAT_MEASURES = (
    "correct",
    "switched",
    "adopted_peer_plurality",
    "switched_from_correct_to_wrong",
)
_TEAM_MEASURES = ("strict_group_majority", "correct_group_majority")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
_RECEIPT_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
# Keep enough space for the escaped provider response, but bound file reads.
_MAX_RECEIPT_BYTES = 512 * 1024


def _path_error(exc: OSError) -> str:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return "unsafe_evidence_path"
    return "receipt_unreadable"


def _read_receipt(directory: int, cell: Cell) -> dict:
    fd = os.open(f"{cell.cell_id}.json", _RECEIPT_FLAGS, dir_fd=directory)
    try:
        info = os.fstat(fd)
        analysis._require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "unsafe_evidence_path",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
    finally:
        os.close(fd)
    analysis._require(len(raw) <= _MAX_RECEIPT_BYTES, "receipt_too_large")
    return contract._read_json(raw.decode("utf-8"), _RECEIPT_FIELDS, "receipt")


def _receipt(directory: int, packet: dict, packet_sha: str, cell: Cell) -> dict:
    require = analysis._require
    row = _read_receipt(directory, cell)
    require(row["schema"] == _RECEIPT_SCHEMA, "receipt_schema_invalid")
    require(row["cell_id"] == cell.cell_id, "cell_id_mismatch")
    require(row["packet_sha256"] == packet_sha, "packet_hash_mismatch")
    status = row["status"]
    require(status in _STATUSES, "receipt_status_invalid")

    prompt = ballot_packet.prompt(packet, cell.map_index, cell.seat_index, cell.arm)
    prompt_sha = analysis._sha(prompt)
    require(row["prompt_sha256"] == prompt_sha, "prompt_hash_mismatch")
    text, model = row["response_text"], row["served_model"]
    response_sha, ballot = None, None
    if status in _WITH_RESPONSE:
        require(model in _MODELS, "served_model_invalid")
        require(isinstance(text, str), "response_text_invalid")
        response_sha = analysis._sha(text)
        require(row["response_sha256"] == response_sha, "response_hash_mismatch")
        try:
            ballot = ballot_packet.decode(packet, cell.map_index, cell.seat_index, text)
        except ballot_packet.InvalidBallot:
            require(status == "invalid_ballot", "response_ballot_invalid")
        else:
            require(status == "complete", "invalid_ballot_decodes")
    else:
        require(
            text is None and model is None and row["response_sha256"] is None,
            "unexpected_response_evidence",
        )
    require(row["ballot"] == ballot, "ballot_mismatch")
    classification = "invalid"
    if status == "complete":
        classification = "complete"
    elif status in _INCOMPLETE:
        classification = "incomplete"
    return {
        "status": classification,
        "reason": None if status == "complete" else status,
        "prompt_sha256": prompt_sha,
        "served_model": model,
        "response_sha256": response_sha,
        "second": ballot,
    }


def _cell(directory: int | str, packet: dict, packet_sha: str, cell: Cell) -> dict:
    receipt = dict.fromkeys(
        ("prompt_sha256", "served_model", "response_sha256", "second")
    )
    if isinstance(directory, str):
        receipt.update(
            status="missing" if directory == "receipt_missing" else "invalid",
            reason=directory,
        )
    else:
        try:
            receipt = _receipt(directory, packet, packet_sha, cell)
        except FileNotFoundError:
            receipt.update(status="missing", reason="receipt_missing")
        except analysis._Excluded as exc:
            receipt.update(status="invalid", reason=exc.reason)
        except OSError as exc:
            receipt.update(status="invalid", reason=_path_error(exc))
        except (ValueError, RecursionError):
            receipt.update(status="invalid", reason="receipt_malformed")

    seats = packet["teams"][cell.map_index]["seats"]
    first = seats[cell.seat_index]["first"]
    peers = Counter(
        seats[seat]["first"]["choice"] for seat in _SEATS if seat != cell.seat_index
    )
    plurality, count = peers.most_common(1)[0]
    unique = count >= 2
    if not unique:
        eligibility = "tied_peers"
    elif first["choice"] == plurality:
        eligibility = "already_agrees"
    else:
        eligibility = "eligible"
    gold = analysis._LABELS[cell.map_index][0]
    initial_correct = first["choice"] == gold
    choice = receipt["second"]["choice"] if receipt["second"] is not None else None
    return {
        "cell_id": cell.cell_id,
        "block": cell.block,
        "map_index": cell.map_index,
        "seat": f"S{cell.seat_index + 1}",
        "arm": cell.arm,
        **receipt,
        "first": first,
        "initial_correct": initial_correct,
        "peer_plurality": {
            "status": "unique" if unique else "tie",
            "choice": plurality if unique else None,
        },
        "adoption_eligibility": eligibility,
        "correct": choice == gold if choice is not None else None,
        "switched": choice != first["choice"] if choice is not None else None,
        "adopted_peer_plurality": (
            choice == plurality
            if choice is not None and eligibility == "eligible"
            else None
        ),
        "switched_from_correct_to_wrong": (
            choice != gold if choice is not None and initial_correct else None
        ),
    }


def _read_cells(study: Path, packet: dict, packet_sha: str) -> dict[Cell, dict]:
    root = os.open(study, _DIRECTORY_FLAGS)
    directory: int | str
    try:
        try:
            directory = os.open("cells", _DIRECTORY_FLAGS, dir_fd=root)
        except FileNotFoundError:
            directory = "receipt_missing"
        except OSError as exc:
            directory = _path_error(exc)
    finally:
        os.close(root)
    try:
        return {cell: _cell(directory, packet, packet_sha, cell) for cell in grid()}
    finally:
        if isinstance(directory, int):
            os.close(directory)


def _group(block: int, index: int, arm: str, cells: dict[Cell, dict]) -> dict:
    votes = Counter(
        row["second"]["choice"]
        for seat in _SEATS
        if (row := cells[Cell(block, index, seat, arm)])["second"] is not None
    )
    observed = sum(votes.values())
    complete = observed == len(_SEATS)
    majority = (
        next((choice for choice, count in votes.items() if count >= 3), None)
        if complete
        else None
    )
    return {
        "block": block,
        "map_index": index,
        "arm": arm,
        "observed_seats": observed,
        "majority_choice": majority,
        "strict_group_majority": majority is not None if complete else None,
        "correct_group_majority": majority == analysis._LABELS[index][0]
        if complete
        else None,
    }


def _counts(values: list[bool]) -> dict:
    return {"numerator": sum(values), "denominator": len(values)}


def _arm_summary(cells: list[dict], groups: list[dict]) -> dict:
    return {
        "scheduled_cells": len(cells),
        "scheduled_groups": len(groups),
        "receipt_status": dict(Counter(row["status"] for row in cells)),
        "peer_plurality_status": dict(
            Counter(row["peer_plurality"]["status"] for row in cells)
        ),
        "adoption_eligibility": dict(
            Counter(row["adoption_eligibility"] for row in cells)
        ),
        "measures": {
            key: _counts([row[key] for row in rows if row[key] is not None])
            for rows, measures in ((cells, _SEAT_MEASURES), (groups, _TEAM_MEASURES))
            for key in measures
        },
    }


def _paired_map(
    seats: list[tuple[dict, dict]], groups: list[tuple[dict, dict]]
) -> dict:
    measures = {}
    for rows, keys in ((seats, _SEAT_MEASURES), (groups, _TEAM_MEASURES)):
        for key in keys:
            pairs = [
                (left[key], right[key])
                for left, right in rows
                if left[key] is not None and right[key] is not None
            ]
            n = len(pairs)
            left = sum(a for a, _ in pairs)
            right = sum(b for _, b in pairs)
            measures[key] = {
                "paired_denominator": n,
                "treatment_numerator": left,
                "comparison_numerator": right,
                "effect": (left - right) / n if n else None,
            }
    return measures


def _contrast(
    treatment: str,
    comparison: str,
    cells: dict[Cell, dict],
    groups: dict[tuple[int, int, str], dict],
) -> dict:
    def summarize(blocks: tuple[int, ...]) -> dict:
        maps = []
        for index in _MAPS:
            seat_pairs = [
                (
                    cells[Cell(block, index, seat, treatment)],
                    cells[Cell(block, index, seat, comparison)],
                )
                for block in blocks
                for seat in _SEATS
            ]
            group_pairs = [
                (groups[block, index, treatment], groups[block, index, comparison])
                for block in blocks
            ]
            maps.append(
                {"map_index": index, "measures": _paired_map(seat_pairs, group_pairs)}
            )
        measures = {}
        for key in (*_SEAT_MEASURES, *_TEAM_MEASURES):
            rows = [row["measures"][key] for row in maps]
            effects = [row["effect"] for row in rows if row["paired_denominator"]]
            measures[key] = {
                field: sum(row[field] for row in rows)
                for field in (
                    "paired_denominator",
                    "treatment_numerator",
                    "comparison_numerator",
                )
            } | {
                "supported_maps": len(effects),
                "effect": mean(effects) if len(effects) == len(_MAPS) else None,
            }
        return {"maps": maps, "measures": measures}

    return {
        "treatment_arm": treatment,
        "comparison_arm": comparison,
        **summarize(_BLOCKS),
        "by_block": [{"block": block, **summarize((block,))} for block in _BLOCKS],
    }


def analyze(packet_path: Path, study: Path, *, expected_sha256: str) -> dict:
    """Read only registered receipts and emit an offline, path-free report."""
    packet = ballot_packet.read_packet(packet_path, expected_sha256)
    cells = _read_cells(study, packet, expected_sha256)
    groups = {
        (block, index, arm): _group(block, index, arm, cells)
        for block in _BLOCKS
        for index in _MAPS
        for arm in ARMS
    }
    by_arm = {}
    for arm in ARMS:
        seats = [row for row in cells.values() if row["arm"] == arm]
        teams = [row for row in groups.values() if row["arm"] == arm]
        by_arm[arm] = _arm_summary(seats, teams) | {
            "by_block": [
                {
                    "block": block,
                    **_arm_summary(
                        [row for row in seats if row["block"] == block],
                        [row for row in teams if row["block"] == block],
                    ),
                }
                for block in _BLOCKS
            ],
        }
    return {
        "schema": "seatlab.committee-peer-ballot-report/v1",
        "task_git_tree": packet["task_git_tree"],
        "packet_sha256": expected_sha256,
        "summary": {
            "primary_contrast": "choices_minus_private",
            "primary_measure": "correct",
            "secondary_contrast": "cited_minus_choices",
            "weighting": "equal_label_map_means_with_all_six_maps_supported",
            "interpretation": "Two blocks and six label maps of one latent vendor problem.",
            "group_measure": "Derived second-ballot majority, not a native trial reward.",
            "peer_measure": (
                "Peer plurality uses original ballots, including those hidden in the private arm."
            ),
            "evidence": (
                "The packet is a trusted-source commitment. "
                "The receipt hashes do not authenticate the provider."
            ),
            "scheduled_cells": len(cells),
            "by_arm": by_arm,
        },
        "cells": list(cells.values()),
        "groups": list(groups.values()),
        "contrasts": {
            "choices_minus_private": _contrast("choices", "private", cells, groups),
            "cited_minus_choices": _contrast("cited", "choices", cells, groups),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("study", type=Path)
    parser.add_argument("--expect-sha256", required=True)
    args = parser.parse_args()
    try:
        report = analyze(args.packet, args.study, expected_sha256=args.expect_sha256)
    except (OSError, ValueError, RecursionError):
        parser.error("the committed packet or study root is invalid or unavailable")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
