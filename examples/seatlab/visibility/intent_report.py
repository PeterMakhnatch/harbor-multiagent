"""Verify the registered box intentions study and report fixed-prefix choices offline."""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, TypedDict

from . import boxes_report, boxes_run, intent_packet, intent_protocol, intent_run

_ARMS = ("masked", "shown")
_COLLISIONS = "excess_new_collisions"
_METRICS = (
    "individually_new",
    "distinct_new",
    _COLLISIONS,
    "historical_repeats",
    "changed_proposals",
)
_RECEIPT = Path("receipt.json")


@dataclass(frozen=True, slots=True)
class _Ledger:
    rows: dict[str, sqlite3.Row] | None
    unexpected: set[str]
    unexpected_rows: int

    @classmethod
    def read(cls, spend_dir: Path, registered: set[str]) -> "_Ledger":
        records = boxes_report._ledger_records(
            spend_dir, prefix="bi-", role=intent_run.ROLE
        )
        if records is None:
            return cls(None, set(), 0)
        rows: dict[str, sqlite3.Row] = {}
        unexpected: set[str] = set()
        unexpected_rows = 0
        for row in records:
            run = row["run"]
            if run not in registered:
                unexpected_rows += 1
            elif row["role"] != intent_run.ROLE or run in rows:
                unexpected.add(run)
                unexpected_rows += 1
            else:
                rows[run] = row
        return cls(rows, unexpected, unexpected_rows)


@contextmanager
def _cells(study: Path) -> Generator[int | None, None, None]:
    study = study.expanduser().absolute()
    boxes_run._require_absolute(study)
    with boxes_run._directory(study) as root:
        boxes_run._require_private(os.fstat(root), directory=True)
        try:
            cells = os.open("cells", boxes_run._DIRECTORY, dir_fd=root)
        except FileNotFoundError:
            yield None
            return
        try:
            boxes_run._require_private(os.fstat(cells), directory=True)
            yield cells
        finally:
            os.close(cells)


@dataclass(frozen=True, slots=True)
class _Call:
    run: str
    status: str
    spend: str
    claim_present: bool
    receipt_present: bool | None
    receipt_status: str | None = None
    box: int | None = None
    reserved: datetime | None = None
    finished: datetime | None = None

    def unexpected(self) -> "_Call":
        return replace(self, status="unexpected", box=None)

    def public(self) -> dict[str, str | bool | None]:
        return {
            "id": self.run,
            "status": self.status,
            "spend": self.spend,
            "claim_present": self.claim_present,
            "receipt_present": self.receipt_present,
            "receipt_status": self.receipt_status,
        }


def _inspect(
    cells: int | None,
    claims: set[str],
    ledger: _Ledger,
    run: str,
    prompt: str | None,
    numbering: int,
) -> _Call:
    row = ledger.rows.get(run) if ledger.rows is not None else None
    claimed = run in claims
    reserved = finished = None
    if run in ledger.unexpected:
        state = "unexpected"
    else:
        try:
            state = boxes_report._spend_state(row, ledger.rows is not None)
            if row is not None:
                reserved = boxes_report._timestamp(row["reserved_at"])
                if state == "settled":
                    finished = boxes_report._timestamp(row["finished_at"])
        except ValueError:
            state = "invalid"
    call = _Call(
        run, "unattempted", state, claimed, False, reserved=reserved, finished=finished
    )
    if claimed and cells is not None:
        try:
            directory = os.open(run, boxes_run._DIRECTORY, dir_fd=cells)
            try:
                boxes_run._require_private(os.fstat(directory), directory=True)
                files = set(os.listdir(directory))
                call = replace(call, receipt_present=_RECEIPT.name in files)
                if files - {_RECEIPT.name}:
                    return call.unexpected()
                if (
                    prompt is not None
                    and call.receipt_present is True
                    and state not in {"invalid", "unexpected"}
                ):
                    verified = boxes_report._replay(
                        boxes_report._receipt(_RECEIPT, directory=directory),
                        expected_run=run,
                        expected_role=intent_run.ROLE,
                        expected_prompt=prompt,
                        expected_digest=intent_packet.PACKET_SHA256,
                        numbering=numbering,
                        row=row,
                        state=state,
                    )
                    return replace(
                        call,
                        status=verified.status,
                        receipt_status=verified.receipt_status,
                        box=verified.original_box,
                    )
            finally:
                os.close(directory)
        except (OSError, ValueError):
            return replace(
                call,
                status="unexpected" if state == "unexpected" else "invalid_evidence",
                receipt_present=True if call.receipt_present is True else None,
            )
    if state == "unexpected" or (prompt is None and (claimed or row is not None)):
        return call.unexpected()
    if state == "invalid":
        return replace(call, status="invalid_evidence")
    if state == "held":
        return replace(call, status="held")
    if claimed or row is not None:
        return replace(call, status="receipt_missing")
    return call


_Calls = tuple[_Call, _Call, _Call]


def _choices(calls: _Calls) -> intent_protocol.Choices | None:
    a, b, c = (call.box for call in calls)
    if a is None or b is None or c is None:
        return None
    return a, b, c


def _out_of_order(calls: Sequence[_Call]) -> set[str]:
    """Use settled finish and reservation times; equal times do not prove an inversion."""
    latest = None
    unexpected: set[str] = set()
    for call in calls:
        if call.reserved is not None:
            if latest is not None and call.reserved < latest:
                unexpected.add(call.run)
            end = call.finished or call.reserved
            if latest is None or end > latest:
                latest = end
    return unexpected


@dataclass(frozen=True, slots=True)
class _Team:
    choices: intent_protocol.Choices
    metrics: dict[str, int]

    def public(self) -> dict[str, list[int] | dict[str, int]]:
        return {"choices": list(self.choices), "metrics": self.metrics}


@dataclass(frozen=True, slots=True)
class _Block:
    block: intent_protocol.Block
    calls: dict[str, _Calls]
    barrier: str
    order_violations: set[str]
    proposals: _Team | None
    teams: dict[str, _Team]

    def delta(self) -> dict[str, int] | None:
        if len(self.teams) != len(_ARMS):
            return None
        return {
            name: self.teams["shown"].metrics[name] - self.teams["masked"].metrics[name]
            for name in _METRICS
        }

    def public(self) -> dict[str, Any]:
        block = self.block
        return {
            "id": block.id,
            "instance": block.instance,
            "numbering": block.numbering,
            "rotation": block.rotation,
            "repetition": block.repetition,
            "arm_order": list(block.arms),
            "proposal_barrier": self.barrier,
            "order_violations": sorted(self.order_violations),
            "proposals": self.proposals.public()
            if self.proposals is not None
            else None,
            "arms": {
                arm: self.teams[arm].public() if arm in self.teams else None
                for arm in _ARMS
            },
            "calls": {
                stage: {
                    label: call.public()
                    for label, call in zip(intent_protocol.LABELS, team, strict=True)
                }
                for stage, team in self.calls.items()
            },
            "shown_minus_masked": self.delta(),
        }


def _block(
    world: intent_packet.World,
    block: intent_protocol.Block,
    cells: int | None,
    claims: set[str],
    ledger: _Ledger,
) -> _Block:
    def read_stage(
        stage: str, proposals: intent_protocol.Choices | None = None
    ) -> _Calls:
        def read_seat(label: int) -> _Call:
            prompt = (
                intent_protocol.prompt(world, block, label, stage, proposals)
                if stage == "proposal" or proposals is not None
                else None
            )
            return _inspect(
                cells,
                claims,
                ledger,
                intent_protocol.call_id(block, label, stage),
                prompt,
                block.numbering,
            )

        return read_seat(0), read_seat(1), read_seat(2)

    proposed = read_stage("proposal")
    proposal_order = _out_of_order(proposed)
    choices = _choices(proposed) if not proposal_order else None
    revisions = {arm: read_stage(arm, choices) for arm in block.arms}
    revised = [call for calls in revisions.values() for call in calls]
    order_violations = _out_of_order((*proposed, *revised))
    last_proposal = max(
        (call.finished for call in proposed if call.finished is not None), default=None
    )
    premature = last_proposal is not None and any(
        call.reserved is not None and call.reserved < last_proposal for call in revised
    )
    barrier = (
        "violated" if premature else "incomplete" if choices is None else "verified"
    )

    def ordered(stage: _Calls) -> _Calls:
        a, b, c = (
            call.unexpected() if call.run in order_violations else call
            for call in stage
        )
        return a, b, c

    calls = {
        "proposal": ordered(proposed),
        **{arm: ordered(team) for arm, team in revisions.items()},
    }
    proposals = (
        _Team(choices, intent_protocol.native_metrics(world, choices))
        if choices is not None
        else None
    )
    teams: dict[str, _Team] = {}
    if barrier == "verified" and choices is not None and not order_violations:
        for arm in _ARMS:
            final = _choices(calls[arm])
            if final is not None:
                metrics = intent_protocol.native_metrics(world, final)
                metrics["changed_proposals"] = sum(
                    before != after
                    for before, after in zip(choices, final, strict=True)
                )
                teams[arm] = _Team(final, metrics)
    return _Block(block, calls, barrier, order_violations, proposals, teams)


class _WorldContrast(TypedDict):
    instance: int
    possible_pairs: int
    complete_pairs: int
    shown_minus_masked_numerator: dict[str, int]
    shown_minus_masked: dict[str, float] | None


class _Contrast(TypedDict):
    possible_pairs: int
    complete_pairs: int
    supported_worlds: int
    required_worlds: int
    full_world_support: bool
    per_world: list[_WorldContrast]
    collision_direction_counts: dict[str, int]
    shown_minus_masked: dict[str, float] | None


def _paired(blocks: Sequence[_Block], worlds: Sequence[int]) -> _Contrast:
    per_world: list[_WorldContrast] = []
    means: dict[str, list[Fraction]] = {name: [] for name in _METRICS}
    directions = dict.fromkeys(("lower", "equal", "higher"), 0)
    for world in worlds:
        possible = [row for row in blocks if row.block.instance == world]
        complete = [delta for row in possible if (delta := row.delta()) is not None]
        total = {name: sum(delta[name] for delta in complete) for name in _METRICS}
        mean = None
        if complete:
            mean = {}
            for name, value in total.items():
                exact = Fraction(value, len(complete))
                means[name].append(exact)
                mean[name] = float(exact)
        for delta in complete:
            change = delta[_COLLISIONS]
            direction = "lower" if change < 0 else "higher" if change > 0 else "equal"
            directions[direction] += 1
        per_world.append(
            {
                "instance": world,
                "possible_pairs": len(possible),
                "complete_pairs": len(complete),
                "shown_minus_masked_numerator": total,
                "shown_minus_masked": mean,
            }
        )
    supported = sum(row["complete_pairs"] > 0 for row in per_world)
    full_support = bool(worlds) and supported == len(worlds)
    return {
        "possible_pairs": len(blocks),
        "complete_pairs": sum(row["complete_pairs"] for row in per_world),
        "supported_worlds": supported,
        "required_worlds": len(worlds),
        "full_world_support": full_support,
        "per_world": per_world,
        "collision_direction_counts": directions,
        "shown_minus_masked": (
            {name: float(sum(values) / len(worlds)) for name, values in means.items()}
            if full_support
            else None
        ),
    }


def _by_initial_collision(
    blocks: Sequence[_Block], worlds: Sequence[int]
) -> dict[str, Any]:
    groups: dict[str, list[_Block]] = {"colliding": [], "noncolliding": []}
    unknown = 0
    for row in blocks:
        if row.proposals is None:
            unknown += 1
        else:
            initial = (
                "colliding" if row.proposals.metrics[_COLLISIONS] else "noncolliding"
            )
            groups[initial].append(row)
    results: dict[str, Any] = {"unknown_initial_blocks": unknown}
    for initial, group in groups.items():
        complete = [row for row in group if len(row.teams) == len(_ARMS)]
        results[initial] = {
            "paired": _paired(group, worlds),
            "transitions_in_complete_pairs": {
                arm: {
                    "final_colliding": sum(
                        row.teams[arm].metrics[_COLLISIONS] > 0 for row in complete
                    ),
                    "final_noncolliding": sum(
                        row.teams[arm].metrics[_COLLISIONS] == 0 for row in complete
                    ),
                }
                for arm in _ARMS
            },
        }
    return results


def analyze(study: Path, spend_dir: Path) -> dict[str, Any]:
    """Publish verified box choices and descriptive contrasts without private artifacts."""
    worlds = intent_packet.load()
    blocks = intent_protocol.blocks()
    registered = {
        intent_protocol.call_id(block, label, stage)
        for block in blocks
        for stage in ("proposal", *_ARMS)
        for label in range(len(intent_protocol.LABELS))
    }
    ledger = _Ledger.read(spend_dir, registered)
    claims: set[str] = set()
    unexpected_claims = 0
    with _cells(study) as cells:
        if cells is not None:
            with os.scandir(cells) as entries:
                for entry in entries:
                    if entry.name in registered:
                        claims.add(entry.name)
                    else:
                        unexpected_claims += 1
        results = [
            _block(worlds[block.instance], block, cells, claims, ledger)
            for block in blocks
        ]
    calls = [
        call for result in results for team in result.calls.values() for call in team
    ]
    ordered_worlds = sorted(worlds)
    return {
        "schema": "seatlab.box-intentions-report/v1",
        "scope": "Fixed-prefix choices in 3 worlds. No native actions or game outcomes.",
        "basis": (
            "Accepted choices require a replayed private receipt and matching direct "
            "settlement. Settlement does not authenticate a provider."
        ),
        "counting": (
            "Only complete pairs count in contrasts. A timestamp inversion excludes "
            "both arms. Average pairs within each world, then weight worlds equally. "
            "Initial-collision groups are descriptive. Choices use original box "
            "numbers in A, B, C order."
        ),
        "packet_sha256": intent_packet.PACKET_SHA256,
        "spend_ledger_available": ledger.rows is not None,
        "coverage": {
            "scheduled_blocks": len(results),
            "scheduled_calls": len(calls),
            "complete_arms": {
                arm: sum(arm in row.teams for row in results) for arm in _ARMS
            },
            "call_status_counts": dict(Counter(call.status for call in calls)),
            "missing_actual_receipts": sum(
                call.receipt_present is False
                and (
                    call.claim_present
                    or call.spend not in {"not_reserved", "unavailable"}
                )
                for call in calls
            ),
            "unexpected_claims": unexpected_claims,
            "unexpected_ledger_rows": ledger.unexpected_rows,
        },
        "primary": _paired(results, ordered_worlds),
        "by_initial_collision": _by_initial_collision(results, ordered_worlds),
        **{
            f"by_{axis}": [
                {
                    axis: index,
                    **_paired(
                        [row for row in results if getattr(row.block, axis) == index],
                        ordered_worlds,
                    ),
                }
                for index in range(size)
            ]
            for axis, size in (("numbering", 2), ("rotation", 3), ("repetition", 2))
        },
        "blocks": [row.public() for row in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.study, args.spend_dir)
    except (OSError, ValueError, sqlite3.Error):
        print(
            json.dumps({"error": "cannot_verify_box_intentions_study"}), file=sys.stderr
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
