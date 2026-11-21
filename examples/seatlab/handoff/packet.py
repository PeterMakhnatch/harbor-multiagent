import hashlib
import json
import re
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class Record:
    id: str
    old_version: str
    new_version: str
    before: str
    after: str
    question: str
    old_best: str
    best: str


@dataclass(frozen=True)
class Case:
    id: str
    cluster: str
    records: tuple[Record, ...]


@dataclass(frozen=True)
class Packet:
    brief: str
    choices: tuple[str, ...]
    cases: tuple[Case, ...]
    sha256: str


@dataclass(frozen=True)
class Block:
    packet: Packet
    index: int
    rep: int

    @property
    def case(self) -> Case:
        return self.packet.cases[self.index]

    @property
    def key(self) -> str:
        return f"ho-{self.fingerprint[:12]}-{self.case.id}-r{self.rep}"

    @property
    def fingerprint(self) -> str:
        return self.packet.sha256


_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,12}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_RECORD_FIELDS = {
    "id",
    "old_version",
    "new_version",
    "before",
    "after",
    "question",
    "old_best",
    "best",
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate packet field")
    return fields


def _fields(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError("unexpected packet fields")
    return cast(dict[str, object], value)


def _items(value: object, minimum: int, maximum: int) -> list[object]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("invalid packet array length or type")
    return cast(list[object], value)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("invalid record ID or choice")
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or len(value) > 20 or _SLUG.fullmatch(value) is None:
        raise ValueError("invalid case or cluster slug")
    return value


def _text(value: object, limit: int, *, printable: bool = True) -> str:
    if not isinstance(value, str) or (printable and not value.isprintable()):
        raise ValueError("invalid packet text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("invalid packet text encoding") from None
    if size > limit:
        raise ValueError("packet text exceeds byte limit")
    return value


def _record(value: object, choices: tuple[str, ...]) -> Record:
    fields = _fields(value, _RECORD_FIELDS)
    record = Record(
        id=_identifier(fields["id"]),
        old_version=_text(fields["old_version"], 64, printable=False),
        new_version=_text(fields["new_version"], 64, printable=False),
        before=_text(fields["before"], 256),
        after=_text(fields["after"], 256),
        question=_text(fields["question"], 256),
        old_best=_identifier(fields["old_best"]),
        best=_identifier(fields["best"]),
    )
    if record.old_best not in choices or record.best not in choices:
        raise ValueError("record answer is not a packet choice")
    if record.old_version == record.new_version and (
        record.before != record.after or record.old_best != record.best
    ):
        raise ValueError("unchanged version has different facts or answers")
    return record


def _case(value: object, choices: tuple[str, ...]) -> Case:
    fields = _fields(value, {"id", "cluster", "records"})
    records = tuple(_record(row, choices) for row in _items(fields["records"], 2, 8))
    if len({record.id for record in records}) != len(records):
        raise ValueError("duplicate record ID")
    if sum(record.old_version != record.new_version for record in records) > 1:
        raise ValueError("case has more than one revised version")
    return Case(_slug(fields["id"]), _slug(fields["cluster"]), records)


def parse(raw: bytes, expected: str | None = None) -> Packet:
    sha256 = hashlib.sha256(raw).hexdigest()
    if expected is not None and sha256 != expected:
        raise ValueError("packet fingerprint mismatch")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise ValueError("invalid packet JSON") from None
    fields = _fields(value, {"schema", "brief", "choices", "cases"})
    if fields["schema"] != "seatlab.handoff-input/v1":
        raise ValueError("unsupported packet schema")
    brief = _text(fields["brief"], 256)
    choices = tuple(_identifier(choice) for choice in _items(fields["choices"], 2, 16))
    if len(set(choices)) != len(choices):
        raise ValueError("duplicate packet choice")
    cases = tuple(_case(case, choices) for case in _items(fields["cases"], 1, 256))
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate case ID")
    return Packet(brief, choices, cases, sha256)


def grid(packet: Packet) -> tuple[Block, ...]:
    return tuple(
        Block(packet, index, rep)
        for index in range(len(packet.cases))
        for rep in range(2)
    )
