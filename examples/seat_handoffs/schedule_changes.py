from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

from .core import canonical_uuid
from .native_records import read_record
from .schedule_bounds import METRICS, Window, bounds, possible
from .schedule_checkpoints import SEATS as CHECKPOINT_SEATS
from .schedule_checkpoints import measure as measure_checkpoints
from .schedule_exit_bounds import measure as measure_exit
from .schedule_exit_clock import ExitPolicy
from .schedule_native import AmbiguousRubric, Team, Write, read_team
from .schedule_tape import peer_returns

SCHEMA = "harbor.schedule-changes/v1"
MAX_INPUTS = 4096
STATUSES = (
    "usable",
    "missing_evidence",
    "invalid_evidence",
    "seat_timeout",
    "rubric_ambiguous",
    "duplicate_trial",
    "conflicting_reference",
)
CHANGES = ("improved", "worsened", "unchanged", "unknown")
CROSS_RETURNS = (
    "new_return",
    "previous_return",
    "sent_not_returned",
    "not_sent",
    "unknown",
)
SAME_SEAT = "not_applicable"
Reference = frozenset[Window]


def _read(path: Path, seat_exit: ExitPolicy | None) -> tuple[str, Team | None]:
    try:
        team = read_team(path, seat_exit=seat_exit)
    except OSError:
        return "missing_evidence", None
    except AmbiguousRubric:
        return "rubric_ambiguous", None
    except (ValueError, RecursionError, OverflowError):
        return "invalid_evidence", None
    return "usable", team


def _groups(statuses: list[str], teams: list[Team | None]) -> dict[str, list[Team]]:
    groups: dict[str, list[Team]] = {}
    for status, team in zip(statuses, teams, strict=True):
        if status != "usable":
            continue
        assert team is not None
        groups.setdefault(team.task_digest, []).append(team)
    return groups


def _correct(choice: Window | None, reference: Reference) -> bool | None:
    assert reference
    if choice is None or choice not in reference:
        return False
    if len(reference) == 1:
        return True
    return None


def _changed(before: Write, after: Write) -> bool | None:
    if before.choice is not None and after.choice is not None:
        return before.choice != after.choice
    if before.choice is None and after.choice is None:
        return False if before.answer_digest == after.answer_digest else None
    return True


def _change(changed: bool | None, before: bool | None, after: bool | None) -> str:
    if changed is False:
        return "unchanged"
    if before is None or after is None:
        return "unknown"
    if before == after:
        return "unchanged"
    return "improved" if after else "worsened"


def _labels(path: Path, team: Team) -> tuple[str, ...]:
    try:
        return peer_returns(path, team)
    except (OSError, ValueError, RecursionError, OverflowError):
        return tuple(
            SAME_SEAT if before.seat == after.seat else "unknown"
            for before, after in pairwise(team.writes)
        )


def _transitions(path: Path, team: Team, reference: Reference) -> list[dict[str, Any]]:
    writes = team.writes
    labels = _labels(path, team)
    rows: list[dict[str, Any]] = []
    for (before, after), label in zip(pairwise(writes), labels, strict=True):
        was = _correct(before.choice, reference)
        now = _correct(after.choice, reference)
        changed = _changed(before, after)
        rows.append(
            {
                "from_n": before.n,
                "to_n": after.n,
                "from_seat": before.seat,
                "to_seat": after.seat,
                "final_write": after.n == len(writes),
                "choice_changed": changed,
                "before_correct": was,
                "after_correct": now,
                "change": _change(changed, was, now),
                "peer_return": label,
            }
        )
    return rows


def _row(
    slot: int, status: str, team: Team | None, transitions: list[dict[str, Any]] | None
) -> dict[str, Any]:
    usable = status == "usable"
    graded = usable or status == "seat_timeout"
    assert usable == (transitions is not None)
    assert team is not None or status in (
        "missing_evidence",
        "invalid_evidence",
        "rubric_ambiguous",
        "duplicate_trial",
    )
    return {
        "slot": slot,
        "status": status,
        "native_trial": team.native_id if team is not None else None,
        "task_digest": team.task_digest if team is not None else None,
        "native_reward": team.reward if graded and team is not None else None,
        "submissions": len(team.writes) if graded and team is not None else None,
        "timed_out": team.timed_out if graded and team is not None else None,
        "transitions": transitions if transitions is not None else [],
    }


def _bounds(
    groups: dict[str, list[Team]], references: dict[str, Reference]
) -> dict[str, list[int]]:
    totals = {key: [0, 0] for key in METRICS}
    for digest, group in groups.items():
        found = bounds(group, references[digest])
        for key in METRICS:
            low, high = found[key]
            assert type(low) is int and type(high) is int and 0 <= low <= high
            totals[key][0] += low
            totals[key][1] += high
    return totals


def _summary(
    rows: list[dict[str, Any]], spans: dict[str, list[int]], include_timeouts: bool
) -> dict[str, Any]:
    statuses = Counter(row["status"] for row in rows)
    moves = [move for row in rows for move in row["transitions"]]
    cross = [move for move in moves if move["from_seat"] != move["to_seat"]]
    changes = Counter(move["change"] for move in cross)
    final = Counter(move["change"] for move in cross if move["final_write"])
    returns = {label: {change: 0 for change in CHANGES} for label in CROSS_RETURNS}
    for move in cross:
        returns[move["peer_return"]][move["change"]] += 1
    summary: dict[str, Any] = {
        "statuses": {status: statuses[status] for status in STATUSES},
        "includes_timed_out": include_timeouts,
        "valid_native_grades": sum(row["native_reward"] is not None for row in rows),
        "graded_timeouts": sum(row["timed_out"] is True for row in rows),
        "transitions": len(moves),
        "same_seat_transitions": len(moves) - len(cross),
        "cross_seat_transitions": len(cross),
    }
    for change in CHANGES:
        summary[f"cross_seat_{change}"] = changes[change]
    summary["final_cross_seat_improved"] = final["improved"]
    summary["final_cross_seat_worsened"] = final["worsened"]
    summary["cross_seat_by_peer_return"] = returns
    summary["bounds"] = spans
    return summary


def _replay_sections(
    report: dict[str, Any],
    groups: dict[str, list[Team]],
    references: dict[str, Reference],
    *,
    checkpoints: bool,
    seat_exit: ExitPolicy | None,
) -> None:
    if not checkpoints and seat_exit is None:
        return
    paired = {
        digest: [team for team in group if len(team.seats) == CHECKPOINT_SEATS]
        for digest, group in groups.items()
    }
    paired = {digest: group for digest, group in paired.items() if group}
    refs = {digest: references[digest] for digest in paired}
    count = sum(len(group) for group in paired.values())
    excluded = sum(len(group) for group in groups.values()) - count
    if checkpoints:
        section = measure_checkpoints(paired, refs)
        assert section["teams"] == count
        report["checkpoints"] = {**section, "not_two_seats": excluded}
    if seat_exit is not None:
        cuts = {}
        for group in paired.values():
            for team in group:
                assert team.seat_exit is not None
                cuts[team.native_id] = team.seat_exit
        section = measure_exit(paired, refs, cuts, drift_ms=seat_exit.drift_ms)
        assert section["teams"] == count
        report["seat_exit"] = {**section, "not_two_seats": excluded}


def empty_report(
    *,
    include_timeouts: bool = False,
    checkpoints: bool = False,
    seat_exit: ExitPolicy | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "inputs": 0,
        "usable_trials": 0,
        "trials": [],
        "summary": _summary([], {key: [0, 0] for key in METRICS}, include_timeouts),
    }
    _replay_sections(report, {}, {}, checkpoints=checkpoints, seat_exit=seat_exit)
    return report


def _partial_id(path: Path) -> str | None:
    if path.is_symlink() or (path / "result.json").is_symlink():
        return None
    try:
        record = read_record(path / "result.json")
    except (OSError, ValueError, RecursionError):
        return None
    if (
        record is None
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
    ):
        return None
    identifier = record.get("id")
    return (
        identifier
        if isinstance(identifier, str) and canonical_uuid(identifier)
        else None
    )


def inspect_changes(
    paths: list[Path],
    *,
    include_timeouts: bool = False,
    checkpoints: bool = False,
    seat_exit: ExitPolicy | None = None,
) -> dict[str, Any]:
    if type(include_timeouts) is not bool:
        raise ValueError("include_timeouts must be a bool")
    if type(checkpoints) is not bool:
        raise ValueError("checkpoints must be a bool")
    if seat_exit is not None and not isinstance(seat_exit, ExitPolicy):
        raise ValueError("seat_exit must be an ExitPolicy")
    if not isinstance(paths, list | tuple) or not 1 <= len(paths) <= MAX_INPUTS:
        raise ValueError(f"supply between 1 and {MAX_INPUTS} trial paths")
    if not all(isinstance(path, Path) for path in paths):
        raise ValueError("every trial path must be a pathlib.Path")
    read = [_read(path, seat_exit) for path in paths]
    statuses = [status for status, _ in read]
    teams = [team for _, team in read]
    identities = [
        team.native_id if team else _partial_id(path)
        for path, team in zip(paths, teams, strict=True)
    ]
    seen = Counter(identifier for identifier in identities if identifier is not None)
    for index, (team, identifier) in enumerate(zip(teams, identities, strict=True)):
        if identifier is not None and seen[identifier] > 1:
            statuses[index] = "duplicate_trial"
        elif team is not None and team.timed_out and not include_timeouts:
            statuses[index] = "seat_timeout"
    usable_groups = _groups(statuses, teams)
    references = {digest: possible(group) for digest, group in usable_groups.items()}
    conflicts = {digest for digest, candidates in references.items() if not candidates}
    groups = {d: g for d, g in usable_groups.items() if d not in conflicts}
    rows: list[dict[str, Any]] = []
    for index, (path, status, team) in enumerate(
        zip(paths, statuses, teams, strict=True)
    ):
        if status == "usable" and team is not None and team.task_digest in conflicts:
            status = "conflicting_reference"
        transitions = None
        if status == "usable" and team is not None:
            transitions = _transitions(path, team, references[team.task_digest])
        rows.append(_row(index + 1, status, team, transitions))
    usable = sum(1 for row in rows if row["status"] == "usable")
    assert len(rows) == len(paths)
    assert usable == sum(len(group) for group in groups.values())
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "inputs": len(paths),
        "usable_trials": usable,
        "trials": rows,
        "summary": _summary(rows, _bounds(groups, references), include_timeouts),
    }
    _replay_sections(
        report, groups, references, checkpoints=checkpoints, seat_exit=seat_exit
    )
    return report
