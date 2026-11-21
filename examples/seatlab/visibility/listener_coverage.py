from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

from . import listener
from . import stored_listener_variation as variation
from .crucible import run_records

if TYPE_CHECKING:
    from .crucible import RunRecord

_TRIALS = 30
_ORIGINAL_CASES = 6
_MIN_HELDOUT = 8
_ENROLLMENT_SHA256 = "3de582268ab4d3cedfce4588cdf5a72ba2247e978ae733741ff1aa51ef4b7761"
_RETAINED_LOGS_SHA256 = (
    "b0c24ebb6c3e4c6ff1365b6b18f89cf104ba098b7b725b76c29e2a9fc8a2747b"
)
_MISSING = frozenset(
    {
        "missing_event_log",
        "unreadable_event_log",
        "missing_original_decision",
        "missing_predecision_fetch",
        "missing_observation_truth",
    }
)
_ROUND_REASONS = _MISSING | {
    "invalid_original_decision_event",
    "invalid_predecision_fetch",
    "incomplete_predecision_fetch",
    "invalid_report_reference",
    "invalid_numeric_report",
    "invalid_observation_truth",
    "conflicting_observation_truth",
    "tied_observation_truth",
}

_CaseKey = tuple[str, int]
_Scores = tuple[tuple[str, int], ...]
_Claim = tuple[tuple[str, _Scores, _Scores], ...]
_TruthTable = tuple[_Scores, ...]


@dataclass(frozen=True)
class _Evidence:
    claim: _Claim
    truth: _TruthTable
    mixed: bool
    near_tie: bool
    tied_reports: int


def _digest(pairs: list[tuple[str, str]]) -> str:
    return listener._sha(json.dumps(sorted(pairs), separators=(",", ":")).encode())


def _enrollment(root: Path) -> dict[str, RunRecord]:
    records = {}
    for record in run_records(root):
        if (
            record.family != listener.FAMILY
            or record.condition not in listener.POLICIES
        ):
            continue
        key = str(UUID(record.key))
        if key != record.key or key in records:
            raise ValueError("invalid trial enrollment")
        records[key] = record
    if (
        len(records) != _TRIALS
        or _digest([(key, record.condition) for key, record in records.items()])
        != _ENROLLMENT_SHA256
    ):
        raise ValueError("unexpected trial enrollment")
    return records


def _read_logs(
    root: Path, records: dict[str, RunRecord]
) -> tuple[dict[str, bytes], dict[str, str]]:
    logs, unavailable = {}, {}
    directories = set()
    for key, record in records.items():
        directory = record.directory.resolve()
        path = directory / listener._EVENT_LOG
        if (
            not directory.is_relative_to(root)
            or directory in directories
            or path.resolve() != path
        ):
            raise ValueError("ambiguous event log source")
        directories.add(directory)
        try:
            if not S_ISREG(path.stat().st_mode):
                raise ValueError("event log is not a regular file")
            logs[key] = path.read_bytes()
        except FileNotFoundError:
            unavailable[key] = "missing_event_log"
        except OSError:
            unavailable[key] = "unreadable_event_log"
    return logs, unavailable


def _original_cases(
    root: Path,
    records: dict[str, RunRecord],
    hashes: dict[str, str],
    unavailable: dict[str, str],
) -> dict[_CaseKey, str]:
    original = {}
    for policy in listener.POLICIES:
        candidates = sorted(
            (key for key, record in records.items() if record.condition == policy),
            key=lambda key: (listener._source_hash(key), key),
        )
        for number, key in enumerate(candidates[:2], 1):
            original[key, number] = policy
    if len(original) != _ORIGINAL_CASES:
        raise ValueError("invalid original listener selection")
    if any(unavailable.get(key) == "unreadable_event_log" for key, _ in original):
        return original
    schedule = listener.build_schedule(root)
    if (
        len(schedule.cases) != _ORIGINAL_CASES
        or {(case.uuid, case.round_number): case.policy for case in schedule.cases}
        != original
        or any(case.log_sha256 != hashes.get(case.uuid) for case in schedule.cases)
    ):
        raise ValueError("original listener selection does not match retained sources")
    return original


def _true_table(rows: list[dict[str, Any]], data: listener._Data) -> _TruthTable:
    values = {}
    for seq in data.truth_seqs:
        payload = rows[seq - 1]["payload"]
        for route in listener._ROUTES:
            for field in listener._FIELDS[payload["seat"]]:
                value = payload[route][field]
                if values.setdefault((route, field), value) != value:
                    raise ValueError("inconsistent validated observation truth")
    sides = tuple(
        tuple((field, values[route, field]) for field in listener._ATTRIBUTES)
        for route in listener._ROUTES
    )
    return min(sides, sides[::-1])


def _evidence(rows: list[dict[str, Any]], number: int) -> _Evidence | str:
    try:
        data = listener._extract(rows, number)
    except listener._Ineligible as error:
        reason = str(error)
        return reason if reason in _ROUND_REASONS else "invalid_round_evidence"
    claim = tuple(
        (seat, tuple(zip(fields, a, strict=True)), tuple(zip(fields, b, strict=True)))
        for (seat, fields), (a, b) in zip(
            listener._FIELDS.items(), data.reports, strict=True
        )
    )
    differences = [sum(a) - sum(b) for a, b in data.reports]
    return _Evidence(
        min(claim, tuple((seat, b, a) for seat, a, b in claim)),
        _true_table(rows, data),
        min(differences) < 0 < max(differences),
        abs(sum(differences)) <= 5 * sum(map(len, listener._FIELDS.values())),
        differences.count(0),
    )


def _summary(cases: list[_Evidence | str]) -> dict[str, Any]:
    evidence = [case for case in cases if isinstance(case, _Evidence)]
    reasons = Counter(case for case in cases if isinstance(case, str))
    return {
        "case_denominator": len(cases),
        "authenticated_eligible_cases": len(evidence),
        "ineligible_cases": sum(reasons.values()),
        "missing_evidence_cases": sum(reasons[reason] for reason in _MISSING),
        "ineligibility_reasons": dict(reasons),
        "distinct_claim_patterns": len({case.claim for case in evidence}),
        "distinct_true_tables": len({case.truth for case in evidence}),
        "mixed_cases": sum(case.mixed for case in evidence),
        "distinct_mixed_claim_patterns": len(
            {case.claim for case in evidence if case.mixed}
        ),
        "near_tie_cases": sum(case.near_tie for case in evidence),
        "report_denominator": len(listener._FIELDS) * len(evidence),
        "tied_reports": sum(case.tied_reports for case in evidence),
    }


def _overlap(
    cases: list[_Evidence | str], original: list[_Evidence | str]
) -> dict[str, int] | None:
    baseline = [case for case in original if isinstance(case, _Evidence)]
    if len(baseline) != _ORIGINAL_CASES:
        return None
    claims, tables = (
        {case.claim for case in baseline},
        {case.truth for case in baseline},
    )
    eligible = [case for case in cases if isinstance(case, _Evidence)]
    overlapping = [
        case for case in eligible if case.claim in claims or case.truth in tables
    ]
    heldout = [
        case
        for case in eligible
        if case.mixed and case.claim not in claims and case.truth not in tables
    ]
    return {
        "eligible_case_denominator_including_original_six": len(eligible),
        "original_overlapping_cases": len(overlapping),
        "strict_heldout_cases": len(heldout),
        "strict_heldout_distinct_true_tables": len({case.truth for case in heldout}),
        "strict_heldout_distinct_mixed_claim_patterns": len(
            {case.claim for case in heldout}
        ),
    }


def analyze(root: Path) -> dict[str, Any]:
    root = root.resolve()
    records = _enrollment(root)
    logs, unavailable = _read_logs(root, records)
    hashes = {key: listener._sha(raw) for key, raw in logs.items()}
    verified = not unavailable
    if verified and _digest(list(hashes.items())) != _RETAINED_LOGS_SHA256:
        raise ValueError("retained event logs do not match the enrolled archive")
    original_keys = _original_cases(root, records, hashes, unavailable)
    cases = {}
    for key, record in records.items():
        if key in unavailable:
            cases.update(
                {(key, number): unavailable[key] for number in variation._ROUNDS}
            )
            continue
        rows = listener._event_rows(logs[key], record.condition)
        cases.update(
            {(key, number): _evidence(rows, number) for number in variation._ROUNDS}
        )
    pooled_cases = list(cases.values())
    original_cases = [cases[key] for key in original_keys]
    original = _summary(original_cases)
    cohort = _summary(pooled_cases)
    overlap = _overlap(pooled_cases, original_cases)
    expected_cases = _TRIALS * len(variation._ROUNDS)
    accounted = cohort["authenticated_eligible_cases"] + cohort["ineligible_cases"]
    passed = (
        verified
        and len(records) == _TRIALS
        and accounted == expected_cases
        and original["authenticated_eligible_cases"] == _ORIGINAL_CASES
        and overlap is not None
        and overlap["strict_heldout_distinct_true_tables"] >= _MIN_HELDOUT
        and overlap["strict_heldout_distinct_mixed_claim_patterns"] >= _MIN_HELDOUT
    )
    return {
        "scope": (
            "Retained report_final5 inputs only. Distinct inputs are not independent seeds. "
            "No route outcomes or model requests."
        ),
        "integrity_scope": "Checks retained archive bytes, not their origin or the model.",
        "retained_log_integrity": "verified" if verified else "unverified_incomplete",
        "expected_trials": _TRIALS,
        "enrolled_trials": len(records),
        "rounds_per_trial": len(variation._ROUNDS),
        "expected_cohort_cases": expected_cases,
        "accounted_cohort_cases": accounted,
        "event_logs_read": len(logs),
        "complete_authenticated_coverage": (
            verified and cohort["authenticated_eligible_cases"] == expected_cases
        ),
        "original_six": original,
        "cohort": cohort,
        "original_overlap_and_holdout": overlap,
        "gate": {
            "status": "pass" if passed else "not_met" if verified else "not_evaluated",
            "passed": passed,
            "minimum_distinct_heldout_true_tables": _MIN_HELDOUT,
            "minimum_distinct_heldout_mixed_claim_patterns": _MIN_HELDOUT,
        },
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Listener coverage requires one archive root.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="python -m examples.seatlab.visibility.listener_coverage")
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.dumps(analyze(args.root), sort_keys=True, indent=2)
    except (OSError, ValueError, TypeError, LookupError, RuntimeError):
        parser.exit(2, "Listener coverage refused an invalid or ambiguous archive.\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
