import os
import re
import stat
from fractions import Fraction
from pathlib import Path
from typing import Any

from .manifest import _directory, load, load_private_json

PRICE = Fraction(1, 4)
ARMS = ("version", "sham")
STATES = ("not_started", "incomplete", "complete")
LIMITS = {
    "reward": (Fraction(0), Fraction(1)),
    "reads": (Fraction(0), Fraction(1)),
    "utility": (-PRICE, Fraction(1)),
}
START_SCHEMA = "seatlab.receiver-study.start/v1"
RESULT_SCHEMA = "seatlab.receiver-study.result/v1"
PENDING = re.compile(r"\.pending-[0-9a-f]{32}")


def _roster(attempts: Path, assignments: list[dict[str, Any]]) -> set[str]:
    allowed = {
        f"{row['ticket']}.{kind}.json"
        for row in assignments
        for kind in ("start", "result")
    }
    present: set[str] = set()
    try:
        fd = _directory(attempts)
    except OSError:
        raise ValueError("cannot inspect attempt directory") from None
    try:
        with os.scandir(fd) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 2 * len(allowed):
                    raise ValueError("too many attempt files")
                if entry.name in allowed:
                    if not entry.is_file(follow_symlinks=False):
                        raise ValueError("invalid attempt file type")
                    present.add(entry.name)
                elif PENDING.fullmatch(entry.name):
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.getuid()
                    ):
                        raise ValueError("invalid pending file")
                else:
                    raise ValueError("unexpected attempt file")
    except OSError:
        raise ValueError("cannot inspect attempt files") from None
    finally:
        os.close(fd)
    return present


def _actions(actions: Any) -> tuple[str, int]:
    if type(actions) is not list or len(actions) not in (1, 2):
        raise ValueError("invalid action sequence")
    reads = len(actions) - 1
    if reads and actions[0] != {"op": "read"}:
        raise ValueError("invalid read action")
    commit = actions[-1]
    if (
        type(commit) is not dict
        or set(commit) != {"op", "slot"}
        or commit["op"] != "commit"
        or type(commit["slot"]) is not str
        or commit["slot"] not in ("A", "B", "C")
    ):
        raise ValueError("invalid commit action")
    return commit["slot"], reads


def _attempt(
    directory: Path, present: set[str], pin: str, row: dict[str, Any]
) -> tuple[str, tuple[int, int] | None]:
    ticket = row["ticket"]
    start, result = f"{ticket}.start.json", f"{ticket}.result.json"
    if start not in present:
        if result in present:
            raise ValueError("result without claim")
        return "not_started", None
    identity = {"plan_sha256": pin, "ticket": ticket}
    if load_private_json(directory / start) != {"schema": START_SCHEMA, **identity}:
        raise ValueError("invalid start evidence")
    if result not in present:
        return "incomplete", None
    data = load_private_json(directory / result)
    if type(data) is not dict or set(data) != {
        "schema",
        "plan_sha256",
        "ticket",
        "offer_sha256",
        "read_sha256",
        "actions",
    }:
        raise ValueError("invalid result fields")
    if (
        data["schema"] != RESULT_SCHEMA
        or data["ticket"] != ticket
        or data["plan_sha256"] != pin
        or data["offer_sha256"] != row["offer_sha256"]
    ):
        raise ValueError("result does not match pinned assignment")
    slot, reads = _actions(data["actions"])
    if data["read_sha256"] != (row["read_sha256"] if reads else None):
        raise ValueError("read digest does not match action and pinned reply")
    return "complete", (row["scores"][slot], reads)


def _interval(low: Fraction, high: Fraction) -> dict[str, str]:
    return {"lower": str(low), "upper": str(high)}


def _arm(rows: list[tuple[str, tuple[int, int] | None]]) -> dict[str, Any]:
    counts = dict.fromkeys(STATES, 0)
    reward = reads = 0
    for state, outcome in rows:
        counts[state] += 1
        if outcome is not None:
            reward += outcome[0]
            reads += outcome[1]
    missing = len(rows) - counts["complete"]
    sums = {
        "reward": Fraction(reward),
        "reads": Fraction(reads),
        "utility": Fraction(reward) - PRICE * reads,
    }
    return {
        "scheduled": len(rows),
        "status_counts": counts,
        "observed_sums": {metric: str(value) for metric, value in sums.items()},
        "bounds": {
            metric: _interval(
                (sums[metric] + missing * low) / len(rows),
                (sums[metric] + missing * high) / len(rows),
            )
            for metric, (low, high) in LIMITS.items()
        },
    }


def assess(root: Path, expected: str) -> dict[str, Any]:
    root = root.expanduser().absolute()
    plan = load(root, expected)
    directory = root / "attempts"
    rows = plan["assignments"]
    present = _roster(directory, rows)
    groups: dict[str, list[tuple[str, tuple[int, int] | None]]] = {
        arm: [] for arm in ARMS
    }
    for row in rows:
        groups[row["arm"]].append(_attempt(directory, present, expected, row))
    if any(len(group) != 48 for group in groups.values()):
        raise ValueError("unbalanced enrollment")
    arms = {arm: _arm(group) for arm, group in groups.items()}
    version, sham = (arms[arm]["bounds"] for arm in ARMS)
    return {
        "schema": "seatlab.receiver-study.report/v1",
        "source": plan["source"],
        "scope": (
            "Descriptive finite-assignment bounds from pinned native action scores. "
            "Missing responses are unresolved, not failures. Assignments are not "
            "independent statistical observations. Source labels are not verified "
            "model identity or evidence of native execution, model use or token cost."
        ),
        "scheduled": len(rows),
        "status_counts": {
            state: sum(arm["status_counts"][state] for arm in arms.values())
            for state in STATES
        },
        "arms": arms,
        "version_minus_sham": {
            metric: _interval(
                Fraction(version[metric]["lower"]) - Fraction(sham[metric]["upper"]),
                Fraction(version[metric]["upper"]) - Fraction(sham[metric]["lower"]),
            )
            for metric in LIMITS
        },
    }
