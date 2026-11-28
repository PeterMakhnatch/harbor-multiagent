import json
import os
import re
from typing import TypedDict

from ..coupled_retention import strategy
from . import loader
from .records import Team, team_name

RECEIPT_SCHEMA = "seatlab.role-ballot-result/v1"
COHORT_DOMAIN = b"seatlab.recorded-team-cohort/v1\x00"
COMPLETE_COUNTS = ("expected", "represented", "valid")
ZERO_COUNTS = ("missing", "duplicate_ids", "stale_ids", "invalid_ids", "unknown_rows")


class CohortEvidence(TypedDict):
    unit: str
    cohort_sha256: str
    teams: list[dict[str, str]]


def _invalid() -> ValueError:
    return ValueError("invalid recorded-team cohort or role evidence")


def _pin(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise _invalid()
    return value


def _read_pair(path: str) -> tuple[bytes, bytes]:
    if type(path) is not str or not path or path == "-":
        raise _invalid()
    path = path.rstrip("/")
    if os.path.basename(path) in ("", ".", "..", "-"):
        raise _invalid()
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return loader._read(directory, "evidence.json"), loader._read(
            directory, "strategy.json"
        )
    finally:
        os.close(directory)


def _counts(value: object, length: int) -> None:
    counts = loader._fields(value, set(COMPLETE_COUNTS + ZERO_COUNTS))
    if not 1 <= length <= 256:
        raise _invalid()
    if any(type(n) is not int for n in counts.values()):
        raise _invalid()
    if any(counts[key] != length for key in COMPLETE_COUNTS):
        raise _invalid()
    if any(counts[key] != 0 for key in ZERO_COUNTS):
        raise _invalid()


def _role(
    path: str, name: str, role: str, decks: set[str]
) -> tuple[dict[str, str], str]:
    evidence, table = _read_pair(path)
    receipt = loader._fields(
        json.loads(
            evidence.decode("utf-8"),
            object_pairs_hook=loader._unique,
            parse_constant=loader._reject,
        ),
        {
            "schema",
            "role",
            "declared_source",
            "deck_sha256",
            "reply_shard_sha256",
            "counts",
            "complete",
            "table_sha256",
            "scope",
        },
    )
    if (
        receipt["schema"] != RECEIPT_SCHEMA
        or receipt["role"] != role
        or receipt["declared_source"] != name
        or receipt["complete"] is not True
        or _pin(receipt["table_sha256"]) != loader.digest(table)
    ):
        raise _invalid()
    scope = receipt["scope"]
    if type(scope) is not str or not 1 <= len(scope) <= 512:
        raise _invalid()
    shards = receipt["reply_shard_sha256"]
    if type(shards) is not list or not 1 <= len(shards) <= 32:
        raise _invalid()
    pins = [_pin(shard) for shard in shards]
    if pins != sorted(pins):
        raise _invalid()
    deck = _pin(receipt["deck_sha256"])
    if deck in decks:
        raise _invalid()
    rules = strategy.load(table, role)
    _counts(receipt["counts"], len(rules))
    decks.add(deck)
    return rules, loader.digest(evidence)


def load(entries: list[list[str]]) -> tuple[list[Team], CohortEvidence]:
    if type(entries) is not list or not 2 <= len(entries) <= 12:
        raise _invalid()
    teams: list[Team] = []
    names: set[str] = set()
    decks: set[str] = set()
    pins: list[list[str]] = []
    try:
        for entry in entries:
            if type(entry) is not list or len(entry) != 3:
                raise _invalid()
            name = team_name(entry[0])
            if name in names:
                raise _invalid()
            names.add(name)
            east, east_pin = _role(entry[1], name, "east", decks)
            west, west_pin = _role(entry[2], name, "west", decks)
            teams.append(Team(name, east, west))
            pins.append([name, east_pin, west_pin])
    except (OSError, ValueError, RecursionError):
        raise _invalid() from None
    cohort_hash = loader.digest(
        COHORT_DOMAIN + json.dumps(pins, separators=(",", ":")).encode("ascii")
    )
    return teams, {
        "unit": "recorded_team",
        "cohort_sha256": cohort_hash,
        "teams": [
            {"name": name, "east_evidence_sha256": east, "west_evidence_sha256": west}
            for name, east, west in pins
        ],
    }
