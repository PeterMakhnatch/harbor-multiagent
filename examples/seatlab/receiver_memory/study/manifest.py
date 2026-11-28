import hashlib
import json
import os
import random
import re
import secrets
import stat
from contextlib import suppress
from itertools import product
from pathlib import Path
from typing import Any

from ....native_study import record_bytes, require
from ....seat_checkpoints.materialize import sync_directory
from ...decision_boundary.revised_temporal import MAX_SEED, SLOTS, VARIANTS
from ...exhibit_enroll import CHECKOUT, target
from ...exhibit_pairs import (
    DIR_FLAGS,
    FILE_FLAGS,
    object_keys,
    reject_constant,
    reject_float,
    unique_object,
)
from ..cohort import PRICE, balanced_seeds
from ..provenance import AGES, COINS, offer, reread
from ..revised import SENDERS, cases

SCHEMA = "seatlab.receiver-study.plan/v1"
ARMS = ("version", "sham")
SOURCES = ("scripted_control", "external_unverified")
COUNT = 96
MAX_BYTES = 1 << 20
TICKET = re.compile(r"[0-9a-f]{32}")
PIN = re.compile(r"[0-9a-f]{64}")
PLAN_FIELDS = {"schema", "start", "sender", "source", "price", "assignments"}
ASSIGNMENT_FIELDS = {
    "ticket",
    "seed",
    "world",
    "age",
    "arm",
    "coin",
    "offer_sha256",
    "read_sha256",
    "scores",
}


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def digest_line(data: bytes) -> str:
    require(type(data) is bytes and 0 < len(data) < MAX_BYTES, "invalid frame size")
    require(b"\n" not in data and b"\r" not in data, "invalid frame delimiter")
    return hashlib.sha256(data + b"\n").hexdigest()


def _private(info: os.stat_result, mode: int) -> None:
    require(info.st_uid == os.getuid(), "private path has wrong owner")
    require(stat.S_IMODE(info.st_mode) == mode, "private path has wrong mode")


def _directory(path: Path | str, *, parent: int | None = None) -> int:
    fd = os.open(path, DIR_FLAGS, dir_fd=parent)
    try:
        _private(os.fstat(fd), 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def publish_private(path: Path, data: bytes) -> None:
    require(type(data) is bytes and 0 < len(data) < MAX_BYTES, "invalid record size")
    directory = _directory(path.parent)
    pending = f".pending-{secrets.token_hex(16)}"
    try:
        fd = os.open(
            pending,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(
                pending,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        finally:
            os.unlink(pending, dir_fd=directory)
            os.fsync(directory)
    finally:
        os.close(directory)


def _read_private(directory: int, name: str) -> bytes:
    fd = os.open(name, FILE_FLAGS, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode), "not a plain private file")
        _private(info, 0o600)
        require(0 < info.st_size < MAX_BYTES, "private file size limit")
        data = handle.read(MAX_BYTES)
        require(len(data) == info.st_size, "private file size changed")
    return data


def _decode(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_float,
        )
    except (UnicodeError, RecursionError):
        raise ValueError("invalid private JSON") from None
    require(type(value) is dict, "private JSON must be an object")
    return value


def load_private_json(path: Path) -> dict[str, Any]:
    try:
        directory = _directory(path.parent)
        try:
            return _decode(_read_private(directory, path.name))
        finally:
            os.close(directory)
    except OSError:
        raise ValueError("cannot read private record") from None


def _header(start: int, sender: str, source: str) -> None:
    require(type(start) is int and 0 <= start <= MAX_SEED, "invalid starting seed")
    require(sender in SENDERS, "invalid sender")
    require(source in SOURCES, "invalid source")


def _winner(scores: object) -> str:
    row = object_keys(scores, set(SLOTS))
    require(
        all(type(score) is int and score in (0, 1) for score in row.values())
        and sum(row.values()) == 1,
        "invalid score vector",
    )
    return next(slot for slot in SLOTS if row[slot] == 1)


def _draw(start: int, sender: str, source: str) -> dict[str, Any]:
    _header(start, sender, source)
    assignments = []
    for pair, seed in balanced_seeds(start).items():
        episodes = cases(seed, sender)
        require(
            len(episodes) == 2 and {case.name for case in episodes} == set(VARIANTS),
            "invalid native world roster",
        )
        for case in episodes:
            require(case.mass == 1, "invalid native world mass")
            require(
                _winner(case.scores) == pair[VARIANTS.index(case.name)],
                "native grades disagree with slot rotation",
            )
            read_digest = digest_line(reread(case))
            for age, arm, coin in product(AGES, ARMS, COINS):
                assignments.append(
                    {
                        "ticket": secrets.token_hex(16),
                        "seed": seed,
                        "world": case.name,
                        "age": age,
                        "arm": arm,
                        "coin": coin,
                        "offer_sha256": digest_line(offer(case, age, arm, coin, PRICE)),
                        "read_sha256": read_digest,
                        "scores": dict(case.scores),
                    }
                )
    require(len(assignments) == COUNT, "invalid assignment count")
    require(len({row["ticket"] for row in assignments}) == COUNT, "ticket collision")
    random.SystemRandom().shuffle(assignments)
    return {
        "schema": SCHEMA,
        "start": start,
        "sender": sender,
        "source": source,
        "price": "1/4",
        "assignments": assignments,
    }


def _validate(plan: dict[str, Any]) -> None:
    object_keys(plan, PLAN_FIELDS)
    require(plan["schema"] == SCHEMA and plan["price"] == "1/4", "invalid plan header")
    _header(plan["start"], plan["sender"], plan["source"])
    rotations = {seed: pair for pair, seed in balanced_seeds(plan["start"]).items()}
    rows = plan["assignments"]
    require(type(rows) is list and len(rows) == COUNT, "invalid assignment count")
    cells = set(product(rotations, VARIANTS, AGES, ARMS, COINS))
    tickets = set()
    reads = {}
    versions = {}
    for raw in rows:
        row = object_keys(raw, ASSIGNMENT_FIELDS)
        ticket = row["ticket"]
        require(
            _matches(TICKET, ticket) and ticket not in tickets, "invalid ticket roster"
        )
        tickets.add(ticket)
        seed, world, age, arm, coin = (
            row[key] for key in ("seed", "world", "age", "arm", "coin")
        )
        require(
            type(seed) is int
            and all(type(label) is str for label in (world, age, arm, coin)),
            "invalid assignment labels",
        )
        cell = (seed, world, age, arm, coin)
        require(cell in cells, "unexpected or duplicate assignment cell")
        cells.remove(cell)
        require(
            _winner(row["scores"]) == rotations[seed][VARIANTS.index(world)],
            "score vector disagrees with slot rotation",
        )
        read_digest, offer_digest = row["read_sha256"], row["offer_sha256"]
        require(
            _matches(PIN, read_digest) and _matches(PIN, offer_digest),
            "invalid frame digest",
        )
        require(
            reads.setdefault((seed, world), read_digest) == read_digest,
            "inconsistent read digest",
        )
        if arm == "version":
            require(
                versions.setdefault((seed, world, age), offer_digest) == offer_digest,
                "inconsistent version offer digest",
            )
    require(not cells, "missing assignment cells")


def seal(root: Path, start: int, sender: str, source: str) -> dict[str, Any]:
    root = target(root)
    plan = _draw(start, sender, source)
    data = record_bytes(plan)
    root.mkdir(mode=0o700)
    attempts = root / "attempts"
    created_attempts = False
    try:
        root.chmod(0o700)
        attempts.mkdir(mode=0o700)
        created_attempts = True
        attempts.chmod(0o700)
        publish_private(root / "plan.json", data)
        sync_directory(root.parent)
    except BaseException:
        if created_attempts:
            with suppress(OSError):
                attempts.rmdir()
        with suppress(OSError):
            root.rmdir()
        raise
    return {
        "schema": SCHEMA,
        "sha256": hashlib.sha256(data).hexdigest(),
        "assignments": COUNT,
        "model_calls": 0,
        "trials": 0,
    }


def load(root: Path, expected: str) -> dict[str, Any]:
    require(_matches(PIN, expected), "invalid plan pin")
    try:
        root = root.expanduser().absolute()
        require(root.name not in ("", ".", ".."), "invalid study root")
        root = root.parent.resolve(strict=True) / root.name
        require(
            not root.is_relative_to(CHECKOUT), "study root must be outside checkout"
        )
        require(
            not any(
                os.path.lexists(parent / ".git") for parent in (root, *root.parents)
            ),
            "study root must be outside git checkouts",
        )
        directory = _directory(root)
        try:
            os.close(_directory("attempts", parent=directory))
            data = _read_private(directory, "plan.json")
        finally:
            os.close(directory)
    except (OSError, RuntimeError):
        raise ValueError("cannot read private plan") from None
    require(hashlib.sha256(data).hexdigest() == expected, "plan pin mismatch")
    plan = _decode(data)
    _validate(plan)
    return plan


def pick(plan: dict[str, Any], ticket: str) -> dict[str, Any]:
    require(_matches(TICKET, ticket), "invalid ticket")
    for row in plan["assignments"]:
        if row["ticket"] == ticket:
            return row
    raise ValueError("unknown ticket")
