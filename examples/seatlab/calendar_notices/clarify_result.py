import argparse
import json
from collections import Counter
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

from ..visibility import calendar_packet as packet
from .repeat_summary import _ratio
from .route_result import RECEIPT_SCHEMA as ROUTING_SCHEMA
from .visible_replay_summary import _sign_flip_p

RECEIPT_SCHEMA = "seatlab.calendar-bounded-clarification/v1"
SUMMARY_SCHEMA = "seatlab.calendar-bounded-clarification-summary/v1"
REGISTERED_ROUTING_SHA256 = (
    "dbcc9c0ccebdc2eb0c1ea1d927b9decc77b6178b0e1a43d8959536f075a0b97c"
)
_ROUTING_STUDY = "role-stress"
_ROUTING_SOURCES = 48
_SOURCES = 25
_WORLDS = 12
_MIN_GAIN = 5
_MAX_P = Fraction(1, 20)
_MAX_BYTES = 128 * 1024
_UNACCEPTED = ("invalid", "missing", "not_attempted")
_PHASE_STATUSES = ("accepted", *_UNACCEPTED)
_DECISION_STATUSES = ("scored", *_UNACCEPTED)
_NATIVE_ARMS = ("raw", "exact", "sender-exact", "receiver-exact")


class _Decision(NamedTuple):
    status: str
    correct: int | None


class _Source(NamedTuple):
    world: str
    arm: str
    nomination_status: str
    target_nominated: bool | None
    sender_status: str
    sender_exact: bool | None
    target_confirmed: bool | None
    blind: _Decision
    peer: _Decision


type _Cohort = dict[tuple[str, str], tuple[str, str]]


def _registered_cohort(routing: object) -> _Cohort:
    data = packet._object(routing, "schema", "study", "sources")
    if data["schema"] != ROUTING_SCHEMA or data["study"] != _ROUTING_STUDY:
        raise ValueError("expected the registered role-stress routing evidence")
    values = packet._list(data["sources"])
    if len(values) != _ROUTING_SOURCES:
        raise ValueError("wrong number of registered routing sources")
    rows = [packet._object(value) for value in values]
    if any(
        not isinstance(row.get(field), str)
        for row in rows
        for field in ("world", "arm")
    ):
        raise ValueError("invalid routing source world or arm")
    rows.sort(key=lambda row: (row["world"], row["arm"]))
    canonical = json.dumps(
        {**data, "sources": rows},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if sha256(canonical).hexdigest() != REGISTERED_ROUTING_SHA256:
        raise ValueError("routing differs from the preregistered role-stress evidence")
    cohort = {
        (row["world"], row["arm"]): (row["trial_uuid"], row["result_sha256"])
        for row in rows
        if row["route"] == "empty"
    }
    if len(cohort) != _SOURCES or len({world for world, _ in cohort}) != _WORLDS:
        raise ValueError("wrong number of registered empty sources or worlds")
    return cohort


def _decision(value: object) -> _Decision:
    row = packet._object(value, "status", "correct")
    status, correct = row["status"], row["correct"]
    if status not in _DECISION_STATUSES:
        raise ValueError("invalid receiver decision status")
    if status == "scored":
        if type(correct) is not int or correct not in (0, 1):
            raise ValueError("scored receiver decision requires a binary integer grade")
    elif correct is not None:
        raise ValueError("unscored receiver decision cannot have a grade")
    return _Decision(status, correct)


def _phase(status: Any, *flags: object) -> str:
    if status not in _PHASE_STATUSES:
        raise ValueError("invalid nomination or sender status")
    if status == "accepted":
        if any(type(flag) is not bool for flag in flags):
            raise ValueError(
                "accepted nomination or sender phase requires boolean flags"
            )
    elif any(flag is not None for flag in flags):
        raise ValueError("unaccepted nomination or sender phase cannot have flags")
    return status


def _source(row: dict[str, Any]) -> _Source:
    target_nominated = row["target_nominated"]
    sender_exact, target_confirmed = row["sender_exact"], row["target_confirmed"]
    nomination = _phase(row["nomination_status"], target_nominated)
    sender = _phase(row["sender_status"], sender_exact, target_confirmed)
    blind, peer = _decision(row["blind"]), _decision(row["peer"])
    if nomination != "accepted" and sender != "not_attempted":
        raise ValueError("sender phase requires an accepted nomination")
    if sender != "accepted" and (
        blind.status != "not_attempted" or peer.status != "not_attempted"
    ):
        raise ValueError("receiver decisions require an accepted sender reply")
    if target_confirmed is True and target_nominated is not True:
        raise ValueError("cannot confirm a target that was not nominated")
    return _Source(
        row["world"],
        row["arm"],
        nomination,
        target_nominated,
        sender,
        sender_exact,
        target_confirmed,
        blind,
        peer,
    )


def _sources(receipt: object, cohort: _Cohort) -> list[_Source]:
    data = packet._object(receipt, "schema", "sources")
    if data["schema"] != RECEIPT_SCHEMA:
        raise ValueError("unexpected bounded clarification receipt schema")
    rows = packet._list(data["sources"])
    if len(rows) != len(cohort):
        raise ValueError("expected every registered empty source exactly once")
    seen = set()
    sources = []
    for value in rows:
        row = packet._object(
            value,
            "world",
            "arm",
            "trial_uuid",
            "result_sha256",
            "nomination_status",
            "target_nominated",
            "sender_status",
            "sender_exact",
            "target_confirmed",
            "blind",
            "peer",
        )
        world, arm = row["world"], row["arm"]
        if not isinstance(world, str) or not isinstance(arm, str):
            raise TypeError("invalid world or native arm")
        key = world, arm
        if key not in cohort or key in seen:
            raise ValueError("unexpected or repeated registered empty source")
        if (row["trial_uuid"], row["result_sha256"]) != cohort[key]:
            raise ValueError(
                "clarification source identity differs from routing evidence"
            )
        sources.append(_source(row))
        seen.add(key)
    return sources


def _stage_coverage(sources: list[_Source]) -> dict[str, dict[str, int]]:
    stages = (
        ("nomination", _PHASE_STATUSES, [row.nomination_status for row in sources]),
        ("sender", _PHASE_STATUSES, [row.sender_status for row in sources]),
        ("blind", _DECISION_STATUSES, [row.blind.status for row in sources]),
        ("peer", _DECISION_STATUSES, [row.peer.status for row in sources]),
    )
    return {
        stage: {
            "expected": len(sources),
            **{status: observed.count(status) for status in statuses},
        }
        for stage, statuses, observed in stages
    }


def _arms(sources: list[_Source]) -> dict[str, dict[str, int]]:
    arms = {}
    for arm, decisions in (
        ("blind", [source.blind for source in sources]),
        ("peer", [source.peer for source in sources]),
    ):
        grades = [
            decision.correct for decision in decisions if decision.correct is not None
        ]
        arms[arm] = {"correct": sum(grades), "denominator": len(grades)}
    return arms


def _outcomes(differences: list[int], expected: int) -> dict[str, int]:
    return {
        "expected": expected,
        "matched": len(differences),
        "wins": sum(difference > 0 for difference in differences),
        "ties": differences.count(0),
        "losses": sum(difference < 0 for difference in differences),
    }


def _primary(sources: list[_Source]) -> tuple[dict[str, Any], str | None]:
    expected = Counter(source.world for source in sources)
    by_world: dict[str, list[int]] = {world: [] for world in expected}
    pairs = []
    source_differences = []
    for source in sources:
        blind, peer = source.blind.correct, source.peer.correct
        if blind is None or peer is None:
            continue
        difference = peer - blind
        pairs.append(source)
        source_differences.append(difference)
        by_world[source.world].append(difference)
    world_differences = [
        sum(differences)
        for world, differences in by_world.items()
        if len(differences) == expected[world]
    ]
    report: dict[str, Any] = {
        "paired_arms": _arms(pairs),
        "source_outcomes": _outcomes(source_differences, _SOURCES),
        "world_outcomes": _outcomes(world_differences, _WORLDS),
        "minimum_gain": _MIN_GAIN,
        "maximum_p": _ratio(_MAX_P),
        "peer_minus_blind": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if len(pairs) != _SOURCES:
        return report, None
    gain = sum(world_differences)
    p = _sign_flip_p(world_differences)
    report.update(
        peer_minus_blind=gain,
        mean_delta=float(Fraction(gain, _SOURCES)),
        two_sided_sign_flip_p=_ratio(p),
    )
    verdict = "pass" if gain >= _MIN_GAIN and p <= _MAX_P else "not_established"
    return report, verdict


def _flag_count(flags: list[bool | None]) -> dict[str, int]:
    return {
        "count": flags.count(True),
        "denominator": len(flags) - flags.count(None),
    }


def _group(sources: list[_Source]) -> dict[str, Any]:
    return {"sources": len(sources), "arms": _arms(sources)}


def _secondary(sources: list[_Source]) -> dict[str, Any]:
    return {
        "target_nominated": _flag_count(
            [source.target_nominated for source in sources]
        ),
        "sender_exact": _flag_count([source.sender_exact for source in sources]),
        "target_confirmed": _flag_count(
            [source.target_confirmed for source in sources]
        ),
        "by_target_nominated": {
            name: _group(
                [source for source in sources if source.target_nominated is flag]
            )
            for name, flag in (("nominated", True), ("not_nominated", False))
        },
        "by_native_arm": {
            arm: _group([source for source in sources if source.arm == arm])
            for arm in _NATIVE_ARMS
        },
    }


def summarize(receipt: object, routing: object) -> dict[str, Any]:
    sources = _sources(receipt, _registered_cohort(routing))
    primary, verdict = _primary(sources)
    return {
        "schema": SUMMARY_SCHEMA,
        "routing_study": _ROUTING_STUDY,
        "registered_routing_sha256": REGISTERED_ROUTING_SHA256,
        "evidence_complete": verdict is not None,
        "verdict": verdict,
        "stage_coverage": _stage_coverage(sources),
        "primary": primary,
        "secondary": _secondary(sources),
        "proof_scope": (
            "Binds the source census and native trial identities to the preregistered "
            "routing evidence. Accepts the reported flags and grades. Does not validate "
            "private messages or direct settlements. Check the charges separately "
            "before publication."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize registered bounded calendar clarification evidence.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    parser.add_argument("--routing", type=Path, required=True, metavar="ROUTING")
    args = parser.parse_args(argv)
    try:
        receipt = packet._json(packet._read(args.receipt, _MAX_BYTES))
        routing = packet._json(packet._read(args.routing, _MAX_BYTES))
        result = summarize(receipt, routing)
    except (OSError, TypeError, ValueError, RecursionError):
        parser.error("invalid or unreadable bounded clarification receipt or routing")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
