"""Screen the registered four-seat peer fan-in cohort for skippable reporters.

Exit 0 when the registered screen passes, 1 when it fails with a valid public
result, and 2 when the task input is invalid or incomplete.
"""

import argparse
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path

from ..visibility import calendar_packet as packet
from . import protocol

SCHEMA = "seatlab.peer-fanin-screen/v1"
GENERATOR_COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
SEED = 1709000311
WORLDS = tuple(range(protocol.FIRST, protocol.FIRST + protocol.COUNT))
EARLY_WORLDS = WORLDS[:8]
THRESHOLDS = {"first8_skippable": 2, "turns_saved": 16, "worlds_skippable": 12}
INTERPRETATION = (
    "Hypothetical truthful complete reports: each reporter, in cyclic order after "
    "the booker, returns exactly its own feasible two-hour starts. prefix_sizes "
    "count the starts left after 0, 1, 2 and 3 reports. No model ran; these are "
    "opportunities, not measured savings."
)
STOCK_SHA256 = {
    "environment/family.py": "1df395b550107d842f460e2ee9bb3cc63cab16375e9efbf8047c621e1249c039",
    "environment/score.py": "6d2164a6f4e93dacf2cb5510dfd0dd6f0dbfe7f1d96e846fdd52303565f1f9df",
    "environment/world.py": "2d7e44e424a32ff6d5c6b7db299a93c58dab02779d94582ab8662bb66e1c1ce6",
}

Invalid = packet._Invalid
Calendars = dict[str, dict[str, list[int]]]

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_NAMES = frozenset(
    {"Ana", "Ben", "Chen", "Dara", "Eli", "Fay"}
    | {"Gus", "Hana", "Ivo", "Jun", "Kai", "Lea"}
)
_SEAT_PEOPLE = (2, 2, 1, 1)
_MIN_SEAT_STARTS = 4
_MAX_FILE_BYTES = 1 << 16
_ANSWER_START = re.compile(r"(09|1[0-5]):00", re.ASCII)
_LIMITS: dict[str, object] = {"max_messages": 200, "max_text": 4000}
_COHORT = {"condition": "talk", "difficulty": "easy", "family": "schedule", **_LIMITS}
_COHORT |= {"seed": SEED, "variant": ""}
_PAYLOAD = {**_COHORT, "seats": list(protocol.SEATS), "world_version": 2}
_METADATA = {**_COHORT, "n_seats": 4, "team_seats": 4, "play_timeout_sec": 600.0}
_MAIN_ENV = {f"FLOTILLA_{key.upper()}": str(value) for key, value in _LIMITS.items()}
_MAIN_ENV |= {"FLOTILLA_CONDITION": "talk", "FLOTILLA_SEATS": "p1,p2,p3,p4"}
_SEAT_ENV = frozenset({"SEAT", "SEAT_BRIEFING", "SEAT_TOKEN", "WORLD_URL"})


@dataclass(frozen=True, slots=True)
class WorldScreen:
    world: int
    booker: str
    reporters: tuple[str, str, str]
    prefix_sizes: tuple[int, int, int, int]
    reports_needed: int
    calendar_sha256: str

    def public(self) -> dict[str, object]:
        skippable = len(self.reporters) - self.reports_needed
        return {**asdict(self), "reporters_skippable": skippable}


@dataclass(frozen=True, slots=True)
class Screen:
    worlds: tuple[WorldScreen, ...]
    source_sha256: str

    def totals(self) -> dict[str, int]:
        if tuple(row.world for row in self.worlds) != WORLDS:
            raise Invalid("screen does not cover every registered world")
        all_turns = sum(len(row.reporters) for row in self.worlds)
        needed = sum(row.reports_needed for row in self.worlds)
        skip = [row.world for row in self.worlds if row.reports_needed < 3]
        return {
            "first8_skippable": sum(world in EARLY_WORLDS for world in skip),
            "reporter_turns_all": all_turns,
            "reporter_turns_needed": needed,
            "turns_saved": all_turns - needed,
            "worlds": len(self.worlds),
            "worlds_skippable": len(skip),
        }

    def failed_thresholds(self) -> list[str]:
        totals = self.totals()
        return [name for name, least in THRESHOLDS.items() if totals[name] < least]

    @property
    def passed(self) -> bool:
        return not self.failed_thresholds()

    def public(self) -> dict[str, object]:
        registration = {**_PAYLOAD, "play_timeout_sec": 600.0}
        registration["worlds"] = [WORLDS[0], WORLDS[-1]]
        return {
            "failed_thresholds": self.failed_thresholds(),
            "generator_commit": GENERATOR_COMMIT,
            "interpretation": INTERPRETATION,
            "passed": self.passed,
            "registration": registration,
            "schema": SCHEMA,
            "screen_sha256": self.source_sha256,
            "stock_sha256": dict(STOCK_SHA256),
            "thresholds": dict(THRESHOLDS),
            "totals": self.totals(),
            "worlds": [row.public() for row in self.worlds],
        }


def roles(world: int) -> tuple[str, tuple[str, str, str]]:
    try:
        booker, first, second, third = protocol.order(world)
    except ValueError:
        raise Invalid("world is outside the registered cohort") from None
    return booker, (first, second, third)


def seat_starts(calendars: Mapping[str, Mapping[str, Sequence[int]]]) -> frozenset[int]:
    found = set()
    for day_index, day in enumerate(_DAYS):
        busy = {hour for week in calendars.values() for hour in week[day]}
        for offset in range(7):
            if 9 + offset not in busy and 10 + offset not in busy:
                found.add(day_index * 7 + offset)
    return frozenset(found)


def calendar_sha256(calendars: Sequence[Calendars]) -> str:
    return packet._digest(
        protocol.wire(dict(zip(protocol.SEATS, calendars, strict=True))).encode()
    )


def world_source(task: Path, world: int) -> tuple[dict[str, tuple[int, ...]], int, str]:
    calendars, starts, answer = _checked_world(task, world)
    found = {seat: tuple(sorted(starts[seat])) for seat in protocol.SEATS}
    return found, answer, calendar_sha256(calendars)


def screen(tasks: Path) -> Screen:
    packet._directory(tasks)
    names = {f"schedule-{world}-talk": world for world in WORLDS}
    present = _entries(tasks)
    missing = sorted(world for name, world in names.items() if name not in present)
    if missing:
        listed = ",".join(map(str, missing))
        raise Invalid(f"incomplete coverage; missing registered worlds {listed}")
    if present != names.keys():
        raise Invalid("task root holds entries outside the registered cohort")
    rows = tuple(_screen_world(tasks / name, world) for name, world in names.items())
    if len({row.calendar_sha256 for row in rows}) != len(rows):
        raise Invalid("registered cohort repeats a calendar world")
    return Screen(rows, packet._digest(packet._read(Path(__file__))))


def render(result: Screen) -> bytes:
    return (protocol.wire(result.public()) + "\n").encode("ascii")


def _entries(directory: Path) -> set[str]:
    try:
        with os.scandir(directory) as scan:
            entries = list(islice(scan, 4 * len(WORLDS) + 1))
    except OSError:
        raise Invalid("cannot list task root") from None
    if len(entries) > 4 * len(WORLDS) or not all(
        entry.is_dir(follow_symlinks=False) for entry in entries
    ):
        raise Invalid("task root holds unexpected entries")
    return {entry.name for entry in entries}


def _screen_world(task: Path, world: int) -> WorldScreen:
    calendars, starts, answer = _checked_world(task, world)
    booker, reporters = roles(world)
    joint = set(starts[booker])
    sizes = [len(joint)]
    for reporter in reporters:
        joint &= starts[reporter]
        sizes.append(len(joint))
    if joint != {answer} or sizes[0] == 1:
        raise Invalid(f"world {world}: reporter prefixes contradict the stored key")
    needed = sizes.index(1)
    return WorldScreen(
        world=world,
        booker=booker,
        reporters=reporters,
        prefix_sizes=(sizes[0], sizes[1], sizes[2], sizes[3]),
        reports_needed=needed,
        calendar_sha256=calendar_sha256(calendars),
    )


def _checked_world(
    task: Path, world: int
) -> tuple[tuple[Calendars, ...], dict[str, frozenset[int]], int]:
    try:
        roles(world)
        packet._directory(task)
        packet._directory(task / "environment")
        for relative, digest in STOCK_SHA256.items():
            if packet._digest(packet._read(task / relative, _MAX_FILE_BYTES)) != digest:
                raise Invalid(f"{relative} differs from the pinned stock source")
        _check_toml(_text(task / "task.toml"), world)
        answer = _check_payload(_read_json(task / "environment/task.json"), world)
        calendars = _compose_calendars(_text(task / "environment/docker-compose.yaml"))
        starts = dict(zip(protocol.SEATS, map(seat_starts, calendars), strict=True))
        if min(map(len, starts.values())) < _MIN_SEAT_STARTS:
            raise Invalid("a seat has fewer starts than the stock minimum")
        if frozenset.intersection(*starts.values()) != {answer}:
            raise Invalid("seats do not share exactly the stored key")
    except Invalid as error:
        raise Invalid(f"world {world}: {error}") from None
    return calendars, starts, answer


def _text(path: Path) -> str:
    try:
        return packet._read(path, _MAX_FILE_BYTES).decode("utf-8")
    except UnicodeError:
        raise Invalid("invalid source encoding") from None


def _read_json(path: Path) -> object:
    return packet._json(packet._read(path, _MAX_FILE_BYTES))


def _fields(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    fields = {key: item for key, item in value.items() if isinstance(key, str)}
    return fields if len(fields) == len(value) else None


def _mismatch(value: object, expected: Mapping[str, object]) -> str | None:
    fields = _fields(value)
    if fields is None or fields.keys() != expected.keys():
        return "fields"
    for key, want in expected.items():
        if type(fields[key]) is not type(want) or fields[key] != want:
            return key
    return None


def _check_toml(text: str, world: int) -> None:
    try:
        config = tomllib.loads(text)
    except (ValueError, RecursionError):
        raise Invalid("invalid task TOML") from None
    field = _mismatch(config.get("metadata"), {**_METADATA, "instance": world})
    if field is not None:
        raise Invalid(f"task.toml metadata {field} differs")
    task, agent = config.get("task"), config.get("agent")
    if (
        config.get("schema_version") != "1.4"
        or not isinstance(task, dict)
        or task.get("name") != f"flotilla/schedule-{world}-talk"
        or _mismatch(agent, {"timeout_sec": 600.0}) is not None
        or config.get("agents") != {seat: {} for seat in protocol.SEATS}
    ):
        raise Invalid("task.toml differs from the registered cohort")


def _check_payload(value: object, world: int) -> int:
    fields = _fields(value)
    if fields is None or "answer_key" not in fields:
        raise Invalid("task.json lacks an answer key")
    rest = {key: item for key, item in fields.items() if key != "answer_key"}
    field = _mismatch(rest, {**_PAYLOAD, "instance": world})
    if field is not None:
        raise Invalid(f"task.json {field} differs")
    key = _fields(fields["answer_key"])
    if key is None or key.keys() != {"day", "start"}:
        raise Invalid("stored answer key is malformed")
    day, start = key["day"], key["start"]
    match = _ANSWER_START.fullmatch(start) if isinstance(start, str) else None
    if not isinstance(day, str) or day not in _DAYS or match is None:
        raise Invalid("stored answer key is malformed")
    return _DAYS.index(day) * 7 + int(match[1]) - 9


def _compose_calendars(text: str) -> tuple[Calendars, ...]:
    top = packet._compose_map([line for line in text.splitlines() if line.strip()], 0)
    if top.keys() != {"x-player", "services", "networks"}:
        raise Invalid("unexpected generated Compose layout")
    services = packet._compose_block(top, "services", 2)
    if services.keys() != {"main", *protocol.SEATS}:
        raise Invalid("unexpected generated Compose services")
    main_service = packet._compose_block(services, "main", 4)
    main = packet._compose_block(main_service, "environment", 6)
    for key, want in _MAIN_ENV.items():
        if packet._json(packet._compose_scalar(main, key)) != want:
            raise Invalid(f"Compose {key} differs from the registered cohort")
    calendars = []
    for seat in protocol.SEATS:
        service = packet._compose_block(services, seat, 4)
        env = packet._compose_block(service, "environment", 6)
        identity = packet._json(packet._compose_scalar(env, "SEAT"))
        if env.keys() != _SEAT_ENV or identity != seat:
            raise Invalid("Compose seat identity does not match its service")
        encoded = packet._json(packet._compose_scalar(env, "SEAT_BRIEFING"))
        if not isinstance(encoded, str):
            raise Invalid("expected a JSON string for seat briefing")
        calendars.append(_calendars(packet._json(encoded)))
    people = {name for seat_calendars in calendars for name in seat_calendars}
    if tuple(map(len, calendars)) != _SEAT_PEOPLE or len(people) != sum(_SEAT_PEOPLE):
        raise Invalid("seat attendee split differs from stock")
    return tuple(calendars)


def _calendars(value: object) -> Calendars:
    people = packet._object(value, "calendars")["calendars"]
    if not isinstance(people, dict) or not people.keys() <= _NAMES:
        raise Invalid("seat briefing attendees differ from stock")
    calendars: Calendars = {}
    for name, week in people.items():
        calendars[name] = {}
        for day, hours in packet._object(week, *_DAYS).items():
            if not isinstance(hours, list):
                raise Invalid("calendar hours differ from stock")
            busy = [
                hour
                for hour in hours
                if isinstance(hour, int) and not isinstance(hour, bool)
            ]
            if (
                len(busy) != len(hours)
                or any(not 9 <= hour <= 16 for hour in busy)
                or busy != sorted(set(busy))
            ):
                raise Invalid("calendar hours differ from stock")
            calendars[name][day] = busy
    return calendars


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -BS -m examples.seatlab.fanin.screen",
        description=(__doc__ or "").strip(),
    )
    parser.add_argument("tasks", type=Path, metavar="TASKS")
    args = parser.parse_args(argv)
    try:
        result = screen(args.tasks)
        output = render(result)
    except Invalid as error:
        print(f"peer-fanin screen: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
