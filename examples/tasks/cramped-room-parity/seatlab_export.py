"""Export client-bound Cramped Room episodes from one completed Harbor trial."""

import argparse
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import UUID

HORIZON = 38
SEATS = ("chef0", "chef1")
ENGINE = "HumanCompatibleAI/overcooked_ai@739950a079cdaed5a44fcc662efc40244c205d06"
PEER_TASK = "fa357189438d0d8c7d2ff4f97b00afb83d75dfa5"
WORLD_LOG = "artifacts/logs/world/kitchen.jsonl"
ACK_LOG = "artifacts/logs/world/seatlab-acks.jsonl"
REWARD_FILE = "verifier/reward.json"
DIRECTIONS = {
    (0, -1): "north",
    (0, 1): "south",
    (1, 0): "east",
    (-1, 0): "west",
    (0, 0): "stay",
}
TRANSITION_FIELDS = {
    "tick",
    "joint_action",
    "state",
    "reward",
    "sparse_reward_by_agent",
    "shaped_reward_by_agent",
    "soup_delivery_by_agent",
}
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_JSONObject = dict[str, object]


class _Frame(TypedDict):
    turn: int
    basis: Literal["bound"]
    view: _JSONObject
    view_sha256: str
    observed: dict[str, str]


class _ExportSummary(TypedDict):
    native_trial_id: str
    case_sha256: str
    reward: int
    deliveries_by_seat: dict[str, int]
    episodes: int
    turns_per_role: int
    episode_sha256: dict[str, str]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> _JSONObject:
    result: _JSONObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON number")


def _finite_float(token: str) -> float:
    number = float(token)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _finite_decimal(token: str) -> Decimal:
    _finite_float(token)
    try:
        return Decimal(token)
    except InvalidOperation as exc:
        raise ValueError("invalid JSON number") from exc


def _check_strings(value: object) -> None:
    if isinstance(value, str) and _SURROGATE.search(value):
        raise ValueError("unpaired Unicode surrogate")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_strings(key)
            _check_strings(item)
    elif isinstance(value, list):
        for item in value:
            _check_strings(item)


def _decode_object(raw: bytes, where: str, *, canonical: bool = False) -> _JSONObject:
    try:
        value: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float if canonical else _finite_decimal,
        )
        _check_strings(value)
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        if canonical and raw != _canonical(value):
            raise ValueError("expected canonical ASCII JSON")
        return cast(_JSONObject, value)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{where}: {exc}") from exc


@contextmanager
def _directory(path: str | Path, *, dir_fd: int | None = None) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def _read_artifact(trial_fd: int, relative: str) -> bytes:
    *parents, name = relative.split("/")
    with ExitStack() as stack:
        directory = trial_fd
        for parent in parents:
            directory = stack.enter_context(_directory(parent, dir_fd=directory))
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(name, flags, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(
                    f"{relative}: expected a regular artifact file with one link"
                )
            return source.read()


def _read_rows(trial_fd: int, relative: str) -> list[_JSONObject]:
    lines = _read_artifact(trial_fd, relative).split(b"\n")
    if len(lines) != HORIZON + 1 or lines[-1] != b"":
        raise ValueError(
            f"{relative}: expected exactly {HORIZON} newline-terminated rows"
        )
    return [
        _decode_object(line, f"{relative} line {index}", canonical=True)
        for index, line in enumerate(lines[:-1], 1)
    ]


def _finished(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _grade(value: object, where: str) -> int:
    if type(value) not in (int, Decimal) or value not in (0, 1):
        raise ValueError(f"{where}: expected an explicit numeric reward of 0 or 1")
    return 1 if value == 1 else 0


def _finished_trial(trial_fd: int) -> tuple[str, int]:
    result = _decode_object(_read_artifact(trial_fd, "result.json"), "result.json")
    native_id = result.get("id")
    if not isinstance(native_id, str):
        raise ValueError("result.json: expected one canonical native UUID in id")
    try:
        if str(UUID(native_id)) != native_id:
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "result.json: expected one canonical native UUID in id"
        ) from exc
    if (
        not _finished(result.get("finished_at"))
        or result.get("reward_valid") is not True
        or result.get("exception_info") is not None
    ):
        raise ValueError(
            "result.json: expected a finished trial with a valid reward and no exception"
        )

    seats = result.get("seat_results")
    if not isinstance(seats, list) or len(seats) != len(SEATS):
        raise ValueError("result.json: expected exactly 2 seat_results")
    seen: set[str] = set()
    for row in seats:
        if not isinstance(row, dict):
            raise ValueError("result.json: expected a seat result object")
        seat = row.get("seat")
        if not isinstance(seat, str) or seat not in SEATS or seat in seen:
            raise ValueError(
                "result.json: expected distinct chef0 and chef1 seat results"
            )
        seen.add(seat)
        info = row.get("agent_info")
        execution = row.get("agent_execution")
        if (
            row.get("under_test") is not True
            or row.get("exception_info") is not None
            or not isinstance(info, dict)
            or info.get("name") != "oracle"
            or not isinstance(execution, dict)
            or not _finished(execution.get("finished_at"))
        ):
            raise ValueError(
                f"result.json: expected a finished under-test oracle for {seat}"
            )

    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = _grade(
        rewards.get("reward") if isinstance(rewards, dict) else None,
        "result.json verifier_result.rewards.reward",
    )
    separate = _decode_object(_read_artifact(trial_fd, REWARD_FILE), REWARD_FILE)
    if separate.keys() != {"reward"}:
        raise ValueError(f"{REWARD_FILE}: expected exactly the reward field")
    if _grade(separate["reward"], REWARD_FILE) != reward:
        raise ValueError(
            "native reward mismatch between result.json and verifier/reward.json"
        )
    return native_id, reward


def _tick_is(value: object, tick: int) -> bool:
    return type(value) is int and value == tick


def _state_at(state: object, tick: int, where: str) -> _JSONObject:
    if not isinstance(state, dict) or not _tick_is(state.get("timestep"), tick):
        raise ValueError(f"{where}: state.timestep must be {tick}")
    return cast(_JSONObject, state)


def _pair(value: object, where: str) -> list[object]:
    if not isinstance(value, list) or len(value) != len(SEATS):
        raise ValueError(f"{where}: expected one value per chef")
    return cast(list[object], value)


def _action_word(value: object, tick: int) -> str:
    if value == "interact":
        return "interact"
    if isinstance(value, list) and len(value) == 2:
        x, y = value
        if type(x) is int and type(y) is int:
            word = DIRECTIONS.get((x, y))
            if word is not None:
                return word
    raise ValueError(f"native tick {tick}: invalid normalized action")


def _transition(
    row: _JSONObject, tick: int
) -> tuple[_JSONObject, list[str], list[int | float], list[bool]]:
    where = f"native tick {tick}"
    if row.keys() != TRANSITION_FIELDS or not _tick_is(row["tick"], tick):
        raise ValueError(f"{where}: expected one native transition at this tick")
    state = _state_at(row["state"], tick, where)
    actions = [_action_word(value, tick) for value in _pair(row["joint_action"], where)]
    raw_sparse = _pair(row["sparse_reward_by_agent"], where)
    shaped = _pair(row["shaped_reward_by_agent"], where)
    raw_deliveries = _pair(row["soup_delivery_by_agent"], where)
    reward = row["reward"]
    error = f"{where}: inconsistent sparse reward or delivery evidence"
    if (
        any(type(value) not in (int, float) for value in raw_sparse)
        or any(type(value) not in (int, float) for value in shaped)
        or any(type(value) is not bool for value in raw_deliveries)
        or type(reward) not in (int, float)
    ):
        raise ValueError(error)
    sparse = cast(list[int | float], raw_sparse)
    deliveries = cast(list[bool], raw_deliveries)
    if reward != sum(sparse) or any(
        value < 0 or (value > 0) != delivered
        for value, delivered in zip(sparse, deliveries, strict=True)
    ):
        raise ValueError(error)
    return state, actions, sparse, deliveries


def _bound_view(
    receipt: _JSONObject, tick: int, previous_post_sha: str | None
) -> tuple[_JSONObject, str]:
    where = f"client ack tick {tick}"
    if receipt.keys() != {"tick", "view", "ack_sha256"} or not _tick_is(
        receipt["tick"], tick
    ):
        raise ValueError(f"{where}: expected one client ack receipt at this tick")
    view = receipt["view"]
    if not isinstance(view, dict) or view.keys() != {"tick", "state"}:
        raise ValueError(f"{where}: expected a public tick/state view")
    view = cast(_JSONObject, view)
    if not _tick_is(view["tick"], tick):
        raise ValueError(f"{where}: view.tick must be {tick}")
    _state_at(view["state"], tick, where)
    view_sha = _digest(view)
    acks = receipt["ack_sha256"]
    if not isinstance(acks, dict) or acks.keys() != set(SEATS):
        raise ValueError(f"{where}: missing client ack for either chef")
    acks = cast(_JSONObject, acks)
    for seat in SEATS:
        if acks[seat] != view_sha:
            raise ValueError(
                f"{where}: {seat} digest does not match the full pre-action view"
            )
    if previous_post_sha is not None and view_sha != previous_post_sha:
        raise ValueError(
            f"{where}: pre-action view does not match the previous native post-state"
        )
    return view, view_sha


def _frames(
    transitions: list[_JSONObject], receipts: list[_JSONObject]
) -> tuple[dict[str, list[_Frame]], dict[str, int], int]:
    frames: dict[str, list[_Frame]] = {seat: [] for seat in SEATS}
    deliveries_by_seat: dict[str, int] = dict.fromkeys(SEATS, 0)
    previous_post_sha = None
    hits_goal = True
    for turn, (transition, receipt) in enumerate(
        zip(transitions, receipts, strict=True), 1
    ):
        state, actions, sparse, deliveries = _transition(transition, turn)
        view, view_sha = _bound_view(receipt, turn - 1, previous_post_sha)
        if turn < HORIZON:
            previous_post_sha = _digest({"tick": turn, "state": state})
        final = turn == HORIZON
        if sparse != [0, 20 if final else 0] or deliveries != [False, final]:
            hits_goal = False
        for seat, action, delivered in zip(SEATS, actions, deliveries, strict=True):
            frames[seat].append(
                {
                    "turn": turn,
                    "basis": "bound",
                    "view": view,
                    "view_sha256": view_sha,
                    "observed": {"action": action},
                }
            )
            deliveries_by_seat[seat] += int(delivered)
    return frames, deliveries_by_seat, int(hits_goal)


def export_trial(trial: Path, out: Path) -> _ExportSummary:
    """Cross-check retained native evidence and create exactly two role-local episodes.

    The native verifier, not this reader, replays and grades the kitchen game.
    """
    with _directory(trial) as trial_fd:
        try:
            receipts = _read_rows(trial_fd, ACK_LOG)
        except FileNotFoundError as exc:
            raise ValueError(
                f"missing client ack log {ACK_LOG}; native transitions are not enough "
                "to reconstruct bound observations"
            ) from exc
        native_id, reward = _finished_trial(trial_fd)
        transitions = _read_rows(trial_fd, WORLD_LOG)
    frames, deliveries_by_seat, endpoint_reward = _frames(transitions, receipts)
    if reward != endpoint_reward:
        raise ValueError(
            "native reward mismatch with the kitchen verifier's delivery endpoint"
        )

    case_sha = _digest(
        {
            "engine": ENGINE,
            "peer_task": PEER_TASK,
            "layout": "cramped_room",
            "horizon": HORIZON,
            "initial_view_sha256": frames[SEATS[0]][0]["view_sha256"],
        }
    )
    episode_hashes: dict[str, str] = {}
    lines: list[bytes] = []
    for seat in SEATS:
        episode: _JSONObject = {
            "schema": "seatlab.episode/v1",
            "id": f"kitchen:{native_id}:{seat}",
            "case_sha256": case_sha,
            "role": seat,
            "native_trial_id": native_id,
            "closed": True,
            "frames": frames[seat],
        }
        episode_hashes[seat] = episode["episode_sha256"] = _digest(episode)
        lines.append(_canonical(episode) + b"\n")
    with _directory(out.parent) as parent_fd:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(out.name, flags, 0o644, dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as output:
            output.writelines(lines)
    return {
        "native_trial_id": native_id,
        "case_sha256": case_sha,
        "reward": reward,
        "deliveries_by_seat": deliveries_by_seat,
        "episodes": len(SEATS),
        "turns_per_role": HORIZON,
        "episode_sha256": episode_hashes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = export_trial(args.trial, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(_canonical(summary).decode("ascii"))


if __name__ == "__main__":
    main()
