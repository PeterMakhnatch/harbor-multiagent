import hashlib
import json
import os
import re
import stat
from typing import Never, cast

from ..coupled_retention import strategy
from .records import Team, team_name

SCHEMA = "seatlab.crossplay-roster/v1"
MAX_FILE_BYTES = 512 * 1024


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _invalid() -> ValueError:
    return ValueError("invalid crossplay roster or pinned strategy")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _invalid()
    return result


def _reject(_: str) -> Never:
    raise _invalid()


def _fields(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or value.keys() != expected:
        raise _invalid()
    return cast(dict[str, object], value)


def _basename(value: object) -> str:
    if (
        type(value) is not str
        or value in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None
    ):
        raise _invalid()
    return value


def _read(directory: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise _invalid()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) != info.st_size or len(raw) > MAX_FILE_BYTES:
            raise _invalid()
        return raw
    finally:
        os.close(fd)


def _rules(directory: int, reference: object, role: str) -> dict[str, str]:
    fields = _fields(reference, {"file", "sha256"})
    name = _basename(fields["file"])
    pin = fields["sha256"]
    if type(pin) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", pin) is None:
        raise _invalid()
    raw = _read(directory, name)
    if digest(raw) != pin:
        raise _invalid()
    return strategy.load(raw, role)


def _parse(directory: int, raw: bytes) -> list[Team]:
    parsed: object = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject
    )
    fields = _fields(parsed, {"schema", "teams"})
    entries = fields["teams"]
    if (
        fields["schema"] != SCHEMA
        or type(entries) is not list
        or not 2 <= len(entries) <= 12
    ):
        raise _invalid()
    names: set[str] = set()
    teams: list[Team] = []
    for entry in entries:
        fields = _fields(entry, {"name", "east", "west"})
        name = team_name(fields["name"])
        if name in names:
            raise _invalid()
        names.add(name)
        teams.append(
            Team(
                name,
                _rules(directory, fields["east"], "east"),
                _rules(directory, fields["west"], "west"),
            )
        )
    return teams


def load(path: str, expected_digest: str | None = None) -> list[Team]:
    if type(path) is not str or not path or path == "-":
        raise _invalid()
    parent, name = os.path.split(path)
    if not name:
        raise _invalid()
    try:
        directory = os.open(parent or ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            raw = _read(directory, name)
            if expected_digest is not None and expected_digest != digest(raw):
                raise _invalid()
            return _parse(directory, raw)
        finally:
            os.close(directory)
    except (OSError, ValueError, RecursionError):
        raise _invalid() from None
