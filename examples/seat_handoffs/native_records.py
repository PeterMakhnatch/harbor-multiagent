import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, cast

from .core import MAX_LOG_BYTES, reject_constant, unique_object


def _read_bytes(path: Path, limit: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with open(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("record exceeds its size bound")
    return data


def _parse(data: bytes) -> dict[str, Any]:
    try:
        record = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError("record is not valid JSON") from error
    if not isinstance(record, dict):
        raise ValueError("record is not a JSON object")
    return cast(dict[str, Any], record)


def read_record(path: Path, limit: int = MAX_LOG_BYTES) -> dict[str, Any] | None:
    data = _read_bytes(path, limit)
    return None if data is None else _parse(data)


def read_record_with_digest(
    path: Path, limit: int
) -> tuple[dict[str, Any], str] | None:
    data = _read_bytes(path, limit)
    if data is None:
        return None
    record = _parse(data)
    return record, hashlib.sha256(data).hexdigest()


def record_digest(record: dict[str, Any]) -> str:
    try:
        text = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except RecursionError as error:
        raise ValueError("native record is too deeply nested") from error
    return hashlib.sha256(text.encode("ascii")).hexdigest()
