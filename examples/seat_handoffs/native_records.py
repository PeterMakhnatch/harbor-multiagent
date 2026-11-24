import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, cast

from .core import MAX_LOG_BYTES, reject_constant, unique_object


def read_record(path: Path, limit: int = MAX_LOG_BYTES) -> dict[str, Any] | None:
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
