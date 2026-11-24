import hashlib
import json
import os
import stat
from datetime import datetime
from decimal import Decimal
from typing import Any

from .account_contract import MAX_SEAT_ROWS, attempt, money, packet, seat
from .job_stream import MAX_TRIALS, RESULT, _children, _present
from .seat_stream import (
    MAX_RESULT_BYTES,
    MAX_SEATS,
    MIB,
    SEAT,
    UUID,
    Malformed,
    Record,
    _constant,
    _read,
    _require,
    _unique,
    result_roster,
)

SOURCE = "native-job"
MAX_TIMESTAMP = 64
MAX_TOTAL_BYTES = 256 * MIB
MAX_RETRIES = 16_384
MAX_REWARD_KEYS = 256
MAX_SLOT_NAME = 255
TOKEN_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")


def _strict_json(data: bytes, reason: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique,
            parse_constant=_constant,
            parse_float=Decimal,
        )
    except (ValueError, RecursionError) as error:
        raise Malformed(reason) from error


def _object(path: str, label: str, remaining: int) -> tuple[Record, int]:
    try:
        size = os.lstat(path).st_size
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is missing or unreadable") from error
    _require(size <= remaining, "job results exceed 256 MiB in total")
    data = _read(path, min(MAX_RESULT_BYTES, remaining), label)
    document = _strict_json(data, f"{label} is not strict UTF-8 JSON")
    _require(type(document) is dict, f"{label} is not a JSON object")
    return document, remaining - len(data)


def _version_one(document: Record, label: str) -> None:
    version = document.get("schema_version", 1)
    _require(type(version) is int and version == 1, f"{label} schema_version is not 1")


def _agreed(values: list[object], limit: int, reason: str) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    first = present[0]
    if not isinstance(first, int) or isinstance(first, bool) or not 0 <= first <= limit:
        raise Malformed(reason)
    if any(type(value) is not int or value != first for value in present):
        raise Malformed(reason)
    return first


def _progress(document: Record) -> tuple[Record, Record]:
    stats, status = document.get("stats"), document.get("status")
    if stats is not None and not isinstance(stats, dict):
        raise Malformed("job result stats is not an object")
    if status is not None and not isinstance(status, dict):
        raise Malformed("job result status is not an object")
    return stats or {}, status or {}


def _finished(document: Record) -> bool | None:
    if "finished_at" not in document:
        return None
    finished = document["finished_at"]
    if finished is None:
        return False
    if not isinstance(finished, str) or not 0 < len(finished) <= MAX_TIMESTAMP:
        raise Malformed("job result finished_at is not an ISO timestamp")
    try:
        datetime.fromisoformat(finished)
    except ValueError as error:
        raise Malformed("job result finished_at is not an ISO timestamp") from error
    return True


def _closed(document: Record, registered: int) -> bool | None:
    stats, status = _progress(document)
    completed = _agreed(
        [
            stats.get("n_completed_trials"),
            stats.get("n_trials"),
            status.get("n_completed_trials"),
        ],
        registered,
        "job result completed count is not one integer up to n_total_trials",
    )
    finished = _finished(document)
    if finished is False or (completed is not None and completed < registered):
        return False
    return True if finished and completed == registered else None


def _retries(document: Record) -> int | None:
    stats, status = _progress(document)
    return _agreed(
        [stats.get("n_retries"), status.get("n_retries")],
        MAX_RETRIES,
        "job result n_retries is not one integer from 0 to 16384",
    )


def _root(job: str) -> tuple[str, int, int | None, bool | None, int]:
    try:
        mode = os.lstat(job).st_mode
    except (OSError, ValueError) as error:
        raise Malformed("job directory is missing or unreadable") from error
    _require(stat.S_ISDIR(mode), "job is not a real non-symlink directory")
    document, remaining = _object(
        os.path.join(job, RESULT), "job result", MAX_TOTAL_BYTES
    )
    _version_one(document, "job result")
    job_id = document.get("id")
    if not isinstance(job_id, str) or UUID.fullmatch(job_id) is None:
        raise Malformed("job result id is not a canonical lowercase UUID")
    total = document.get("n_total_trials")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or not 0 <= total <= MAX_TRIALS
    ):
        raise Malformed("job result n_total_trials is not an integer from 0 to 4096")
    closed = _closed(document, total)
    return job_id, total, _retries(document), closed, remaining


def _participants(config: Record) -> list[str] | None:
    declared = config.get("participants")
    if declared is None:
        return None
    _require(
        type(declared) is list and len(declared) <= MAX_SEATS,
        "trial config participants is not a list of at most 256",
    )
    names: list[str] = []
    for participant in declared:
        _require(type(participant) is dict, "trial config participant is not an object")
        name = participant.get("seat")
        _require(
            type(name) is str and SEAT.fullmatch(name) is not None,
            "trial config participant seat is not a safe name",
        )
        names.append(name)
    _require(len(set(names)) == len(names), "trial config participant seats repeat")
    return names or None


def _grade(document: Record) -> tuple[str, int | float | None]:
    valid, verifier = document.get("reward_valid"), document.get("verifier_result")
    _require(
        valid is None or type(valid) is bool,
        "trial result reward_valid is not a boolean",
    )
    _require(
        verifier is None or type(verifier) is dict,
        "trial verifier_result is not an object",
    )
    rewards = (verifier or {}).get("rewards")
    _require(
        rewards is None or (type(rewards) is dict and len(rewards) <= MAX_REWARD_KEYS),
        "trial rewards is not an object of at most 256 entries",
    )
    _require(
        all(type(v) in (int, float, Decimal) for v in (rewards or {}).values()),
        "trial reward is not a number",
    )
    _require(
        valid is not True or bool(rewards), "trial result marks absent rewards valid"
    )
    if not rewards:
        return "unknown", None
    if valid is False:
        return "invalid", None
    reward = rewards.get("reward")
    if valid is True and reward is not None:
        return "valid", float(reward) if type(reward) is Decimal else reward
    return "unknown", None


def _count(context: Record, field: str) -> int | None:
    value = context.get(field)
    _require(value is None or type(value) is int, "seat token count is not an integer")
    return value


def _agent(entry: Record) -> tuple[str | None, str | None, str | None]:
    info = entry.get("agent_info")
    if info is None:
        return None, None, None
    _require(type(info) is dict, "seat agent_info is not an object")
    harness = info.get("name")
    _require(type(harness) is str, "seat agent_info name is not a string")
    model = info.get("model_info")
    if model is None:
        return harness, None, None
    _require(type(model) is dict, "seat model_info is not an object")
    name, provider = model.get("name"), model.get("provider")
    _require(type(name) is str, "seat model_info name is not a string")
    _require(
        provider is None or type(provider) is str,
        "seat model_info provider is not a string",
    )
    return harness, provider, name


def _seat_row(entry: Record, under_test: bool, disposition: str | None) -> Record:
    harness, provider, model = _agent(entry)
    context = entry.get("agent_result")
    _require(
        context is None or type(context) is dict, "seat agent_result is not an object"
    )
    tokens = [_count(context or {}, field) for field in TOKEN_FIELDS]
    cost = (context or {}).get("cost_usd")
    return seat(
        entry["seat"],
        under_test=under_test,
        disposition=disposition,
        harness=harness,
        reported_provider=provider,
        reported_model=model,
        input_tokens=tokens[0],
        cache_tokens=tokens[1],
        output_tokens=tokens[2],
        harness_usd=None if cost is None else money(cost),
    )


def _seats(document: Record, roster: list[str] | None) -> tuple[str, list[Record]]:
    if document.get("seat_results") is None:
        if roster is None:
            raise Malformed("trial result is not a multi-seat result")
        trial = document.get("id")
        if not isinstance(trial, str) or UUID.fullmatch(trial) is None:
            raise Malformed("result id is not a canonical lowercase UUID")
        return trial, [seat(name) for name in roster]
    trial, declared = result_roster(document)
    _require(
        roster is None or set(roster) == set(declared),
        "trial seat_results disagree with config participants",
    )
    rows = [
        _seat_row(entry, *declared[entry["seat"]]) for entry in document["seat_results"]
    ]
    return trial, rows


def _slot(slot_name: str) -> str:
    _require(
        type(slot_name) is str
        and 0 < len(slot_name) <= MAX_SLOT_NAME
        and slot_name not in (".", "..")
        and "/" not in slot_name
        and "\0" not in slot_name,
        "slot name is not one directory name",
    )
    return hashlib.sha256(os.fsencode(slot_name)).hexdigest()


def project_result(document: Record, *, job_id: str, slot_name: str) -> Record:
    _require(type(document) is dict, "trial result is not a JSON object")
    _require(
        type(job_id) is str and UUID.fullmatch(job_id) is not None,
        "job id is not a canonical lowercase UUID",
    )
    slot = _slot(slot_name)
    _version_one(document, "trial result")
    config = document.get("config")
    if not isinstance(config, dict):
        raise Malformed("trial result config is not an object")
    _version_one(config, "trial config")
    _require(config.get("job_id") == job_id, "trial result belongs to another job")
    _require(
        document.get("trial_name") == slot_name
        and config.get("trial_name", slot_name) == slot_name,
        "trial result names another directory",
    )
    trial, rows = _seats(document, _participants(config))
    grade_state, reward = _grade(document)
    return attempt(trial, slot, grade_state, reward, rows)


Root = tuple[str, int, int | None, bool | None, int]


def build(job: str) -> Record:
    return collect(job, _root(job))


def collect(job: str, root: Root) -> Record:
    job_id, registered, retries, job_closed, remaining = root
    attempts: list[Record] = []
    trials: set[str] = set()
    rows = 0
    for name in _children(job):
        path = os.path.join(job, name, RESULT)
        if not _present(path, "trial result"):
            continue
        _require(
            len(attempts) < registered,
            "job holds more trial results than n_total_trials",
        )
        document, remaining = _object(path, "trial result", remaining)
        row = project_result(document, job_id=job_id, slot_name=name)
        _require(row["trial"] not in trials, "two directories hold the same trial")
        rows += len(row["seats"])
        _require(rows <= MAX_SEAT_ROWS, "job has too many seat rows")
        trials.add(row["trial"])
        attempts.append(row)
    return packet(SOURCE, job_id, registered, retries, attempts, job_closed=job_closed)
