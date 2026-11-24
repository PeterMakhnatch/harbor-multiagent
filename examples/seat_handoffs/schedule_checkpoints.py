from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .schedule_bounds import WINDOWS, Window
from .schedule_native import CONDITIONS, MAX_WRITES, TASK_DIGEST, Team, Write

SCHEMA = "harbor.schedule-checkpoints/v1"
RULES = ("first_legal", "both_legal", "agreement")
COHORTS = ("all", "talk", "silent")
TARGETS = ("improved", "worsened", "net", "prefix_successes", "policy_successes")
EARLY_TARGETS = TARGETS[:4]
VERDICTS = ("lossless_on_archive", "rejected", "unresolved")
SEATS = 2
MAX_TEAMS = 4096
Reference = frozenset[Window]


@dataclass(frozen=True)
class _Entry:
    native_id: str
    condition: str
    timed_out: bool
    reward: int
    length: int
    cut: dict[str, int | None]
    frozen: dict[str, Window | None]


def _two_seats(seats: object) -> bool:
    return (
        isinstance(seats, tuple)
        and len(seats) == SEATS
        and all(isinstance(seat, str) for seat in seats)
        and len(set(seats)) == SEATS
    )


def cutoffs(writes: Sequence[Write], seats: tuple[str, ...]) -> dict[str, int | None]:
    if not _two_seats(seats):
        raise ValueError("checkpoint cutoffs need exactly two distinct seats")
    if not isinstance(writes, Sequence) or len(writes) > MAX_WRITES:
        raise ValueError("native writes are not a bounded sequence")
    latest: dict[str, Window | None] = dict.fromkeys(seats)
    found: dict[str, int | None] = dict.fromkeys(RULES)
    for index, write in enumerate(writes, start=1):
        if not isinstance(write, Write) or write.n != index or write.seat not in seats:
            raise ValueError("native writes are not ordered two-seat submissions")
        if write.choice is not None and write.choice not in WINDOWS:
            raise ValueError("native write normalizes outside the schedule windows")
        latest[write.seat] = write.choice
        first, second = (latest[seat] for seat in seats)
        legal = first is not None and second is not None
        if found["first_legal"] is None and write.choice is not None:
            found["first_legal"] = index
        if found["both_legal"] is None and legal:
            found["both_legal"] = index
        if found["agreement"] is None and legal and first == second:
            found["agreement"] = index
    reached = [found[rule] for rule in RULES]
    assert all(
        later is None or (earlier is not None and earlier <= later)
        for earlier, later in zip(reached, reached[1:], strict=False)
    )
    return found


def _check_shape(groups: object, references: object) -> None:
    if not isinstance(groups, dict) or not isinstance(references, dict):
        raise ValueError("groups and references must be dicts")
    total = 0
    for group in groups.values():
        if not isinstance(group, list) or not group:
            raise ValueError("every task group must be a nonempty list of teams")
        total += len(group)
        if total > MAX_TEAMS:
            raise ValueError(f"checkpoints accept at most {MAX_TEAMS} teams")
        for team in group:
            if not isinstance(team, Team):
                raise ValueError("every group member must be a native Team")
            if not _two_seats(team.seats):
                raise ValueError("checkpoint measurement accepts only two-seat teams")


def _check_reference(reference: object, group: list[Team]) -> None:
    if not isinstance(reference, frozenset) or not reference:
        raise ValueError("every task needs a nonempty reference of schedule windows")
    if not reference <= WINDOWS:
        raise ValueError("a reference names a window outside the schedule")
    for team in group:
        final = team.writes[-1].choice if team.writes else None
        for window in reference:
            if (final == window) != (team.reward == 1):
                raise ValueError("a reference window contradicts a native grade")


def _check_groups(groups: dict[str, list[Team]], references: dict[str, Any]) -> None:
    if groups.keys() != references.keys():
        raise ValueError("references must cover exactly the grouped tasks")
    seen: set[str] = set()
    for digest, group in groups.items():
        if not isinstance(digest, str) or TASK_DIGEST.fullmatch(digest) is None:
            raise ValueError("task groups must be keyed by native task checksum")
        for team in group:
            if team.task_digest != digest:
                raise ValueError("a team is grouped under another task checksum")
            if team.condition not in CONDITIONS:
                raise ValueError("a team has an unknown condition")
            if type(team.reward) is not int or team.reward not in (0, 1):
                raise ValueError("a team has no binary native grade")
            if type(team.timed_out) is not bool:
                raise ValueError("a team has no timeout flag")
            if not isinstance(team.native_id, str) or team.native_id in seen:
                raise ValueError("native team ids must be unique strings")
            seen.add(team.native_id)
        _check_reference(references[digest], group)


def _entry(team: Team) -> _Entry:
    found = cutoffs(team.writes, team.seats)
    frozen: dict[str, Window | None] = {}
    for rule, cut in found.items():
        frozen[rule] = None if cut is None else team.writes[cut - 1].choice
        assert (cut is None) == (frozen[rule] is None)
    return _Entry(
        native_id=team.native_id,
        condition=team.condition,
        timed_out=team.timed_out,
        reward=team.reward,
        length=len(team.writes),
        cut=found,
        frozen=frozen,
    )


def _known(frozen: Window, reference: Reference) -> bool | None:
    assert reference
    if frozen not in reference:
        return False
    if len(reference) == 1:
        return True
    return None


def _checkpoint(entry: _Entry, rule: str, reference: Reference) -> dict[str, Any]:
    cut, frozen = entry.cut[rule], entry.frozen[rule]
    if cut is None:
        return {
            "cutoff": None,
            "reached": False,
            "final": False,
            "early": False,
            "later_writes": None,
            "prefix_correct": None,
        }
    assert frozen is not None and 1 <= cut <= entry.length
    return {
        "cutoff": cut,
        "reached": True,
        "final": cut == entry.length,
        "early": cut < entry.length,
        "later_writes": entry.length - cut,
        "prefix_correct": _known(frozen, reference),
    }


def _row(entry: _Entry, reference: Reference) -> dict[str, Any]:
    return {
        "native_id": entry.native_id,
        "condition": entry.condition,
        "timed_out": entry.timed_out,
        "writes": entry.length,
        "final_grade": entry.reward,
        "checkpoints": {rule: _checkpoint(entry, rule, reference) for rule in RULES},
    }


def _targets(entries: list[_Entry], rule: str, window: Window) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entry in entries:
        native = entry.reward == 1
        if entry.cut[rule] is None:
            counts["policy_successes"] += int(native)
            continue
        prefix = entry.frozen[rule] == window
        counts["improved"] += int(prefix and not native)
        counts["worsened"] += int(native and not prefix)
        counts["prefix_successes"] += int(prefix)
        counts["policy_successes"] += int(prefix)
    counts["net"] = counts["improved"] - counts["worsened"]
    return {target: counts[target] for target in TARGETS}


def _span(
    tasks: dict[str, list[_Entry]],
    references: dict[str, Reference],
    rule: str,
    targets: tuple[str, ...],
) -> dict[str, list[int]]:
    totals = {target: [0, 0] for target in targets}
    for digest, entries in tasks.items():
        if not entries:
            continue
        windows = sorted(references[digest])
        scored = [_targets(entries, rule, window) for window in windows]
        for target in targets:
            values = [score[target] for score in scored]
            totals[target][0] += min(values)
            totals[target][1] += max(values)
    assert all(low <= high for low, high in totals.values())
    return totals


def _verdict(completed_early: int, worsened: list[int]) -> str:
    if worsened[0] > 0:
        return "rejected"
    if completed_early >= 1 and worsened[1] == 0:
        return "lossless_on_archive"
    return "unresolved"


def _early(entry: _Entry, rule: str) -> bool:
    cut = entry.cut[rule]
    return cut is not None and cut < entry.length


def _rule(
    tasks: dict[str, list[_Entry]], references: dict[str, Reference], rule: str
) -> dict[str, Any]:
    entries = [entry for group in tasks.values() for entry in group]
    reached = [entry for entry in entries if entry.cut[rule] is not None]
    early_tasks = {
        digest: [entry for entry in group if _early(entry, rule)]
        for digest, group in tasks.items()
    }
    early = [entry for group in early_tasks.values() for entry in group]
    spans = _span(tasks, references, rule, TARGETS)
    early_spans = _span(early_tasks, references, rule, EARLY_TARGETS)
    native = sum(entry.reward for entry in entries)
    assert spans["policy_successes"] == [native + bound for bound in spans["net"]]
    assert all(spans[target] == early_spans[target] for target in TARGETS[:3])
    completed = sum(not entry.timed_out for entry in early)
    return {
        "reached": len(reached),
        "early": len(early),
        "final_only": len(reached) - len(early),
        "abstain": len(entries) - len(reached),
        "subsequent_writes": sum(e.length - (e.cut[rule] or 0) for e in reached),
        "native_final_successes_reached": sum(entry.reward for entry in reached),
        **spans,
        "early_stratum": {
            "teams": len(early),
            "completed": completed,
            "subsequent_writes": sum(e.length - (e.cut[rule] or 0) for e in early),
            "native_final_successes": sum(entry.reward for entry in early),
            **early_spans,
        },
        "verdict": _verdict(completed, spans["worsened"]),
    }


def _cohort(
    tasks: dict[str, list[_Entry]], references: dict[str, Reference]
) -> dict[str, Any]:
    entries = [entry for group in tasks.values() for entry in group]
    return {
        "eligible": len(entries),
        "tasks": sum(1 for group in tasks.values() if group),
        "graded_timeouts": sum(entry.timed_out for entry in entries),
        "native_final_successes": sum(entry.reward for entry in entries),
        "recorded_writes": sum(entry.length for entry in entries),
        "rules": {rule: _rule(tasks, references, rule) for rule in RULES},
    }


def measure(
    groups: dict[str, list[Team]], references: dict[str, frozenset[Window]]
) -> dict[str, Any]:
    _check_shape(groups, references)
    _check_groups(groups, references)
    tasks = {
        digest: [_entry(team) for team in group] for digest, group in groups.items()
    }
    cohorts: dict[str, Any] = {}
    for cohort in COHORTS:
        selected = {
            digest: [e for e in group if cohort in ("all", e.condition)]
            for digest, group in tasks.items()
        }
        cohorts[cohort] = _cohort(selected, references)
    rows = sorted(
        (_row(entry, references[d]) for d, group in tasks.items() for entry in group),
        key=lambda row: row["native_id"],
    )
    assert len(rows) == cohorts["all"]["eligible"]
    assert len(rows) == cohorts["talk"]["eligible"] + cohorts["silent"]["eligible"]
    return {
        "schema": SCHEMA,
        "rules": list(RULES),
        "targets": list(TARGETS),
        "verdicts": list(VERDICTS),
        "teams": len(rows),
        "tasks": len(tasks),
        "cohorts": cohorts,
        "trials": rows,
    }
