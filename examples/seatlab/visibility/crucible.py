"""Project the report_final5 Crucible trial census onto server-side chat evidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from .core import Event, Trial

_REPORT = Path("experiments/report_final5/report.json")
_WAVE_MANIFEST = "wave1_manifest.json"
_WORLD_LOG = Path("artifacts/logs/world/events.jsonl")
_COLLECTION = Path("artifacts/manifest.json")

_FAMILIES = {
    "supply-chain-sabotage": "supply",
    "reward-hack-contagion": "reward",
    "migration-turf-war": "migration",
    "epistemic-crucible": "epistemic",
}
_CHANNELS = {
    "supply-chain-sabotage": {"chat_public": "public", "chat_dm": "dm"},
    "reward-hack-contagion": {"chat_post": "public"},
    "migration-turf-war": {"board_post": "board"},
    "epistemic-crucible": {"chat_message": "public", "scout_report": "round_reports"},
}
# Only accepted task decisions. Polls, lifecycle /done, diagnostics, and the
# side effects of a decision already recorded by another event are not actions.
_ACTIONS = {
    "supply-chain-sabotage": {"accuse", "build", "mine", "refine", "scan", "stash", "transfer"},
    "reward-hack-contagion": {
        "plan_write",
        "review_approve",
        "review_commit",
        "review_reject",
        "submit",
        "test_run",
    },
    "migration-turf-war": {
        "permission_restoration",
        "permission_revocation",
        "repo_commit",
        "service_deploy",
        "truce_signature",
        "watchdog_registration",
    },
    "epistemic-crucible": {"decision", "vote"},
}


@dataclass(frozen=True)
class RunRecord:
    """Private native run reference, not a publication record."""

    directory: Path = field(repr=False)
    family: str
    condition: str
    key: str = field(repr=False)
    seats: tuple[str, ...]
    finished: bool | None
    _closed_seats: frozenset[str] = field(repr=False)
    _sources: tuple[Path, ...] = field(repr=False)


def _read_json(path: Path, description: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"Crucible {description} is missing or unreadable") from None


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _model_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().casefold() not in {"unknown", "none", "null", "oracle", "nop"}
    )


def _run_record(
    directory: Path, row: dict[str, Any], report_path: Path, manifest_path: Path
) -> tuple[RunRecord, bool]:
    result_path = directory / "result.json"
    result = _read_json(result_path, "native result")
    if not isinstance(result, dict) or result.get("trial_name") != directory.name:
        raise ValueError("Crucible native result does not match its wave reference")
    try:
        parsed_key = UUID(result["id"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("Crucible native trial UUID is invalid") from None
    if not parsed_key.int:
        raise ValueError("Crucible native trial UUID is nil")

    config = result.get("config")
    participants = config.get("participants") if isinstance(config, dict) else None
    if not isinstance(participants, list) or not participants:
        raise ValueError("Crucible native roster is missing")
    declared_model = row.get("model")
    if not _model_name(declared_model):
        raise ValueError("Crucible wave model is not a known model")
    seats = []
    for participant in participants:
        if not isinstance(participant, dict) or not _present(participant.get("seat")):
            raise ValueError("Crucible native roster contains an invalid seat")
        if participant.get("model_name") != declared_model:
            raise ValueError("Crucible configured model does not match its wave")
        seats.append(participant["seat"])
    if len(set(seats)) != len(seats):
        raise ValueError("Crucible native roster contains duplicate seats")

    closed_seats = set()
    has_seat_error = False
    seat_results = result.get("seat_results")
    if seat_results is not None and not isinstance(seat_results, list):
        raise ValueError("Crucible native seat results are invalid")
    seen_seats = set()
    for seat_result in seat_results or []:
        seat = seat_result.get("seat") if isinstance(seat_result, dict) else None
        if not isinstance(seat, str) or seat not in seats or seat in seen_seats:
            raise ValueError("Crucible native seat results disagree with the roster")
        seen_seats.add(seat)
        agent_info = seat_result.get("agent_info")
        if agent_info is not None:
            model = agent_info.get("model_info") if isinstance(agent_info, dict) else None
            name = model.get("name") if isinstance(model, dict) else None
            provider = model.get("provider") if isinstance(model, dict) else None
            if (
                not _model_name(name)
                or not _model_name(provider)
                or f"{provider}/{name}" != declared_model
            ):
                raise ValueError("Crucible recorded model is unknown or disagrees with its wave")
        if seat_result.get("exception_info") is not None:
            has_seat_error = True
            continue
        execution = seat_result.get("agent_execution")
        if isinstance(execution, dict) and _present(execution.get("finished_at")):
            if agent_info is None:
                raise ValueError("Crucible completed seat has no recorded model")
            closed_seats.add(seat)

    error = result.get("exception_info")
    if error is not None and not isinstance(error, dict):
        raise ValueError("Crucible native trial exception is invalid")
    if error is not None:
        # Exact report.py selector: trial-level exception type, not result finish
        # time, seat error, error message, or the presence of a verifier reward.
        timeout = "timeout" in str(error.get("exception_type") or "").lower()
        finished = False
        closed_seats.clear()
    elif has_seat_error:
        timeout = False
        finished = False
    else:
        timeout = False
        finished = True if _present(result.get("finished_at")) and len(closed_seats) == len(seats) else None
        if not _present(result.get("finished_at")):
            closed_seats.clear()

    return (
        RunRecord(
            directory,
            row["family"],
            row["condition"],
            str(parsed_key),
            tuple(seats),
            finished,
            frozenset(closed_seats),
            (report_path, manifest_path, result_path),
        ),
        timeout,
    )


def run_records(root: Path, *, excluded: bool = False) -> Iterator[RunRecord]:
    """Enumerate the published cohort or its disjoint timeout exclusions.

    Only rows named in the wave manifests under the report's corpus_roots
    qualify. Check native identities and report totals across both cohorts before
    yielding. Do not discover trials by path resemblance or replace a run.
    """
    root = root.resolve()
    report_path = root / _REPORT
    report = _read_json(report_path, "report_final5 index")
    if not isinstance(report, dict) or report.get("generated_by") != "crucible report":
        raise ValueError("Crucible report_final5 index is invalid")
    roots = report.get("corpus_roots")
    if not isinstance(roots, list) or not roots or not all(map(_present, roots)):
        raise ValueError("Crucible report_final5 corpus roots are invalid")
    if report.get("exclude_timeouts") is not True:
        raise ValueError("Crucible report_final5 does not declare its timeout selector")
    seen_roots: set[Path] = set()
    seen_keys: set[str] = set()
    census_sources = [report_path]
    included_by_arm: Counter[tuple[str, str]] = Counter()
    selected = []
    n_excluded = 0
    for relative in roots:
        subpath = Path(relative)
        wave = (root / subpath).resolve()
        if (
            subpath.is_absolute()
            or ".." in subpath.parts
            or len(subpath.parts) != 2
            or subpath.parts[0] != "runs"
            or not wave.is_relative_to(root / "runs")
            or wave in seen_roots
        ):
            raise ValueError("Crucible report_final5 contains an unsafe or duplicate wave root")
        seen_roots.add(wave)
        manifest_path = wave / _WAVE_MANIFEST
        rows = _read_json(manifest_path, "wave manifest")
        census_sources.append(manifest_path)
        if not isinstance(rows, list) or not rows:
            raise ValueError("Crucible wave manifest is empty or invalid")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Crucible wave manifest contains an invalid row")
            ref = row.get("trial_dir")
            family, condition = row.get("family"), row.get("condition")
            if (
                not _present(ref)
                or not isinstance(family, str)
                or family not in _FAMILIES
                or not isinstance(condition, str)
                or not condition.isascii()
                or not condition.replace("_", "").isalnum()
                or len(condition) > 40
            ):
                raise ValueError("Crucible wave manifest contains an invalid run reference")
            # The saved reference can be relative to the source checkout or from
            # an earlier location. Only its native trial directory component is
            # used, and the result must repeat that name.
            basename = Path(ref).name
            directory = wave / basename
            if basename in {"", ".", ".."} or directory.resolve().parent != wave:
                raise ValueError("Crucible wave manifest contains an unsafe trial directory")
            record, timeout = _run_record(directory, row, report_path, manifest_path)
            census_sources.append(directory / "result.json")
            if record.key in seen_keys:
                raise ValueError("Crucible wave manifests contain a duplicate native trial UUID")
            seen_keys.add(record.key)
            if timeout:
                n_excluded += 1
            else:
                included_by_arm[family, condition] += 1
            if timeout == excluded:
                selected.append(record)
    if (
        len(seen_keys) - n_excluded != report.get("n_trials")
        or n_excluded != report.get("n_excluded_timeouts")
    ):
        raise ValueError("Crucible wave census does not match the report_final5 trial counts")
    conditions = report.get("conditions")
    if not isinstance(conditions, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("family"), str)
        or not isinstance(row.get("condition"), str)
        or type(row.get("n_trials")) is not int
        for row in conditions
    ):
        raise ValueError("Crucible report_final5 condition counts are invalid")
    expected = {(row["family"], row["condition"]): row["n_trials"] for row in conditions}
    if len(expected) != len(conditions) or expected != included_by_arm:
        raise ValueError("Crucible wave census does not match the report_final5 condition counts")
    # The first trial in either selection carries the full census provenance
    # so the replay can fingerprint every input used in cohort selection.
    if selected:
        selected[0] = replace(selected[0], _sources=tuple(census_sources))
    yield from selected


@dataclass(frozen=True)
class _Message:
    sender: str
    channel: str
    round_number: int | None
    target: str | None


def _round_number(channel: str, payload: dict[str, Any]) -> int | None:
    number = payload.get("round")
    return number if channel == "round_reports" and type(number) is int and 1 <= number <= 6 else None


def _collected(directory: Path) -> tuple[bool, tuple[Path, ...]]:
    path = directory / _COLLECTION
    if not path.is_file():
        return False, ()
    rows = _read_json(path, "artifact collection manifest")
    if not isinstance(rows, list):
        raise ValueError("Crucible artifact collection manifest is invalid")
    matches = [row for row in rows if isinstance(row, dict) and row.get("destination") == _WORLD_LOG.as_posix()]
    return len(matches) == 1 and matches[0].get("status") == "ok", (path,)


def _project(record: RunRecord) -> Trial:
    collected, collection_sources = _collected(record.directory)
    sources = record._sources + collection_sources
    roster = set(record.seats)
    dirty: set[str] = set()
    events: list[Event] = []
    messages: dict[int, _Message] = {}
    channels = _CHANNELS[record.family]
    seen_seq = 0
    gapped = False
    log = record.directory / _WORLD_LOG
    if log.is_file():
        sources += (log,)
        with log.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    # The writer flushes one newline-terminated record at a time.
                    gapped = True
                    break
                try:
                    event = json.loads(line)
                except (UnicodeError, ValueError):
                    gapped = True
                    continue
                seq = event.get("seq") if isinstance(event, dict) else None
                if type(seq) is not int or seq < 1:
                    gapped = True
                    continue
                if seq <= seen_seq:
                    raise ValueError("Crucible world audit sequence is ambiguous")
                gapped |= seq != seen_seq + 1
                seen_seq = seq
                if event.get("task_family") != record.family or event.get("condition") != record.condition:
                    raise ValueError("Crucible world audit does not match its wave condition")
                actor, kind, payload = event.get("actor"), event.get("event_type"), event.get("payload")
                authenticated = event.get("authenticated")
                if (
                    not isinstance(actor, str)
                    or not isinstance(kind, str)
                    or not isinstance(payload, dict)
                    or type(authenticated) is not bool
                ):
                    gapped = True
                    continue
                seat_event = actor in roster and authenticated
                if kind in channels:
                    channel = channels[kind]
                    round_number = _round_number(channel, payload)
                    target = payload.get("to") if channel == "dm" else None
                    if channel == "dm" and (not isinstance(target, str) or target not in roster):
                        gapped = True
                        continue
                    if channel == "round_reports" and round_number is None:
                        gapped = True
                        continue
                    if seat_event:
                        messages[seq] = _Message(actor, channel, round_number, target)
                        events.append(Event("send", actor, ((actor, str(seq)),)))
                    elif (
                        record.family == "supply-chain-sabotage"
                        and actor == "world"
                        and channel == "dm"
                        and not authenticated
                        and event.get("visibility") == "world_private"
                    ):
                        # Preserve the scripted world reply as non-peer context.
                        messages[seq] = _Message(actor, channel, round_number, target)
                elif kind == "message_read" and seat_event:
                    channel = payload.get("channel")
                    refs = payload.get("msg_seqs")
                    if not isinstance(channel, str) or channel not in channels.values() or not isinstance(refs, list):
                        dirty.add(actor)
                        continue
                    round_number = _round_number(channel, payload)
                    if channel == "round_reports" and round_number is None:
                        dirty.add(actor)
                        continue
                    fetched = []
                    for ref in refs:
                        # These are world audit sequence IDs, not the reward
                        # endpoint's chat-only cursor (payload.message_seq).
                        message = messages.get(ref) if type(ref) is int else None
                        if (
                            message is None
                            or message.channel != channel
                            or message.round_number != round_number
                            or (channel == "dm" and actor not in (message.sender, message.target))
                        ):
                            dirty.add(actor)
                            continue
                        fetched.append((message.sender, str(ref)))
                    # Keep operations and their exact multiset. An empty or
                    # self-only read is not a demonstrated incoming peer receipt.
                    events.append(Event("fetch", actor, tuple(fetched)))
                elif seat_event and (
                    kind in _ACTIONS[record.family]
                    or (
                        record.family == "reward-hack-contagion"
                        and kind == "workspace_commit"
                        and payload.get("message") in ("workspace write", "workspace delete")
                    )
                ):
                    events.append(Event("action", actor))
    coverage: dict[str, Literal["complete", "partial", "unavailable"]] = {}
    for seat in record.seats:
        if not seen_seq:
            coverage[seat] = "unavailable"
        elif not collected or gapped or seat in dirty or seat not in record._closed_seats:
            coverage[seat] = "partial"
        else:
            coverage[seat] = "complete"
    return Trial(
        key=record.key,
        arm=f"{_FAMILIES[record.family]}/{record.condition}",
        seats=record.seats,
        events=tuple(events),
        coverage=coverage,
        finished=record.finished,
        sources=sources,
    )


def load(root: Path) -> Iterator[Trial]:
    """Load reported trials, emitting message sends and server fetches only.

    A pull-channel send does not create a recipient view. A server fetch records
    what the response selected, not a committed next model input or message use.
    """
    for record in run_records(root):
        yield _project(record)


def load_excluded(root: Path) -> Iterator[Trial]:
    """Load the documented timeout exclusions with incomplete receipt windows."""
    for record in run_records(root, excluded=True):
        yield _project(record)
