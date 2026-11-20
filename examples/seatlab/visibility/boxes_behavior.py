"""Audit actual native behavior in the completed E1 boxes contrast.

The audit reads authenticated world responses. It does not establish what a
model saw, what a message meant, or why an agent opened a box.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

from . import muster
from .core import Trial

_ARMS = ("e1/indep-3", "e1/team-3")
_EARLY_LIMIT = 13


def _trials(root: Path) -> list[Trial]:
    trials = [trial for trial in muster.load(root) if trial.arm in _ARMS]
    if len(trials) != 60 or any(trial.finished is not True for trial in trials):
        raise ValueError("E1 behavior panel is not the 60 completed native trials")
    if any(
        any(coverage != "complete" for coverage in trial.coverage.values())
        for trial in trials
    ):
        raise ValueError("E1 behavior panel has an unresolved world audit")
    return trials

def _source_instance(trial: Trial) -> tuple[int, Fraction]:
    job_config, _, trial_result = (json.loads(path.read_bytes()) for path in trial.sources[:3])
    lock = json.loads((trial.sources[2].parent / "lock.json").read_bytes())
    name = trial_result["trial_name"]
    instance = int(name.split("__", 1)[0].removeprefix("boxes-i"))
    expected = next(
        task
        for task in job_config["tasks"]
        if Path(task["path"]).name == f"boxes-i{instance:02d}"
    )
    task = trial_result["config"]["task"]
    task_path = Path(task["path"])
    if (
        trial_result["id"] != trial.key
        or task_path.name != f"boxes-i{instance:02d}"
        or Path(trial_result["task_id"]["path"]) != task_path
        or Path(lock["task"]["path"]) != task_path
        or not str(lock["task"]["digest"]).startswith("sha256:")
        or not trial_result["task_checksum"]
        or any(task.get(key) != expected.get(key) for key in expected if key != "path")
        or not trial_result["reward_valid"]
        or trial_result["exception_info"] is not None
        or not trial_result["finished_at"]
    ):
        raise ValueError(f"{trial.arm} contains an unmatched native trial")
    if any(
        participant.get("resume_trajectory") or participant.get("load_trajectory")
        for participant in job_config["participants"]
    ):
        raise ValueError(f"{trial.arm} loads or resumes a trajectory")
    reward = trial_result["verifier_result"]["rewards"]["reward"]
    if type(reward) not in (int, float):
        raise ValueError(f"{trial.arm} has no native reward")
    if trial.sources[-1].parent.parent != trial.sources[2].parent:
        raise ValueError(f"{trial.arm} has world evidence outside its native trial")
    return instance, Fraction(reward).limit_denominator()


def _rows(path: Path) -> Iterator[dict]:
    for seq, line in enumerate(path.read_bytes().splitlines(), 1):
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("seq") != seq:
            raise ValueError(f"{path.name} breaks the continuous world sequence")
        yield row


def _accepted(row: dict) -> bool:
    return row.get("late") is not True and row.get("ok") is True and isinstance(row.get("seat"), str)


def _audit(trial: Trial) -> tuple[tuple[tuple[str, str, str], ...], int]:
    """Classify accepted opens after the full fetch sequence is known.

    A late or failed fetch is not a verified peer fetch. Message text is ignored.
    """
    rows = tuple(_rows(trial.sources[-1]))
    close = rows[-1]
    response = close.get("response")
    if (
        close.get("kind") != "admin.close"
        or close.get("method") != "POST"
        or close.get("seat") is not None
        or close.get("ok") is not True
        or not isinstance(response, dict)
        or response.get("closed") is not True
        or response.get("events") != len(rows)
    ):
        raise ValueError(f"{trial.arm} has no authenticated close over its world audit")

    posts: dict[int, str] = {}
    peer_fetch: dict[str, int] = {}
    for row in rows[:-1]:
        if not _accepted(row):
            continue
        kind, method, seat = row.get("kind"), row.get("method"), row["seat"]
        response = row.get("response")
        if kind == "msg" and method == "POST":
            message_id = response.get("id") if isinstance(response, dict) else None
            if type(message_id) is not int or response.get("from") != seat or message_id in posts:
                raise ValueError(f"{trial.arm} has an unresolved accepted message")
            posts[message_id] = seat
        elif kind == "msgs" and method == "GET":
            batch = response.get("messages") if isinstance(response, dict) else None
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) or item.get("from") != posts.get(item.get("id"))
                for item in batch
            ):
                raise ValueError(f"{trial.arm} has an unresolved message fetch")
            if any(item["from"] != seat for item in batch):
                peer_fetch.setdefault(seat, row["seq"])

    seen: dict[int, set[str]] = defaultdict(set)
    opened: list[tuple[str, str, str]] = []
    for row in rows[:-1]:
        if not _accepted(row) or row.get("kind") != "open" or row.get("method") != "POST":
            continue
        response = row.get("response")
        box = response.get("box") if isinstance(response, dict) else None
        request = row.get("request")
        seat = row["seat"]
        if (
            type(box) is not int
            or box < 1
            or not isinstance(request, dict)
            or request.get("box") != box
        ):
            raise ValueError(f"{trial.arm} has an unresolved accepted open")
        owners = seen[box]
        if not owners:
            relation = "first"
        elif seat not in owners:
            relation = "peer"
        elif owners == {seat}:
            relation = "own"
        else:
            relation = "both"
        fetch_at = peer_fetch.get(seat)
        if fetch_at is None:
            timing = "no-fetch"
        elif row["seq"] < fetch_at:
            timing = "before"
        else:
            timing = "after"
        ordinal = "early" if len(opened) < _EARLY_LIMIT else "late"
        opened.append((ordinal, timing, relation))
        owners.add(seat)
    if opened and opened[0][2] != "first":
        raise ValueError(f"{trial.arm} records a first open as a repeat")
    return tuple(opened), len(peer_fetch)


def _cross_seat(relation: str) -> bool:
    return relation in {"peer", "both"}


def _share(rows: tuple[tuple[str, str, str], ...]) -> Fraction | None:
    if not rows:
        return None
    return Fraction(sum(_cross_seat(row[2]) for row in rows), len(rows))


def _mean(values: list[Fraction], expected: int) -> Fraction:
    if len(values) != expected:
        raise ValueError("native denominator is incomplete")
    return sum(values, Fraction()) / len(values)


def _dispersion(values: list[Fraction]) -> list[Fraction]:
    ordered = sorted(values)
    return [ordered[-1] - ordered[0], *ordered]


def _count(
    rows: list[tuple[str, str, str]], timing: str, ordinal: str | None = None
) -> dict[str, int]:
    selected = [row for row in rows if row[1] == timing and (ordinal is None or row[0] == ordinal)]
    own = sum(row[2] == "own" for row in selected)
    peer_only = sum(row[2] == "peer" for row in selected)
    both = sum(row[2] == "both" for row in selected)
    return {
        "accepted_opens": len(selected),
        "cross_seat_repeats": peer_only + both,
        "own": own,
        "peer_only": peer_only,
        "both": both,
    }


def analyze(root: Path) -> dict[str, object]:
    """Return path-free native-trial aggregates for the actual E1 contrast."""
    grouped: dict[tuple[str, int], list[tuple[Fraction, tuple[tuple[str, str, str], ...], int, int]]] = (
        defaultdict(list)
    )
    for trial in _trials(root):
        instance, reward = _source_instance(trial)
        actions, fetch_seats = _audit(trial)
        grouped[(trial.arm, instance)].append((reward, actions, fetch_seats, len(trial.seats)))

    by_arm: dict[str, dict[str, object]] = {}
    for arm in _ARMS:
        rewards: list[Fraction] = []
        repeats: list[Fraction] = []
        arm_actions: list[tuple[str, str, str]] = []
        instances = []
        for instance in range(1, len(muster._INSTANCES) + 1):
            rows = grouped[(arm, instance)]
            reward_values = [reward for reward, _, _, _ in rows]
            repeat_values = [_share(actions) for _, actions, _, _ in rows]
            if len(rows) != muster._ATTEMPTS or None in repeat_values:
                raise ValueError(f"{arm} does not preserve 3 scored native trials")
            rewards.append(_mean(reward_values, muster._ATTEMPTS))
            repeats.append(_mean(repeat_values, muster._ATTEMPTS))
            arm_actions.extend(action for _, actions, _, _ in rows for action in actions)
            instances.append(
                {
                    "instance": instance,
                    "trials": muster._ATTEMPTS,
                    "mean_reward": rewards[-1],
                    "mean_cross_seat_repeat_share": repeats[-1],
                    "reward_dispersion": _dispersion(reward_values),
                }
            )
        by_arm[arm] = {
            "native_trials": muster._ATTEMPTS * len(muster._INSTANCES),
            "mean_reward": _mean(rewards, len(muster._INSTANCES)),
            "mean_cross_seat_repeat_share": _mean(repeats, len(muster._INSTANCES)),
            "relations": {
                "accepted_opens": len(arm_actions),
                "cross_seat_repeats": sum(_cross_seat(row[2]) for row in arm_actions),
                "own": sum(row[2] == "own" for row in arm_actions),
                "peer_only": sum(row[2] == "peer" for row in arm_actions),
                "both": sum(row[2] == "both" for row in arm_actions),
            },
            "instances": instances,
        }

    team = [
        row
        for instance in range(1, 11)
        for row in grouped[("e1/team-3", instance)]
    ]
    team_flat = [action for _, actions, _, _ in team for action in actions]
    timings = ("before", "no-fetch", "after")
    return {
        "population": "e1-boxes-behavior",
        "matching": "source-task-instance",
        "weighting": "equal-instance",
        "repeat_definition": "peer-only-plus-both",
        "attempts_per_instance": muster._ATTEMPTS,
        "arms": by_arm,
        "talk": {
            "trials": len(team),
            "seats": sum(seats for *_, seats in team),
            "trials_with_peer_fetch": sum(fetch_seats > 0 for _, _, fetch_seats, _ in team),
            "seats_with_peer_fetch": sum(fetch_seats for _, _, fetch_seats, _ in team),
            "before_first_peer_fetch": _count(team_flat, "before"),
            "no_peer_fetch": _count(team_flat, "no-fetch"),
            "after_first_peer_fetch": _count(team_flat, "after"),
            "early_late": {
                ordinal: {timing: _count(team_flat, timing, ordinal) for timing in timings}
                for ordinal in ("early", "late")
            },
        },
        "unresolved": {"world_audits": 0, "accepted_opens": 0, "peer_fetches": 0},
    }


def _json(value: object) -> object:
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    json.dump(_json(analyze(args.root)), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
