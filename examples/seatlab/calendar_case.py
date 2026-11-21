from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn, TypedDict, override
from uuid import UUID

from .receiver_panel import (
    _MAX_LINE_BYTES,
    _MAX_REPLY_BYTES,
    _reject_constant,
    _unique_keys,
    _worker_line,
)
from .visibility import calendar_packet, calendar_protocol

_VIEWS = ("factual", "lists", "checked")
_SYSTEM = "Answer only with JSON."
_SCHEMA = "seatlab.calendar-case/v1"
_OWNER = "owner.json"
_REQUESTS = "requests.jsonl"
_MAX_OWNER_BYTES = 4096
_MAX_PACKET_BYTES = len(_VIEWS) * _MAX_LINE_BYTES
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_PUBLIC_FIELDS = (
    "trial_uuid",
    "result_sha256",
    "stock_semantics_sha256",
    "world",
    "arm",
    "requests_sha256",
)
_OWNER_FIELDS = (
    *_PUBLIC_FIELDS,
    "schema",
    "native_reply_sha256",
    "native_reward",
    "target",
    "target_visible",
    "prompt_sha256",
)


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid arguments. Use --help.\n")


class _Request(TypedDict):
    id: str
    prompt_sha256: str
    messages: list[dict[str, str]]


class _Score(TypedDict):
    status: Literal["missing", "invalid", "scored"]
    correct: int | None
    reply_sha256: str | None


@dataclass(frozen=True, slots=True)
class _Capsule:
    source: dict[str, str]
    target: int
    requests: dict[str, _Request]


def _wire(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _json(data: bytes) -> object:
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )


def _jsonl(data: bytes) -> list[bytes]:
    if not data:
        return []
    *lines, tail = data.split(b"\n", len(_VIEWS) + 1)
    if (
        tail
        or len(lines) > len(_VIEWS)
        or any(not 0 < len(line) < _MAX_LINE_BYTES for line in lines)
    ):
        raise ValueError("invalid JSONL packet")
    return lines


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    fd = os.open(path.anchor or ".", _DIRECTORY_FLAGS)
    try:
        for part in path.parts[bool(path.anchor) :]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_at(directory: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("expected a size-limited regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("input exceeds the byte limit")
        return data


def _write_capsule(path: Path, owner: bytes, requests: bytes) -> None:
    with _directory(path.parent) as parent:
        previous = os.umask(0o077)
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        finally:
            os.umask(previous)
        directory = None
        created: list[str] = []
        try:
            directory = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=parent)
            for name, data in ((_OWNER, owner), (_REQUESTS, requests)):
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                created.append(name)
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(fd, 0o600)
                    stream.write(data)
        except BaseException:
            if directory is not None:
                for name in created:
                    with suppress(OSError):
                        os.unlink(name, dir_fd=directory)
            with suppress(OSError):
                os.rmdir(path.name, dir_fd=parent)
            raise
        finally:
            if directory is not None:
                os.close(directory)


def _request(case_id: str, prompt: str) -> _Request:
    encoded = prompt.encode("utf-8")
    if not 0 < len(encoded) <= _MAX_REPLY_BYTES:
        raise ValueError("invalid user prompt size")
    return {
        "id": case_id,
        "prompt_sha256": sha256(encoded).hexdigest(),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }


def _parse_request(line: bytes, case_id: str, commitment: str) -> _Request:
    record = calendar_packet._object(_json(line), "id", "prompt_sha256", "messages")
    messages = record["messages"]
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("expected exactly two messages")
    user = calendar_packet._object(messages[1], "role", "content")
    if not isinstance(user["content"], str):
        raise TypeError("expected a text user prompt")
    request = _request(case_id, user["content"])
    if record != request or request["prompt_sha256"] != commitment:
        raise ValueError("request commitment or envelope mismatch")
    return request


def _parse_capsule(owner_data: bytes, packet: bytes) -> _Capsule:
    owner = calendar_packet._object(_json(owner_data), *_OWNER_FIELDS)
    if owner["schema"] != _SCHEMA:
        raise ValueError("unsupported capsule schema")
    for field in (
        "result_sha256",
        "stock_semantics_sha256",
        "native_reply_sha256",
        "requests_sha256",
    ):
        calendar_packet._check_digest(owner[field])
    if sha256(packet).hexdigest() != owner["requests_sha256"]:
        raise ValueError("request packet digest mismatch")
    trial_uuid = owner["trial_uuid"]
    if (
        not isinstance(trial_uuid, str)
        or len(trial_uuid) != 36
        or str(UUID(trial_uuid)) != trial_uuid
    ):
        raise ValueError("expected a canonical trial UUID")
    world, arm = owner["world"], owner["arm"]
    if (
        not isinstance(world, str)
        or not re.fullmatch(r"w[0-9]{2}", world)
        or arm
        not in ("raw", "exact", "sender-exact", "receiver-exact", "lists", "checked")
        or type(owner["native_reward"]) is not int
        or owner["native_reward"] not in (0, 1)
        or type(owner["target_visible"]) is not bool
    ):
        raise ValueError("invalid source metadata")
    target = calendar_protocol._checked_slot(owner["target"])
    commitments = calendar_packet._object(owner["prompt_sha256"], *_VIEWS)
    requests = {
        view: _parse_request(line, f"{trial_uuid}/{view}", commitments[view])
        for view, line in zip(_VIEWS, _jsonl(packet), strict=True)
    }
    return _Capsule(
        source={field: owner[field] for field in _PUBLIC_FIELDS},
        target=target,
        requests=requests,
    )


def _load_capsule(path: Path) -> _Capsule:
    with _directory(path) as directory:
        owner = _read_at(directory, _OWNER, _MAX_OWNER_BYTES)
        packet = _read_at(directory, _REQUESTS, _MAX_PACKET_BYTES)
    return _parse_capsule(owner, packet)


def _export(trial: Path, spend_dir: Path, out: Path) -> None:
    from . import calendar_case_source

    source = calendar_case_source.load(trial, spend_dir)
    if source.prompts.keys() != set(_VIEWS):
        raise ValueError("unexpected source views")
    requests = {
        view: _request(f"{source.trial_uuid}/{view}", source.prompts[view])
        for view in _VIEWS
    }
    packet = b"".join(_wire(request) for request in requests.values())
    owner = _wire(
        {
            "schema": _SCHEMA,
            "trial_uuid": str(source.trial_uuid),
            "result_sha256": source.result_sha256,
            "stock_semantics_sha256": source.stock_semantics_sha256,
            "world": source.world,
            "arm": source.arm,
            "native_reply_sha256": source.native_reply_sha256,
            "native_reward": source.native_reward,
            "target": source.target,
            "target_visible": source.target_visible,
            "prompt_sha256": {
                view: request["prompt_sha256"] for view, request in requests.items()
            },
            "requests_sha256": sha256(packet).hexdigest(),
        }
    )
    _parse_capsule(owner, packet)
    _write_capsule(out, owner, packet)


def _read_replies(path: Path, capsule: _Capsule) -> dict[str, tuple[str, str]]:
    with _directory(path.parent) as directory:
        data = _read_at(directory, path.name, _MAX_PACKET_BYTES)
    allowed = {
        request["id"]: request["prompt_sha256"] for request in capsule.requests.values()
    }
    observed = {}
    for line in _jsonl(data):
        case_id, commitment, reply = _worker_line(line + b"\n")
        if case_id in observed or allowed.get(case_id) != commitment:
            raise ValueError("unexpected worker ID or commitment")
        encoded = reply.encode("utf-8")
        if len(encoded) > _MAX_REPLY_BYTES:
            raise ValueError("reply exceeds the byte limit")
        observed[case_id] = (reply, sha256(encoded).hexdigest())
    return observed


def _score(target: int, observed: tuple[str, str] | None) -> _Score:
    if observed is None:
        return {"status": "missing", "correct": None, "reply_sha256": None}
    reply, digest = observed
    try:
        slot = calendar_protocol.decode_slot(reply)
    except (ValueError, RecursionError):
        return {"status": "invalid", "correct": None, "reply_sha256": digest}
    return {"status": "scored", "correct": int(slot == target), "reply_sha256": digest}


def _compare(capsule: _Capsule, left: Path, right: Path) -> dict[str, object]:
    left_replies = _read_replies(left, capsule)
    right_replies = _read_replies(right, capsule)
    views = {}
    for view, request in capsule.requests.items():
        case_id = request["id"]
        a = _score(capsule.target, left_replies.get(case_id))
        b = _score(capsule.target, right_replies.get(case_id))
        lc, rc = a["correct"], b["correct"]
        views[view] = {
            "left": a,
            "right": b,
            "right_minus_left": rc - lc if lc is not None and rc is not None else None,
        }
    return {"source": capsule.source, "views": views}


def main() -> int:
    parser = _Parser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", allow_abbrev=False)
    export.add_argument("trial", type=Path)
    export.add_argument("--spend-dir", required=True, type=Path)
    export.add_argument("--out", required=True, type=Path)
    emit = commands.add_parser("emit", allow_abbrev=False)
    emit.add_argument("capsule", type=Path)
    emit.add_argument("--view", choices=_VIEWS, required=True)
    compare = commands.add_parser("compare", allow_abbrev=False)
    compare.add_argument("capsule", type=Path)
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    try:
        args = parser.parse_args()
        if args.command == "export":
            _export(args.trial, args.spend_dir, args.out)
        else:
            capsule = _load_capsule(args.capsule)
            record = (
                capsule.requests[args.view]
                if args.command == "emit"
                else _compare(capsule, args.left, args.right)
            )
            sys.stdout.buffer.write(_wire(record))
    except (ImportError, KeyError, OSError, RecursionError, TypeError, ValueError):
        print("Cannot process calendar case.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
