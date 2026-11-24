import argparse
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple

from ..native_study import require
from .contract import load_registration
from .worker_layout import assigned, digest, make_plan, validate_plan
from .worker_root import (
    BINDINGS,
    DIRECTORY,
    ROOT_BINDING,
    bind,
    binding_state,
    open_root,
)

STATUS_SCHEMA = "harbor.seat-seal.worker-status/v1"
RESULT = "result.json"
COORDINATION = "trials-root"
MAX_ROOT_ENTRIES = 4096
OBSERVED = ("absent", "directory", "result_file", "unavailable")
FOREIGN = ("directory", "result_file", "unavailable")

Record = dict[str, Any]


class Survey(NamedTuple):
    observed: dict[str, str]
    foreign: dict[str, int]
    unregistered: int
    binding: str


def _load(
    registration_path: Path, plan_record: Record, worker: int
) -> tuple[Record, Record, list[str]]:
    require(isinstance(registration_path, Path), "registration path is not a Path")
    try:
        registration = load_registration(registration_path)
    except OSError as error:
        raise ValueError(
            f"registration is not a plain file: {error.strerror}"
        ) from None
    plan = validate_plan(plan_record)
    require(
        plan["registration"] == digest(registration),
        "worker plan binds another registration",
    )
    require(
        plan["task_files"] == registration["task_files"],
        "worker plan binds another task package",
    )
    require(
        make_plan(registration, plan["workers"], plan["layout"]) == plan,
        "worker plan differs from the plan its registration derives",
    )
    require(
        type(worker) is int and 0 <= worker < plan["workers"],
        f"worker must be 0..{plan['workers'] - 1}",
    )
    tickets = assigned(plan, worker)
    assert tickets == sorted(set(tickets)) and len(tickets) >= 1
    return registration, plan, tickets


def _observe(root: int, name: str) -> str:
    try:
        info = os.lstat(name, dir_fd=root)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unavailable"
    if not stat.S_ISDIR(info.st_mode):
        return "unavailable"
    try:
        trial = os.open(name, DIRECTORY, dir_fd=root)
    except OSError:
        return "unavailable"
    try:
        opened = os.fstat(trial)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            return "unavailable"
        try:
            result = os.lstat(RESULT, dir_fd=trial)
        except FileNotFoundError:
            return "directory"
        except OSError:
            return "unavailable"
        return "result_file" if stat.S_ISREG(result.st_mode) else "unavailable"
    finally:
        os.close(trial)


def _entries(root: int) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(root) as listing:
            for entry in listing:
                require(
                    len(names) < MAX_ROOT_ENTRIES,
                    f"trials directory has over {MAX_ROOT_ENTRIES} entries",
                )
                names.append(entry.name)
    except OSError as error:
        raise ValueError(
            f"trials directory cannot be listed: {error.strerror}"
        ) from None
    return names


def _survey(
    root: int,
    plan_digest: str,
    worker: int,
    tickets: list[str],
    registered: frozenset[str],
) -> Survey:
    assert set(tickets) <= registered
    observed = {ticket: _observe(root, ticket) for ticket in tickets}
    foreign = dict.fromkeys(FOREIGN, 0)
    unregistered = 0
    for name in _entries(root):
        if name in observed or name == ROOT_BINDING:
            continue
        if name in registered:
            seen = _observe(root, name)
            foreign["unavailable" if seen == "absent" else seen] += 1
        else:
            unregistered += 1
    binding = binding_state(root, plan_digest, worker)
    survey = Survey(observed, foreign, unregistered, binding)
    assert survey.binding in BINDINGS
    assert all(value in OBSERVED for value in observed.values())
    return survey


def _warnings(survey: Survey) -> list[str]:
    warnings: list[str] = []
    if survey.binding == "other":
        warnings.append(
            "this trials directory is bound to another worker or plan, "
            "or carries a pre-release plan-only binding; this worker never starts here"
        )
    if survey.binding == "unavailable":
        warnings.append("this trials directory has an unusable worker binding")
    if survey.binding == "unbound" and any(
        value != "absent" for value in survey.observed.values()
    ):
        warnings.append(
            "this unbound trials directory already holds assigned tickets; "
            "their starts cannot be attributed to this worker"
        )
    foreign = sum(survey.foreign.values())
    if foreign:
        warnings.append(
            f"{foreign} registered tickets of other workers are in this trials directory"
        )
    if survey.foreign["unavailable"] or "unavailable" in survey.observed.values():
        warnings.append(
            "some registered ticket entries are unavailable; none is reissued"
        )
    if survey.unregistered:
        warnings.append(f"{survey.unregistered} entries are outside the registration")
    warnings.append(
        "starts are exclusive only within this trials directory; "
        "another machine needs this shared directory or a single owner of the shard"
    )
    warnings.append(
        "absent counts only this trials directory: it is no evidence that a ticket "
        "never started elsewhere, and a new directory never resumes or retries another; "
        "collect every directory used so a repeated ticket shows as ambiguous"
    )
    return warnings


def status(registration_path: Path, plan: Record, worker: int, trials: Path) -> Record:
    registration, loaded, tickets = _load(registration_path, plan, worker)
    plan_digest = digest(loaded)
    registered = frozenset(cell["ticket"] for cell in registration["cells"])
    with ExitStack() as stack:
        root = open_root(stack, trials)
        survey = _survey(root, plan_digest, worker, tickets, registered)
    counts = dict.fromkeys(OBSERVED, 0)
    for value in survey.observed.values():
        counts[value] += 1
    assert sum(counts.values()) == len(tickets)
    return {
        "schema": STATUS_SCHEMA,
        "plan": plan_digest,
        "layout": loaded["layout"],
        "workers": loaded["workers"],
        "worker": worker,
        "coordination": COORDINATION,
        "root_binding": survey.binding,
        "tickets": [
            {"ticket": ticket, "observed": survey.observed[ticket]}
            for ticket in tickets
        ],
        "counts": counts,
        "foreign": survey.foreign,
        "unregistered_entries": survey.unregistered,
        "warnings": _warnings(survey),
    }


def _candidates(
    trials: Path,
    plan_digest: str,
    worker: int,
    tickets: list[str],
    registered: frozenset[str],
) -> list[str]:
    with ExitStack() as stack:
        root = open_root(stack, trials)
        survey = _survey(root, plan_digest, worker, tickets, registered)
        require(
            survey.binding in ("unbound", "same"),
            f"trials directory binding is {survey.binding}; no trial was started",
        )
        require(
            sum(survey.foreign.values()) == 0,
            "registered tickets of other workers are in this trials directory; "
            "no trial was started",
        )
        fresh = [ticket for ticket in tickets if survey.observed[ticket] == "absent"]
        require(
            survey.binding == "same" or len(fresh) == len(tickets),
            "this unbound trials directory already holds assigned tickets; "
            "no trial was started",
        )
        if fresh and survey.binding == "unbound":
            bind(root, plan_digest, worker)
    assert fresh == sorted(fresh)
    return fresh


def _native_args(
    registration_path: Path,
    task: Path,
    trials: Path,
    ticket: str,
    allow_named_seats: bool,
    interval: float | None,
) -> argparse.Namespace:
    require(isinstance(task, Path), "task directory is not a Path")
    require(type(allow_named_seats) is bool, "allow_named_seats is not a bool")
    require(
        interval is None or type(interval) in (int, float),
        "interval is not a number",
    )
    return argparse.Namespace(
        registration=registration_path,
        task=task,
        trials=trials,
        ticket=ticket,
        allow_named_seats=allow_named_seats,
        interval=None if interval is None else float(interval),
    )


async def run_next(
    registration_path: Path,
    plan: Record,
    worker: int,
    task: Path,
    trials: Path,
    *,
    allow_named_seats: bool = False,
    interval: float | None = None,
) -> Record | None:
    registration, loaded, tickets = _load(registration_path, plan, worker)
    from . import run as native_run

    def native_args(ticket: str) -> argparse.Namespace:
        return _native_args(
            registration_path, task, trials, ticket, allow_named_seats, interval
        )

    try:
        native_run.inputs(native_args(tickets[0]))
    except OSError as error:
        raise ValueError(
            f"task or trials directory is unusable: {error.strerror}"
        ) from None
    registered = frozenset(cell["ticket"] for cell in registration["cells"])
    fresh = _candidates(trials, digest(loaded), worker, tickets, registered)
    for ticket in fresh:
        try:
            ok = await native_run.run(native_args(ticket), summary_to_stderr=True)
        except native_run.AlreadyStarted:
            continue
        return {"ticket": ticket, "native_runner_ok": ok is True}
    return None
