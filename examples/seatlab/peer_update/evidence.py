import os
import stat
from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from zipfile import BadZipFile, ZipFile

from ..handoff.external_audit import Identity, _receipts
from ..turn_fork.packet import _digest, _object
from ..visibility.clarify_run import _directory, _private
from ..visibility.omp_seat import _MAX_TURN_BYTES, _validate_model
from ..visibility.route_run import Answer, _validate_answer
from . import protocol

_PANEL = "seatlab.peer-update-panel/v1"
_REPORT = "seatlab.peer-update-report/v1"
_SERVICE = "seatlab.peer-update-service/v1"
_ROLE = "dispatch"
_ROW = ("index", "source_id", "obs_id", "sample_id", "target", "arm", "prompt_sha256")
_MAX_ROWS = 48
_MAX_LOG_BYTES = 16 * 1024 * 1024
_MAX_LOG_MEMBERS = 4096
type _Document = dict[str, Any]


def _rows(value: object) -> list[_Document]:
    if not isinstance(value, list) or len(value) != _MAX_ROWS:
        raise ValueError("expected the 48 registered peer-update rows")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError("expected registered peer-update objects")
    return cast(list[_Document], value)


def _index(panel: _Document) -> dict[str, _Document]:
    if panel.get("schema") != _PANEL:
        raise ValueError("invalid peer-update panel")
    rows = {}
    for index, raw in enumerate(_rows(panel.get("rows"))):
        row = _object(raw, *_ROW)
        sample_id = row["sample_id"]
        if (
            type(row["index"]) is not int
            or row["index"] != index
            or not isinstance(sample_id, str)
            or not 0 < len(sample_id) <= 128
            or sample_id in rows
        ):
            raise ValueError("invalid peer-update row registration")
        if row["prompt_sha256"] is not None:
            _digest(row["prompt_sha256"])
        rows[sample_id] = row
    return rows


def _input_digest(text: object) -> str:
    if not isinstance(text, str) or not 0 < len(text) <= _MAX_TURN_BYTES:
        raise ValueError("invalid peer-update sample input")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid peer-update sample input") from None
    if len(raw) > _MAX_TURN_BYTES:
        raise ValueError("invalid peer-update sample input")
    return sha256(raw).hexdigest()


def _join(rows: dict[str, _Document], samples: list[_Document]) -> dict[str, _Document]:
    if not isinstance(samples, list) or len(samples) > _MAX_ROWS:
        raise ValueError("too many peer-update samples")
    joined: dict[str, _Document] = {}
    for raw in samples:
        sample = _object(raw, "id", "input", "output", "error")
        sample_id = sample["id"]
        if not isinstance(sample_id, str) or not 0 < len(sample_id) <= 128:
            raise ValueError("unknown peer-update sample")
        row = rows.get(sample_id)
        if row is None or row["prompt_sha256"] is None:
            raise ValueError("unknown or unavailable peer-update sample")
        if sample_id in joined:
            raise ValueError("duplicate peer-update sample")
        if _input_digest(sample["input"]) != row["prompt_sha256"]:
            raise ValueError("peer-update sample prompt mismatch")
        joined[sample_id] = sample
    return joined


def _result(
    packet: _Document, board: _Document, row: _Document, sample: _Document | None
) -> _Document:
    state = "unavailable" if row["prompt_sha256"] is None else "unclaimed"
    turn = outcome = None
    if sample is not None:
        state = "incomplete"
        try:
            raw, _ = _validate_answer(Answer(sample["output"]))
        except ValueError:
            return {**row, "state": state, "turn": turn, "outcome": outcome}
        turn = {
            "prompt_sha256": row["prompt_sha256"],
            "response_sha256": sha256(raw).hexdigest(),
        }
        if sample["error"] is None:
            try:
                outcome = protocol.grade(packet, board, row, sample["output"])
            except ValueError:
                pass
            else:
                state = "complete"
    return {**row, "state": state, "turn": turn, "outcome": outcome}


def from_samples(
    packet: _Document, board: _Document, panel: _Document, samples: list[_Document]
) -> _Document:
    rows = _index(panel)
    joined = _join(rows, samples)
    return {
        "schema": _REPORT,
        "panel_sha256": protocol.panel_digest(panel),
        "rows": [
            _result(packet, board, row, joined.get(sample_id))
            for sample_id, row in rows.items()
        ],
    }


def _log_stream(path: Path) -> BytesIO:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_LOG_BYTES:
            raise ValueError("invalid Inspect log size or file type")
        raw = source.read(_MAX_LOG_BYTES + 1)
    if not 0 < len(raw) <= _MAX_LOG_BYTES:
        raise ValueError("invalid Inspect log size")
    stream = BytesIO(raw)
    if raw.startswith(b"PK\x03\x04"):
        with ZipFile(stream) as archive:
            members = archive.infolist()
            if (
                len(members) > _MAX_LOG_MEMBERS
                or sum(member.file_size for member in members) > _MAX_LOG_BYTES
            ):
                raise ValueError("oversized Inspect archive")
        stream.seek(0)
    return stream


def collect(
    packet: _Document, board: _Document, panel: _Document, logs: list[Path]
) -> _Document:
    samples: list[_Document] = []
    if not isinstance(logs, list) or len(logs) > _MAX_ROWS:
        raise ValueError("too many peer-update Inspect logs")
    if not logs:
        return from_samples(packet, board, panel, samples)
    from inspect_ai.log import read_eval_log

    for path in logs:
        try:
            with _log_stream(path) as stream:
                log = read_eval_log(stream)
            logged = log.samples or []
            if log.invalidated or len(samples) + len(logged) > _MAX_ROWS:
                raise ValueError
            for sample in logged:
                if sample.epoch != 1 or sample.error_retries or sample.invalidation:
                    raise ValueError
                output = sample.output
                failed = (
                    sample.error is not None
                    or sample.limit is not None
                    or output.error is not None
                )
                samples.append(
                    {
                        "id": sample.id,
                        "input": sample.input,
                        "output": output.completion
                        if output.choices
                        else output.completion or None,
                        "error": True if failed else None,
                    }
                )
        except (BadZipFile, OSError, KeyError, TypeError, ValueError, RecursionError):
            raise ValueError("invalid peer-update Inspect log") from None
    return from_samples(packet, board, panel, samples)


def _expected(rows: list[_Document]) -> tuple[Counter[Identity], int]:
    expected: Counter[Identity] = Counter()
    missing = 0
    for raw in rows:
        row = _object(raw, *_ROW, "state", "turn", "outcome")
        state, turn = row["state"], row["turn"]
        if state not in ("unavailable", "unclaimed", "incomplete", "complete"):
            raise ValueError
        if (row["prompt_sha256"] is None) != (state == "unavailable"):
            raise ValueError
        if turn is None:
            if state == "complete":
                raise ValueError
            missing += state == "incomplete"
            continue
        turn = _object(turn, "prompt_sha256", "response_sha256")
        prompt = _digest(turn["prompt_sha256"])
        if state not in ("complete", "incomplete") or prompt != row["prompt_sha256"]:
            raise ValueError
        expected[_ROLE, prompt, _digest(turn["response_sha256"])] += 1
    return expected, missing


def service(report: _Document, receipts: Path, model: str) -> _Document:
    try:
        report = _object(report, "schema", "panel_sha256", "rows")
        if report["schema"] != _REPORT or not receipts.is_absolute():
            raise ValueError
        panel_digest = _digest(report["panel_sha256"])
        _validate_model(model)
        expected, missing = _expected(_rows(report["rows"]))
        with _directory(receipts) as fd:
            _private(fd, mode=0o700)
            dispatch = _receipts(fd, _ROLE, model, expected, missing)
        dispatch["incomplete_without_reply"] = dispatch.pop("callback_errors")
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid_peer_update_service_evidence") from None
    return {
        "schema": _SERVICE,
        "panel_sha256": panel_digest,
        "requested_model": model,
        "verified_served_model": model if dispatch["matched"] else None,
        "roles": {_ROLE: dispatch},
    }
