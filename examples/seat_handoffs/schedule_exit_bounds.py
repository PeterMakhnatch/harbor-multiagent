from collections import Counter
from dataclasses import dataclass
from typing import Any

from .core import canonical_uuid
from .schedule_bounds import WINDOWS, Window
from .schedule_checkpoints import VERDICTS, _check_groups, _check_shape
from .schedule_exit_clock import (
    ABSENT_REASONS,
    CLOCK_REASONS,
    EXIT_REASONS,
    UNKNOWN_REASONS,
    ExitCut,
    ExitPolicy,
)
from .schedule_exit_clock import ALIGNMENTS as CLOCK_ALIGNMENTS
from .schedule_exit_clock import TRIGGERS as CLOCK_TRIGGERS
from .schedule_native import MAX_WRITES, Team, Write

SCHEMA = "harbor.schedule-exit-bounds/v1"
TRIGGERS = ("observed", "absent", "unknown")
ALIGNMENTS = ("unanchored", "fresh_interval", "clock_unknown", "inapplicable")
CLEAN_COHORTS = {0: "clean_0", 1: "clean_1", 2: "clean_2", None: "clean_unknown"}
COHORTS = (
    "all",
    "talk",
    "silent",
    "completed",
    "graded_timeout",
    "clean_0",
    "clean_1",
    "clean_2",
    "clean_unknown",
)
PARTITIONS = (
    ("talk", "silent"),
    ("completed", "graded_timeout"),
    ("clean_0", "clean_1", "clean_2", "clean_unknown"),
)
TARGETS = ("gained", "lost", "net", "policy_successes")
PRIMARY_COHORT = "completed"
UNANCHORED_REASON = "unanchored_prefixes"
FRESH_REASON = "fresh_interval_bounds"
OBSERVATION_MODELS = ("unanchored", "conditional_fresh_world")
SEATS = 2
assert frozenset(TRIGGERS) == CLOCK_TRIGGERS
assert frozenset(ALIGNMENTS) == CLOCK_ALIGNMENTS
assert EXIT_REASONS == (
    ABSENT_REASONS | UNKNOWN_REASONS | CLOCK_REASONS | {UNANCHORED_REASON, FRESH_REASON}
)


@dataclass(frozen=True)
class _Trial:
    native_id: str
    condition: str
    timed_out: bool
    reward: int
    length: int
    cut: ExitCut
    choices: frozenset[Window]
    invalid: bool

    def grades(self, key: Window) -> tuple[int, int]:
        high = int(key in self.choices)
        low = int(not self.invalid and self.choices == {key})
        assert 0 <= low <= high <= 1
        return low, high


def _check_writes(team: Team) -> None:
    writes = team.writes
    if not isinstance(writes, tuple) or len(writes) > MAX_WRITES:
        raise ValueError("native writes are not a bounded tuple")
    for index, write in enumerate(writes, start=1):
        if not isinstance(write, Write) or write.n != index:
            raise ValueError("native writes are not ordered submissions")
        if write.seat not in team.seats:
            raise ValueError("a native write names a seat outside the team")
        if write.choice is not None and write.choice not in WINDOWS:
            raise ValueError("native write normalizes outside the schedule windows")


def _check_fields(cut: ExitCut, team: Team) -> None:
    if type(cut.trigger) is not str or cut.trigger not in TRIGGERS:
        raise ValueError("exit cut has an unknown trigger status")
    if type(cut.alignment) is not str or cut.alignment not in ALIGNMENTS:
        raise ValueError("exit cut has an unknown alignment status")
    if type(cut.reason) is not str or cut.reason not in EXIT_REASONS:
        raise ValueError("exit cut reason is outside the published exit reasons")
    length = len(team.writes)
    if type(cut.lo) is not int or type(cut.hi) is not int:
        raise ValueError("exit cut prefix range is not integral")
    if not 0 <= cut.lo <= cut.hi <= length:
        raise ValueError("exit cut prefix range lies outside the native writes")
    clean = cut.clean_seats
    if clean is not None and (type(clean) is not int or not 0 <= clean <= SEATS):
        raise ValueError("exit cut clean seat count is outside the team")
    seat = cut.first_seat
    if seat is not None and (type(seat) is not str or seat not in team.seats):
        raise ValueError("exit cut first seat is outside the team")


def _check_unobserved(cut: ExitCut, length: int) -> None:
    expected = {
        "absent": (ABSENT_REASONS, (length, length)),
        "unknown": (UNKNOWN_REASONS, (0, length)),
    }
    reasons, prefixes = expected[cut.trigger]
    if cut.alignment != "inapplicable" or cut.first_seat is not None:
        raise ValueError("an unobserved trigger must be inapplicable with no seat")
    if cut.reason not in reasons:
        raise ValueError("an unobserved trigger carries a reason of another status")
    if (cut.lo, cut.hi) != prefixes:
        raise ValueError("absent keeps the final; unknown allows every prefix")


def _check_observed(cut: ExitCut, length: int, drift_ms: int | None) -> None:
    if cut.first_seat is None or cut.clean_seats == 0:
        raise ValueError("an observed trigger needs a known clean first seat")
    if drift_ms is None:
        allowed = {"unanchored": frozenset({UNANCHORED_REASON})}
    else:
        allowed = {"fresh_interval": frozenset({FRESH_REASON})}
        allowed["clock_unknown"] = CLOCK_REASONS
    if cut.alignment not in allowed or cut.reason not in allowed[cut.alignment]:
        raise ValueError("an observed trigger disagrees with the exit clock policy")
    if cut.alignment != "fresh_interval" and (cut.lo, cut.hi) != (0, length):
        raise ValueError("an observed cut without a fresh interval allows every prefix")


def _check_cut(cut: object, team: Team, drift_ms: int | None) -> ExitCut:
    if not isinstance(cut, ExitCut):
        raise ValueError("every native team needs one ExitCut")
    _check_fields(cut, team)
    if cut.trigger == "observed":
        _check_observed(cut, len(team.writes), drift_ms)
    else:
        _check_unobserved(cut, len(team.writes))
    return cut


def _trial(team: Team, cut: ExitCut) -> _Trial:
    choices: set[Window] = set()
    invalid = cut.lo == 0
    for index in range(max(cut.lo, 1) - 1, cut.hi):
        choice = team.writes[index].choice
        if choice is None:
            invalid = True
        else:
            choices.add(choice)
    assert cut.hi > 0 or (invalid and not choices)
    return _Trial(
        native_id=team.native_id,
        condition=team.condition,
        timed_out=team.timed_out,
        reward=team.reward,
        length=len(team.writes),
        cut=cut,
        choices=frozenset(choices),
        invalid=invalid,
    )


def _key_bounds(trials: list[_Trial], key: Window) -> dict[str, list[int]]:
    gained, lost = [0, 0], [0, 0]
    for trial in trials:
        low, high = trial.grades(key)
        if trial.cut.trigger == "absent":
            assert low == high == trial.reward
        if trial.reward == 1:
            lost[0] += 1 - high
            lost[1] += 1 - low
        else:
            gained[0] += low
            gained[1] += high
    native = sum(trial.reward for trial in trials)
    net = [gained[0] - lost[1], gained[1] - lost[0]]
    policy = [native + net[0], native + net[1]]
    assert 0 <= policy[0] <= policy[1] <= len(trials)
    return {"gained": gained, "lost": lost, "net": net, "policy_successes": policy}


def _task_bounds(
    trials: list[_Trial], reference: frozenset[Window]
) -> dict[str, list[int]]:
    scored = [_key_bounds(trials, key) for key in sorted(reference)]
    assert scored
    return {
        target: [
            min(score[target][0] for score in scored),
            max(score[target][1] for score in scored),
        ]
        for target in TARGETS
    }


def _later_writes(trials: list[_Trial]) -> dict[str, Any]:
    certain = [trial for trial in trials if trial.cut.hi < trial.length]
    possible = [trial for trial in trials if trial.cut.lo < trial.length]
    assert len(certain) <= len(possible) <= len(trials)
    return {
        "certain_teams": len(certain),
        "certain_completed_teams": sum(not trial.timed_out for trial in certain),
        "possible_teams": len(possible),
        "count": [
            sum(trial.length - trial.cut.hi for trial in trials),
            sum(trial.length - trial.cut.lo for trial in trials),
        ],
    }


def _verdict(lost: list[int], certain_completed: int) -> str:
    if lost[0] > 0:
        return "rejected"
    if lost[1] == 0 and certain_completed >= 1:
        return "lossless_on_archive"
    return "unresolved"


def _cohort(
    tasks: dict[str, list[_Trial]], references: dict[str, frozenset[Window]]
) -> dict[str, Any]:
    trials = [trial for group in tasks.values() for trial in group]
    totals = {target: [0, 0] for target in TARGETS}
    for digest, group in tasks.items():
        if not group:
            continue
        task = _task_bounds(group, references[digest])
        for target in TARGETS:
            totals[target][0] += task[target][0]
            totals[target][1] += task[target][1]
    native = sum(trial.reward for trial in trials)
    triggers = Counter(trial.cut.trigger for trial in trials)
    alignments = Counter(trial.cut.alignment for trial in trials)
    later = _later_writes(trials)
    assert all(low <= high for low, high in totals.values())
    assert totals["policy_successes"] == [native + bound for bound in totals["net"]]
    assert totals["lost"][1] <= native and totals["gained"][1] <= len(trials) - native
    assert sum(triggers.values()) == sum(alignments.values()) == len(trials)
    return {
        "teams": len(trials),
        "tasks": sum(1 for group in tasks.values() if group),
        "graded_timeouts": sum(trial.timed_out for trial in trials),
        "native_final_successes": native,
        "recorded_writes": sum(trial.length for trial in trials),
        "triggers": {trigger: triggers[trigger] for trigger in TRIGGERS},
        "alignments": {alignment: alignments[alignment] for alignment in ALIGNMENTS},
        **totals,
        "later_writes": later,
        "verdict": _verdict(totals["lost"], later["certain_completed_teams"]),
    }


def _memberships(trial: _Trial) -> tuple[str, ...]:
    outcome = "graded_timeout" if trial.timed_out else "completed"
    return ("all", trial.condition, outcome, CLEAN_COHORTS[trial.cut.clean_seats])


def _check_partitions(cohorts: dict[str, dict[str, Any]]) -> None:
    whole = cohorts["all"]
    for parts in PARTITIONS:
        for count in ("teams", "native_final_successes", "recorded_writes"):
            assert sum(cohorts[part][count] for part in parts) == whole[count]
        for target in TARGETS:
            assert sum(cohorts[part][target][0] for part in parts) <= whole[target][0]
            assert sum(cohorts[part][target][1] for part in parts) >= whole[target][1]


def _row(trial: _Trial, reference: frozenset[Window]) -> dict[str, Any]:
    grades = [trial.grades(key) for key in sorted(reference)]
    cut = trial.cut
    return {
        "native_id": trial.native_id,
        "condition": trial.condition,
        "native_grade": trial.reward,
        "timed_out": trial.timed_out,
        "writes": trial.length,
        "clean_seats": cut.clean_seats,
        "trigger": cut.trigger,
        "first_seat": cut.first_seat,
        "reason": cut.reason,
        "alignment": cut.alignment,
        "prefix": [cut.lo, cut.hi],
        "later_writes": [trial.length - cut.hi, trial.length - cut.lo],
        "grade": [min(low for low, _ in grades), max(high for _, high in grades)],
    }


def _trials(
    groups: dict[str, list[Team]], cuts: dict[str, ExitCut], drift_ms: int | None
) -> dict[str, list[_Trial]]:
    if not isinstance(cuts, dict):
        raise ValueError("exit cuts must be a dict keyed by native id")
    ids = {team.native_id for group in groups.values() for team in group}
    if cuts.keys() != ids:
        raise ValueError("exit cuts must match the native team ids exactly")
    tasks: dict[str, list[_Trial]] = {}
    for digest, group in groups.items():
        tasks[digest] = []
        for team in group:
            if not canonical_uuid(team.native_id):
                raise ValueError("native team id is not a canonical trial id")
            _check_writes(team)
            cut = _check_cut(cuts[team.native_id], team, drift_ms)
            tasks[digest].append(_trial(team, cut))
    assert sum(len(group) for group in tasks.values()) == len(ids)
    return tasks


def measure(
    groups: dict[str, list[Team]],
    references: dict[str, frozenset[Window]],
    cuts: dict[str, ExitCut],
    *,
    drift_ms: int | None,
) -> dict[str, Any]:
    _check_shape(groups, references)
    _check_groups(groups, references)
    ExitPolicy(drift_ms=drift_ms)
    tasks = _trials(groups, cuts, drift_ms)
    members: dict[str, dict[str, list[_Trial]]] = {
        name: {digest: [] for digest in tasks} for name in COHORTS
    }
    for digest, group in tasks.items():
        for trial in group:
            for name in _memberships(trial):
                members[name][digest].append(trial)
    cohorts = {name: _cohort(members[name], references) for name in COHORTS}
    _check_partitions(cohorts)
    rows = sorted(
        (_row(trial, references[d]) for d, group in tasks.items() for trial in group),
        key=lambda row: row["native_id"],
    )
    assert len(rows) == cohorts["all"]["teams"]
    return {
        "schema": SCHEMA,
        "observation_model": OBSERVATION_MODELS[drift_ms is not None],
        "clock_error_ms": drift_ms,
        "primary_cohort": PRIMARY_COHORT,
        "cohort_names": list(COHORTS),
        "targets": list(TARGETS),
        "verdicts": list(VERDICTS),
        "teams": len(rows),
        "tasks": len(tasks),
        "cohorts": cohorts,
        "trials": rows,
    }
