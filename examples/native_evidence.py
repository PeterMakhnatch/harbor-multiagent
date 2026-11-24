import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any, cast

Record = dict[str, Any]


class Incomplete(Exception):
    pass


class Invalid(Exception):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise Invalid(reason)


def require_usage(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def read_bytes(trial: Path, parts: tuple[str, ...], limit: int) -> bytes:
    assert parts and limit > 0
    label = "/".join(parts)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    opened: list[int] = []
    try:
        opened.append(os.open(trial, flags | os.O_DIRECTORY))
        for name in parts[:-1]:
            opened.append(os.open(name, flags | os.O_DIRECTORY, dir_fd=opened[-1]))
        fd = os.open(parts[-1], flags, dir_fd=opened[-1])
    except FileNotFoundError:
        raise Incomplete(f"{label} is missing") from None
    except OSError:
        raise Invalid(f"{label} is not reachable through plain directories") from None
    finally:
        for descriptor in opened:
            os.close(descriptor)
    with open(fd, "rb") as handle:
        require(
            stat.S_ISREG(os.fstat(handle.fileno()).st_mode),
            f"{label} is not a regular file",
        )
        data = handle.read(limit + 1)
    require(len(data) <= limit, f"{label} exceeds {limit} bytes")
    return data


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def _constant(name: str) -> None:
    raise ValueError(f"non-finite JSON number {name}")


def strict_object(data: bytes, label: str) -> Record:
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Invalid(f"{label} is not strict JSON") from None
    require(type(value) is dict, f"{label} is not a JSON object")
    return value


def same(left: object, right: object) -> bool:
    stack = [(left, right)]
    while stack:
        first, second = stack.pop()
        if type(first) is not type(second):
            return False
        if type(first) is dict:
            left_map = cast(dict[str, object], first)
            right_map = cast(dict[str, object], second)
            if left_map.keys() != right_map.keys():
                return False
            stack.extend((left_map[key], right_map[key]) for key in left_map)
        elif type(first) is list:
            left_list = cast(list[object], first)
            right_list = cast(list[object], second)
            if len(left_list) != len(right_list):
                return False
            stack.extend(zip(left_list, right_list))
        elif first != second:
            return False
    return True


def canonical_uuid(value: object, version: int | None = None) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and (version is None or parsed.version == version)


def seat_map(
    participants: object, label: str, roles: tuple[str, ...]
) -> dict[str, Record]:
    assert len(roles) == 2 and roles[0] != roles[1]
    require(
        type(participants) is list and len(cast(list[object], participants)) == 2,
        f"{label} is not two seats",
    )
    seats: dict[str, Record] = {}
    for participant in cast(list[object], participants):
        require(isinstance(participant, dict), f"{label} has a non-object seat")
        seat = cast(Record, participant).get("seat")
        require(
            isinstance(seat, str) and seat not in seats,
            f"{label} has a bad or repeated seat",
        )
        seats[cast(str, seat)] = {
            key: value
            for key, value in cast(Record, participant).items()
            if value is not None
        }
    require(set(seats) == set(roles), f"{label} does not bind {' and '.join(roles)}")
    return seats


def check_binding(
    record: object,
    env: dict[str, str],
    team: dict[str, Record],
    roles: tuple[str, ...],
    label: str,
) -> None:
    require(type(record) is dict, f"{label} is not an object")
    environment = cast(Record, record).get("environment")
    require(type(environment) is dict, f"{label} has no environment")
    require(
        same(cast(Record, environment).get("env"), env),
        f"{label} env is not the cell",
    )
    seats = seat_map(
        cast(Record, record).get("participants"), f"{label} participants", roles
    )
    require(same(seats, team), f"{label} participants differ from the registration")


def native_reward(result: Record) -> int:
    verifier = result.get("verifier_result")
    require(type(verifier) is dict, "native verifier result is missing")
    rewards = cast(Record, verifier).get("rewards")
    require(
        type(rewards) is dict and set(cast(Record, rewards)) == {"reward"},
        "native rewards are not one reward",
    )
    reward = cast(Record, rewards)["reward"]
    require(
        type(reward) in (int, float) and reward in (0, 1),
        "native reward is not binary",
    )
    return int(reward)
