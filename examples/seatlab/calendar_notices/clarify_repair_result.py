import argparse
import json
from collections import Counter
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

from ..visibility import calendar_packet as packet
from . import clarify_result as parent_result
from . import route_result
from .repeat_summary import _ratio
from .visible_replay_summary import _sign_flip_p

RECEIPT_SCHEMA = "seatlab.calendar-clarification-repair/v1"
SUMMARY_SCHEMA = "seatlab.calendar-clarification-repair-summary/v1"
REGISTERED_PARENT_SHA256 = (
    "f7ebb4d487d14395540f19252daf73bd73d0a47743a74ec62434a0b29664bdde"
)
_ROUTING = "calendar-booking-route-role-stress.json"
_ARMS = ("model", "local")
_REPEATS = 2
_MIN_GAIN = _REPEATS * parent_result._MIN_GAIN


class _Call(NamedTuple):
    run: str
    decision: parent_result._Decision


class _Pair(NamedTuple):
    model: _Call
    local: _Call


class _Source(NamedTuple):
    world: str
    arm: str
    nominated: bool
    repeats: tuple[_Pair, _Pair]


type _Registration = dict[tuple[str, str], tuple[str, str, bool, bool]]


def _registration(parent: object) -> _Registration:
    routing = route_result._read(route_result._RESULTS / _ROUTING)
    cohort = parent_result._registered_cohort(routing)
    sources = parent_result._sources(parent, cohort)
    registration = {}
    for source in sources:
        key = source.world, source.arm
        nominated, same_reply = source.target_nominated, source.sender_exact
        if nominated is None or same_reply is None:
            raise ValueError("repair requires an accepted parent nomination and reply")
        registration[key] = (*cohort[key], nominated, same_reply)
    data = packet._object(parent)
    rows = sorted(data["sources"], key=lambda row: (row["world"], row["arm"]))
    canonical = json.dumps(
        {**data, "sources": rows},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if sha256(canonical).hexdigest() != REGISTERED_PARENT_SHA256:
        raise ValueError("parent differs from the registered clarification evidence")
    return registration


def _call(value: object, run: str) -> _Call:
    row = packet._object(value, "run", "status", "correct")
    if row["run"] != run:
        raise ValueError("unexpected physical repair run")
    return _Call(
        run,
        parent_result._decision({"status": row["status"], "correct": row["correct"]}),
    )


def _repeat(value: object, prefix: str, shared: bool) -> _Pair:
    row = packet._object(value, *_ARMS)
    if shared:
        run = f"{prefix}-same"
        model, local = _call(row["model"], run), _call(row["local"], run)
        if model != local:
            raise ValueError("shared physical run has conflicting logical grades")
        return _Pair(model, local)
    return _Pair(
        _call(row["model"], f"{prefix}-model"),
        _call(row["local"], f"{prefix}-local"),
    )


def _sources(receipt: object, registration: _Registration) -> list[_Source]:
    data = packet._object(receipt, "schema", "sources")
    if data["schema"] != RECEIPT_SCHEMA:
        raise ValueError("unexpected clarification repair receipt schema")
    rows = packet._list(data["sources"])
    if len(rows) != len(registration):
        raise ValueError("expected every registered repair source exactly once")
    sources = []
    seen = set()
    for raw in rows:
        row = packet._object(
            raw,
            "world",
            "arm",
            "trial_uuid",
            "result_sha256",
            "nominated",
            "same_reply",
            "repeats",
        )
        world, arm = row["world"], row["arm"]
        if not isinstance(world, str) or not isinstance(arm, str):
            raise TypeError("invalid world or native arm")
        key = world, arm
        if key not in registration or key in seen:
            raise ValueError("unexpected or repeated registered repair source")
        trial, digest, nominated, shared = registration[key]
        if (row["trial_uuid"], row["result_sha256"]) != (trial, digest) or (
            row["nominated"] is not nominated or row["same_reply"] is not shared
        ):
            raise ValueError("repair identity or flags differ from the parent evidence")
        repeats = packet._list(row["repeats"])
        if len(repeats) != _REPEATS:
            raise ValueError("expected both repeats, including unscored runs")
        prefix = f"csfix-{world}-{arm}"
        decisions = (
            _repeat(repeats[0], f"{prefix}-r0", shared),
            _repeat(repeats[1], f"{prefix}-r1", shared),
        )
        sources.append(_Source(world, arm, nominated, decisions))
        seen.add(key)
    return sources


def _calls(sources: list[_Source]) -> tuple[list[_Call], dict[str, _Call]]:
    logical = []
    physical = {}
    for source in sources:
        for pair in source.repeats:
            logical.extend(pair)
            runs = (pair.model,) if pair.model.run == pair.local.run else pair
            for call in runs:
                if call.run in physical:
                    raise ValueError("repeated physical repair run")
                physical[call.run] = call
    return logical, physical


def _coverage(calls: list[_Call]) -> dict[str, int]:
    counts = Counter(call.decision.status for call in calls)
    return {
        "expected": len(calls),
        **{status: counts[status] for status in parent_result._DECISION_STATUSES},
    }


def _arm_report(sources: list[_Source], side: int) -> dict[str, Any]:
    repeats = [(s.repeats[0][side], s.repeats[1][side]) for s in sources]
    calls = [call for pair in repeats for call in pair]
    grades = [
        call.decision.correct for call in calls if call.decision.correct is not None
    ]
    matched = [
        (left.decision.correct, right.decision.correct)
        for left, right in repeats
        if left.decision.correct is not None and right.decision.correct is not None
    ]
    return {
        "correct": sum(grades),
        "denominator": len(grades),
        "coverage": _coverage(calls),
        "grade_disagreement": {
            "disagreements": sum(left != right for left, right in matched),
            "denominator": len(matched),
        },
    }


def _deltas(source: _Source) -> list[int]:
    differences = []
    for model, local in source.repeats:
        if model.decision.correct is not None and local.decision.correct is not None:
            differences.append(local.decision.correct - model.decision.correct)
    return differences


def _group(sources: list[_Source]) -> dict[str, Any]:
    differences = [delta for source in sources for delta in _deltas(source)]
    expected = len(sources) * _REPEATS
    outcomes = parent_result._outcomes(differences, expected)
    return {
        "sources": len(sources),
        "arms": {arm: _arm_report(sources, side) for side, arm in enumerate(_ARMS)},
        "repeat_outcomes": outcomes,
        "helpful": outcomes["wins"],
        "harmful": outcomes["losses"],
        "local_minus_model": sum(differences) if len(differences) == expected else None,
    }


def _primary(sources: list[_Source]) -> tuple[dict[str, Any], str | None]:
    source_deltas = []
    source_differences = []
    world_deltas: dict[str, int | None] = {source.world: 0 for source in sources}
    for source in sources:
        differences = _deltas(source)
        delta = sum(differences) if len(differences) == _REPEATS else None
        source_deltas.append(
            {"world": source.world, "arm": source.arm, "local_minus_model": delta}
        )
        if delta is not None:
            source_differences.append(delta)
        current = world_deltas[source.world]
        world_deltas[source.world] = (
            None if current is None or delta is None else current + delta
        )
    world_differences = [delta for delta in world_deltas.values() if delta is not None]
    report = {
        **_group(sources),
        "source_deltas": source_deltas,
        "world_deltas": world_deltas,
        "source_outcomes": parent_result._outcomes(source_differences, len(sources)),
        "world_outcomes": parent_result._outcomes(world_differences, len(world_deltas)),
        "minimum_gain": _MIN_GAIN,
        "maximum_p": _ratio(parent_result._MAX_P),
        "net_gain_per_25": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if len(world_differences) != parent_result._WORLDS:
        return report, None
    gain = sum(world_differences)
    p = _sign_flip_p(world_differences)
    report.update(
        net_gain_per_25=_ratio(Fraction(gain, _REPEATS)),
        mean_delta=float(Fraction(gain, len(sources) * _REPEATS)),
        two_sided_sign_flip_p=_ratio(p),
    )
    verdict = (
        "pass" if gain >= _MIN_GAIN and p <= parent_result._MAX_P else "not_established"
    )
    return report, verdict


def summarize(receipt: object, parent: object, cost: object) -> dict[str, Any]:
    sources = _sources(receipt, _registration(parent))
    logical, physical = _calls(sources)
    charges = route_result._cost(cost, set(physical))
    if any(
        call.decision.status == "not_attempted" and charges.get(run) is not None
        for run, call in physical.items()
    ):
        raise ValueError("unattempted physical run has a settled charge")
    physical_cost = route_result._economics(list(physical), charges)
    primary, verdict = _primary(sources)
    if physical_cost["missing_calls"] or physical_cost["held_calls"]:
        verdict = None
    return {
        "schema": SUMMARY_SCHEMA,
        "registered_parent_sha256": REGISTERED_PARENT_SHA256,
        "evidence_complete": verdict is not None,
        "verdict": verdict,
        "calls": {
            "logical": _coverage(logical),
            "physical": _coverage(list(physical.values())),
            "shared_physical": len(logical) - len(physical),
        },
        "cost": {
            "physical": physical_cost,
            "logical": route_result._economics([call.run for call in logical], charges),
        },
        "primary": primary,
        "by_nominated": {
            label: _group([source for source in sources if source.nominated is flag])
            for label, flag in (("nominated", True), ("not_nominated", False))
        },
        "proof_scope": (
            "Binds repair coverage and shared runs to the public parent receipt. "
            "Logical cost counts a shared charge in both arms; physical cost counts "
            "it once. Accepts reported grades and validates public charge arithmetic. "
            "Does not authenticate private prompts, replies, or provider settlements."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize registered calendar clarification repair evidence.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    parser.add_argument("--parent", type=Path, required=True, metavar="PARENT")
    parser.add_argument("--cost", type=Path, required=True, metavar="COST")
    args = parser.parse_args(argv)
    try:
        result = summarize(
            route_result._read(args.receipt),
            route_result._read(args.parent),
            route_result._read(args.cost),
        )
    except (OSError, TypeError, ValueError, RecursionError):
        parser.error("invalid or unreadable clarification repair evidence or cost")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
