"""Read the scored Muster E1/E2 boxes panel from its explicit e1run root.

The world log audits authenticated HTTP requests and their returned payloads.
It does not establish that a model received the payload as input. The saved
Terminus-2 trajectories record rendered terminal output, not model requests.
"""

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from .core import Coverage, Event, MessagePair, Trial

# The preregistered E2 contrasts reuse the E1 independent control, not a new job.
_ARMS = (
    "e1/solo-a",
    "e1/solo-b",
    "e1/solo-x3",
    "e1/indep-3",
    "e1/team-3",
    "e2/indep-3-salt",
    "e2/indep-3-mixed",
)
_ATTEMPTS = 3
_INSTANCES = tuple(f"boxes-i{i:02}" for i in range(1, 11))
_ARTIFACT = "artifacts/logs/world/events.jsonl"


def _json(path: Path):
    return json.loads(path.read_bytes())


def _message(row: object) -> MessagePair | None:
    if not isinstance(row, dict):
        return None
    sender, message_id = row.get("from"), row.get("id")
    if not isinstance(sender, str) or type(message_id) is not int or message_id < 1:
        return None
    return sender, str(message_id)


def _project(
    path: Path, seats: tuple[str, ...], channel: bool
) -> tuple[tuple[Event, ...], dict[str, Coverage]]:
    """Project the continuous, pre-close window; never treat a gap as silence."""
    events: list[Event] = []
    posts: dict[str, MessagePair] = {}
    uncertain: set[str] = set()
    seen_row = False
    closed = False
    for seq, line in enumerate(path.read_bytes().splitlines(), 1):
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            break
        if not isinstance(row, dict) or row.get("seq") != seq:
            break
        seen_row = True
        kind, method, seat = row.get("kind"), row.get("method"), row.get("seat")
        response = row.get("response")
        if row.get("ok") is False or row.get("late") is True:
            continue
        if row.get("ok") is not True:
            uncertain.update(seats)
            continue
        if kind == "admin.close" and method == "POST" and seat is None:
            closed = (
                isinstance(response, dict)
                and response.get("closed") is True
                and response.get("events") == seq
            )
            break
        if not isinstance(seat, str) or seat not in seats:
            # A response with no authenticated reader cannot prove a fetch.
            uncertain.update(seats)
            continue
        if kind == "open" and method == "POST":
            events.append(Event("action", seat))
        elif kind == "msg" and method == "POST":
            message = _message(response)
            if not channel or message is None or message[0] != seat or message[1] in posts:
                uncertain.update(seats)
                continue
            posts[message[1]] = message
            events.append(Event("send", seat, (message,)))
            # The world appends to a shared list that /msgs serves to every seat.
            # This proves eligibility, not delivery or a second fetch.
            events.extend(Event("available", peer, (message,)) for peer in seats if peer != seat)
        elif kind == "msgs" and method == "GET":
            batch = response.get("messages") if isinstance(response, dict) else None
            if not channel or not isinstance(batch, list):
                uncertain.add(seat)
                continue
            messages: list[MessagePair] = []
            for item in batch:
                message = _message(item)
                if message is None or posts.get(message[1]) != message:
                    uncertain.add(seat)
                    break
                messages.append(message)
            else:
                # Preserve actual response order, repeated GETs, and self messages.
                events.append(Event("fetch", seat, tuple(messages)))

    coverage: dict[str, Coverage] = {}
    for seat in seats:
        if not seen_row:
            coverage[seat] = "unavailable"
        elif not closed or seat in uncertain:
            coverage[seat] = "partial"
        else:
            coverage[seat] = "complete"
    return tuple(events), coverage


def _world_sources(trial_dir: Path) -> tuple[Path | None, tuple[Path, ...]]:
    verifier = trial_dir / "verifier/events.jsonl"
    if verifier.is_file():
        # Harbor collects artifacts before verification. The verifier snapshot
        # includes close and can also include requests the earlier copy misses.
        return verifier, (verifier,)
    manifest = trial_dir / "artifacts/manifest.json"
    if not manifest.is_file():
        return None, ()
    artifact = trial_dir / _ARTIFACT
    entries = _json(manifest)
    if artifact.is_file() and isinstance(entries, list) and any(
        isinstance(entry, dict)
        and entry.get("source") == "/logs/world/events.jsonl"
        and entry.get("destination") == _ARTIFACT
        and entry.get("type") == "file"
        and entry.get("status") == "ok"
        for entry in entries
    ):
        return artifact, (manifest, artifact)
    return None, (manifest,)


def _load_trial(result_path: Path, arm: str, job_id: str, job_sources: tuple[Path, ...]) -> Trial:
    result = _json(result_path)
    name = result_path.parent.name
    if (
        result["config"]["job_id"] != job_id
        or result["trial_name"] != name
        or result["task_name"] != f"muster/{name.split('__', 1)[0]}"
    ):
        raise ValueError(f"{arm} contains a trial outside its native job manifest")
    key = str(UUID(result["id"]))
    seats = tuple(participant["seat"] for participant in result["config"]["participants"])
    if not seats or tuple(row["seat"] for row in result["seat_results"]) != seats:
        raise ValueError(f"{arm} contains an inconsistent native roster")
    world, sources = _world_sources(result_path.parent)
    events: tuple[Event, ...] = ()
    coverage: dict[str, Coverage] = dict.fromkeys(seats, "unavailable")
    if world is not None:
        events, coverage = _project(world, seats, channel=arm == "e1/team-3")
    finished = bool(result["finished_at"]) if "finished_at" in result else None
    return Trial(key, arm, seats, events, coverage, finished, (*job_sources, result_path, *sources))


def load(root: Path) -> Iterator[Trial]:
    """Yield the 210 canonical native trials; reject a wrong or incomplete panel."""
    seen: set[str] = set()
    expected = Counter(dict.fromkeys(_INSTANCES, _ATTEMPTS))
    for arm in _ARMS:
        label = arm.split("/")[1]
        job_dir = root / label
        config_path, job_path = job_dir / "config.json", job_dir / "result.json"
        if not config_path.is_file() or not job_path.is_file():
            raise ValueError(f"Muster e1run root is missing the {arm} job manifest")
        config, job = _json(config_path), _json(job_path)
        if (
            config["job_name"] != label
            or config["n_attempts"] != _ATTEMPTS
            or Counter(Path(task["path"]).name for task in config["tasks"]) != Counter(_INSTANCES)
            or job["n_total_trials"] != expected.total()
        ):
            raise ValueError(f"{arm} does not match the preregistered Muster panel")

        trial_paths = sorted(job_dir.glob("boxes-*/result.json"))
        if Counter(p.parent.name.split("__", 1)[0] for p in trial_paths) != expected:
            raise ValueError(f"{arm} does not have 3 native trials for each of its 10 tasks")
        for path in trial_paths:
            trial = _load_trial(path, arm, job["id"], (config_path, job_path))
            if trial.key in seen:
                raise ValueError(f"{arm} repeats a native trial UUID")
            seen.add(trial.key)
            yield trial
