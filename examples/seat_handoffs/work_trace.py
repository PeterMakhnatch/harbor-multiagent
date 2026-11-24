import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .core import MAX_LOG_BYTES
from .native_records import read_record
from .schedule_exit_clock import (
    PLAY,
    STOCK_PAIR,
    TIMEOUT,
    _micros,
    _ordered,
    _points,
    _Unsafe,
)

REASONS = frozenset(
    {
        "missing_trajectory",
        "invalid_trajectory",
        "unsupported_trajectory",
        "seat_binding_mismatch",
        "target_not_clean",
        "peer_status_unsupported",
        "return_outside_peer",
        "clock_unusable",
        "usage_missing",
        "usage_inconsistent",
    }
)
SCHEMA_VERSION = "ATIF-v1.8"
HARNESS = ("terminus-2", "2.0.0")
TRAJECTORY = "trajectory.json"
TRAJECTORY_PREFIX = "trajectory"
MAX_TRAJECTORY_BYTES = MAX_LOG_BYTES
MAX_STEPS = 4096
MAX_ENTRIES = 256
MAX_TOKENS = 10**12
TRAJECTORY_KEYS = frozenset(
    {"schema_version", "session_id", "agent", "steps", "final_metrics"}
)
AGENT_REQUIRED = frozenset({"name", "version", "model_name"})
AGENT_KEYS = AGENT_REQUIRED | {"extra"}
AGENT_EXTRA_KEYS = frozenset({"parser", "temperature", "llm_kwargs"})
INITIAL_KEYS = frozenset({"step_id", "timestamp", "source", "message"})
STEP_REQUIRED = frozenset({"step_id", "timestamp", "source", "model_name", "metrics"})
STEP_KEYS = STEP_REQUIRED | {
    "message",
    "reasoning_content",
    "tool_calls",
    "observation",
}
METRIC_REQUIRED = frozenset({"prompt_tokens", "completion_tokens"})
METRIC_KEYS = METRIC_REQUIRED | {"cached_tokens", "cost_usd"}
FINAL_REQUIRED = frozenset({"total_prompt_tokens", "total_completion_tokens"})
FINAL_KEYS = FINAL_REQUIRED | {"total_cached_tokens", "total_cost_usd"}

Usage = tuple[int, int]


@dataclass(frozen=True)
class Step:
    end_us: int
    prompt: int
    completion: int


@dataclass(frozen=True)
class Receipt:
    exit_us: int
    peer_start_us: int
    peer_end_us: int
    initial_us: int
    steps: tuple[Step, ...]
    native: Usage


class ReceiptError(ValueError):
    def __init__(self, reason: str) -> None:
        assert reason in REASONS
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReceiptError(reason)


def _record(value: Any, reason: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ReceiptError(reason)
    return value


def _records(
    value: object, low: int, high: int, reason: str
) -> Iterator[dict[str, Any]]:
    assert 0 <= low <= high <= MAX_STEPS + 1
    if type(value) is not list or not low <= len(value) <= high:
        raise ReceiptError(reason)
    return (_record(row, reason) for row in value)


def _text(value: object, reason: str) -> str:
    if type(value) is not str or not value:
        raise ReceiptError(reason)
    return value


def _tokens(value: object, reason: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TOKENS:
        raise ReceiptError(reason)
    return value


def _seat(value: object, taken: dict[str, dict[str, Any]]) -> str:
    if type(value) is not str or value not in STOCK_PAIR or value in taken:
        raise ReceiptError("seat_binding_mismatch")
    return value


def _peer(first: str) -> str:
    _require(first in STOCK_PAIR, "seat_binding_mismatch")
    peer = STOCK_PAIR[1 - STOCK_PAIR.index(first)]
    assert peer != first
    return peer


def _trial_name(trial: Path, result: dict[str, Any]) -> str:
    name = _text(result.get("trial_name"), "seat_binding_mismatch")
    config = _record(result.get("config"), "seat_binding_mismatch")
    _require(trial.name == name, "seat_binding_mismatch")
    _require(config.get("trial_name") == name, "seat_binding_mismatch")
    _require(
        "source_trial" in config and config["source_trial"] is None,
        "unsupported_trajectory",
    )
    return name


def _configured(result: dict[str, Any], peer: str) -> str:
    rows = result["config"].get("participants")
    pair = len(STOCK_PAIR)
    found: dict[str, dict[str, Any]] = {}
    for row in _records(rows, pair, pair, "seat_binding_mismatch"):
        seat = _seat(row.get("seat"), found)
        _require(row.get("name") == HARNESS[0], "unsupported_trajectory")
        _require(
            row.get("resume_trajectory") is False
            and row.get("load_trajectory") is None,
            "unsupported_trajectory",
        )
        found[seat] = row
    return _text(found[peer].get("model_name"), "seat_binding_mismatch")


def _seat_rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = result.get("seat_results")
    pair = len(STOCK_PAIR)
    found: dict[str, dict[str, Any]] = {}
    for row in _records(rows, pair, pair, "seat_binding_mismatch"):
        _require(row.get("under_test") is True, "seat_binding_mismatch")
        found[_seat(row.get("seat"), found)] = row
    assert set(found) == set(STOCK_PAIR)
    return found


def _times(
    record: dict[str, Any], specs: tuple[tuple[str | None, str], ...]
) -> tuple[int, ...]:
    found = _points(record, specs)
    _require(found is not None, "clock_unusable")
    assert found is not None
    times = tuple(point for point in found if point is not None)
    _require(len(times) == len(specs) and _ordered(times), "clock_unusable")
    return times


def _host_times(
    result: dict[str, Any], rows: dict[str, dict[str, Any]], first: str, peer: str
) -> tuple[int, int, int]:
    trial_start, play_start, play_end = _times(result, PLAY)
    spans: dict[str, tuple[int, ...]] = {}
    for seat in STOCK_PAIR:
        span = _times(rows[seat], PLAY[1:])
        _require(_ordered((trial_start, play_start, *span, play_end)), "clock_unusable")
        spans[seat] = span
    target = rows[first]
    _require(
        "exception_info" in target and target["exception_info"] is None,
        "target_not_clean",
    )
    peer_row = rows[peer]
    _require("exception_info" in peer_row, "peer_status_unsupported")
    info = peer_row["exception_info"]
    timed_out = type(info) is dict and info.get("exception_type") == TIMEOUT
    _require(info is None or timed_out, "peer_status_unsupported")
    exit_us = spans[first][1]
    peer_start, peer_end = spans[peer]
    _require(peer_start < exit_us < peer_end, "return_outside_peer")
    return exit_us, peer_start, peer_end


def _actual_model(row: dict[str, Any], configured: str) -> str:
    info = _record(row.get("agent_info"), "seat_binding_mismatch")
    harness = (info.get("name"), info.get("version"))
    _require(harness == HARNESS, "unsupported_trajectory")
    model = _record(info.get("model_info"), "seat_binding_mismatch")
    actual = _text(model.get("name"), "seat_binding_mismatch")
    provider = _text(model.get("provider"), "seat_binding_mismatch")
    _require(configured == f"{provider}/{actual}", "seat_binding_mismatch")
    assert actual != configured
    return actual


def _native_meter(row: dict[str, Any]) -> tuple[Usage, object]:
    meter = _record(row.get("agent_result"), "usage_missing")
    prompt = _tokens(meter.get("n_input_tokens"), "usage_missing")
    completion = _tokens(meter.get("n_output_tokens"), "usage_missing")
    metadata = _record(meter.get("metadata"), "unsupported_trajectory")
    summaries = metadata.get("summarization_count")
    _require(type(summaries) is int and summaries == 0, "unsupported_trajectory")
    return (prompt, completion), meter.get("model_usage")


def _real(path: Path, directory: bool) -> None:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError as error:
        raise ReceiptError("missing_trajectory") from error
    except OSError as error:
        raise ReceiptError("invalid_trajectory") from error
    _require(
        stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode), "invalid_trajectory"
    )


def _sole_session(folder: Path) -> None:
    names: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                _require(len(names) < MAX_ENTRIES, "unsupported_trajectory")
                names.append(entry.name)
    except OSError as error:
        raise ReceiptError("invalid_trajectory") from error
    sessions = [name for name in names if name.startswith(TRAJECTORY_PREFIX)]
    _require(sessions == [TRAJECTORY], "unsupported_trajectory")


def _trajectory(trial: Path, peer: str) -> dict[str, Any]:
    folder = trial
    _real(folder, True)
    for part in ("seats", peer, "agent"):
        folder = folder / part
        _real(folder, True)
    path = folder / TRAJECTORY
    _real(path, False)
    _sole_session(folder)
    try:
        record = read_record(path, MAX_TRAJECTORY_BYTES)
    except (OSError, ValueError) as error:
        raise ReceiptError("invalid_trajectory") from error
    if record is None:
        raise ReceiptError("missing_trajectory")
    return record


def _bind(record: dict[str, Any], name: str, peer: str, configured: str) -> None:
    _require(set(record) == TRAJECTORY_KEYS, "unsupported_trajectory")
    _require(record["schema_version"] == SCHEMA_VERSION, "unsupported_trajectory")
    _require(record["session_id"] == f"{name}__{peer}", "seat_binding_mismatch")
    agent = _record(record["agent"], "invalid_trajectory")
    keys = set(agent)
    _require(AGENT_REQUIRED <= keys <= AGENT_KEYS, "unsupported_trajectory")
    _require((agent["name"], agent["version"]) == HARNESS, "unsupported_trajectory")
    extra = agent.get("extra")
    _require(
        extra is None or (type(extra) is dict and set(extra) <= AGENT_EXTRA_KEYS),
        "unsupported_trajectory",
    )
    _require(agent["model_name"] == configured, "seat_binding_mismatch")


def _moment(value: object) -> int:
    try:
        return _micros(value)
    except _Unsafe as error:
        raise ReceiptError("clock_unusable") from error


def _step(row: dict[str, Any]) -> tuple[Step, str]:
    keys = set(row)
    _require(STEP_REQUIRED <= keys <= STEP_KEYS, "unsupported_trajectory")
    _require(row["source"] == "agent", "unsupported_trajectory")
    model = _text(row["model_name"], "invalid_trajectory")
    metrics = _record(row["metrics"], "usage_missing")
    _require(set(metrics) <= METRIC_KEYS, "unsupported_trajectory")
    prompt, completion = metrics.get("prompt_tokens"), metrics.get("completion_tokens")
    _require(prompt is not None and completion is not None, "usage_missing")
    step = Step(
        end_us=_moment(row["timestamp"]),
        prompt=_tokens(prompt, "invalid_trajectory"),
        completion=_tokens(completion, "invalid_trajectory"),
    )
    return step, model


def _steps(
    rows: object, peer_start: int, peer_end: int
) -> tuple[int, tuple[Step, ...], frozenset[str]]:
    records = list(_records(rows, 1, MAX_STEPS + 1, "invalid_trajectory"))
    for index, row in enumerate(records, start=1):
        step_id = row.get("step_id")
        _require(type(step_id) is int and step_id == index, "invalid_trajectory")
    initial = records[0]
    _require(set(initial) == INITIAL_KEYS, "unsupported_trajectory")
    _require(initial["source"] == "user", "unsupported_trajectory")
    initial_us = _moment(initial["timestamp"])
    parsed = tuple(_step(row) for row in records[1:])
    steps = tuple(step for step, _ in parsed)
    ends = (peer_start, initial_us, *(step.end_us for step in steps), peer_end)
    _require(
        all(earlier <= later for earlier, later in pairwise(ends)), "clock_unusable"
    )
    return initial_us, steps, frozenset(model for _, model in parsed)


def _final(record: dict[str, Any], native: Usage) -> None:
    final = _record(record["final_metrics"], "usage_missing")
    keys = set(final)
    _require(keys <= FINAL_KEYS, "unsupported_trajectory")
    pair = (final.get("total_prompt_tokens"), final.get("total_completion_tokens"))
    _require(None not in pair, "usage_missing")
    totals = tuple(_tokens(value, "invalid_trajectory") for value in pair)
    _require(totals == native, "usage_inconsistent")


def _corroborate(
    usage: object,
    steps: tuple[Step, ...],
    models: frozenset[str],
    native: Usage,
    names: tuple[str, str],
) -> None:
    prompt = sum(step.prompt for step in steps)
    completion = sum(step.completion for step in steps)
    _require(prompt <= native[0] and completion <= native[1], "usage_inconsistent")
    if not steps:
        _require(usage is None, "usage_inconsistent")
        return
    _require(len(models) == 1, "unsupported_trajectory")
    (model,) = models
    _require(model in names, "seat_binding_mismatch")
    meters = _record(usage, "usage_missing")
    _require(set(meters) == {model}, "usage_inconsistent")
    entry = _record(meters[model], "usage_inconsistent")
    meter = (
        _tokens(entry.get("n_input_tokens"), "usage_inconsistent"),
        _tokens(entry.get("n_output_tokens"), "usage_inconsistent"),
    )
    _require(meter == (prompt, completion), "usage_inconsistent")


def read_receipt(trial: Path, result: dict[str, Any], first: str) -> Receipt:
    assert isinstance(trial, Path) and type(result) is dict and type(first) is str
    peer = _peer(first)
    name = _trial_name(trial, result)
    configured = _configured(result, peer)
    rows = _seat_rows(result)
    exit_us, peer_start, peer_end = _host_times(result, rows, first, peer)
    actual = _actual_model(rows[peer], configured)
    native, usage = _native_meter(rows[peer])
    record = _trajectory(trial, peer)
    _bind(record, name, peer, configured)
    initial_us, steps, models = _steps(record["steps"], peer_start, peer_end)
    _final(record, native)
    _corroborate(usage, steps, models, native, (actual, configured))
    return Receipt(
        exit_us=exit_us,
        peer_start_us=peer_start,
        peer_end_us=peer_end,
        initial_us=initial_us,
        steps=steps,
        native=native,
    )
