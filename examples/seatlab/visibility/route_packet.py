"""Capture and check the fixed, offline SeatLab route packet.

The packet includes the scorer's key. Role models receive only projected prompts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

if TYPE_CHECKING:
    from . import listener

_SCHEMA = "seatlab.visibility-route-packet/v1"
_MAX_PACKET_BYTES = 65_536
_MAX_SEQ = (1 << 53) - 1  # Exact in JSON implementations that use binary64.
_ROUNDS = range(1, 7)
_POLICIES = range(3)
_REASONS = frozenset(
    {
        "missing_event_log",
        "duplicate_event_key",
        "invalid_event_log",
        "missing_original_decision",
        "invalid_original_decision_event",
        "missing_predecision_fetch",
        "invalid_predecision_fetch",
        "incomplete_predecision_fetch",
        "invalid_report_reference",
        "invalid_numeric_report",
        "invalid_observation_truth",
        "conflicting_observation_truth",
        "missing_observation_truth",
        "tied_observation_truth",
    }
)
_EVIDENCE_KEYS = (
    "reports",
    "best",
    "truth",
    "report_seqs",
    "fetch_seq",
    "decision_seq",
    "truth_seqs",
)

TruthRow = tuple[int, int, int]
Truth = tuple[TruthRow, TruthRow]


@dataclass(frozen=True)
class Case:
    """One source round. Numeric rows use the original physical route order."""

    policy: int
    round: int
    reports: tuple[listener.Report, ...] | None
    best: int | None
    source_sha256: str | None
    trial_sha256: str
    reason: str | None = None
    truth: Truth | None = None
    report_seqs: tuple[int, ...] | None = None
    fetch_seq: int | None = None
    decision_seq: int | None = None
    truth_seqs: tuple[int, ...] | None = None


class _InvalidPacket(ValueError):
    """A failure message safe to publish without packet or archive content."""


def _encode(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _object(value: object, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != set(keys):
        raise _InvalidPacket("unexpected packet fields")
    return value


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidPacket("duplicate packet key")
        result[key] = value
    return result


def _noninteger(_: str) -> NoReturn:
    raise _InvalidPacket("invalid packet number")


def _digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _InvalidPacket("invalid SHA-256 commitment")
    return value


def _integer(value: object, lo: int, hi: int) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise _InvalidPacket("invalid packet integer")
    return value


def _array(value: object, size: int) -> list[Any]:
    if not isinstance(value, list) or len(value) != size:
        raise _InvalidPacket("unexpected packet array shape")
    return value


def _rows(value: object, width: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    a, b = _array(value, 2)
    return (
        tuple(_integer(score, 0, 100) for score in _array(a, width)),
        tuple(_integer(score, 0, 100) for score in _array(b, width)),
    )


def _report(value: object) -> listener.Report:
    (a0, a1), (b0, b1) = _rows(value, 2)
    return (a0, a1), (b0, b1)


def _truth(value: object) -> Truth:
    (a0, a1, a2), (b0, b1, b2) = _rows(value, 3)
    return (a0, a1, a2), (b0, b1, b2)


def _best(truth: Truth) -> int:
    a, b = map(sum, truth)
    if a == b:
        raise _InvalidPacket("tied physical route scores")
    return int(b > a)


def _case(value: object, policy: int, round_number: int, seen: set[int]) -> Case:
    row = _object(
        value,
        "policy",
        "round",
        "source_sha256",
        "trial_sha256",
        "reason",
        *_EVIDENCE_KEYS,
    )
    if (
        _integer(row["policy"], 0, 2) != policy
        or _integer(row["round"], 1, 6) != round_number
    ):
        raise _InvalidPacket("unexpected policy or round order")
    trial_sha = _digest(row["trial_sha256"])
    source_sha = None if row["source_sha256"] is None else _digest(row["source_sha256"])
    reason = row["reason"]
    if reason is not None:
        if not isinstance(reason, str) or reason not in _REASONS:
            raise _InvalidPacket("unknown ineligible reason")
        if (reason == "missing_event_log") != (source_sha is None) or any(
            row[key] is not None for key in _EVIDENCE_KEYS
        ):
            raise _InvalidPacket("inconsistent ineligible case")
        return Case(policy, round_number, None, None, source_sha, trial_sha, reason)
    if source_sha is None:
        raise _InvalidPacket("eligible case without a ledger commitment")

    reports = tuple(_report(report) for report in _array(row["reports"], 3))
    truth = _truth(row["truth"])
    best = _integer(row["best"], 0, 1)
    if best != _best(truth):
        raise _InvalidPacket("physical route scores disagree with best route")

    fetch = _integer(row["fetch_seq"], 1, _MAX_SEQ)
    decision = _integer(row["decision_seq"], fetch + 1, _MAX_SEQ)
    # Report references follow seat order, not arrival order. Only reports and
    # fetch must precede the decision. Private reads can occur later.
    report_seqs = tuple(
        _integer(seq, 1, fetch - 1) for seq in _array(row["report_seqs"], 3)
    )
    raw_truth_seqs = row["truth_seqs"]
    if not isinstance(raw_truth_seqs, list) or len(raw_truth_seqs) < 3:
        raise _InvalidPacket("insufficient private truth references")
    truth_seqs = tuple(_integer(seq, 2, _MAX_SEQ) for seq in raw_truth_seqs)
    if any(a >= b for a, b in zip(truth_seqs, truth_seqs[1:])):
        raise _InvalidPacket("private truth references are not in ledger order")
    for seq in (*report_seqs, fetch, decision, *truth_seqs):
        if seq in seen:
            raise _InvalidPacket("reused event reference within one ledger")
        seen.add(seq)
    return Case(
        policy,
        round_number,
        reports,
        best,
        source_sha,
        trial_sha,
        truth=truth,
        report_seqs=report_seqs,
        fetch_seq=fetch,
        decision_seq=decision,
        truth_seqs=truth_seqs,
    )


def _decode(raw: bytes) -> tuple[Case, ...]:
    if len(raw) > _MAX_PACKET_BYTES:
        raise _InvalidPacket("packet exceeds the size cap")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_float=_noninteger,
            parse_constant=_noninteger,
        )
    except _InvalidPacket:
        raise
    except (ValueError, RecursionError):
        raise _InvalidPacket("invalid packet JSON") from None
    packet = _object(value, "schema", "cases")
    if packet["schema"] != _SCHEMA:
        raise _InvalidPacket("unexpected packet schema")
    rows = iter(_array(packet["cases"], len(_POLICIES) * len(_ROUNDS)))
    cases: list[Case] = []
    trials: set[str] = set()
    ledgers: set[str] = set()
    for policy in _POLICIES:
        event_refs: set[int] = set()
        group = [_case(next(rows), policy, rnd, event_refs) for rnd in _ROUNDS]
        trial, ledger = group[0].trial_sha256, group[0].source_sha256
        if any(
            (case.trial_sha256, case.source_sha256) != (trial, ledger) for case in group
        ):
            raise _InvalidPacket("source changes within a policy")
        if trial in trials or (ledger is not None and ledger in ledgers):
            raise _InvalidPacket("source repeats across policies")
        trials.add(trial)
        if ledger is not None:
            ledgers.add(ledger)
        cases.extend(group)
    if raw != _encode(value):
        raise _InvalidPacket("packet is not canonical JSON")
    return tuple(cases)


def load(path: Path, expected_sha256: str) -> tuple[Case, ...]:
    """Verify the expected byte commitment, then load all 18 fixed positions."""
    expected = _digest(expected_sha256)
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_PACKET_BYTES + 1)
    except OSError:
        raise _InvalidPacket("cannot read route packet") from None
    if len(raw) > _MAX_PACKET_BYTES:
        raise _InvalidPacket("packet exceeds the size cap")
    if sha256(raw).hexdigest() != expected:
        raise _InvalidPacket("unexpected route packet SHA-256")
    return _decode(raw)


@dataclass(frozen=True)
class _Source:
    trial_sha256: str
    path: Path
    expected_sha256: str | None


def _selected_sources(root: Path) -> tuple[_Source, ...]:
    from . import listener

    selected = tuple(
        case for case in listener.build_schedule(root).cases if case.round_number == 1
    )
    if tuple(case.policy for case in selected) != listener.POLICIES:
        raise _InvalidPacket("unexpected listener policy order")
    targets = {(case.policy, case.uuid) for case in selected}
    paths: dict[tuple[str, str], Path] = {}
    for record in listener.run_records(root):
        if (
            record.family != listener.FAMILY
            or record.condition not in listener.POLICIES
        ):
            continue
        key = record.condition, str(UUID(record.key))
        if key not in targets:
            continue
        path = (record.directory / listener._EVENT_LOG).resolve()
        if not path.is_relative_to(root) or (key in paths and paths[key] != path):
            raise _InvalidPacket("ambiguous or out-of-scope source ledger")
        paths[key] = path
    if paths.keys() != targets:
        raise _InvalidPacket("selected source ledger cannot be resolved")
    return tuple(
        _Source(
            sha256(case.uuid.encode("ascii")).hexdigest(),
            paths[case.policy, case.uuid],
            case.log_sha256,
        )
        for case in selected
    )


def _physical_truth(rows: list[dict[str, Any]], data: listener._Data) -> Truth:
    from . import listener

    # _extract already checked these exact references for family, policy, round,
    # world-private recipient, authenticated read link and overlapping scores.
    # Check the read links are distinct and belong to the same world seed too.
    merged: tuple[dict[str, int], dict[str, int]] = ({}, {})
    read_refs: set[int] = set()
    decision = rows[data.decision_seq - 1]
    for seq in data.truth_seqs:
        row = rows[seq - 1]
        payload = row["payload"]
        read_seq = payload["read_seq"]
        if read_seq in read_refs or any(
            event.get("seed") != decision.get("seed")
            for event in (row, rows[read_seq - 1])
        ):
            raise listener._Ineligible("invalid_observation_truth")
        read_refs.add(read_seq)
        fields = listener._FIELDS[payload["seat"]]
        scores = listener._scores(payload, fields, "invalid_observation_truth")
        for route, values in zip(merged, scores, strict=True):
            route.update(zip(fields, values, strict=True))
    a, b = merged
    latency, reliability, safety = listener._ATTRIBUTES
    truth = (
        (a[latency], a[reliability], a[safety]),
        (b[latency], b[reliability], b[safety]),
    )
    if _best(truth) != data.best_route_index:
        raise _InvalidPacket("physical route scores disagree with listener extraction")
    return truth


def _safe_reason(error: listener._Ineligible) -> str:
    reason = str(error)
    if reason not in _REASONS:
        raise _InvalidPacket("unknown listener ineligibility") from None
    return reason


def _source_cases(policy: int, source: _Source) -> list[Case]:
    from . import listener

    try:
        raw = source.path.read_bytes()
    except FileNotFoundError:
        if source.expected_sha256 is not None:
            raise _InvalidPacket("source ledger changed during export") from None
        return [
            Case(
                policy, rnd, None, None, None, source.trial_sha256, "missing_event_log"
            )
            for rnd in _ROUNDS
        ]
    digest = sha256(raw).hexdigest()
    if digest != source.expected_sha256:
        raise _InvalidPacket("source ledger changed during export")
    try:
        rows = listener._event_rows(raw, listener.POLICIES[policy])
    except listener._Ineligible as error:
        reason = _safe_reason(error)
        return [
            Case(policy, rnd, None, None, digest, source.trial_sha256, reason)
            for rnd in _ROUNDS
        ]
    cases: list[Case] = []
    for rnd in _ROUNDS:
        try:
            data = listener._extract(rows, rnd)
            truth = _physical_truth(rows, data)
        except listener._Ineligible as error:
            cases.append(
                Case(
                    policy,
                    rnd,
                    None,
                    None,
                    digest,
                    source.trial_sha256,
                    _safe_reason(error),
                )
            )
        else:
            cases.append(
                Case(
                    policy,
                    rnd,
                    data.reports,
                    data.best_route_index,
                    digest,
                    source.trial_sha256,
                    truth=truth,
                    report_seqs=data.report_seqs,
                    fetch_seq=data.fetch_seq,
                    decision_seq=data.decision_seq,
                    truth_seqs=data.truth_seqs,
                )
            )
    return cases


def _assemble_packet(sources: tuple[_Source, ...]) -> bytes:
    cases = [
        asdict(case)
        for policy, source in zip(_POLICIES, sources, strict=True)
        for case in _source_cases(policy, source)
    ]
    raw = _encode({"schema": _SCHEMA, "cases": cases})
    _decode(raw)
    return raw


def export_packet(archive: Path) -> bytes:
    """Capture the first scheduled UUID per policy, including ineligible rounds."""
    try:
        return _assemble_packet(_selected_sources(archive.resolve()))
    except _InvalidPacket:
        raise
    except (OSError, ValueError, RuntimeError):
        raise _InvalidPacket("cannot capture the registered archive sources") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("archive", type=Path, metavar="ARCHIVE")
    check = sub.add_parser("check")
    check.add_argument("packet", type=Path, metavar="PACKET")
    check.add_argument("sha256", metavar="SHA256")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            sys.stdout.buffer.write(export_packet(args.archive))
        else:
            cases = load(args.packet, args.sha256)
            summary = {
                "schema": _SCHEMA,
                "sha256": args.sha256,
                "cases": len(cases),
                "eligible_by_policy": [
                    sum(
                        case.policy == policy and case.reports is not None
                        for case in cases
                    )
                    for policy in _POLICIES
                ],
            }
            sys.stdout.buffer.write(_encode(summary))
    except _InvalidPacket as error:
        parser.exit(2, f"route_packet: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
