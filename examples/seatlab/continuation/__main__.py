from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn

from . import panel, score, source

_REPO = Path(__file__).resolve().parents[3]
_OWNER = "owner.json"
_OWNER_PARTIAL = ".owner.json.partial"
_REQUESTS = "requests.jsonl"
_OWNER_SCHEMA = "seatlab.continuation-owner/v1"
_ORDERS_PER_COHORT = 3
_MAX_COHORTS = 64
_MAX_ID_CHARS = 256
_MAX_OWNER_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_LINE_BYTES = 8 * 1024 * 1024
_MAX_REQUESTS_BYTES = 64 * 1024 * 1024
_MAX_REPLY_BYTES = 64 * 1024
_MAX_REPLY_LINE_BYTES = 6 * _MAX_REPLY_BYTES + 1024
_MAX_REPLY_FILES = 64
_REQUEST_FIELDS = ("id", "prompt_sha256", "prompt")
_REPLY_FIELDS = ("id", "prompt_sha256", "reply")
_HEX64 = re.compile(r"[0-9a-f]{64}")

_Commitments = list[list[tuple[str, str]]]


def _shard(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", value)
    if match is not None:
        index, count = map(int, match.groups())
        if 0 <= index < count:
            return index, count
    raise argparse.ArgumentTypeError("expected INDEX/COUNT with 0 <= INDEX < COUNT")


def _encode(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _decode(data: bytes) -> Any:
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )


def _open_nonblocking(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NONBLOCK)


def _open_private(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NONBLOCK | os.O_NOFOLLOW)


def _read_bounded(path: Path, limit: int) -> bytes:
    with open(path, "rb", opener=_open_private) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError(f"{str(path)!r}: expected a regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{str(path)!r}: exceeds {limit} bytes")
    return data


def _outside_checkout(path: Path) -> Path:
    if path.resolve().is_relative_to(_REPO):
        raise ValueError(
            f"{str(path)!r}: must be outside the git checkout {str(_REPO)!r}"
        )
    return path


def _prepared_dir(dest: Path) -> Path:
    _outside_checkout(dest)
    mode = os.lstat(dest).st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"{str(dest)!r}: expected a prepared directory, not a symlink")
    if mode & 0o077:
        raise ValueError(f"{str(dest)!r}: prepared directory must be private (0700)")
    return dest


def _order(order: object, seen: set[str]) -> tuple[str, str]:
    if not isinstance(order, dict):
        raise TypeError("owner: each order must be an object")
    order_id, digest = order.get("id"), order.get("prompt_sha256")
    if not isinstance(order_id, str) or not 1 <= len(order_id) <= _MAX_ID_CHARS:
        raise ValueError(f"owner: order IDs must be 1 to {_MAX_ID_CHARS} characters")
    if order_id in seen:
        raise ValueError(f"owner: duplicate order ID {order_id!r}")
    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
        raise ValueError(f"owner: order {order_id!r} needs a lowercase hex SHA-256")
    seen.add(order_id)
    return order_id, digest


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    value = _decode(data)
    if not isinstance(value, dict):
        raise TypeError(f"{label}: expected a JSON object")
    return value


def _commitments(owner: dict[str, Any]) -> _Commitments:
    if owner.get("schema") != _OWNER_SCHEMA:
        raise ValueError(f"owner: expected schema {_OWNER_SCHEMA}")
    index = owner.get("index_sha256")
    if not isinstance(index, str) or _HEX64.fullmatch(index) is None:
        raise ValueError("owner: index_sha256 must be a lowercase hex SHA-256")
    cohorts = owner.get("cohorts")
    if not isinstance(cohorts, list) or not 1 <= len(cohorts) <= _MAX_COHORTS:
        raise ValueError(f"owner: expected 1 to {_MAX_COHORTS} cohorts")
    seen: set[str] = set()
    groups: _Commitments = []
    for cohort in cohorts:
        orders = cohort.get("orders") if isinstance(cohort, dict) else None
        if not isinstance(orders, list) or len(orders) != _ORDERS_PER_COHORT:
            raise ValueError(
                f"owner: each cohort needs exactly {_ORDERS_PER_COHORT} orders"
            )
        groups.append([_order(order, seen) for order in orders])
    return groups


def _expected(groups: _Commitments) -> dict[str, str]:
    return {order_id: digest for group in groups for order_id, digest in group}


def _string_fields(
    value: Any, fields: tuple[str, str, str], label: str
) -> tuple[str, str, str]:
    if not isinstance(value, dict) or value.keys() != set(fields):
        raise ValueError(f"{label}: expected exactly {', '.join(fields)}")
    first, second, third = value[fields[0]], value[fields[1]], value[fields[2]]
    if not (
        isinstance(first, str) and isinstance(second, str) and isinstance(third, str)
    ):
        raise TypeError(f"{label}: {', '.join(fields)} must be strings")
    return first, second, third


def _request_line(record: object, expected: dict[str, str]) -> tuple[str, bytes]:
    order_id, digest, prompt = _string_fields(record, _REQUEST_FIELDS, "request")
    if order_id not in expected:
        raise ValueError(f"request {order_id!r}: unknown order ID")
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if digest != expected[order_id] or prompt_digest != digest:
        raise ValueError(
            f"request {order_id!r}: prompt SHA-256 does not match the owner"
        )
    line = _encode(record)
    if len(line) > _MAX_REQUEST_LINE_BYTES:
        raise ValueError(
            f"request {order_id!r}: exceeds {_MAX_REQUEST_LINE_BYTES} bytes"
        )
    return order_id, line


def _request_lines(records: Sequence[object], groups: _Commitments) -> dict[str, bytes]:
    expected = _expected(groups)
    if len(records) != len(expected):
        raise ValueError(f"expected {len(expected)} requests, found {len(records)}")
    lines: dict[str, bytes] = {}
    for record in records:
        order_id, line = _request_line(record, expected)
        if order_id in lines:
            raise ValueError(f"request {order_id!r}: duplicate order ID")
        lines[order_id] = line
    if sum(map(len, lines.values())) > _MAX_REQUESTS_BYTES:
        raise ValueError(f"requests exceed {_MAX_REQUESTS_BYTES} bytes")
    return lines


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with open(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(dest: Path, owner: bytes, requests: bytes) -> None:
    os.mkdir(dest, 0o700)
    try:
        _write_new(dest / _REQUESTS, requests)
        _write_new(dest / _OWNER_PARTIAL, owner)
        os.rename(dest / _OWNER_PARTIAL, dest / _OWNER)
        _fsync_dir(dest)
        _fsync_dir(dest.parent)
    except BaseException:
        for name in (_OWNER, _OWNER_PARTIAL, _REQUESTS):
            with suppress(OSError):
                os.unlink(dest / name)
        with suppress(OSError):
            os.rmdir(dest)
        raise


def _prepare(cache: Path, dest: Path, view: str) -> dict[str, Any]:
    _outside_checkout(cache)
    _outside_checkout(dest)
    if os.path.lexists(dest):
        raise ValueError(f"{str(dest)!r}: already exists; choose a fresh directory")
    owner, requests = panel.prepare(source.load(cache), view=view)
    owner_bytes = _encode(owner)
    if len(owner_bytes) > _MAX_OWNER_BYTES:
        raise ValueError(f"owner exceeds {_MAX_OWNER_BYTES} bytes")
    checked = _json_object(owner_bytes, "owner")
    groups = _commitments(checked)
    if not isinstance(requests, list):
        raise TypeError("panel.prepare must return a list of requests")
    lines = _request_lines(requests, groups)
    request_bytes = b"".join(lines[order_id] for order_id in _expected(groups))
    _publish(dest, owner_bytes, request_bytes)
    return {
        "dest": str(dest.resolve()),
        "index_sha256": checked["index_sha256"],
        "cohorts": len(groups),
        "requests": len(lines),
        "owner_sha256": hashlib.sha256(owner_bytes).hexdigest(),
        "requests_sha256": hashlib.sha256(request_bytes).hexdigest(),
    }


def _owner(dest: Path) -> tuple[dict[str, Any], _Commitments]:
    _prepared_dir(dest)
    try:
        data = _read_bounded(dest / _OWNER, _MAX_OWNER_BYTES)
    except FileNotFoundError as exc:
        raise ValueError(
            f"{str(dest)!r}: incomplete prepare; {_OWNER} is missing"
        ) from exc
    owner = _json_object(data, _OWNER)
    return owner, _commitments(owner)


def _emit(dest: Path, shard: tuple[int, int]) -> bytes:
    _, groups = _owner(dest)
    index, count = shard
    if count > len(groups):
        raise ValueError(f"shard count {count} exceeds {len(groups)} cohorts")
    data = _read_bounded(dest / _REQUESTS, _MAX_REQUESTS_BYTES)
    if not data.endswith(b"\n"):
        raise ValueError(f"{_REQUESTS}: incomplete prepare; missing final newline")
    rows = data.split(b"\n")[:-1]
    lines = _request_lines([_decode(row) for row in rows], groups)
    return b"".join(
        lines[order_id]
        for position, group in enumerate(groups)
        if position % count == index
        for order_id, _ in group
    )


def _reply_record(line: bytes, expected: dict[str, str]) -> dict[str, str]:
    if len(line) > _MAX_REPLY_LINE_BYTES:
        raise ValueError("line exceeds the byte limit")
    if not line.endswith(b"\n"):
        raise ValueError("line must end with a newline")
    record = _decode(line[:-1])
    order_id, digest, reply = _string_fields(record, _REPLY_FIELDS, "reply")
    if order_id not in expected:
        raise ValueError(f"unknown order ID {order_id!r}")
    if digest != expected[order_id]:
        raise ValueError(f"stale prompt_sha256 for order {order_id!r}")
    if len(reply.encode("utf-8")) > _MAX_REPLY_BYTES:
        raise ValueError(f"reply exceeds {_MAX_REPLY_BYTES} bytes in UTF-8")
    return {"id": order_id, "prompt_sha256": digest, "reply": reply}


def _replies(files: list[Path], expected: dict[str, str]) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for path in files:
        with open(path, "rb", opener=_open_nonblocking) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError(f"{str(path)!r}: expected a regular file")
            for number in range(1, len(expected) + 2):
                line = handle.readline(_MAX_REPLY_LINE_BYTES + 1)
                if not line:
                    break
                try:
                    record = _reply_record(line, expected)
                    if record["id"] in found:
                        raise ValueError(f"duplicate order ID {record['id']!r}")
                except (ValueError, TypeError, RecursionError) as exc:
                    raise ValueError(f"{str(path)!r}:{number}: {exc}") from exc
                found[record["id"]] = record
    return [found[order_id] for order_id in expected if order_id in found]


def _score(dest: Path, files: list[Path]) -> dict[str, Any]:
    owner, groups = _owner(dest)
    return score.report(owner, _replies(files, _expected(groups)))


def _arguments(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.continuation",
        description="Fetch, prepare, emit, or score the blind continuation panel.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download the public source cache")
    fetch.add_argument("cache", type=Path, metavar="CACHE")
    prepare = commands.add_parser(
        "prepare", help="write owner.json and requests.jsonl to a fresh private DEST"
    )
    prepare.add_argument("cache", type=Path, metavar="CACHE")
    prepare.add_argument("dest", type=Path, metavar="DEST")
    prepare.add_argument("--view", choices=("posts", "activity"), default="posts")
    emit = commands.add_parser(
        "emit", help="print one shard of whole cohorts as request JSONL"
    )
    emit.add_argument("dest", type=Path, metavar="DEST")
    emit.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    grade = commands.add_parser(
        "score",
        help="score reply JSONL against DEST/owner.json",
        description=(
            "Read up to 64 UTF-8 JSONL reply files. Each line needs exactly id, "
            "prompt_sha256, and reply. Missing orders are allowed and reported as "
            "coverage. Unknown, duplicate, stale, or malformed lines exit 2 with no report."
        ),
    )
    grade.add_argument("dest", type=Path, metavar="DEST")
    grade.add_argument("replies", nargs="*", type=Path, metavar="REPLY")
    args = parser.parse_args(argv)
    if args.command == "score" and len(args.replies) > _MAX_REPLY_FILES:
        grade.error(f"expected at most {_MAX_REPLY_FILES} reply files")
    return parser, args


def main(argv: list[str] | None = None) -> int:
    parser, args = _arguments(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "fetch":
            source.fetch(_outside_checkout(args.cache))
            output = _encode({"cache": str(args.cache.resolve())})
        elif args.command == "prepare":
            output = _encode(_prepare(args.cache, args.dest, args.view))
        elif args.command == "emit":
            output = _emit(args.dest, args.shard)
        else:
            output = _encode(_score(args.dest, args.replies))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
