import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zipfile import BadZipFile

from ..handoff.external_audit import Identity, _receipts
from ..peer_update.evidence import _log_stream
from ..turn_fork.packet import _digest, _object
from ..visibility.clarify_run import _directory, _private
from ..visibility.omp_seat import _validate_model
from . import protocol

if TYPE_CHECKING:
    from inspect_ai.log import EvalSample

_REPORT = "seatlab.rolepack-report/v1"
_SERVICE = "seatlab.rolepack-service/v1"
_ROLE = "dispatch"
_SLOT = (
    "index",
    "sample_id",
    "record_id",
    "task",
    "world",
    "draw",
    "arm",
    "prompt_sha256",
)
_STATES = ("unavailable", "unclaimed", "incomplete", "complete")
_MAX_ROWS = 4 * protocol._MAX_RECORDS
_MAX_LABEL = protocol._MAX_NAME_BYTES + len(":d0:original")
type _Doc = dict[str, Any]


def _result(record: _Doc, row: _Doc, sample: _Doc) -> None:
    row["state"] = "incomplete"
    reply = sample["output"]
    try:
        raw = protocol._utf8(reply, protocol._MAX_TURN_BYTES, "sample output")
    except ValueError:
        return
    row["turn"] = {
        "prompt_sha256": row["prompt_sha256"],
        "response_sha256": sha256(raw).hexdigest(),
    }
    if sample["error"] is not None:
        return
    try:
        grade = protocol.grade(record, reply)
    except (ValueError, RecursionError):
        return
    values, correct = _grade_parts(grade)
    row.update(state="complete", outcome={"values": values, "correct": correct})


def _assemble(pack: _Doc, batches: Iterable[list[_Doc]]) -> _Doc:
    planned = protocol.plan(pack)
    if len(planned) > _MAX_ROWS:
        raise ValueError("too many rolepack slots")
    rows = {
        row["sample_id"]: {
            **row,
            "state": "unavailable" if row["prompt_sha256"] is None else "unclaimed",
            "turn": None,
            "outcome": None,
        }
        for row in planned
    }
    records = {record["id"]: record for record in pack["records"]}
    for samples in batches:
        if not isinstance(samples, list) or len(samples) > _MAX_ROWS:
            raise ValueError("too many rolepack samples")
        for raw in samples:
            sample = _object(raw, "id", "input", "output", "error")
            if not _label(sample["id"]):
                raise ValueError("unknown rolepack sample")
            row = rows.get(sample["id"])
            if row is None or row["state"] == "unavailable":
                raise ValueError("unknown or unavailable rolepack sample")
            if row["state"] != "unclaimed":
                raise ValueError("duplicate rolepack sample")
            raw_input = protocol._utf8(
                sample["input"], protocol._MAX_TURN_BYTES, "sample input"
            )
            if sha256(raw_input).hexdigest() != row["prompt_sha256"]:
                raise ValueError("rolepack sample prompt mismatch")
            _result(records[row["record_id"]], row, sample)
    return {
        "schema": _REPORT,
        "pack_sha256": protocol.digest(pack),
        "rows": list(rows.values()),
    }


def from_samples(pack: _Doc, samples: list[_Doc]) -> _Doc:
    return _assemble(pack, (samples,))


def _normalized(sample: "EvalSample") -> _Doc:
    if (
        sample.epoch != 1
        or sample.error_retries
        or sample.invalidation is not None
        or (sample.turn_count is not None and sample.turn_count not in (0, 1))
    ):
        raise ValueError("not a first-attempt Inspect sample")
    calls = 0
    failed = sample.error is not None or sample.limit is not None
    for event in sample.events:
        if event.event != "model":
            continue
        calls += 1
        if calls != 1 or event.retries or event.cache == "read":
            raise ValueError("not an independent first model call")
        if (
            len(event.input) != 1
            or event.input[0].role != "user"
            or not isinstance(sample.input, str)
            or event.input[0].content != sample.input
            or event.tools
        ):
            raise ValueError(
                "the model did not receive the registered role input alone"
            )
        if (
            event.output is not None
            and event.output.completion != sample.output.completion
        ):
            raise ValueError("reported completion is not the first model reply")
        failed = failed or event.error is not None
    output = sample.output
    if len(output.choices) > 1:
        raise ValueError("Inspect sample has multiple reply choices")
    if output.choices and (calls != 1 or output.choices[0].message.tool_calls):
        raise ValueError("a scored answer requires exactly one tool-free model event")
    return {
        "id": sample.id,
        "input": sample.input,
        "output": output.completion if output.choices else output.completion or None,
        "error": True if failed or output.error is not None else None,
    }


def _load_log(path: Path) -> list[_Doc]:
    from inspect_ai.log import read_eval_log

    try:
        if not isinstance(path, Path) or ".." in path.parts:
            raise ValueError("invalid Inspect log path")
        with _log_stream(path) as stream:
            log = read_eval_log(stream, resolve_attachments=True)
        samples = log.samples or []
        if log.invalidated or len(samples) > _MAX_ROWS:
            raise ValueError("invalidated or oversized Inspect log")
        return [_normalized(sample) for sample in samples]
    except (BadZipFile, OSError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid first-attempt rolepack Inspect log") from None


def collect(pack: _Doc, logs: list[Path]) -> _Doc:
    if not isinstance(logs, list) or len(logs) > _MAX_ROWS:
        raise ValueError("too many rolepack Inspect logs")
    return _assemble(pack, (_load_log(path) for path in logs))


def collect_claimed(pack: _Doc, logs: list[Path], claims: set[str]) -> _Doc:
    """Do not present a claimed sample with no final archive as available to run."""
    result = collect(pack, logs)
    remaining = claims.copy()
    for row in result["rows"]:
        if row["sample_id"] not in claims:
            if row["state"] not in ("unavailable", "unclaimed"):
                raise ValueError("unclaimed sample in a registered archive")
            continue
        remaining.remove(row["sample_id"])
        if row["state"] == "unavailable":
            raise ValueError("unavailable sample claimed")
        if row["state"] == "unclaimed":
            row["state"] = "incomplete"
    if remaining:
        raise ValueError("unregistered sample claimed")
    return result


def _label(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MAX_LABEL


def _grade_parts(raw: object) -> tuple[list[str | int], list[bool]]:
    grade = _object(raw, "values", "correct")
    values, correct = grade["values"], grade["correct"]
    if (
        not isinstance(values, list)
        or not isinstance(correct, list)
        or not 0 < len(values) == len(correct) <= protocol._MAX_ITEMS
        or any(type(flag) is not bool for flag in correct)
    ):
        raise ValueError("invalid rolepack grade fields")
    for value in values:
        protocol._primitive(value)
    return values, correct


def _report_rows(report: _Doc) -> tuple[str, list[_Doc]]:
    report = _object(report, "schema", "pack_sha256", "rows")
    rows = report["rows"]
    if (
        report["schema"] != _REPORT
        or not isinstance(rows, list)
        or len(rows) > _MAX_ROWS
    ):
        raise ValueError("invalid rolepack report")
    return _digest(report["pack_sha256"]), rows


def _slot(raw: object, index: int) -> _Doc:
    row = _object(raw, *_SLOT, "state", "turn", "outcome")
    if (
        type(row["index"]) is not int
        or row["index"] != index
        or type(row["draw"]) is not int
        or row["draw"] not in (0, 1)
        or row["arm"] not in ("original", "peer")
        or any(
            not _label(row[key]) for key in ("sample_id", "record_id", "task", "world")
        )
        or row["sample_id"] != f"{row['record_id']}:d{row['draw']}:{row['arm']}"
    ):
        raise ValueError("invalid rolepack report slot")
    return row


def _turn(row: _Doc) -> tuple[str, str] | None:
    state, prompt, turn = row["state"], row["prompt_sha256"], row["turn"]
    if (
        state not in _STATES
        or (prompt is None) != (state == "unavailable")
        or (row["outcome"] is not None) != (state == "complete")
        or state == "complete"
        and turn is None
        or state in ("unclaimed", "unavailable")
        and turn is not None
    ):
        raise ValueError("invalid rolepack slot state")
    if prompt is not None:
        _digest(prompt)
    if turn is None:
        return None
    turn = _object(turn, "prompt_sha256", "response_sha256")
    if _digest(turn["prompt_sha256"]) != prompt:
        raise ValueError("rolepack turn prompt mismatch")
    return prompt, _digest(turn["response_sha256"])


def _verify_grade(record: _Doc, grade: object) -> None:
    values, correct = _grade_parts(grade)
    rubric = record["rubric"]
    if len(values) != len(rubric["items"]):
        raise ValueError("rolepack grade item count mismatch")
    if rubric["scalar"]:
        answer = values[0]
    else:
        answer = {
            item["key"]: value
            for item, value in zip(rubric["items"], values, strict=True)
        }
    reply = json.dumps(
        {rubric["field"]: answer}, ensure_ascii=False, separators=(",", ":")
    )
    if protocol.grade(record, reply) != {"values": values, "correct": correct}:
        raise ValueError("rolepack grade mismatch")


def verify(pack: _Doc, report: _Doc) -> _Doc:
    planned = protocol.plan(pack)
    digest, rows = _report_rows(report)
    if digest != protocol.digest(pack) or len(rows) != len(planned):
        raise ValueError("rolepack report does not match the pack")
    records = {record["id"]: record for record in pack["records"]}
    for index, (raw, slot) in enumerate(zip(rows, planned, strict=True)):
        row = _slot(raw, index)
        if any(row[key] != slot[key] for key in _SLOT):
            raise ValueError("unregistered rolepack report slot")
        _turn(row)
        if row["state"] == "complete":
            _verify_grade(records[row["record_id"]], row["outcome"])
    return report


def _expected(rows: list[_Doc]) -> tuple[Counter[Identity], int]:
    expected: Counter[Identity] = Counter()
    seen: set[str] = set()
    missing = 0
    for index, raw in enumerate(rows):
        row = _slot(raw, index)
        if row["sample_id"] in seen:
            raise ValueError("duplicate rolepack report slot")
        seen.add(row["sample_id"])
        turn = _turn(row)
        if row["state"] == "complete":
            _grade_parts(row["outcome"])
        if turn is None:
            missing += row["state"] == "incomplete"
        else:
            expected[_ROLE, *turn] += 1
    return expected, missing


def service(report: _Doc, receipts: Path, model: str) -> _Doc:
    try:
        digest, rows = _report_rows(report)
        if not isinstance(receipts, Path) or not receipts.is_absolute():
            raise ValueError("receipt path must be absolute")
        _validate_model(model)
        expected, missing = _expected(rows)
        with _directory(receipts) as fd:
            _private(fd, mode=0o700)
            dispatch = _receipts(fd, _ROLE, model, expected, missing)
        dispatch["incomplete_without_reply"] = dispatch.pop("callback_errors")
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid_rolepack_service_evidence") from None
    return {
        "schema": _SERVICE,
        "pack_sha256": digest,
        "requested_model": model,
        "verified_served_model": model if dispatch["matched"] else None,
        "roles": {_ROLE: dispatch},
    }
