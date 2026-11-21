"""Read the 276 original Flotilla trials selected by Muster's arms-manifest.

Pass the original .flotilla-local/jobs directory, not the decomposition copies.
The c1 world-v2 stratum is separate from the w1/w2 world-v1 controls. Later
canonical waves and other pilots are not in this panel.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from .core import Coverage, Event, MessagePair, Trial

# (wave, native job stem, attempted instances per team arm). All solo arms
# have instances 0-11. The two truncated pilot jobs have contiguous prefixes.
_PANEL = (
    ("c1", "c1-ds", 12),
    ("c1", "c1-glm47", 12),
    ("c1", "c1-gptoss", 12),
    ("c1", "c1-qwen36", 12),
    ("w1", "w1-schedule-ds", 7),
    ("w2", "w2-schedule-glm-4-7-flash", 12),
    ("w2", "w2-schedule-gpt-oss-120b", 12),
    ("w2", "w2-schedule-qwen3-6-35b", 11),
)
_TASK = re.compile(r"schedule-([0-9]{3})-(solo|silent|talk)")


def _object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError):
        raise ValueError("invalid Flotilla archive JSON") from None
    if not isinstance(value, dict):
        raise ValueError("expected a Flotilla archive JSON object")
    return value


def _task_slot(name: object) -> tuple[str, int]:
    if isinstance(name, str) and (match := _TASK.fullmatch(Path(name).name)):
        return match[2], int(match[1])
    raise ValueError("missing original Flotilla schedule task identity")


def _matches_world(
    data: dict, seats: tuple[str, ...], condition: str, version: int
) -> bool:
    # Version 1 omitted the version field in both start and final state.
    return (
        data.get("seats") == list(seats)
        and data.get("condition") == condition
        and data.get("world_version", 1) == version
    )


def _snapshot_agrees(
    state: dict, sends: list[tuple[str, str, str]], submits: list[tuple[int, str]]
) -> bool:
    messages, submissions = state.get("messages"), state.get("submissions")
    return (
        state.get("frozen") is True
        and isinstance(messages, list)
        and all(isinstance(row, dict) for row in messages)
        and [(row.get("id"), row.get("from"), row.get("to")) for row in messages]
        == sends
        and isinstance(submissions, list)
        and all(isinstance(row, dict) for row in submissions)
        and [(row.get("n"), row.get("seat")) for row in submissions] == submits
    )


def _read_evidence(
    directory: Path,
    seats: tuple[str, ...],
    condition: str,
    version: int,
    play_ended: bool,
) -> tuple[tuple[Event, ...], Coverage, tuple[Path, ...]]:
    tape = directory / "artifacts/logs/world/events.jsonl"
    try:
        stream = tape.open("rb")
    except FileNotFoundError:
        return (), "unavailable", ()

    # Seq is allocated under the world lock. Its order is exact even where the
    # millisecond clock ties. A damaged prefix cannot justify later snapshots.
    events: list[Event] = []
    sends: list[tuple[str, str, str]] = []
    submits: list[tuple[int, str]] = []
    inboxes: dict[str, list[MessagePair]] = {seat: [] for seat in seats}
    started = False
    reached_end = False
    with stream:
        for seq, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except (UnicodeError, ValueError):
                break
            if not isinstance(row, dict) or row.get("seq") != seq:
                break
            kind, seat, data = row.get("kind"), row.get("seat"), row.get("data")
            if not isinstance(kind, str) or not isinstance(data, dict):
                break
            if seq == 1:
                if kind != "start" or not _matches_world(
                    data, seats, condition, version
                ):
                    break
                started = True
            elif kind == "start":
                break
            elif kind in ("send", "deliver", "submit"):
                if not isinstance(seat, str) or seat not in inboxes:
                    break
                if kind == "send":
                    mid, to = data.get("id"), data.get("to")
                    if (
                        condition != "talk"
                        or mid != f"m{len(sends) + 1}"
                        or not isinstance(to, str)
                        or (to != "all" and to not in inboxes)
                    ):
                        break
                    sends.append((mid, seat, to))
                    pair = (seat, mid)
                    events.append(Event("send", seat, (pair,)))
                    for recipient, inbox in inboxes.items():
                        if recipient != seat and to in ("all", recipient):
                            inbox.append(pair)
                            # Routing makes the row eligible now, regardless
                            # of whether a later GET actually retrieves it.
                            events.append(Event("available", recipient, (pair,)))
                elif kind == "deliver":
                    inbox = inboxes[seat]
                    if condition != "talk" or data.get("ids") != [
                        mid for _, mid in inbox
                    ]:
                        break
                    # This server event records the response prepared for this
                    # GET, not terminal output, an actor prompt, or actual use.
                    events.append(Event("fetch", seat, tuple(inbox)))
                else:
                    n = len(submits) + 1
                    if data.get("n") != n or not isinstance(data.get("answer"), dict):
                        break
                    submits.append((n, seat))
                    # The service accepted this write to the team answer slot.
                    # It need not be a valid calendar choice. Polls, rejected
                    # writes, and other requests cannot change that slot.
                    events.append(Event("action", seat))
        else:
            reached_end = True

    coverage: Coverage = "partial" if started else "unavailable"
    sources = (tape,)
    # Reconcile the recorded play cutoff, not the whole run until freeze.
    # The later state can show a gap but cannot identify or order any GETs.
    if started and reached_end and play_ended:
        snapshot = directory / "verifier/world-state.json"
        try:
            state = _object(snapshot)
        except ValueError:
            sources += (snapshot,)
        else:
            if state is not None:
                sources += (snapshot,)
                if _matches_world(
                    state, seats, condition, version
                ) and _snapshot_agrees(state, sends, submits):
                    coverage = "complete"
    return tuple(events), coverage, sources


def _load_job(
    root: Path, job: str, wave: str, counts: dict[str, int]
) -> Iterator[Trial]:
    directory = root / job
    job_path = directory / "result.json"
    job_result = _object(job_path)
    if job_result is None or not isinstance(job_result.get("id"), str):
        raise ValueError(f"missing original Flotilla job: {job}")

    expected = {
        (condition, n) for condition, count in counts.items() for n in range(count)
    }
    selected = {}
    for trial_dir in sorted(directory.iterdir()):
        if not trial_dir.is_dir():
            continue
        result_path = trial_dir / "result.json"
        result = _object(result_path)
        identity_path = result_path if result is not None else trial_dir / "config.json"
        config = result.get("config") if result is not None else _object(identity_path)
        if not isinstance(config, dict) or config.get("job_id") != job_result["id"]:
            raise ValueError(
                f"unexpected trial directory in original Flotilla job: {job}"
            )
        if config.get("source_trial") is not None or (
            result and result.get("source") is not None
        ):
            raise ValueError(f"transferred trial in original Flotilla job: {job}")
        task = config.get("task")
        if not isinstance(task, dict):
            raise ValueError(f"missing original Flotilla job task: {job}")
        slot = _task_slot(task.get("path"))
        if result is not None and _task_slot(result.get("task_name")) != slot:
            raise ValueError(f"conflicting Flotilla task identity: {job}")
        if slot not in expected or slot in selected:
            raise ValueError(f"unexpected or duplicate Flotilla task slot: {job}")
        selected[slot] = (trial_dir, result, identity_path)
    if selected.keys() != expected:
        raise ValueError(f"incomplete original Flotilla job: {job}")

    version = 2 if wave == "c1" else 1
    for (condition, _), (trial_dir, result, identity_path) in selected.items():
        seats = ("p1",) if condition == "solo" else ("p1", "p2")
        play_end = result.get("agent_execution") if result else None
        events, coverage, sources = _read_evidence(
            trial_dir,
            seats,
            condition,
            version,
            isinstance(play_end, dict) and bool(play_end.get("finished_at")),
        )
        if result is not None and result.get("id") is not None:
            try:
                key = str(UUID(result["id"]))
            except (AttributeError, TypeError, ValueError):
                raise ValueError(f"invalid Flotilla trial identity: {job}") from None
        else:
            key = f"flotilla:{job}/{trial_dir.name}"
        yield Trial(
            key,
            f"{wave}/v{version}/{condition}",
            seats,
            events,
            dict.fromkeys(seats, coverage),
            None if result is None else bool(result.get("finished_at")),
            (job_path, identity_path, *sources),
        )


def load(root: Path) -> Iterator[Trial]:
    """Load original job folders with the frozen decomposition task slots."""
    seen: set[str] = set()
    for wave, stem, size in _PANEL:
        for suffix, counts in (
            ("1seat", {"solo": 12}),
            ("2seat", {"silent": size, "talk": size}),
        ):
            for trial in _load_job(root, f"{stem}-{suffix}", wave, counts):
                if trial.key in seen:
                    raise ValueError(
                        "duplicate Flotilla trial identity in original jobs"
                    )
                seen.add(trial.key)
                yield trial
