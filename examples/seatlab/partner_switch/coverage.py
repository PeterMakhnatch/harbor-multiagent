import os
import stat
from pathlib import Path
from typing import Any

from . import planio
from .engine import ROLES

SCHEMA = "seatlab.partner-switch-coverage/v1"
SCOPE = (
    "Read-only coverage of final captures in a trusted private operator directory. "
    "Absent output does not mean no calls were made."
)


def _capture(
    plan: planio.Plan, fd: int, name: str, role: str
) -> tuple[str, str | None]:
    deck = plan.decks[name, role]
    try:
        info = os.stat(deck.folder, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "invalid", None
    if not stat.S_ISDIR(info.st_mode):
        return "invalid", None
    try:
        _, pin = planio.read_capture(plan, fd, name, role)
    except (OSError, ValueError, RecursionError):
        return "invalid", None
    return "complete", pin


def _counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "expected": len(jobs),
        **{
            state: sum(job["state"] == state for job in jobs)
            for state in ("missing", "invalid", "complete")
        },
    }


def inspect(
    plan: planio.Plan, root: Path, workers: int, worker: int | None
) -> dict[str, Any]:
    if type(workers) is not int or not 1 <= workers <= 12:
        raise ValueError("expected 1 to 12 workers")
    if worker is not None and (type(worker) is not int or not 0 <= worker < workers):
        raise ValueError("invalid worker index")
    try:
        fd = planio._directory(root)
    except (OSError, ValueError):
        raise ValueError("expected an existing capture root directory") from None
    jobs: list[dict[str, Any]] = []
    try:
        for name in plan.names:
            for role in ROLES:
                deck = plan.decks[name, role]
                state, pin = _capture(plan, fd, name, role)
                jobs.append(
                    {
                        "team": name,
                        "role": role,
                        "folder": deck.folder,
                        "deck_sha256": deck.pin,
                        "assigned_worker": len(jobs) % workers,
                        "state": state,
                        "expected": len(deck.packets),
                        "capture_sha256": pin,
                    }
                )
    finally:
        os.close(fd)
    selected = [
        job for job in jobs if worker is None or job["assigned_worker"] == worker
    ]
    return {
        "schema": SCHEMA,
        "stage": plan.stage,
        "study_sha256": plan.pin,
        "workers": workers,
        "worker": worker,
        "all": _counts(jobs),
        "selected": _counts(selected),
        "jobs": selected,
        "scope": SCOPE,
    }
