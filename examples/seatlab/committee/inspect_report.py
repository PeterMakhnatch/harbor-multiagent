"""Verify and summarize SeatLab receiver decisions from a local Inspect .eval log."""

import argparse
import json
import os
import sqlite3
import stat
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any
from zipfile import BadZipFile
from zlib import error as DecompressionError

from ..visibility import deepseek, spend
from . import ballot_packet, packing

if TYPE_CHECKING:
    from inspect_ai.log import EvalLog, EvalSample
    from inspect_ai.model import ChatMessage, ModelOutput

type _JsonObject = dict[str, Any]
type _MeasureRow = Mapping[str, Any]
type _LedgerRows = dict[str, list[_JsonObject]]
type _CellRows = dict[packing.Cell, _JsonObject]
type _GroupRows = dict[tuple[int, int, str], _JsonObject]

_ARMS = ("separate", "indexed")
_SEAT_MEASURES = (
    "exact_totals",
    "correct_choice",
    "correct_visible_choice",
    "order_followed",
)
_GROUP_MEASURES = ("strict_majority", "correct_majority")
_SYSTEM = "Answer only with JSON."
_DIRECT = f"seatlab-direct/{deepseek.MODEL}"
_SERVED = {deepseek.MODEL, deepseek.REVISION.lower()}
_SCORER = "seatlab_facts_score"


def _read_log(path: Path) -> tuple["EvalLog | None", str | None]:
    if path.suffix != ".eval":
        return None, "unsupported_log_format"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None, "nonregular_log"
            try:
                from inspect_ai.log import read_eval_log
            except ImportError:
                return None, "inspect_ai_unavailable"
            return read_eval_log(
                stream, format="eval", resolve_attachments="core"
            ), None
    except FileNotFoundError:
        return None, "log_absent"
    except (
        BadZipFile,
        DecompressionError,
        EOFError,
        KeyError,
        OSError,
        ValueError,
        RuntimeError,
    ):
        return None, "log_unreadable"


def _matches_input(messages: "str | list[ChatMessage]", prompt: str) -> bool:
    return (
        isinstance(messages, list)
        and len(messages) == 2
        and messages[0].role == "system"
        and messages[0].content == _SYSTEM
        and messages[1].role == "user"
        and messages[1].content == prompt
        and not messages[1].tool_call_id
    )


def _plain_assistant(message: "ChatMessage") -> bool:
    return (
        message.role == "assistant"
        and not message.tool_calls
        and (
            isinstance(message.content, str)
            or all(part.type in ("text", "reasoning") for part in message.content)
        )
    )


def _same_json(a: object, b: object) -> bool:
    # Python equality alone would also accept True as 1 and 1 as 1.0.
    try:
        return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(
            b, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        return False


def _observed_output(sample: "EvalSample") -> "ModelOutput | None":
    if sample.output.choices or sample.output.completion:
        return sample.output
    outputs = [
        event.output
        for event in sample.events
        if event.event == "model"
        and not event.pending
        and event.error is None
        and (event.output.choices or event.output.completion)
    ]
    return outputs[0] if len(outputs) == 1 else None


def _inspect_sample(
    packet: _JsonObject,
    cell: packing.Cell,
    sample: "EvalSample",
    *,
    replayed: bool,
    direct: bool,
) -> tuple[str, str | None, str | None, packing.ScoreResult | None]:
    """Return the disposition, reason, generation evidence and recomputed score."""
    expected_prompt = packing.prompt(packet, cell)
    expected_metadata = {
        "block": cell.block,
        "map_index": cell.map_index,
        "seat_index": cell.seat_index,
        "arm": cell.arm,
        "packet_sha256": packing.PACKET_SHA256,
    }
    metadata = {name: sample.metadata.get(name) for name in expected_metadata}
    if sample.epoch != 1:
        return "invalid_evidence", "unexpected_epoch", None, None
    if sample.invalidation is not None:
        return "invalid_evidence", "sample_invalidated", None, None
    if not _same_json(metadata, expected_metadata):
        return "invalid_evidence", "sample_metadata_mismatch", None, None
    if not _matches_input(sample.input, expected_prompt):
        return "invalid_evidence", "sample_input_mismatch", None, None
    if sample.target not in ("", []) or any(
        (sample.choices, sample.files, sample.setup, sample.sandbox)
    ):
        return "invalid_evidence", "unexpected_sample_configuration", None, None

    events = [event for event in sample.events if event.event == "model"]
    output = sample.output
    if any(event.event == "tool" for event in sample.events) or any(
        event.tools or event.tool_choice != "none" for event in events
    ):
        return "invalid_evidence", "tool_activity", None, None
    if replayed or any(event.cache == "read" for event in events):
        return "replayed", "reused_generation", None, None
    if (
        len(events) > 1
        or len(output.choices) > 1
        or sample.error_retries
        or any(event.retries for event in events)
    ):
        return "invalid_evidence", "multiple_generation_attempts", None, None
    if not output.choices:
        if output.completion:
            return "invalid_evidence", "output_without_assistant_choice", None, None
        if _observed_output(sample) is not None:
            return "invalid_evidence", "missing_final_output", None, None
        return "missing_generation", "no_assistant_output", None, None
    if not _plain_assistant(output.message) or output.stop_reason == "tool_calls":
        return "invalid_evidence", "tool_or_nontext_output", None, None
    if sample.error is not None or output.completion != output.message.text:
        return "invalid_evidence", "sample_output_mismatch", None, None

    if sample.messages:
        if (
            len(sample.messages) != 3
            or not _matches_input(sample.messages[:2], expected_prompt)
            or not _plain_assistant(sample.messages[-1])
            or sample.messages[-1].text != output.completion
        ):
            return "invalid_evidence", "conversation_mismatch", None, None
    generation = "conversation_only" if sample.messages else "output_only"
    if events:
        event = events[0]
        if not _matches_input(event.input, expected_prompt):
            return "invalid_evidence", "model_input_mismatch", None, None
        if (event.model == _DIRECT) != direct:
            return "invalid_evidence", "model_event_identity_mismatch", None, None
        actual = event.output
        if (
            event.pending
            or event.error is not None
            or len(actual.choices) != 1
            or not _plain_assistant(actual.message)
            or actual.message.text != output.completion
            or actual.completion != output.completion
            or actual.stop_reason != output.stop_reason
            or actual.model != output.model
            or actual.usage != output.usage
        ):
            return "invalid_evidence", "model_output_mismatch", None, None
        generation = "model_event"

    try:
        result = packing.score(packet, cell, output.completion)
    except RecursionError:
        return "invalid_evidence", "response_nesting_limit", generation, None
    expected_score = {True: "C", False: "I", None: "N"}[result["exact_totals"]]
    if (
        sample.scores is None
        or sample.scores.keys() != {_SCORER}
        or sample.scores[_SCORER].value != expected_score
        or not _same_json(sample.scores[_SCORER].metadata, result)
    ):
        return "invalid_evidence", "scorer_mismatch", generation, None
    return result["status"], None, generation, result


def _ledger(
    spend_dir: Path | None, cells: dict[str, packing.Cell]
) -> tuple[_JsonObject, _LedgerRows]:
    if spend_dir is None:
        return {"status": "not_requested"}, {}
    try:
        calls: list[_JsonObject] = spend.report(spend_dir.absolute(), run_prefix="ix-")[
            "calls"
        ]
    except (
        ArithmeticError,
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
    ):
        return {"status": "unavailable"}, {}
    rows: defaultdict[str, list[_JsonObject]] = defaultdict(list)
    extra = Counter()
    for row in calls:
        cell = cells.get(row["run"])
        if cell is None:
            extra["unregistered_cell_rows"] += 1
        elif row["role"] != cell.role:
            extra["wrong_role_rows"] += 1
        else:
            rows[cell.cell_id].append(row)
    return {
        "status": "read",
        "eligible_rows": sum(map(len, rows.values())),
        "unsettled_eligible_rows": sum(
            row["estimated_usd_high"] is None
            for entries in rows.values()
            for row in entries
        ),
        "unregistered_cell_rows": extra["unregistered_cell_rows"],
        "wrong_role_rows": extra["wrong_role_rows"],
    }, rows


def _public_model(value: object) -> str | None:
    folded = value.lower() if isinstance(value, str) else None
    return folded if folded in _SERVED else None


def _match_spend(
    ledger_status: str,
    rows: list[_JsonObject],
    output: "ModelOutput | None",
    *,
    direct: bool,
) -> _JsonObject | None:
    if ledger_status == "not_requested":
        return {"status": "ledger_required"} if direct else None
    if ledger_status != "read":
        return {"status": "ledger_unavailable"}
    if len(rows) != 1:
        return {"status": "missing_ledger_row" if not rows else "duplicate_ledger_rows"}
    row = rows[0]
    held = row["estimated_usd_high"] is None
    served = row["served_model"]
    hit, miss, generated = row["cache_hit"], row["cache_miss"], row["output"]
    valid_usage = all(type(n) is int and n >= 0 for n in (hit, miss, generated))
    receipt = {
        "served_model": _public_model(served),
        "cache_hit": hit if valid_usage else None,
        "cache_miss": miss if valid_usage else None,
        "output": generated if valid_usage else None,
        "estimated_usd_low": row["estimated_usd_low"],
        "estimated_usd_high": row["estimated_usd_high"],
        "unsettled_hold_usd": row["unsettled_hold_usd"],
    }
    if held:
        return {"status": "unsettled_hold", **receipt}
    if output is None or not (output.choices or output.completion):
        return {"status": "settled_without_output", **receipt}

    mismatches = []
    if not direct or receipt["served_model"] is None or output.model != served:
        mismatches.append("model_identity")
    usage = output.usage
    observed = None
    if usage is None:
        mismatches.append("missing_usage")
    else:
        observed = {
            "input_tokens": usage.input_tokens,
            "input_tokens_cache_read": usage.input_tokens_cache_read,
            "input_tokens_cache_write": usage.input_tokens_cache_write,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
        # The guarded adapter records provider total input here, including hits.
        if not valid_usage:
            mismatches.append("ledger_usage")
        else:
            expected = {
                "input_tokens": hit + miss,
                "input_tokens_cache_read": hit,
                "output_tokens": generated,
                "total_tokens": hit + miss + generated,
            }
            mismatches.extend(
                key for key, value in expected.items() if observed[key] != value
            )
        if usage.input_tokens_cache_write not in (None, 0):
            mismatches.append("input_tokens_cache_write")
    return {
        "status": "mismatch" if mismatches else "matched",
        "mismatches": mismatches,
        "inspect_served_model": _public_model(output.model),
        "inspect_usage": observed,
        **receipt,
    }


def _counts(
    rows: Sequence[_MeasureRow], keys: tuple[str, ...]
) -> dict[str, dict[str, int]]:
    result = {}
    for key in keys:
        observations = [row[key] for row in rows if row[key] is not None]
        result[key] = {"numerator": sum(observations), "denominator": len(observations)}
    return result


def _summarize(rows: list[_JsonObject], groups: list[_JsonObject]) -> _JsonObject:
    accepted = [row["result"] for row in rows if row["status"] == "accepted"]
    return {
        "scheduled_cells": len(rows),
        "scheduled_groups": len(groups),
        "cell_status": dict(Counter(row["status"] for row in rows)),
        "generation_evidence": dict(
            Counter(row["generation"] for row in rows if row["generation"] is not None)
        ),
        "field_order": dict(
            Counter(",".join(row["field_order"]) for row in rows if row["field_order"])
        ),
        "measures": _counts(accepted, _SEAT_MEASURES)
        | _counts(groups, _GROUP_MEASURES),
    }


def _groups(rows: _CellRows, blocks: list[int], maps: list[int]) -> _GroupRows:
    groups: _GroupRows = {}
    for block in blocks:
        for index in maps:
            by_arm = {
                arm: [
                    row["result"]
                    for cell, row in rows.items()
                    if (cell.block, cell.map_index, cell.arm) == (block, index, arm)
                    and row["status"] == "accepted"
                ]
                for arm in _ARMS
            }
            matched = all(len(accepted) == 4 for accepted in by_arm.values())
            for arm, accepted in by_arm.items():
                votes = Counter(result["choice"] for result in accepted)
                majority = next((label for label, n in votes.items() if n >= 3), None)
                groups[block, index, arm] = {
                    "block": block,
                    "map_index": index,
                    "arm": arm,
                    "accepted_seats": len(accepted),
                    "matched": matched,
                    "majority_choice": majority if matched else None,
                    "strict_majority": (majority is not None) if matched else None,
                    "correct_majority": (
                        any(
                            result["choice"] == majority and result["correct_choice"]
                            for result in accepted
                        )
                        if matched
                        else None
                    ),
                }
    return groups


def _paired(
    pairs: Sequence[tuple[_MeasureRow, _MeasureRow]], keys: tuple[str, ...]
) -> _JsonObject:
    measures = {}
    for key in keys:
        observed = [
            (a[key], b[key])
            for a, b in pairs
            if a[key] is not None and b[key] is not None
        ]
        indexed = sum(a for a, _ in observed)
        separate = sum(b for _, b in observed)
        n = len(observed)
        measures[key] = {
            "indexed_numerator": indexed,
            "separate_numerator": separate,
            "paired_denominator": n,
            "effect": (indexed - separate) / n if n else None,
        }
    return measures


def _contrast(
    rows: _CellRows, groups: _GroupRows, blocks: list[int], maps: list[int]
) -> _JsonObject:
    def summarize(selected: list[int]) -> _JsonObject:
        by_map: list[_JsonObject] = []
        for index in maps:
            seats = []
            group_pairs = []
            for block in selected:
                for seat in range(4):
                    indexed = rows[packing.Cell(block, index, seat, "indexed")]
                    separate = rows[packing.Cell(block, index, seat, "separate")]
                    if indexed["status"] == separate["status"] == "accepted":
                        seats.append((indexed["result"], separate["result"]))
                group_pairs.append(
                    (groups[block, index, "indexed"], groups[block, index, "separate"])
                )
            measures = _paired(seats, _SEAT_MEASURES) | _paired(
                group_pairs, _GROUP_MEASURES
            )
            by_map.append({"map_index": index, "measures": measures})
        effects = {}
        for key in (*_SEAT_MEASURES, *_GROUP_MEASURES):
            per_map = [row["measures"][key] for row in by_map]
            supported = [row["effect"] for row in per_map if row["effect"] is not None]
            effects[key] = {
                name: sum(row[name] for row in per_map)
                for name in (
                    "indexed_numerator",
                    "separate_numerator",
                    "paired_denominator",
                )
            } | {
                "supported_maps": len(supported),
                "effect": mean(supported) if len(supported) == len(maps) else None,
            }
        return {"by_map": by_map, "measures": effects}

    return {
        **summarize(blocks),
        "by_block": [{"block": block, **summarize([block])} for block in blocks],
    }


def _execution_order(samples: dict[str, list["EvalSample"]]) -> _JsonObject:
    """Compare observed sample start times to the registered relative order."""
    records = [
        sample for cell in packing.grid() for sample in samples.get(cell.cell_id, ())
    ]
    diagnostic = {
        "observed_samples": len(records),
        "start_order": "not_assessed",
        "nonoverlap": None,
    }
    if (
        len(records) < 2
        or len({sample.id for sample in records}) != len(records)
        or any(sample.epoch != 1 or not sample.started_at for sample in records)
    ):
        return diagnostic
    try:
        starts = [datetime.fromisoformat(sample.started_at or "") for sample in records]
        if len(set(starts)) != len(starts):
            return diagnostic
        chronological = sorted(range(len(records)), key=starts.__getitem__)
        nonoverlap = None
        if all(sample.completed_at for sample in records):
            ends = [
                datetime.fromisoformat(sample.completed_at or "") for sample in records
            ]
            if all(start <= end for start, end in zip(starts, ends, strict=True)):
                nonoverlap = all(
                    ends[left] <= starts[right]
                    for left, right in pairwise(chronological)
                )
    except (TypeError, ValueError):
        return diagnostic
    return diagnostic | {
        "basis": "sample_started_at",
        "start_order": "matches"
        if chronological == list(range(len(records)))
        else "differs",
        "nonoverlap": nonoverlap,
    }


def _analyze_log(
    packet: _JsonObject,
    log: "EvalLog | None",
    log_reason: str | None,
    spend_dir: Path | None,
) -> _JsonObject:
    grid = packing.grid()
    registered = {cell.cell_id: cell for cell in grid}
    blocks = sorted({cell.block for cell in grid})
    maps = sorted({cell.map_index for cell in grid})
    ledger, ledger_rows = _ledger(spend_dir, registered)
    samples: defaultdict[str, list["EvalSample"]] = defaultdict(list)
    sample_uuids = Counter()
    event_uuids = Counter()
    unregistered = 0
    direct = log is not None and log.eval.model == _DIRECT
    if log is not None:
        if log.version != 2 or log.eval.task != "seatlab_facts":
            log_reason = "unsupported_version_or_task"
        elif log.samples is None:
            log_reason = "samples_unavailable"
        else:
            for sample in log.samples:
                sample_id = sample.id
                if not isinstance(sample_id, str) or sample_id not in registered:
                    unregistered += 1
                    continue
                samples[sample_id].append(sample)
                if sample.uuid is not None:
                    sample_uuids[sample.uuid] += 1
                event_uuids.update(
                    {
                        event.uuid
                        for event in sample.events
                        if event.event == "model" and event.uuid is not None
                    }
                )
    log_status = {
        None: "read",
        "log_absent": "absent",
        "samples_unavailable": "samples_unavailable",
    }.get(log_reason, "unsupported")

    rows: _CellRows = {}
    action_spend = Counter()
    for cell in grid:
        records = samples[cell.cell_id]
        sample = records[0] if len(records) == 1 else None
        output = _observed_output(sample) if sample is not None else None
        receipt = _match_spend(
            ledger["status"], ledger_rows.get(cell.cell_id, []), output, direct=direct
        )
        if output is not None or (
            sample is None
            and any(_observed_output(record) is not None for record in records)
        ):
            action_spend[
                "duplicate_sample_id"
                if sample is None
                else receipt["status"]
                if receipt is not None
                else "external_without_ledger"
            ] += 1
        result = None
        generation = None
        field_order = None
        if sample is None:
            status, reason = (
                ("invalid_evidence", "duplicate_sample_id")
                if records
                else ("missing_sample", log_reason or "sample_not_logged")
            )
        else:
            reused = sample_uuids[sample.uuid] > 1 or any(
                event_uuids[event.uuid] > 1
                for event in sample.events
                if event.event == "model"
            )
            status, reason, generation, result = _inspect_sample(
                packet, cell, sample, replayed=reused, direct=direct
            )
            if status in ("accepted", "invalid_response") and (
                direct or spend_dir is not None
            ):
                if receipt is None or receipt["status"] != "matched":
                    status, reason = "unverified_direct", "ledger_not_matched"
                elif generation != "model_event":
                    status, reason = "unverified_direct", "model_event_unavailable"
            if status == "accepted":
                field_order = list(json.loads(sample.output.completion))
        rows[cell] = {
            "cell_id": cell.cell_id,
            "block": cell.block,
            "map_index": cell.map_index,
            "seat_index": cell.seat_index,
            "arm": cell.arm,
            "status": status,
            "reason": reason,
            "generation": generation,
            "spend": receipt,
            "result": result if status in ("accepted", "invalid_response") else None,
            "field_order": field_order,
        }

    groups = _groups(rows, blocks, maps)

    def summarize(
        arm: str, block: int | None = None, index: int | None = None
    ) -> _JsonObject:
        return _summarize(
            [
                row
                for cell, row in rows.items()
                if cell.arm == arm
                and (block is None or cell.block == block)
                and (index is None or cell.map_index == index)
            ],
            [
                row
                for (b, m, a), row in groups.items()
                if a == arm
                and (block is None or b == block)
                and (index is None or m == index)
            ],
        )

    by_arm = {
        arm: {
            **summarize(arm),
            "by_map": [
                {"map_index": index, **summarize(arm, index=index)} for index in maps
            ],
            "by_block": [
                {
                    "block": block,
                    **summarize(arm, block),
                    "by_map": [
                        {"map_index": index, **summarize(arm, block, index)}
                        for index in maps
                    ],
                }
                for block in blocks
            ],
        }
        for arm in _ARMS
    }
    return {
        "schema": "seatlab.committee-inspect-report/v1",
        "packet_sha256": packing.PACKET_SHA256,
        "model_source": ("inspect_guarded_direct" if direct else "inspect_external")
        if log_status in ("read", "samples_unavailable")
        else None,
        "log": {
            "status": log_status,
            "reason": log_reason,
            "inspect_status": log.status if log is not None else None,
            "recorded_samples": len(log.samples)
            if log is not None and log.samples is not None
            else None,
            "unregistered_samples": unregistered,
            "duplicate_samples": sum(
                len(records) - 1 for records in samples.values() if records
            ),
            "execution_order": _execution_order(samples),
        },
        "spend": ledger
        | {
            "observed_action_cells": sum(action_spend.values()),
            "observed_action_status": dict(action_spend),
            "scope": "Cell and role, served model, and tokens; not response text or provider authentication.",
        },
        "summary": {
            "scheduled_cells": len(rows),
            "cell_status": dict(Counter(row["status"] for row in rows.values())),
            "primary_measure": "exact_totals",
            "weighting": "Equal means across all 6 maps with accepted pairs in every map.",
            "interpretation": (
                "Independent receiver decisions on one latent problem. "
                "Majorities require all 4 accepted decisions in both arms; they are not native rewards."
            ),
            "by_arm": by_arm,
        },
        "indexed_minus_separate": _contrast(rows, groups, blocks, maps),
        "cells": list(rows.values()),
        "groups": list(groups.values()),
    }


def analyze(
    packet_path: Path,
    log_path: Path,
    *,
    expected_sha256: str,
    spend_dir: Path | None = None,
) -> _JsonObject:
    """Read the fixed packet and Inspect log, with optional guarded ledger evidence."""
    if expected_sha256 != packing.PACKET_SHA256:
        raise ValueError("expected SHA-256 is not the registered packet digest")
    packet = ballot_packet.read_packet(packet_path, expected_sha256)
    log, reason = _read_log(log_path)
    return _analyze_log(packet, log, reason, spend_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("log", type=Path)
    parser.add_argument("--expect-sha256", required=True)
    parser.add_argument("--spend-dir", type=Path)
    args = parser.parse_args()
    try:
        report = analyze(
            args.packet,
            args.log,
            expected_sha256=args.expect_sha256,
            spend_dir=args.spend_dir,
        )
    except (OSError, ValueError, RecursionError):
        parser.error(
            "the registered packet or its expected SHA-256 is invalid or unavailable"
        )
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
