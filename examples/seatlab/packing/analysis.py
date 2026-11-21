import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from runpy import run_path
from statistics import fmean
from typing import Any, TypeGuard

from ..handoff import dispatch, packet
from ..handoff.packet import Block, Record
from ..visibility.route_run import Answer, _validate_answer

_ARMS = ("prefix", "rewrite")
_STATES = ("unclaimed", "unbound", "claimed")
_SOURCES = ("missing", "invalid", "usable")
_DECISIONS = ("complete", "invalid_decide", "missing_decide")
_REWRITES = ("invalid_rewrite", "missing_rewrite")
_STATUSES = ("no_source", *_REWRITES, *_DECISIONS)
_STAGES = ("rewrite", "both-decide", "prefix-decide", "rewrite-decide")
_LIMIT = 96
_HASH = re.compile(r"[0-9a-f]{64}")

type _Row = dict[str, Any]
type _Results = dict[str, dict[str, bool]]
type _Pair = tuple[float | None, float | None]


@dataclass(frozen=True)
class _Evidence:
    block: Block
    state: str
    source_status: str
    source_bytes: int | None
    arms: dict[str, _Row]
    turns: dict[str, _Row]
    shared: bool


def _digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _register(blocks: tuple[Block, ...], rows: object, source: object) -> list[_Row]:
    if (
        not _digest(source)
        or not isinstance(rows, list)
        or not 0 < len(blocks) == len(rows) <= 512
        or len({block.key for block in blocks}) != len(blocks)
    ):
        raise ValueError("invalid packing register")
    for block, row in zip(blocks, rows, strict=True):
        if (
            not isinstance(row, dict)
            or row.keys()
            != {
                "block",
                "case",
                "cluster",
                "rep",
                "fingerprint",
                "state",
                "outcome",
                "turns",
            }
            or row["block"] != f"np-{source[:12]}-{block.case.id}-r{block.rep}"
            or row["fingerprint"] != source
            or row["case"] != block.case.id
            or row["cluster"] != block.case.cluster
            or type(row["rep"]) is not int
            or row["rep"] != block.rep
            or row["state"] not in _STATES
        ):
            raise ValueError("invalid packing row identity")
    return rows


def _turns(row: _Row) -> dict[str, _Row]:
    raw_turns = row["turns"]
    if (
        not isinstance(raw_turns, list)
        or len(raw_turns) > 3
        or (raw_turns and row["state"] != "claimed")
    ):
        raise ValueError("invalid packing turns")
    stages = {f"{row['block']}-{stage}": stage for stage in _STAGES}
    turns: dict[str, _Row] = {}
    halted = False
    for turn in raw_turns:
        if not isinstance(turn, dict) or turn.keys() != {
            "run",
            "role",
            "status",
            "prompt_sha256",
            "response_sha256",
            "usage",
        }:
            raise ValueError("invalid packing turn fields")
        run = turn["run"]
        stage = stages.pop(run, None) if isinstance(run, str) else None
        role = "route_review" if stage == "rewrite" else "route_decide"
        if (
            halted
            or stage is None
            or turn["role"] != role
            or not _digest(turn["prompt_sha256"])
        ):
            raise ValueError("invalid packing turn identity")
        if turn["status"] == "callback_error":
            if turn["response_sha256"] is not None or turn["usage"] is not None:
                raise ValueError("invalid failed packing receipt")
            halted = True
        elif turn["status"] == "received" and _digest(turn["response_sha256"]):
            _check_usage(turn["usage"])
        else:
            raise ValueError("invalid received packing receipt")
        turns[stage] = turn
    return turns


def _check_usage(usage: object) -> None:
    if usage is None:
        return
    if not isinstance(usage, dict) or usage.keys() != {
        "served_model",
        "input_tokens",
        "cache_tokens",
        "output_tokens",
    }:
        raise ValueError("invalid packing usage")
    _, parsed = _validate_answer(Answer("", **usage))
    if parsed is None:
        raise ValueError("invalid empty packing usage")


def _outcome(block: Block, value: object) -> tuple[str, int | None, dict[str, _Row]]:
    if not isinstance(value, dict) or value.keys() != {
        "source_status",
        "source_note_bytes",
        "source_note_sha256",
        "arms",
    }:
        raise ValueError("invalid packing outcome")
    status, size, digest = (
        value["source_status"],
        value["source_note_bytes"],
        value["source_note_sha256"],
    )
    if status not in _SOURCES or (
        not (type(size) is int and 1 <= size <= 8192 and _digest(digest))
        if status == "usable"
        else size is not None or digest is not None
    ):
        raise ValueError("invalid packing source metadata")
    arms = value["arms"]
    if not isinstance(arms, dict) or arms.keys() != set(_ARMS):
        raise ValueError("invalid packing arms")
    for arm in arms.values():
        _check_arm(block, arm)
    if size is None:
        if any(arm["status"] != "no_source" for arm in arms.values()):
            raise ValueError("unexpected packing without a usable source")
    elif (
        arms["prefix"]["status"] not in _DECISIONS
        or arms["rewrite"]["status"] == "no_source"
    ):
        raise ValueError("invalid packing arm with a usable source")
    elif size <= _LIMIT:
        if any(
            (arm["packed_bytes"], arm["packed_sha256"]) != (size, digest)
            for arm in arms.values()
        ):
            raise ValueError("unexpected packing of a short source")
    elif not _LIMIT - 3 <= arms["prefix"]["packed_bytes"] <= _LIMIT or any(
        arm["packed_sha256"] == digest for arm in arms.values()
    ):
        raise ValueError("invalid packing of a long source")
    return status, size, arms


def _check_arm(block: Block, arm: object) -> None:
    if not isinstance(arm, dict) or arm.keys() != {
        "status",
        "packed_bytes",
        "packed_sha256",
        "decision_stage",
        "choices",
    }:
        raise ValueError("invalid packing arm fields")
    status, size, digest, stage, choices = (
        arm["status"],
        arm["packed_bytes"],
        arm["packed_sha256"],
        arm["decision_stage"],
        arm["choices"],
    )
    if status not in _STATUSES:
        raise ValueError("invalid packing arm status")
    if status in _DECISIONS:
        if type(size) is not int or not 1 <= size <= _LIMIT or not _digest(digest):
            raise ValueError("invalid packed note metadata")
    elif size is not None or digest is not None or stage is not None:
        raise ValueError("unexpected packed note metadata")
    if status == "complete":
        if (
            not isinstance(choices, dict)
            or choices.keys() != {record.id for record in block.case.records}
            or any(
                not isinstance(choice, str) or choice not in block.packet.choices
                for choice in choices.values()
            )
        ):
            raise ValueError("invalid packing choices")
    elif choices is not None:
        raise ValueError("unexpected choices for an incomplete arm")


def _sequence(
    block: Block, size: int | None, arms: dict[str, _Row], turns: dict[str, _Row]
) -> bool:
    prefix, rewrite = (arms[name] for name in _ARMS)
    shared = size is not None and bool(
        prefix["packed_sha256"] == rewrite["packed_sha256"]
    )
    expected: list[str] = []
    if size is not None and size > _LIMIT:
        expected.append("rewrite")
        received = turns.get("rewrite", {}).get("status") == "received"
        if received == (rewrite["status"] == "missing_rewrite"):
            raise ValueError("invalid rewrite receipt status")
    if shared and prefix != rewrite:
        raise ValueError("inconsistent shared packing decision")
    order = _ARMS if (block.index + block.rep) % 2 == 0 else _ARMS[::-1]
    for name in order:
        arm = arms[name]
        if arm["status"] not in _DECISIONS:
            continue
        stage = "both-decide" if shared else f"{name}-decide"
        if stage not in expected:
            expected.append(stage)
        received = turns.get(stage, {}).get("status") == "received"
        if (
            received == (arm["status"] == "missing_decide")
            or arm["decision_stage"] != stage
        ):
            raise ValueError("invalid packing decision receipt")
    if list(turns) != expected[: len(turns)]:
        raise ValueError("invalid packing turn order")
    return shared


def _evidence(
    blocks: tuple[Block, ...], rows: object, source: object
) -> list[_Evidence]:
    evidence = []
    for block, row in zip(blocks, _register(blocks, rows, source), strict=True):
        status, size, arms = _outcome(block, row["outcome"])
        turns = _turns(row)
        shared = _sequence(block, size, arms, turns)
        evidence.append(
            _Evidence(block, row["state"], status, size, arms, turns, shared)
        )
    return evidence


def _packet_scores(evidence: _Evidence) -> _Results:
    return {
        name: {
            record.id: arm["choices"][record.id] == record.best
            for record in evidence.block.case.records
        }
        for name, arm in evidence.arms.items()
        if arm["status"] == "complete"
    }


def _unchanged(records: tuple[Record, ...], results: _Results) -> _Pair:
    ids = [record.id for record in records if record.old_version == record.new_version]
    if not ids:
        raise ValueError("packing case has no unchanged record")
    values = {
        name: sum(scores[id_] for id_ in ids) / len(ids)
        for name, scores in results.items()
    }
    return values.get("prefix"), values.get("rewrite")


def _cluster(pairs: list[_Pair]) -> _Row:
    complete = [
        rewrite - prefix
        for prefix, rewrite in pairs
        if prefix is not None and rewrite is not None
    ]
    lower = [
        (rewrite if rewrite is not None else 0.0)
        - (prefix if prefix is not None else 1.0)
        for prefix, rewrite in pairs
    ]
    upper = [
        (rewrite if rewrite is not None else 1.0)
        - (prefix if prefix is not None else 0.0)
        for prefix, rewrite in pairs
    ]
    return {
        "scheduled_pairs": len(pairs),
        "complete_pairs": len(complete),
        "gain": sum(value > 0 for value in complete),
        "loss": sum(value < 0 for value in complete),
        "tie": sum(value == 0 for value in complete),
        "paired_complete_delta": fmean(complete) if complete else None,
        "worst_delta": fmean(lower),
        "best_delta": fmean(upper),
    }


def _primary(evidence: list[_Evidence], scores: list[_Results]) -> _Row:
    groups: dict[str, list[_Pair]] = defaultdict(list)
    for item, results in zip(evidence, scores, strict=True):
        groups[item.block.case.cluster].append(
            _unchanged(item.block.case.records, results)
        )
    clusters = {cluster: _cluster(pairs) for cluster, pairs in groups.items()}
    covered = sum(row["complete_pairs"] > 0 for row in clusters.values())
    point = None
    if covered == len(clusters):
        point = fmean(row["paired_complete_delta"] for row in clusters.values())
    else:
        for row in clusters.values():
            row["paired_complete_delta"] = None
    return {
        "kind": "rewrite_minus_prefix_unchanged",
        **{
            field: sum(row[field] for row in clusters.values())
            for field in ("scheduled_pairs", "complete_pairs", "gain", "loss", "tie")
        },
        "clusters_with_pairs": covered,
        "enrolled_clusters": len(clusters),
        "paired_complete_delta": point,
        "worst_delta": fmean(row["worst_delta"] for row in clusters.values()),
        "best_delta": fmean(row["best_delta"] for row in clusters.values()),
        "by_cluster": clusters,
    }


def _bytes(values: list[int]) -> _Row:
    return {
        "count": len(values),
        "total": sum(values),
        "min": min(values, default=None),
        "max": max(values, default=None),
        "mean": fmean(values) if values else None,
    }


def _accuracy(totals: Counter[str]) -> _Row:
    observed = totals["observed"]
    return {
        "scheduled": totals["scheduled"],
        "observed": observed,
        "correct": totals["correct"],
        "accuracy": totals["correct"] / observed if observed else None,
    }


def _arm_summary(name: str, evidence: list[_Evidence], scores: list[_Results]) -> _Row:
    statuses: Counter[str] = Counter(dict.fromkeys(_STATUSES, 0))
    bytes_used: list[int] = []
    unchanged: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    changed_target: Counter[str] = Counter()
    stale = 0
    for item, results in zip(evidence, scores, strict=True):
        arm = item.arms[name]
        statuses[arm["status"]] += 1
        if arm["packed_bytes"] is not None:
            bytes_used.append(arm["packed_bytes"])
        for record in item.block.case.records:
            revised = record.old_version != record.new_version
            counts = (changed,) if revised else (unchanged,)
            if revised and record.best != record.old_best:
                counts += (changed_target,)
                stale += (
                    name in results and arm["choices"][record.id] == record.old_best
                )
            for total in counts:
                total["scheduled"] += 1
                if name in results:
                    total["observed"] += 1
                    total["correct"] += results[name][record.id]
    return {
        "statuses": dict(statuses),
        "packed_bytes": _bytes(bytes_used),
        "unchanged_records": _accuracy(unchanged),
        "changed_records": _accuracy(changed),
        "changed_target_records": {**_accuracy(changed_target), "stale_choices": stale},
    }


def _calls(evidence: list[_Evidence]) -> _Row:
    by_stage = {
        stage: Counter({"received": 0, "callback_error": 0}) for stage in _STAGES
    }
    for item in evidence:
        for stage, turn in item.turns.items():
            by_stage[stage][turn["status"]] += 1
    calls = {stage: counts.total() for stage, counts in by_stage.items()}
    shared = calls["both-decide"]
    return {
        "recorded_physical_calls": sum(calls.values()),
        "by_stage": {stage: dict(counts) for stage, counts in by_stage.items()},
        "shared_final_calls": shared,
        "strategy_cost_exposure": {
            name: {
                "packing_calls": calls["rewrite"] if name == "rewrite" else 0,
                "successor_calls": calls[f"{name}-decide"] + shared,
            }
            for name in _ARMS
        },
    }


def _summarize(evidence: list[_Evidence], scores: list[_Results]) -> _Row:
    sizes = [item.source_bytes for item in evidence if item.source_bytes is not None]
    states: Counter[str] = Counter(dict.fromkeys(_STATES, 0))
    sources: Counter[str] = Counter(dict.fromkeys(_SOURCES, 0))
    for item in evidence:
        states[item.state] += 1
        sources[item.source_status] += 1
    return {
        "primary": _primary(evidence, scores),
        "arms": {name: _arm_summary(name, evidence, scores) for name in _ARMS},
        "counts": {
            "blocks": dict(states),
            "sources": dict(sources),
            "source_note_bytes": _bytes(sizes),
            "sources_within_limit": sum(size <= _LIMIT for size in sizes),
            "sources_over_limit": sum(size > _LIMIT for size in sizes),
            "shared_packed_notes": sum(item.shared for item in evidence),
            "shared_final_complete": sum(
                item.shared and set(result) == set(_ARMS)
                for item, result in zip(evidence, scores, strict=True)
            ),
            **_calls(evidence),
        },
    }


def summarize(blocks: tuple[Block, ...], rows: list[_Row]) -> _Row:
    source = rows[0].get("fingerprint") if rows else None
    evidence = _evidence(blocks, rows, source)
    return _summarize(evidence, [_packet_scores(item) for item in evidence])


def _native_scores(evidence: list[_Evidence]) -> tuple[list[_Results], _Row]:
    world = dispatch._world()
    score = run_path(str(dispatch._TASK / "tests" / "score.py"))["score"]
    worlds: set[str] = set()
    results: list[_Results] = []
    draws = choices_checked = shared = 0
    for item in evidence:
        native: _Results = {}
        cluster = item.block.case.cluster
        for name, expected in _packet_scores(item).items():
            if item.shared and native:
                native[name] = next(iter(native.values()))
                continue
            graded = dispatch._grade(
                world, score, int(cluster[1:]), item.arms[name]["choices"]
            )
            if graded.keys() != expected.keys() or any(
                type(graded[key]) is not bool or graded[key] != correct
                for key, correct in expected.items()
            ):
                raise ValueError("packing choice does not match native score")
            native[name] = graded
            worlds.add(cluster)
            draws += 1
            choices_checked += len(graded)
            shared += item.shared
        results.append(native)
    return results, {
        "observed_worlds": len(worlds),
        "observed_dossiers": draws,
        "observed_native_choices": choices_checked,
        "shared_dossiers": shared,
    }


def stock(report: _Row) -> _Row:
    if report.get("schema") != "seatlab.packing-report/v1":
        raise ValueError("invalid packing report schema")
    native = packet.parse(dispatch.packet_bytes())
    if report.get("packet_sha256") != native.sha256:
        raise ValueError("packing stock requires the default native packet")
    blocks = packet.grid(native)
    evidence = _evidence(blocks, report.get("outcomes"), report.get("source_sha256"))
    results, confirmations = _native_scores(evidence)
    summary = _summarize(evidence, results)
    for field, expected in summary.items():
        if field != "primary" and field not in report:
            continue
        if json.dumps(report.get(field), sort_keys=True) != json.dumps(
            expected, sort_keys=True
        ):
            raise ValueError("published packing projection does not match native score")
    return {
        "mismatches": 0,
        "packet_sha256": native.sha256,
        "source_sha256": report["source_sha256"],
        "registered_worlds": summary["primary"]["enrolled_clusters"],
        **confirmations,
        "primary": summary["primary"],
        "arms": summary["arms"],
    }
