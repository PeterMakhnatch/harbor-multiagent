from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import fmean
from typing import Any

from .packet import Block, Record


_POLICIES = ("bound", "anonymous")
_SELECT_FAILURES = ("missing_select", "invalid_select")
_DECIDE_FAILURES = ("missing_decide", "invalid_decide")
_ARM_STATUSES = ("no_note", *_SELECT_FAILURES, *_DECIDE_FAILURES, "complete")
_STATES = ("unclaimed", "unbound", "claimed")
_NOTES = ("missing", "invalid", "valid")
_DRAW_KINDS = ("neither_selected", "one_selected", "same_record", "different_records")
_ROLES = {"note": "route_review", "both-decide": "route_decide"} | {
    f"{policy}-{action}": "route_decide"
    for policy in _POLICIES
    for action in ("select", "decide")
}

type _Row = dict[str, Any]
type _Pair = tuple[float | None, float | None, bool]


def _register(blocks: tuple[Block, ...], public: Sequence[object]) -> dict[str, _Row]:
    register = {block.key: block for block in blocks}
    rows: dict[str, _Row] = {}
    if len(register) != len(blocks) or len(public) != len(blocks):
        raise ValueError("invalid handoff register size")
    for row in public:
        if not isinstance(row, dict):
            raise ValueError("invalid handoff register row")
        key = row.get("block")
        if not isinstance(key, str) or key not in register or key in rows:
            raise ValueError("invalid handoff register key")
        block = register[key]
        if (
            row.get("fingerprint") != block.fingerprint
            or row.get("case") != block.case.id
            or row.get("cluster") != block.case.cluster
            or type(row.get("rep")) is not int
            or row["rep"] != block.rep
            or row.get("state") not in _STATES
        ):
            raise ValueError("invalid handoff register row")
        rows[key] = row
    return rows


def _arms(block: Block, outcome: object) -> tuple[str, dict[str, _Row], bool]:
    if not isinstance(outcome, dict) or outcome.get("note_status") not in _NOTES:
        raise ValueError("invalid handoff note status")
    note = outcome["note_status"]
    arms = outcome.get("arms")
    if not isinstance(arms, dict) or arms.keys() != set(_POLICIES):
        raise ValueError("invalid handoff arms")
    ids = {record.id for record in block.case.records}
    for arm in arms.values():
        if not isinstance(arm, dict) or arm.keys() != {
            "status",
            "lookup",
            "decision_stage",
            "choices",
        }:
            raise ValueError("invalid handoff arm")
        status, lookup, choices = arm["status"], arm["lookup"], arm["choices"]
        if status not in _ARM_STATUSES or (status == "no_note") != (note != "valid"):
            raise ValueError("invalid handoff arm status")
        if status == "complete" or status in _DECIDE_FAILURES:
            if not isinstance(lookup, str) or lookup not in ids:
                raise ValueError("invalid handoff lookup")
        elif lookup is not None:
            raise ValueError("unexpected handoff lookup")
        if status == "complete":
            if (
                not isinstance(choices, dict)
                or choices.keys() != ids
                or any(value not in block.packet.choices for value in choices.values())
            ):
                raise ValueError("invalid handoff delivered choices")
        elif choices is not None:
            raise ValueError("unexpected handoff delivered choices")
    bound, anonymous = (arms[policy] for policy in _POLICIES)
    shared = bound["lookup"] is not None and bound["lookup"] == anonymous["lookup"]
    for policy, arm in arms.items():
        stage = (
            "both-decide"
            if shared
            else f"{policy}-decide"
            if arm["lookup"] is not None
            else None
        )
        if arm["decision_stage"] != stage and not (
            arm["decision_stage"] is None and arm["status"] == "missing_decide"
        ):
            raise ValueError("invalid handoff decision stage")
    if shared and (bound["status"], bound["choices"]) != (
        anonymous["status"],
        anonymous["choices"],
    ):
        raise ValueError("inconsistent shared handoff decision")
    return note, arms, shared


def _count_turns(block: Block, row: _Row, counts: dict[str, Counter[str]]) -> None:
    turns = row.get("turns")
    if not isinstance(turns, list):
        raise ValueError("invalid handoff turns")
    expected = {f"{block.key}-{stage}": role for stage, role in _ROLES.items()}
    for turn in turns:
        run = turn.get("run") if isinstance(turn, dict) else None
        if not isinstance(run, str) or run not in expected:
            raise ValueError("invalid handoff turn key")
        role = expected.pop(run)
        status = turn.get("status")
        if turn.get("role") != role or status not in ("received", "callback_error"):
            raise ValueError("invalid handoff turn status")
        counts[role][status] += 1


def _cluster(pairs: list[_Pair]) -> _Row:
    differences: list[float] = []
    worst, best = 0.0, 0.0
    for bound, anonymous, shared in pairs:
        if bound is not None and anonymous is not None:
            differences.append(bound - anonymous)
        if not shared:
            worst += (0.0 if bound is None else bound) - (
                1.0 if anonymous is None else anonymous
            )
            best += (1.0 if bound is None else bound) - (
                0.0 if anonymous is None else anonymous
            )
    gain = sum(delta > 0 for delta in differences)
    loss = sum(delta < 0 for delta in differences)
    return {
        "complete_pairs": len(differences),
        "scheduled_pairs": len(pairs),
        "gain": gain,
        "loss": loss,
        "tie": len(differences) - gain - loss,
        "paired_complete_delta": fmean(differences) if differences else None,
        "worst_delta": worst / len(pairs),
        "best_delta": best / len(pairs),
    }


def _comparison(groups: dict[str, list[_Pair]]) -> _Row:
    clusters = {cluster: _cluster(pairs) for cluster, pairs in groups.items()}
    complete = [
        row["paired_complete_delta"]
        for row in clusters.values()
        if row["complete_pairs"]
    ]
    return {
        "kind": "bound_minus_anonymous",
        **{
            key: sum(row[key] for row in clusters.values())
            for key in ("complete_pairs", "scheduled_pairs", "gain", "loss", "tie")
        },
        "clusters_with_pairs": len(complete),
        "enrolled_clusters": len(clusters),
        "paired_complete_delta": fmean(complete)
        if complete and len(complete) == len(clusters)
        else None,
        "worst_delta": fmean(row["worst_delta"] for row in clusters.values())
        if clusters
        else None,
        "best_delta": fmean(row["best_delta"] for row in clusters.values())
        if clusters
        else None,
        "by_cluster": clusters,
    }


def _score(
    totals: Counter[str], records: tuple[Record, ...], choices: dict[str, str] | None
) -> None:
    totals["scheduled"] += len(records)
    if choices is not None:
        totals["observed"] += len(records)
        totals["correct"] += sum(
            choices[record.id] == record.best for record in records
        )


def _accuracy(totals: Counter[str]) -> _Row:
    scheduled, observed, correct = (
        totals[key] for key in ("scheduled", "observed", "correct")
    )
    return {
        "scheduled": scheduled,
        "observed": observed,
        "correct": correct,
        "accuracy": correct / observed if observed else None,
    }


def _arm_summary(observations: list[tuple[Block, _Row, Record | None]]) -> _Row:
    statuses: Counter[str] = Counter(dict.fromkeys(_ARM_STATUSES, 0))
    all_records: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    source_sensitive: Counter[str] = Counter()
    stable: Counter[str] = Counter()
    selected = read_changed = 0
    for block, arm, target in observations:
        statuses[arm["status"]] += 1
        choices = arm["choices"]
        _score(all_records, block.case.records, choices)
        if target is None:
            _score(stable, block.case.records, choices)
            continue
        selected += arm["lookup"] is not None
        read_changed += arm["lookup"] == target.id
        _score(changed, (target,), choices)
        if target.old_best != target.best:
            _score(source_sensitive, (target,), choices)
    return {
        "statuses": dict(statuses),
        "changed_read": {
            "scheduled": changed["scheduled"],
            "selected": selected,
            "read_changed": read_changed,
            "rate_among_selected": read_changed / selected if selected else None,
        },
        "all_records": _accuracy(all_records),
        "changed_records": _accuracy(changed),
        "source_sensitive_changes": _accuracy(source_sensitive),
        "stable_records": _accuracy(stable),
    }


def summarize(blocks: tuple[Block, ...], public: Sequence[object]) -> _Row:
    rows = _register(blocks, public)
    changed: dict[str, list[_Pair]] = defaultdict(list)
    stable: dict[str, list[_Pair]] = defaultdict(list)
    observations: dict[str, list[tuple[Block, _Row, Record | None]]] = {
        policy: [] for policy in _POLICIES
    }
    notes: Counter[str] = Counter(dict.fromkeys(_NOTES, 0))
    states: Counter[str] = Counter(dict.fromkeys(_STATES, 0))
    draws: Counter[str] = Counter(dict.fromkeys(_DRAW_KINDS, 0))
    physical = {
        role: Counter({"received": 0, "callback_error": 0})
        for role in dict.fromkeys(_ROLES.values())
    }
    shared_complete = 0
    for block in blocks:
        row = rows[block.key]
        note, arms, shared = _arms(block, row.get("outcome"))
        if row["state"] != "claimed" and (note != "missing" or row.get("turns") != []):
            raise ValueError("unexpected unclaimed handoff outcome")
        notes[note] += 1
        states[row["state"]] += 1
        _count_turns(block, row, physical)
        reads = sum(arm["lookup"] is not None for arm in arms.values())
        draw = (
            "same_record"
            if shared
            else "different_records"
            if reads == 2
            else "one_selected"
            if reads
            else "neither_selected"
        )
        draws[draw] += 1
        shared_complete += shared and arms["bound"]["status"] == "complete"
        target = next(
            (
                record
                for record in block.case.records
                if record.old_version != record.new_version
            ),
            None,
        )
        records = (target,) if target is not None else block.case.records
        scores: dict[str, float | None] = {}
        for policy, arm in arms.items():
            observations[policy].append((block, arm, target))
            choices = arm["choices"]
            scores[policy] = (
                None
                if choices is None
                else sum(choices[record.id] == record.best for record in records)
                / len(records)
            )
        groups = changed if target is not None else stable
        groups[block.case.cluster].append(
            (scores["bound"], scores["anonymous"], shared)
        )
    return {
        "primary": _comparison(changed),
        "stable_controls": _comparison(stable),
        "arms": {policy: _arm_summary(arm) for policy, arm in observations.items()},
        "counts": {
            "blocks": dict(states),
            "notes": dict(notes),
            "selection_draws": dict(draws),
            "shared_final_complete": shared_complete,
            "physical_turns": {role: dict(status) for role, status in physical.items()},
        },
    }
