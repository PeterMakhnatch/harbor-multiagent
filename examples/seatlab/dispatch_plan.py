"""Seal, encode, and read a pinned Dispatch Handoff panel registration."""

import hashlib
import hmac
import json
import os
import re
import stat
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

type Json = dict[str, Any]

SCHEMA = "seatlab.dispatch-registration/v1"
PLAN_SCHEMA = "seatlab.panel-plan/v1"
CONDITIONS = ("silent", "stale", "fresh", "reordered", "pooled")
POLICIES = ("oracle", "arrival", "hosted")
JOBS = (1, 2)
MAX_EPISODES = 256
MAX_CELLS = 1024
MAX_SOURCES = 64
MAX_BYTES = 1 << 20
REGISTRATION_KEYS = frozenset({"schema", "jobs", "plan", "workload"})
PLAN_KEYS = frozenset(
    {
        "schema",
        "native",
        "policy",
        "hosted_route",
        "seeds",
        "conditions",
        "episodes",
        "source_sha256",
        "cells",
    }
)

_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:free")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*")
_SOURCE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")

BAD_MODEL = "hosted requires SEATLAB_FREE_MODEL in author/model:free form"
BAD_PROVIDER = "hosted requires an explicit SEATLAB_FREE_PROVIDER slug"
BAD_EPISODES = "episodes must be between 1 and 256"
BAD_JOBS = "jobs must be 1 or 2"
BAD_PLAN = "plan is not a native seatlab.panel-plan/v1 object"
BAD_NATIVE = "plan native pin is malformed"
BAD_POLICY = "plan policy is unknown"
BAD_ROUTE = "plan hosted route does not match its policy"
BAD_SEEDS = "plan seeds must be sorted unique signed 64-bit integers"
BAD_CONDITIONS = "plan conditions must be unique and in canonical order"
TOO_MANY_CELLS = "plan has more than 1024 cells"
BAD_SOURCES = "plan source checksum manifest is malformed"
BAD_CELLS = "plan cells do not match its seeds and conditions"
BAD_REGISTRATION = "registration is not a seatlab.dispatch-registration/v1 object"
NOT_SEALED = "registration does not match its recomputed workload"
BAD_PIN = "pin must be a lowercase SHA-256 hex digest"
BAD_PATH = "registration path must be a Path"
SYMLINK = "registration must not be a symbolic link"
UNREADABLE = "registration file cannot be opened"
NOT_REGULAR = "registration must be a regular file"
TOO_LARGE = "registration is larger than 1 MiB"
PIN_MISMATCH = "registration bytes do not match the pinned SHA-256"
BAD_JSON = "registration is not strict UTF-8 JSON"
DUPLICATE_KEY = "registration repeats a JSON key"
NOT_CANONICAL = "registration bytes are not canonical"


class _DuplicateKey(ValueError):
    pass


def max_reply_tokens(episodes: int) -> int:
    if type(episodes) is not int or not 1 <= episodes <= MAX_EPISODES:
        raise ValueError(BAD_EPISODES)
    return 2048 + 80 * episodes


def hosted_route(model: object, provider: object) -> dict[str, str]:
    if type(model) is not str or not _MODEL.fullmatch(model):
        raise ValueError(BAD_MODEL)
    if type(provider) is not str or not _PROVIDER.fullmatch(provider):
        raise ValueError(BAD_PROVIDER)
    return {"model": model, "provider": provider}


def _dump(value: object, error: str) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise ValueError(error) from None
    return text.encode("utf-8") + b"\n"


def _native(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or value.keys() != {"expected_version", "olam_base"}:
        raise ValueError(BAD_NATIVE)
    data = cast(Json, value)
    version, commit = data["expected_version"], data["olam_base"]
    if not (
        type(version) is str
        and type(commit) is str
        and _VERSION.fullmatch(version)
        and _COMMIT.fullmatch(commit)
    ):
        raise ValueError(BAD_NATIVE)
    return {"expected_version": version, "olam_base": commit}


def _route(policy: str, value: object) -> dict[str, str] | None:
    if policy != "hosted":
        if value is not None:
            raise ValueError(BAD_ROUTE)
        return None
    if not isinstance(value, dict) or value.keys() != {"model", "provider"}:
        raise ValueError(BAD_ROUTE)
    route = cast(Json, value)
    return hosted_route(route["model"], route["provider"])


def _seeds(value: object) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CELLS:
        raise ValueError(BAD_SEEDS)
    if any(type(seed) is not int or not -(2**63) <= seed < 2**63 for seed in value):
        raise ValueError(BAD_SEEDS)
    seeds = cast(list[int], value)
    if any(left >= right for left, right in pairwise(seeds)):
        raise ValueError(BAD_SEEDS)
    return seeds.copy()


def _conditions(value: object) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise ValueError(BAD_CONDITIONS)
    canonical = [condition for condition in CONDITIONS if condition in value]
    if not canonical or value != canonical:
        raise ValueError(BAD_CONDITIONS)
    return canonical


def _sources(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_SOURCES:
        raise ValueError(BAD_SOURCES)
    if any(
        type(name) is not str
        or type(digest) is not str
        or not _SOURCE.fullmatch(name)
        or not _SHA256.fullmatch(digest)
        for name, digest in value.items()
    ):
        raise ValueError(BAD_SOURCES)
    return cast(dict[str, str], value).copy()


def _cells(seeds: list[int], conditions: list[str]) -> list[dict[str, Any]]:
    if len(seeds) * len(conditions) > MAX_CELLS:
        raise ValueError(TOO_MANY_CELLS)
    return [
        {"cell_id": f"seed-{seed}--{condition}", "seed": seed, "condition": condition}
        for seed in seeds
        for condition in conditions
    ]


def _workload(
    policy: str, episodes: int, cells: list[dict[str, Any]]
) -> dict[str, int]:
    teams = len(cells)
    pooled = sum(cell["condition"] == "pooled" for cell in cells)
    inventory_calls = teams - pooled if policy == "hosted" else 0
    dispatch_calls = teams if policy == "hosted" else 0
    hosted_calls = inventory_calls + dispatch_calls
    workload = {
        "native_teams": teams,
        "shipment_cases": teams * episodes,
        "native_seat_slots": 2 * teams,
        "pooled_nop_seats": pooled,
        "hosted_inventory_calls_max": inventory_calls,
        "hosted_dispatch_calls_max": dispatch_calls,
        "hosted_seat_calls_max": hosted_calls,
        "hosted_completion_tokens_max": hosted_calls * max_reply_tokens(episodes),
    }
    assert 1 <= teams <= MAX_CELLS and 0 <= pooled <= teams
    assert 0 <= hosted_calls <= workload["native_seat_slots"]
    return workload


def normalize(value: object) -> Json:
    if not isinstance(value, dict) or value.keys() != PLAN_KEYS:
        raise ValueError(BAD_PLAN)
    plan = cast(Json, value)
    if plan["schema"] != PLAN_SCHEMA:
        raise ValueError(BAD_PLAN)
    policy = plan["policy"]
    if type(policy) is not str or policy not in POLICIES:
        raise ValueError(BAD_POLICY)
    episodes = plan["episodes"]
    max_reply_tokens(episodes)
    seeds = _seeds(plan["seeds"])
    conditions = _conditions(plan["conditions"])
    canonical = {
        "schema": PLAN_SCHEMA,
        "native": _native(plan["native"]),
        "policy": policy,
        "hosted_route": _route(policy, plan["hosted_route"]),
        "seeds": seeds,
        "conditions": conditions,
        "episodes": episodes,
        "source_sha256": _sources(plan["source_sha256"]),
        "cells": _cells(seeds, conditions),
    }
    if _dump(plan["cells"], BAD_CELLS) != _dump(canonical["cells"], BAD_CELLS):
        raise ValueError(BAD_CELLS)
    return canonical


def seal(plan: object, jobs: int) -> Json:
    if type(jobs) is not int or jobs not in JOBS:
        raise ValueError(BAD_JOBS)
    canonical = normalize(plan)
    return {
        "schema": SCHEMA,
        "jobs": jobs,
        "plan": canonical,
        "workload": _workload(
            canonical["policy"], canonical["episodes"], canonical["cells"]
        ),
    }


def _registration(value: object) -> Json:
    if (
        not isinstance(value, dict)
        or value.keys() != REGISTRATION_KEYS
        or value.get("schema") != SCHEMA
    ):
        raise ValueError(BAD_REGISTRATION)
    registration = cast(Json, value)
    if type(registration["workload"]) is not dict:
        raise ValueError(NOT_SEALED)
    sealed = seal(registration["plan"], registration["jobs"])
    if _dump(registration["workload"], NOT_SEALED) != _dump(
        sealed["workload"], NOT_SEALED
    ):
        raise ValueError(NOT_SEALED)
    return sealed


def encode(registration: Json) -> bytes:
    content = _dump(_registration(registration), NOT_SEALED)
    if len(content) > MAX_BYTES:
        raise ValueError(TOO_LARGE)
    return content


def _read_regular(path: Path) -> bytes:
    if not isinstance(path, Path):
        raise TypeError(BAD_PATH)
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise ValueError(SYMLINK)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(UNREADABLE) from None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ValueError(NOT_REGULAR)
        if status.st_size > MAX_BYTES:
            raise ValueError(TOO_LARGE)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError(TOO_LARGE)
        return raw
    except OSError:
        raise ValueError(UNREADABLE) from None
    finally:
        os.close(descriptor)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(DUPLICATE_KEY)
        result[key] = value
    return result


def _reject(_: str) -> None:
    raise ValueError(BAD_JSON)


def read_pinned(path: Path, expected_sha256: str) -> Json:
    if type(expected_sha256) is not str or not _SHA256.fullmatch(expected_sha256):
        raise ValueError(BAD_PIN)
    raw = _read_regular(path)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        raise ValueError(PIN_MISMATCH)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_reject,
            parse_float=_reject,
        )
    except _DuplicateKey:
        raise ValueError(DUPLICATE_KEY) from None
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError(BAD_JSON) from None
    sealed = _registration(value)
    if _dump(sealed, NOT_SEALED) != raw:
        raise ValueError(NOT_CANONICAL)
    return sealed
