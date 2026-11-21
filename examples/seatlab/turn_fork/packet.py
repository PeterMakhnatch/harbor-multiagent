import json
import os
import re
import stat
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import UUID

SCHEMA = "seatlab.turn-fork-packet/v1"
SEEDS = tuple(range(6501, 6507))
REPETITIONS = (0, 1)
_MAX_PACKET_BYTES = 64 * 1024
_BAYS = ("A", "B", "C", "D")


def _read_bytes(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("invalid evidence file type or size")
        raw = stream.read(limit + 1)
    if len(raw) != info.st_size:
        raise ValueError("evidence file size changed")
    return raw


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate evidence key")
    return value


def _invalid_number(_: str) -> NoReturn:
    raise ValueError("invalid evidence number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not isfinite(value):
        raise ValueError("nonfinite evidence number")
    return value


def _decode(raw: bytes, *, integers_only: bool) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_invalid_number,
            parse_float=_invalid_number if integers_only else _finite_float,
        )
    except (ValueError, RecursionError):
        raise ValueError("invalid evidence JSON") from None


def _object(value: object, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != set(keys):
        raise ValueError("unexpected evidence fields")
    return cast(dict[str, Any], value)


def _digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a canonical SHA-256 commitment")
    return value


def _trial_id(value: object) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            trial = UUID(value)
        except ValueError:
            pass
        else:
            if trial.int != 0 and str(trial) == value:
                return value
    raise ValueError("expected a canonical native trial UUID")


def _bay_pair(value: object) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("expected two bays")
    left, right = value
    if (
        not isinstance(left, str)
        or not isinstance(right, str)
        or left not in _BAYS
        or right not in _BAYS
        or left >= right
    ):
        raise ValueError("expected distinct bays in canonical order")


def _case(value: object, case_id: int) -> None:
    case = _object(value, "id", "utilities", "reports", "after")
    if type(case["id"]) is not int or case["id"] != case_id:
        raise ValueError("expected six cases in ID order")
    utilities = _object(case["utilities"], *_BAYS)
    if any(type(rank) is not int for rank in utilities.values()) or set(
        utilities.values()
    ) != {1, 2, 3, 4}:
        raise ValueError("expected a permutation of utility ranks 1 to 4")
    reports = case["reports"]
    if not isinstance(reports, list) or len(reports) != 2:
        raise ValueError("expected two accepted reports per case")
    for revision, value in enumerate(reports, start=1):
        report = _object(value, "revision", "available")
        if type(report["revision"]) is not int or report["revision"] != revision:
            raise ValueError("expected accepted reports in revision order")
        _bay_pair(report["available"])
    _bay_pair(case["after"])


def _source(value: object) -> tuple[str, str]:
    source = _object(
        value,
        "source_id",
        "seed",
        "repetition",
        "trial_id",
        "world_sha256",
        "upstream",
        "card",
        "cases",
    )
    seed, repetition = source["seed"], source["repetition"]
    if (
        type(seed) is not int
        or seed not in SEEDS
        or type(repetition) is not int
        or repetition not in REPETITIONS
    ):
        raise ValueError("unregistered turn-fork source")
    source_id = f"s{seed}-r{repetition}"
    if source["source_id"] != source_id:
        raise ValueError("source ID does not match the registered slot")
    trial_id = _trial_id(source["trial_id"])
    _digest(source["world_sha256"])
    for turn in _object(source["upstream"], "inventory", "brief").values():
        hashes = _object(turn, "prompt_sha256", "response_sha256")
        for commitment in hashes.values():
            _digest(commitment)
    card = source["card"]
    if (
        not isinstance(card, str)
        or len(card) != 6
        or any(bay not in _BAYS for bay in card)
    ):
        raise ValueError("expected a six-letter card")
    cases = source["cases"]
    if not isinstance(cases, list) or len(cases) != 6:
        raise ValueError("expected six cases per source")
    for case_id, case in enumerate(cases):
        _case(case, case_id)
    return source_id, trial_id


def _validate(value: object) -> dict[str, Any]:
    packet = _object(value, "schema", "source_report_sha256", "sources")
    if packet["schema"] != SCHEMA:
        raise ValueError("unsupported turn-fork packet schema")
    _digest(packet["source_report_sha256"])
    sources = packet["sources"]
    if not isinstance(sources, list) or len(sources) > len(SEEDS) * len(REPETITIONS):
        raise ValueError("invalid turn-fork source count")
    seen_sources: set[str] = set()
    seen_trials: set[str] = set()
    for source in sources:
        source_id, trial_id = _source(source)
        if source_id in seen_sources or trial_id in seen_trials:
            raise ValueError("duplicate turn-fork source or trial")
        seen_sources.add(source_id)
        seen_trials.add(trial_id)
    return packet


def load(path: Path, digest: str) -> dict[str, Any]:
    expected = _digest(digest)
    try:
        raw = _read_bytes(path, _MAX_PACKET_BYTES)
    except OSError:
        raise ValueError("cannot read turn-fork packet") from None
    if sha256(raw).hexdigest() != expected:
        raise ValueError("turn-fork packet SHA-256 mismatch")
    return _validate(_decode(raw, integers_only=True))
