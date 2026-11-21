import json
import re
from collections.abc import Iterator
from hashlib import sha256
from io import BytesIO
from math import isfinite
from typing import Any, cast

SCHEMA = "seatlab.rolepack/v1"
_MAX_RECORDS = 1024
_MAX_ITEMS = 128
_MAX_CHOICES = 256
_MAX_NAME_BYTES = 128
_MAX_ACTION_BYTES = 512
_MAX_TURN_BYTES = 1 << 20
_MAX_JSON_BYTES = 16 << 20
_MAX_INTEGER_BITS = 4096
_HANDOFF = (
    "\n\nA prior receiver proposed the following action. "
    "Respond to the original task in its required format.\n"
)
_ARMS = ("original", "peer")
_SHA256 = re.compile(r"[0-9a-f]{64}")

type Document = dict[str, Any]
type Primitive = str | int


def _utf8(value: object, limit: int, label: str) -> bytes:
    if type(value) is not str or len(value) > limit:
        raise ValueError(f"invalid {label}")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"invalid {label}") from None
    if len(raw) > limit:
        raise ValueError(f"invalid {label}")
    return raw


def _canonical_chunks(obj: object) -> Iterator[bytes]:
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    size = 1
    try:
        for fragment in encoder.iterencode(obj):
            chunk = fragment.encode("utf-8")
            size += len(chunk)
            if size > _MAX_JSON_BYTES:
                raise ValueError("canonical JSON is too large")
            yield chunk
    except (TypeError, UnicodeError, RecursionError):
        raise ValueError("cannot encode canonical rolepack JSON") from None
    yield b"\n"


def canonical_bytes(obj: object) -> bytes:
    output = BytesIO()
    output.writelines(_canonical_chunks(obj))
    return output.getvalue()


def digest(obj: object) -> str:
    commitment = sha256()
    for chunk in _canonical_chunks(obj):
        commitment.update(chunk)
    return commitment.hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> Document:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite JSON number")


def _finite_float(text: str) -> float:
    number = float(text)
    if not isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _integer(text: str) -> int:
    if len(text) > 1235:
        raise ValueError("JSON integer is too large")
    value = int(text)
    if value.bit_length() > _MAX_INTEGER_BITS:
        raise ValueError("JSON integer is too large")
    return value


def loads(text: str | bytes) -> Any:
    if isinstance(text, bytes):
        if len(text) > _MAX_JSON_BYTES:
            raise ValueError("JSON input is too large")
        try:
            text = text.decode("utf-8")
        except UnicodeError:
            raise ValueError("invalid UTF-8 JSON input") from None
    else:
        _utf8(text, _MAX_JSON_BYTES, "JSON input")
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            parse_int=_integer,
        )
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("invalid rolepack JSON") from None


def _object(value: object, label: str, *keys: str) -> Document:
    if type(value) is not dict or len(value) != len(keys) or value.keys() != set(keys):
        raise ValueError(f"invalid {label} fields")
    return cast(Document, value)


def _list(value: object, minimum: int, maximum: int, label: str) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError(f"invalid {label} count")
    return value


def _name(value: object, label: str, limit: int = _MAX_NAME_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not _utf8(value, limit, label)
        or not value.isprintable()
    ):
        raise ValueError(f"invalid {label}")
    if not value.strip():
        raise ValueError(f"invalid {label}")
    return value


def _sha(value: object, label: str) -> None:
    if type(value) is not str or len(value) != 64 or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")


def _primitive(value: object) -> Primitive:
    if type(value) is int and value.bit_length() <= _MAX_INTEGER_BITS:
        return value
    if type(value) is str:
        _utf8(value, _MAX_ACTION_BYTES, "action string")
        return value
    raise ValueError("action must be a bounded string or integer")


def _choices(value: object, minimum: int, label: str) -> set[Primitive]:
    choices = _list(value, minimum, _MAX_CHOICES, label)
    unique = {_primitive(choice) for choice in choices}
    if len(unique) != len(choices):
        raise ValueError(f"duplicate {label}")
    return unique


def _validate_rubric(value: object) -> Document:
    rubric = _object(value, "rubric", "field", "scalar", "items")
    field = _name(rubric["field"], "rubric field")
    scalar = rubric["scalar"]
    if type(scalar) is not bool:
        raise ValueError("invalid rubric scalar flag")
    items = _list(rubric["items"], 1, 1 if scalar else _MAX_ITEMS, "rubric items")
    seen: set[str] = set()
    for raw_item in items:
        item = _object(raw_item, "rubric item", "key", "valid", "optimal")
        key = _name(item["key"], "item key")
        if key in seen or (scalar and key != field):
            raise ValueError("invalid or duplicate rubric item key")
        seen.add(key)
        valid = _choices(item["valid"], 1, "legal actions")
        optimal = _choices(item["optimal"], 0, "optimal actions")
        if not optimal <= valid:
            raise ValueError("optimal action is not legal")
    return rubric


def _answer_values(rubric: Document, value: object) -> list[Primitive]:
    field = rubric["field"]
    payload = _object(value, "answer", field)[field]
    items = rubric["items"]
    if rubric["scalar"]:
        values = [_primitive(payload)]
    else:
        keys = [item["key"] for item in items]
        vector = _object(payload, "answer vector", *keys)
        values = [_primitive(vector[key]) for key in keys]
    for item, primitive in zip(items, values, strict=True):
        if primitive not in item["valid"]:
            raise ValueError("answer contains an illegal action")
    return values


def _peer_prompt(record: Document) -> str:
    peer = record["peer"]
    if peer is None:
        raise ValueError("peer proposal is unavailable")
    text = record["prompt"] + _HANDOFF + canonical_bytes(peer["answer"]).decode("utf-8")
    _utf8(text, _MAX_TURN_BYTES, "peer prompt")
    return text


def _validate_record(value: object) -> Document:
    record = _object(
        value,
        "record",
        "id",
        "task",
        "world",
        "prompt",
        "prompt_sha256",
        "peer",
        "rubric",
    )
    for label in ("id", "task", "world"):
        _name(record[label], f"record {label}")
    raw = _utf8(record["prompt"], _MAX_TURN_BYTES, "original prompt")
    _sha(record["prompt_sha256"], "original prompt hash")
    if not raw or sha256(raw).hexdigest() != record["prompt_sha256"]:
        raise ValueError("original prompt hash mismatch")
    rubric = _validate_rubric(record["rubric"])
    if record["peer"] is not None:
        peer = _object(
            record["peer"], "peer", "answer", "run", "source_sha256", "response_sha256"
        )
        _name(peer["run"], "prior run", limit=512)
        _sha(peer["source_sha256"], "public source hash")
        if peer["response_sha256"] is not None:
            _sha(peer["response_sha256"], "prior response hash")
        _answer_values(rubric, peer["answer"])
    return record


def _checked_pack(pack: object) -> tuple[Document, dict[str, str | None]]:
    document = _object(pack, "pack", "schema", "records")
    if document["schema"] != SCHEMA:
        raise ValueError("unsupported rolepack schema")
    records = _list(document["records"], 1, _MAX_RECORDS, "records")
    peer_hashes: dict[str, str | None] = {}
    size = len(canonical_bytes({"schema": SCHEMA, "records": []})) - 1
    for value in records:
        record = _validate_record(value)
        record_id = record["id"]
        if record_id in peer_hashes:
            raise ValueError("duplicate record ID")
        size += sum(map(len, _canonical_chunks(record)))
        if size > _MAX_JSON_BYTES:
            raise ValueError("rolepack is too large")
        peer_hashes[record_id] = (
            None
            if record["peer"] is None
            else sha256(_peer_prompt(record).encode("utf-8")).hexdigest()
        )
    return document, peer_hashes


def validate(pack: object) -> Document:
    document, _ = _checked_pack(pack)
    return document


def prompt(record: Document, arm: str) -> str:
    record = _validate_record(record)
    if arm == "original":
        return record["prompt"]
    if arm == "peer":
        return _peer_prompt(record)
    raise ValueError("unknown rolepack arm")


def plan(pack: Document) -> list[Document]:
    document, peer_hashes = _checked_pack(pack)
    records = sorted(document["records"], key=lambda r: (r["task"], r["id"]))
    rows: list[Document] = []
    for draw in (0, 1):
        for round_offset in (0, 1):
            for offset, record in enumerate(records):
                arm_index = (offset + round_offset) % len(_ARMS)
                arm = _ARMS[arm_index]
                record_id = record["id"]
                commitment = peer_hashes[record_id]
                if commitment is not None and arm == "original":
                    commitment = record["prompt_sha256"]
                rows.append(
                    {
                        "index": len(rows),
                        "sample_id": f"{record_id}:d{draw}:{arm}",
                        "record_id": record_id,
                        "task": record["task"],
                        "world": record["world"],
                        "draw": draw,
                        "arm": arm,
                        "prompt_sha256": commitment,
                    }
                )
    return rows


def grade(record: Document, reply: str) -> Document:
    rubric = _validate_record(record)["rubric"]
    _utf8(reply, _MAX_TURN_BYTES, "response")
    values = _answer_values(rubric, loads(reply))
    return {
        "values": values,
        "correct": [
            value in item["optimal"]
            for item, value in zip(rubric["items"], values, strict=True)
        ],
    }
