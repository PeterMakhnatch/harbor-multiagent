import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...seat_checkpoints.materialize import sync_directory
from ...ship_inflight.materialize import publish
from ..convention_crossplay import ballot_cohort, ballot_deck, loader
from ..convention_crossplay.records import team_name
from ..coupled_retention import strategy
from ..coupled_retention.native import MAX_SEED
from ..exhibit_enroll import target, write_private
from .engine import OPTIONS, ROLES

SCHEMA = "seatlab.partner-switch-study/v1"
CAPTURE_DOMAIN = b"seatlab.partner-switch-captures/v1\x00"
MAX_STUDY_BYTES = 32 << 10
Key = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Deck:
    folder: str
    pin: str
    packets: set[bytes]


@dataclass(frozen=True, slots=True)
class Plan:
    stage: str
    start: int
    names: tuple[str, ...]
    parents: dict[str, str]
    pin: str
    decks: dict[Key, Deck]


def _invalid() -> ValueError:
    return ValueError("invalid partner-switch study or capture")


def _encode(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("ascii")


def _settings(
    stage: object, start: object, names: object, parents: object
) -> tuple[str, int, tuple[str, ...], dict[str, str]]:
    if (
        type(stage) is not str
        or stage not in ("report", "vote")
        or type(start) is not int
        or not 0 <= start <= MAX_SEED
        or type(names) is not tuple
        or not 2 <= len(names) <= 6
    ):
        raise _invalid()
    roster = tuple(team_name(name) for name in names)
    if len(set(roster)) != len(roster):
        raise _invalid()
    fields = loader._fields(
        parents,
        set()
        if stage == "report"
        else {"report_plan_sha256", "report_captures_sha256"},
    )
    pins = {key: ballot_cohort._pin(value) for key, value in fields.items()}
    return stage, start, roster, pins


def _folders(names: tuple[str, ...]) -> dict[Key, str]:
    return {
        (name, role): f"t{index:02}-{role}"
        for index, name in enumerate(names)
        for role in ROLES
    }


def _directory(path: Path) -> int:
    if path.name in ("", ".", "..", "-"):
        raise _invalid()
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read(directory: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise _invalid()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) != info.st_size:
            raise _invalid()
        return raw
    finally:
        os.close(fd)


def write(
    root: Path | str,
    stage: str,
    start: int,
    names: tuple[str, ...],
    parents: dict[str, str],
    packets: dict[Key, set[bytes]],
) -> dict[str, Any]:
    stage, start, names, parents = _settings(stage, start, names, parents)
    folders = _folders(names)
    if type(packets) is not dict or packets.keys() != folders.keys():
        raise _invalid()
    out = target(Path(root))
    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix=".seatlab-partner-switch-", dir=out.parent
    ) as temp:
        staged = Path(temp) / "study"
        staged.mkdir(mode=0o700)
        os.chmod(staged, 0o700)
        pins: set[str] = set()
        for (team, role), folder in folders.items():
            prompts = packets[team, role]
            manifest, requests = ballot_deck.make(role, OPTIONS, prompts, "slot")
            pin = loader.digest(manifest)
            if pin in pins:
                raise _invalid()
            pins.add(pin)
            directory = staged / folder
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
            write_private(directory / "deck.json", manifest)
            write_private(directory / "requests.jsonl", requests)
            sync_directory(directory)
            entries.append(
                {
                    "team": team,
                    "role": role,
                    "folder": folder,
                    "deck_sha256": pin,
                    "count": len(prompts),
                }
            )
        study = {
            "schema": SCHEMA,
            "stage": stage,
            "start": start,
            "names": names,
            "parents": parents,
            "decks": entries,
        }
        raw = _encode(study)
        if len(raw) > MAX_STUDY_BYTES:
            raise _invalid()
        pin = loader.digest(raw)
        write_private(staged / "study.json", raw)
        load(staged, pin)
        sync_directory(staged)
        publish(staged, out)
        sync_directory(out.parent)
    return {**study, "study_sha256": pin}


def _deck(
    directory: int,
    role: str,
    folder: str,
    pin: str,
    count: int,
    tickets: set[str],
) -> Deck:
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        manifest = _read(fd, "deck.json", ballot_deck.MAX_MANIFEST_BYTES)
        requests = _read(fd, "requests.jsonl", ballot_deck.MAX_REQUESTS_BYTES)
    finally:
        os.close(fd)
    actual_role, actions, field, roster = ballot_deck.parse(manifest, requests, pin)
    if (
        (actual_role, actions, field) != (role, OPTIONS, "slot")
        or len(roster) != count
        or not tickets.isdisjoint(roster)
    ):
        raise _invalid()
    tickets.update(roster)
    return Deck(folder, pin, {packet for _, packet in roster.values()})


def _load(directory: int, expected_pin: str) -> Plan:
    raw = _read(directory, "study.json", MAX_STUDY_BYTES)
    if loader.digest(raw) != ballot_cohort._pin(expected_pin):
        raise _invalid()
    fields = loader._fields(
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=loader._unique,
            parse_constant=loader._reject,
        ),
        {"schema", "stage", "start", "names", "parents", "decks"},
    )
    if fields["schema"] != SCHEMA or type(fields["names"]) is not list:
        raise _invalid()
    stage, start, names, parents = _settings(
        fields["stage"], fields["start"], tuple(fields["names"]), fields["parents"]
    )
    folders = _folders(names)
    entries = fields["decks"]
    if type(entries) is not list or len(entries) != len(folders):
        raise _invalid()
    decks: dict[Key, Deck] = {}
    pins: set[str] = set()
    tickets: set[str] = set()
    for ((name, role), folder), entry in zip(folders.items(), entries, strict=True):
        entry = loader._fields(
            entry, {"team", "role", "folder", "deck_sha256", "count"}
        )
        pin = ballot_cohort._pin(entry["deck_sha256"])
        count = entry["count"]
        if (
            entry["team"] != name
            or entry["role"] != role
            or entry["folder"] != folder
            or pin in pins
            or type(count) is not int
            or not 1 <= count <= strategy.MAX_OBSERVATIONS
        ):
            raise _invalid()
        pins.add(pin)
        decks[name, role] = _deck(directory, role, folder, pin, count, tickets)
    return Plan(stage, start, names, parents, expected_pin, decks)


def load(root: Path | str, expected_pin: str) -> Plan:
    try:
        fd = _directory(Path(root))
        try:
            return _load(fd, expected_pin)
        finally:
            os.close(fd)
    except (OSError, ValueError, RecursionError):
        raise _invalid() from None


def read_capture(
    plan: Plan, fd: int, name: str, role: str
) -> tuple[dict[str, str], str]:
    key = name, role
    if name not in plan.names or role not in ROLES or key not in plan.decks:
        raise _invalid()
    deck = plan.decks[key]
    folder = f"t{plan.names.index(name):02}-{role}"
    if deck.folder != folder:
        raise _invalid()
    seen: set[str] = set()
    table, pin = ballot_cohort._role(folder, name, role, seen, parent_fd=fd)
    if seen != {deck.pin} or len(table) != len(deck.packets):
        raise _invalid()
    for packet in deck.packets:
        choice = table.get(strategy.digest(packet))
        if choice not in OPTIONS:
            raise _invalid()
        if plan.stage == "report":
            view = json.loads(packet)
            if (
                type(view) is not dict
                or view.get("role") != role
                or view.get("stage") != "report"
                or type(view.get("own")) is not list
                or choice not in view["own"]
            ):
                raise _invalid()
    return table, pin


def recover(plan: Plan, root: Path | str) -> tuple[dict[Key, dict[str, str]], str]:
    path = Path(root)
    rules: dict[Key, dict[str, str]] = {}
    evidence: list[list[str]] = []
    try:
        fd = _directory(path)
        try:
            for name, role in plan.decks:
                table, pin = read_capture(plan, fd, name, role)
                rules[name, role] = table
                evidence.append([name, role, pin])
        finally:
            os.close(fd)
    except (OSError, ValueError, RecursionError):
        raise _invalid() from None
    capture_pin = loader.digest(CAPTURE_DOMAIN + _encode([plan.pin, evidence]))
    return rules, capture_pin
