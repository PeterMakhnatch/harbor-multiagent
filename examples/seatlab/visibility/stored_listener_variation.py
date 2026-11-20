"""Measure native listener variation across matched archived evidence.

The matching design is fixed before choice inspection. A decision-maker round
contributes only its first authenticated decision. Its evidence is the last
authenticated complete report GET before that decision. Primary equality uses
condition, model, round, and the actual msg_seqs order of
(reporting role, allowlisted numbers). That match is not an identical prompt.
The multiset sensitivity keeps role and numbers, drops position, and stays
separate.

This is a secondary analysis of recorded trials. It makes no model request.
It does not claim binomial precision or intrinsic model randomness: prompts
and history may differ, and rounds and pair groups are dependent. Native
reward is route accuracy. Trial-level safety is not a per-decision payout,
so payout is omitted.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import listener
from .crucible import RunRecord, run_records

_RECEIVER = "decision_maker"
_ROUTES = ("route_alpha", "route_beta")
_FLAGS = ("none", "scout_1", "scout_2", "scout_3")
_ROUNDS = range(1, 7)
_COMPLETE = 3


class _Invalid(ValueError):
    pass


@dataclass(frozen=True)
class _Observation:
    condition: str
    model: str
    round_number: int
    ordered: tuple[Any, ...]
    multiset: tuple[tuple[Any, int], ...]
    choice: int
    flag: int
    truth: int
    evidence_key: str


def _model(record: RunRecord) -> str:
    result = json.loads((record.directory / "result.json").read_bytes())
    config = result.get("config") if isinstance(result, dict) else None
    participants = config.get("participants") if isinstance(config, dict) else None
    names = {
        row.get("model_name")
        for row in participants or []
        if isinstance(row, dict)
    }
    if len(names) != 1 or not isinstance(next(iter(names)), str):
        raise ValueError("Crucible recorded model is unavailable")
    return next(iter(names))


def _reports(
    rows: list[dict[str, Any]], refs: object, round_number: int
) -> tuple[tuple[str, Any], ...]:
    if not isinstance(refs, list) or len(refs) != _COMPLETE:
        raise _Invalid("incomplete")
    ordered: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for seq in refs:
        if type(seq) is not int or not 0 < seq <= len(rows):
            raise _Invalid("invalid")
        row = rows[seq - 1]
        seat = row.get("actor")
        payload = row.get("payload")
        if (
            row.get("event_type") != "scout_report"
            or row.get("authenticated") is not True
            or row.get("visibility") != "public"
            or not isinstance(seat, str)
            or seat not in listener._FIELDS
            or not isinstance(payload, dict)
            or payload.get("seat") != seat
            or not listener._is_round(row, round_number)
        ):
            raise _Invalid("invalid")
        if seat in seen:
            raise _Invalid("duplicate")
        seen.add(seat)
        ordered.append((
            seat,
            listener._scores(payload.get("claim"), listener._FIELDS[seat], "invalid"),
        ))
    if seen != set(listener._FIELDS):
        raise _Invalid("incomplete")
    return tuple(ordered)


def _observe(record: RunRecord, model: str) -> list[_Observation | str]:
    path = record.directory / listener._EVENT_LOG
    try:
        rows = listener._event_rows(path.read_bytes(), record.condition)
    except (OSError, listener._Ineligible):
        return ["missing"] * len(_ROUNDS)

    results: list[_Observation | str] = []
    for round_number in _ROUNDS:
        candidates = [
            row for row in rows
            if row.get("actor") == _RECEIVER and listener._is_round(row, round_number)
            and row.get("event_type") in {"message_read", "decision"}
        ]
        decision = next((row for row in candidates if row.get("event_type") == "decision"), None)
        if decision is None:
            results.append("missing")
            continue
        if decision.get("authenticated") is not True or decision.get("visibility") != "world_audit":
            results.append("invalid")
            continue
        payload = decision["payload"]
        choice, flag = payload.get("choice"), payload.get("flagged_liar")
        if choice not in _ROUTES or flag not in _FLAGS:
            results.append("invalid")
            continue
        reads = [
            row for row in candidates
            if row.get("event_type") == "message_read" and row["seq"] < decision["seq"]
        ]
        ordered = None
        for fetch in reversed(reads):
            fetched = fetch.get("payload")
            if (
                fetch.get("authenticated") is not True
                or fetch.get("visibility") != "world_audit"
                or not isinstance(fetched, dict)
                or fetched.get("channel") != "round_reports"
            ):
                continue
            try:
                ordered = _reports(rows, fetched.get("msg_seqs"), round_number)
            except _Invalid:
                continue
            break
        if ordered is None:
            results.append("missing")
            continue
        try:
            truth = listener._truth(rows, round_number)[0]
        except listener._Ineligible:
            results.append("invalid")
            continue
        counts = Counter(ordered)
        results.append(_Observation(
            record.condition,
            model,
            round_number,
            ordered,
            tuple(sorted((item, counts[item]) for item in counts)),
            _ROUTES.index(choice),
            _FLAGS.index(flag),
            truth,
            record.key,
        ))
    return results


def _disagreements(values: list[int]) -> int:
    counts = Counter(values)
    total = len(values)
    agreed = sum(count * (count - 1) // 2 for count in counts.values())
    return total * (total - 1) // 2 - agreed


def _pairs(rows: list[_Observation], key: str) -> dict[str, int]:
    grouped: dict[tuple[Any, ...], list[_Observation]] = defaultdict(list)
    for row in rows:
        grouped[(row.condition, row.model, row.round_number, getattr(row, key))].append(row)
    repeated = [group for group in grouped.values() if len({row.evidence_key for row in group}) >= 2]
    choice = liar = pair_total = 0
    trials: set[str] = set()
    for group in repeated:
        distinct = list({row.evidence_key: row for row in group}.values())
        pair_total += len(distinct) * (len(distinct) - 1) // 2
        trials.update(row.evidence_key for row in distinct)
        choice += _disagreements([row.choice for row in distinct])
        liar += _disagreements([row.flag for row in distinct])
    return {
        "groups": len(repeated),
        "comparison_pairs": pair_total,
        "distinct_source_trials": len(trials),
        "shared_numeric_inputs": len(repeated),
        "choice_disagreement": choice,
        "liar_disagreement": liar,
    }


def _counts(rows: Iterable[_Observation]) -> dict[str, int]:
    rows = list(rows)
    return {
        "decisions": len(rows),
        "distinct_trials": len({row.evidence_key for row in rows}),
        "route_correct": sum(row.choice == row.truth for row in rows),
    }


def analyze(root: Path) -> dict[str, Any]:
    """Return collection-independent aggregate counts for the Crucible cohort."""
    records = [
        record for record in run_records(root)
        if record.family == listener.FAMILY and record.condition in listener.POLICIES
    ]
    models = {record.key: _model(record) for record in records}
    observed = [_observe(record, models[record.key]) for record in records]
    rows = [row for trial in observed for row in trial if isinstance(row, _Observation)]
    missing = Counter(
        reason for trial in observed for reason in trial if isinstance(reason, str)
    )
    by_cell: dict[tuple[str, int], list[_Observation]] = defaultdict(list)
    for row in rows:
        by_cell[row.condition, row.round_number].append(row)

    cells = []
    for condition in listener.POLICIES:
        for round_number in _ROUNDS:
            selected = by_cell[condition, round_number]
            cells.append({
                "condition": condition,
                "model": models[next(row.evidence_key for row in selected)] if selected else None,
                "round": round_number,
                "denominator": len(selected),
                "counts": _counts(selected),
                "ordered": _pairs(selected, "ordered"),
                "multiset_sensitivity": _pairs(selected, "multiset"),
            })
    return {
        "method": "first_decision_after_last_complete_authenticated_get",
        "primary": "actual_msg_seqs_order",
        "matching": "condition_model_round_role_and_allowlisted_numbers",
        "not_conditioned_on": "full_prompt_or_history",
        "payout": "omitted_not_per_decision",
        "native_trials": len(records),
        "missing_evidence": dict(missing),
        "cells": cells,
        "pooled": {
            "denominator": len(rows),
            "counts": _counts(rows),
            "ordered": _pairs(rows, "ordered"),
            "multiset_sensitivity": _pairs(rows, "multiset"),
            "dependence": "rounds_and_pair_groups_are_dependent",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
