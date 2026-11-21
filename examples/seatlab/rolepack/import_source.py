import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from ..peer_update.evidence import _log_stream
from . import evidence, protocol

if TYPE_CHECKING:
    from inspect_ai.log import EvalLog, EvalSample

_INDEX_SCHEMA = "seatlab.role-index/v1"


type _SampleID = str | int


def _sample_id(value: object) -> _SampleID:
    if type(value) is int and -(1 << 63) <= value < 1 << 63:
        return value
    return protocol._name(value, "sample ID", limit=512)


def _index(index: protocol.Document) -> dict[_SampleID, protocol.Document]:
    document = protocol._object(index, "role index", "schema", "records")
    if document["schema"] != _INDEX_SCHEMA:
        raise ValueError("unsupported role index schema")
    entries = protocol._list(
        document["records"], 1, protocol._MAX_RECORDS, "index records"
    )
    selected: dict[_SampleID, protocol.Document] = {}
    ids: set[str] = set()
    for raw in entries:
        entry = protocol._object(
            raw, "index record", "id", "task", "world", "sample_id", "rubric"
        )
        record_id = protocol._name(entry["id"], "record id")
        sample_id = _sample_id(entry["sample_id"])
        protocol._name(entry["task"], "record task")
        protocol._name(entry["world"], "record world")
        protocol._validate_rubric(entry["rubric"])
        if record_id in ids:
            raise ValueError("duplicate record ID in role index")
        if sample_id in selected:
            raise ValueError("duplicate sample ID in role index")
        ids.add(record_id)
        selected[sample_id] = entry
    return selected


def _has_relogged_sample(
    stream: BytesIO, selected: dict[_SampleID, protocol.Document]
) -> bool:
    with ZipFile(stream) as archive:
        seen: set[str] = set()
        repeated: set[str] = set()
        for name in archive.namelist():
            if name.startswith("samples/") and name.endswith(".json"):
                if name in seen:
                    repeated.add(name)
                seen.add(name)
        for member in archive.infolist():
            if member.filename not in repeated:
                continue
            with archive.open(member) as source:
                raw = json.load(source)
            sample_id = raw.get("id") if isinstance(raw, dict) else None
            if type(sample_id) in (str, int) and sample_id in selected:
                return True
    return False


def _read_archive(
    path: Path, selected: dict[_SampleID, protocol.Document]
) -> tuple["EvalLog", str]:
    from inspect_ai.log import read_eval_log

    try:
        if not isinstance(path, Path) or path.suffix != ".eval":
            raise ValueError("expected a native Inspect archive")
        with _log_stream(path) as stream:
            if stream.read(4) != b"PK\x03\x04":
                raise ValueError("expected a native Inspect archive")
            stream.seek(0)
            with stream.getbuffer() as contents:
                commitment = sha256(contents).hexdigest()
            relogged = _has_relogged_sample(stream, selected)
            stream.seek(0)
            log = None if relogged else read_eval_log(stream, resolve_attachments=False)
        if log is not None and len(log.samples or []) > evidence._MAX_ROWS:
            raise ValueError("too many Inspect samples")
    except (BadZipFile, OSError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid rolepack Inspect archive") from None
    if log is None:
        raise ValueError("duplicate indexed Inspect sample")
    return log, commitment


def _observed_reply(sample: "EvalSample", reply: str) -> bool:
    if sample.output.empty or sample.output.stop_reason not in ("stop", "unknown"):
        return False
    for event in sample.events:
        if event.event == "model":
            output = event.output
            return (
                output is not None
                and output.error is None
                and len(output.choices) == 1
                and output.stop_reason in ("stop", "unknown")
                and not output.message.tool_calls
                and output.message.text == reply
            )
    return False


def _peer(
    sample: "EvalSample",
    normalized: protocol.Document,
    rubric: protocol.Document,
    commitment: str,
) -> protocol.Document | None:
    if normalized["error"] is not None:
        return None
    reply = normalized["output"]
    try:
        raw = protocol._utf8(reply, protocol._MAX_TURN_BYTES, "response")
        if not _observed_reply(sample, reply):
            return None
        answer = protocol.loads(reply)
        protocol._answer_values(rubric, answer)
    except ValueError:
        return None
    return {
        "run": f"inspect-number:{sample.id}"
        if isinstance(sample.id, int)
        else sample.id,
        "answer": answer,
        "source_sha256": commitment,
        "response_sha256": sha256(raw).hexdigest(),
    }


def _record(
    entry: protocol.Document, sample: "EvalSample", commitment: str
) -> tuple[protocol.Document, int]:
    from inspect_ai.log import resolve_sample_attachments

    # Inspect expands message content, but not a scalar EvalSample.input.
    if isinstance(sample.input, str) and sample.input.startswith(
        ("attachment://", "tc://")
    ):
        key = sample.input.partition("://")[2]
        if key not in sample.attachments:
            raise ValueError("missing native Inspect input attachment")
        sample = sample.model_copy(update={"input": sample.attachments[key]})
    sample = resolve_sample_attachments(sample)
    try:
        normalized = evidence._normalized(sample)
    except ValueError:
        raise ValueError("invalid first-attempt rolepack Inspect sample") from None
    prompt = normalized["input"]
    raw = protocol._utf8(prompt, protocol._MAX_TURN_BYTES, "original prompt")
    if not raw:
        raise ValueError("missing original prompt")
    rubric = entry["rubric"]
    return {
        "id": entry["id"],
        "task": entry["task"],
        "world": entry["world"],
        "rubric": rubric,
        "prompt": prompt,
        "prompt_sha256": sha256(raw).hexdigest(),
        "peer": _peer(sample, normalized, rubric, commitment),
    }, len(raw)


def build(index: protocol.Document, logs: list[Path]) -> protocol.Document:
    selected = _index(index)
    protocol._list(logs, 0, evidence._MAX_ROWS, "Inspect logs")
    records: dict[_SampleID, protocol.Document] = {}
    prompt_bytes = 0
    for path in logs:
        log, commitment = _read_archive(path, selected)
        for sample in log.samples or []:
            entry = selected.get(sample.id)
            if entry is None:
                continue
            if sample.id in records:
                raise ValueError("duplicate indexed Inspect sample")
            if log.invalidated:
                raise ValueError("indexed sample belongs to an invalidated archive")
            record, size = _record(entry, sample, commitment)
            prompt_bytes += size
            if prompt_bytes > protocol._MAX_JSON_BYTES:
                raise ValueError("rolepack is too large")
            records[sample.id] = record
    if len(records) != len(selected):
        raise ValueError("indexed Inspect sample is absent")
    ordered = sorted(
        records.values(), key=lambda record: (record["task"], record["id"])
    )
    return protocol.validate({"schema": protocol.SCHEMA, "records": ordered})
