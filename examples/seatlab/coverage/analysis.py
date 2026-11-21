import re
from collections import Counter
from hashlib import sha256
from runpy import run_path
from statistics import fmean
from typing import Any, TypeGuard

from ..handoff import dispatch, packet
from ..handoff.packet import Block
from ..visibility.clarify_run import _encode
from ..visibility.route_run import Answer, _validate_answer

_SCHEMA = "seatlab.coverage-report/v1"
_POLICIES = ("batch", "checklist", "owners")
_SOURCES = ("ready", "no_note", "no_lookup", "no_request")
_STATES = ("unclaimed", "unbound", "claimed")
_UNKNOWN = ("missing", "callback_error")
_HASH = re.compile(r"[0-9a-f]{64}")
_SLOT_FIELDS = {"block", "case", "cluster", "rep", "status", "prompt_sha256"}
_ROW_FIELDS = {
    "run",
    "role",
    "slot",
    "policy",
    "target",
    "state",
    "status",
    "choices",
    "prompt_sha256",
    "response_sha256",
    "usage",
}
_USAGE_FIELDS = {"served_model", "input_tokens", "cache_tokens", "output_tokens"}
_REPORT_FIELDS = {
    "schema",
    "packet_sha256",
    "source_sha256",
    "slots",
    "rows",
    "primary",
    "secondary",
    "arms",
    "counts",
}


type _Row = dict[str, Any]
type _Evidence = dict[int, dict[str, list[_Row]]]
type _Scores = dict[str, dict[str, bool]]
type _Pair = tuple[int | None, int | None]


def _digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _check_slots(
    blocks: tuple[Block, ...], source: object, slots: object
) -> list[_Row]:
    if (
        not isinstance(slots, list)
        or not 0 < len(slots) == len(blocks) <= 512
        or not _digest(source)
        or not _digest(blocks[0].fingerprint)
        or blocks != packet.grid(blocks[0].packet)
        or len({block.key for block in blocks}) != len(blocks)
    ):
        raise ValueError("invalid coverage source register")
    for block, slot in zip(blocks, slots, strict=True):
        if (
            not isinstance(slot, dict)
            or slot.keys() != _SLOT_FIELDS
            or slot["block"] != block.key
            or (slot["case"], slot["cluster"]) != (block.case.id, block.case.cluster)
            or type(slot["rep"]) is not int
            or slot["rep"] != block.rep
            or slot["status"] not in _SOURCES
            or (
                not _digest(slot["prompt_sha256"])
                if slot["status"] == "ready"
                else slot["prompt_sha256"] is not None
            )
        ):
            raise ValueError("invalid coverage source slot")
    expected = sha256(
        _encode(
            {
                "schema": "seatlab.coverage-source/v1",
                "packet_sha256": blocks[0].fingerprint,
                "slots": slots,
            }
        )
    ).hexdigest()
    if source != expected:
        raise ValueError("coverage source fingerprint mismatch")
    return slots


def _check_usage(usage: object) -> None:
    if usage is None:
        return
    if not isinstance(usage, dict) or usage.keys() != _USAGE_FIELDS:
        raise ValueError("invalid coverage usage")
    _, parsed = _validate_answer(Answer("", **usage))
    if parsed is None:
        raise ValueError("invalid coverage usage")


def _check_receipt(row: _Row, ids: tuple[str, ...], options: tuple[str, ...]) -> None:
    state, status = row["state"], row["status"]
    if state not in _STATES or not _digest(row["prompt_sha256"]):
        raise ValueError("invalid coverage claim or prompt fingerprint")
    choices = row["choices"]
    if status not in _UNKNOWN and status != "received":
        raise ValueError("invalid coverage receipt status")
    if status != "missing" and state != "claimed":
        raise ValueError("unclaimed coverage receipt")
    if status in _UNKNOWN:
        if any(
            row[field] is not None for field in ("choices", "response_sha256", "usage")
        ):
            raise ValueError("invalid missing coverage response")
        return
    if not _digest(row["response_sha256"]):
        raise ValueError("invalid coverage response fingerprint")
    _check_usage(row["usage"])
    if choices is not None and (
        not isinstance(choices, dict)
        or choices.keys() != set(ids)
        or any(value not in options for value in choices.values())
    ):
        raise ValueError("invalid coverage choices")


def _evidence(
    blocks: tuple[Block, ...], source: str, slots: list[_Row], rows: object
) -> _Evidence:
    expected: dict[str, tuple[int, str, str | None, tuple[str, ...]]] = {}
    grouped: _Evidence = {}
    for index, (block, slot) in enumerate(zip(blocks, slots, strict=True)):
        if slot["status"] != "ready":
            continue
        grouped[index] = {policy: [] for policy in _POLICIES}
        ids = tuple(record.id for record in block.case.records)
        prefix = f"cv-{source[:12]}-{index:03d}-"
        for policy in _POLICIES[:2]:
            expected[prefix + policy] = (index, policy, None, ids)
        for owner, target in enumerate(ids):
            expected[f"{prefix}owner{owner}"] = (index, "owners", target, (target,))
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("coverage physical row count mismatch")
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _ROW_FIELDS:
            raise ValueError("invalid coverage row fields")
        run = row["run"]
        binding = expected.pop(run, None) if isinstance(run, str) else None
        if binding is None:
            raise ValueError("unregistered or duplicate coverage row")
        slot, policy, target, ids = binding
        if (
            type(row["slot"]) is not int
            or (row["slot"], row["policy"], row["target"]) != (slot, policy, target)
            or row["role"] != "route_decide"
            or (
                policy == "batch"
                and row["prompt_sha256"] != slots[slot]["prompt_sha256"]
            )
        ):
            raise ValueError("invalid coverage row binding")
        _check_receipt(row, ids, blocks[slot].packet.choices)
        grouped[slot][policy].append(row)
    return grouped


def _delivery(rows: list[_Row]) -> int | None:
    if any(row["status"] == "received" and row["choices"] is None for row in rows):
        return 0
    if any(row["status"] in _UNKNOWN for row in rows):
        return None
    return 1


def _cluster(pairs: list[_Pair]) -> _Row:
    complete = [
        right - left for left, right in pairs if left is not None and right is not None
    ]
    return {
        "scheduled_pairs": len(pairs),
        "complete_pairs": len(complete),
        "gain": complete.count(1),
        "loss": complete.count(-1),
        "tie": complete.count(0),
        "paired_complete_delta": fmean(complete) if complete else None,
        "worst_delta": fmean(
            (right if right is not None else 0) - (left if left is not None else 1)
            for left, right in pairs
        )
        if pairs
        else None,
        "best_delta": fmean(
            (right if right is not None else 1) - (left if left is not None else 0)
            for left, right in pairs
        )
        if pairs
        else None,
    }


def _comparison(
    blocks: tuple[Block, ...], evidence: _Evidence, left: str, right: str
) -> _Row:
    groups: dict[str, list[_Pair]] = {block.case.cluster: [] for block in blocks}
    for slot, arms in evidence.items():
        groups[blocks[slot].case.cluster].append(
            (_delivery(arms[left]), _delivery(arms[right]))
        )
    clusters = {name: _cluster(pairs) for name, pairs in groups.items()}
    complete = [
        row["paired_complete_delta"]
        for row in clusters.values()
        if row["complete_pairs"]
    ]
    eligible = [row for row in clusters.values() if row["scheduled_pairs"]]
    return {
        "kind": f"{right}_minus_{left}_delivery",
        **{
            field: sum(row[field] for row in clusters.values())
            for field in ("scheduled_pairs", "complete_pairs", "gain", "loss", "tie")
        },
        "enrolled_clusters": len(clusters),
        "eligible_clusters": len(eligible),
        "clusters_with_pairs": len(complete),
        "paired_complete_delta": fmean(complete)
        if len(complete) == len(clusters)
        else None,
        "worst_delta": fmean(row["worst_delta"] for row in eligible)
        if eligible
        else None,
        "best_delta": fmean(row["best_delta"] for row in eligible)
        if eligible
        else None,
        "by_cluster": clusters,
    }


def _bounds(known: int, unknown: int, total: int) -> _Row:
    assert 0 <= known <= known + unknown <= total
    return {
        "lower": known / total if total else None,
        "upper": (known + unknown) / total if total else None,
    }


def _arm(
    name: str, blocks: tuple[Block, ...], evidence: _Evidence, scores: _Scores
) -> _Row:
    deliveries: Counter[int | None] = Counter()
    calls: Counter[str] = Counter()
    states: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    eligible_records = unknown_choices = 0
    correct = correct_on_complete = records_on_complete = valid_choices = 0
    for slot, by_policy in evidence.items():
        rows = by_policy[name]
        delivery = _delivery(rows)
        deliveries[delivery] += 1
        records = len(blocks[slot].case.records)
        eligible_records += records
        for row in rows:
            statuses = row["status"]
            calls[statuses] += 1
            states[row["state"]] += 1
            if statuses in _UNKNOWN:
                unknown_choices += 1 if name == "owners" else records
            result = scores.get(row["run"])
            if result is None:
                continue
            right = sum(result.values())
            sizes[str(len(result))] += 1
            correct += right
            valid_choices += len(result)
            if delivery == 1:
                correct_on_complete += right
                records_on_complete += len(result)
    return {
        "eligible_requests": len(evidence),
        "scheduled_calls": calls.total(),
        "call_states": {state: states[state] for state in _STATES},
        "recorded_attempts": calls["received"] + calls["callback_error"],
        "received_calls": calls["received"],
        "valid_responses": sizes.total(),
        "invalid_responses": calls["received"] - sizes.total(),
        "missing_calls": calls["missing"],
        "callback_errors": calls["callback_error"],
        "choices_per_valid_response": dict(sizes),
        "eligible_records": eligible_records,
        "valid_choices": valid_choices,
        "unknown_choices": unknown_choices,
        "complete_deliveries": deliveries[1],
        "failed_deliveries": deliveries[0],
        "unknown_deliveries": deliveries[None],
        "delivery_rate_bounds": _bounds(deliveries[1], deliveries[None], len(evidence)),
        "records_on_complete_deliveries": records_on_complete,
        "correct_on_complete_deliveries": correct_on_complete,
        "accuracy_on_complete_deliveries": (
            correct_on_complete / records_on_complete if records_on_complete else None
        ),
        "correct_delivered": correct,
        "correct_per_eligible_record_bounds": _bounds(
            correct, unknown_choices, eligible_records
        ),
    }


def _summary(
    blocks: tuple[Block, ...], slots: list[_Row], evidence: _Evidence, scores: _Scores
) -> _Row:
    statuses = Counter(slot["status"] for slot in slots)
    arms = {name: _arm(name, blocks, evidence, scores) for name in _POLICIES}
    return {
        "primary": _comparison(blocks, evidence, "batch", "checklist"),
        "secondary": _comparison(blocks, evidence, "checklist", "owners"),
        "arms": arms,
        "counts": {
            "registered_slots": len(slots),
            "eligible_requests": len(evidence),
            "ineligible_slots": len(slots) - len(evidence),
            "source_statuses": {status: statuses[status] for status in _SOURCES},
            **{
                field: sum(arm[field] for arm in arms.values())
                for field in (
                    "scheduled_calls",
                    "recorded_attempts",
                    "received_calls",
                    "missing_calls",
                    "callback_errors",
                )
            },
        },
    }


def summarize(
    blocks: tuple[Block, ...], source_sha256: str, slots: list[_Row], rows: list[_Row]
) -> _Row:
    _check_slots(blocks, source_sha256, slots)
    evidence = _evidence(blocks, source_sha256, slots, rows)
    scores = {
        row["run"]: {
            record.id: row["choices"][record.id] == record.best
            for record in blocks[row["slot"]].case.records
            if record.id in row["choices"]
        }
        for row in rows
        if row["choices"] is not None
    }
    return _summary(blocks, slots, evidence, scores)


def _native_scores(blocks: tuple[Block, ...], rows: list[_Row]) -> tuple[_Scores, _Row]:
    world = dispatch._world()
    score = run_path(str(dispatch._TASK / "tests" / "score.py"))["score"]
    scores: _Scores = {}
    by_policy = {name: {"scorer_calls": 0, "observed_choices": 0} for name in _POLICIES}
    worlds: set[str] = set()
    for row in rows:
        observed = row["choices"]
        if observed is None:
            continue
        block = blocks[row["slot"]]
        cluster = block.case.cluster
        submitted = {
            record.id: block.packet.choices[0] for record in block.case.records
        } | observed
        graded = dispatch._grade(world, score, int(cluster[1:]), submitted)
        projected: dict[str, bool] = {}
        for record in block.case.records:
            if record.id not in observed:
                continue
            actual = graded.get(record.id)
            if type(actual) is not bool or actual != (
                observed[record.id] == record.best
            ):
                raise ValueError("coverage choice does not match native score")
            projected[record.id] = actual
        scores[row["run"]] = projected
        worlds.add(cluster)
        counts = by_policy[row["policy"]]
        counts["scorer_calls"] += 1
        counts["observed_choices"] += len(projected)
    return scores, {
        "observed_worlds": len(worlds),
        "observed_dossiers": len(scores),
        "observed_native_choices": sum(map(len, scores.values())),
        "by_policy": by_policy,
    }


def stock(report: _Row) -> _Row:
    if report.keys() - {"meter"} != _REPORT_FIELDS or report["schema"] != _SCHEMA:
        raise ValueError("invalid coverage report fields or schema")
    native = packet.parse(dispatch.packet_bytes())
    if report["packet_sha256"] != native.sha256:
        raise ValueError("coverage stock requires the default native packet")
    blocks = packet.grid(native)
    source, slots, rows = report["source_sha256"], report["slots"], report["rows"]
    slots = _check_slots(blocks, source, slots)
    evidence = _evidence(blocks, source, slots, rows)
    scores, confirmations = _native_scores(blocks, rows)
    summary = _summary(blocks, slots, evidence, scores)
    try:
        matches = all(
            _encode(report[field]) == _encode(expected)
            for field, expected in summary.items()
        )
    except (TypeError, ValueError, RecursionError):
        matches = False
    if not matches:
        raise ValueError("published coverage summary does not match native score")
    return {
        "mismatches": 0,
        "packet_sha256": native.sha256,
        "source_sha256": source,
        "registered_worlds": summary["primary"]["enrolled_clusters"],
        **confirmations,
        **summary,
    }
