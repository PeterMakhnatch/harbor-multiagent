"""Prepare private replay inputs for the three registered box decisions."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harbor.models.task.task import Task
from harbor.publisher.packager import Packager

from . import boxes_packet as bp
from . import intent_packet

_MESSAGE_COUNTS = {1: 9, 2: 7, 6: 9}
# (Accepted prefix SHA-256, legacy task checksum, Harbor package digest).
_REGISTERED_IDENTITIES = {
    1: (
        "37316f2ab717d268f96c204ab1557160a35f98a80fa7920e5924f5151984f3c4",
        "74b69b5208eca4eadea861e9281872ae949b3fc3c0d94956fdf2f469abda9197",
        "sha256:2c2c2b8eb7a08f893e1473d29d3e28616310e55110d37f76d17c821cc81cd257",
    ),
    2: (
        "ce3f82f548908259957b7d0c396f640d267ec1f3bb3ed8d2da5483d61ea3705c",
        "a0e5ac70843011169d1a71b1282dc70bc6fa5988a537bcb8af88767d78e56c80",
        "sha256:6d49df15cb8f675802a14cebb8da816827891d71cca3fc470cf258de02516be1",
    ),
    6: (
        "d0e54313f0c5666f15e590796e3aacedae7af9639974ca3404689bee78d01b03",
        "ff44cc698c93cd6a1913d7bcc859c5f94412ecb70fa562fc7f14a6f05f43ec28",
        "sha256:68fa2ec6f521b63547a31b7ed662a72c67e6c77d83d013c66267925cbccd13d1",
    ),
}


@dataclass(frozen=True, slots=True)
class Action:
    seat: str
    kind: str
    request: dict[str, Any] = field(repr=False)
    response: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class Cut:
    instance: int
    task: Path = field(repr=False)
    actions: tuple[Action, ...] = field(repr=False)
    task_checksum: str
    task_digest: str
    prefix_sha256: str
    baseline_flags: int


def _task_identity(task: Path) -> tuple[str, str]:
    try:
        # As with the retained files, do not follow a substituted path to the task.
        bp._read(task / "task.toml", bp._NATIVE_LIMIT)
        checksum = Task(task).checksum
        digest, _ = Packager.compute_content_hash(task)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("cannot read the native ownership task source") from None
    return checksum, f"sha256:{digest}"


def _require_task_identity(current: tuple[str, str], retained: tuple[str, str]) -> None:
    if current[0] != retained[0]:
        if current[1] == retained[1]:
            raise ValueError(
                "task source legacy checksum differs from the retained result; "
                "the package digest still matches, but files outside the package "
                "(such as __pycache__) can change the legacy checksum"
            )
        raise ValueError(
            "task source legacy checksum and package digest differ from the retained trial"
        )
    if current[1] != retained[1]:
        raise ValueError("task source package digest differs from the retained lock")


def _prefix(raw: bytes, opens: list[bp._Open], anchor: bp._Open) -> tuple[Action, ...]:
    actions: list[Action] = []
    messages: list[dict[str, Any]] = []
    accepted_opens = iter(opens)
    # _service has validated sequence, timing, methods, authentication and open
    # replies. Walk the same bytes and stop at the exact anchor, not its time.
    for line in raw.splitlines():
        row = bp._decode(line)
        if not row["ok"]:
            continue
        seat, kind = row["seat"], row["kind"]
        request, response = row["request"], row["response"]
        if kind == "open":
            if next(accepted_opens) is anchor:
                return tuple(actions)
        elif kind == "msg":
            text = request.get("text") if isinstance(request, dict) else None
            bp._require(
                bp._object(request, {"text"})
                and isinstance(text, str)
                and bool(text.strip())
                and len(text) <= 2000
                and response == {"from": seat, "id": len(messages) + 1, "text": text},
                "invalid_prefix_message",
            )
            messages.append(response)
        elif kind == "msgs":
            bp._require(response == {"messages": messages}, "invalid_prefix_mailbox")
            continue
        else:
            continue
        actions.append(Action(seat, kind, request, response))
    raise ValueError("registered anchor is missing from the ownership event log")


def _verify_world(actions: tuple[Action, ...], world: intent_packet.World) -> None:
    histories: dict[str, list[tuple[int, int, bool, int]]] = {
        seat: [] for seat in bp._SEATS
    }
    for action in actions:
        if action.kind == "open":
            reply = action.response
            histories[action.seat].append(
                (reply["box"], reply["room"], reply["flag"], reply["budget_left"])
            )
    for seat, case in zip(bp._SEATS, world.seats, strict=True):
        own = histories[seat]
        peers = {
            box
            for other, past in histories.items()
            if other != seat
            for box, _, _, _ in past
        }
        if (
            case.instance != world.instance
            or not case.earlier_observation
            or tuple((box, room, flag) for box, room, flag, _ in own) != case.own
            or not own
            or own[-1][3] != case.budget_left
            or tuple(sorted(peers)) != case.peers
        ):
            raise ValueError("ownership open history differs from the pinned world")
    if (
        sum(action.kind == "msg" for action in actions)
        != _MESSAGE_COUNTS[world.instance]
    ):
        raise ValueError("ownership message count differs from the registered prefix")


def prefix_sha256(actions: tuple[Action, ...]) -> str:
    """Hash the exact accepted action sequence and payloads."""
    prefix = json.dumps(
        [asdict(action) for action in actions],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return bp._sha(prefix)


def prepare(archive: Path, tasks: Path, instance: int) -> Cut:
    """Bind the original task and accepted pre-anchor actions; never backfill."""
    if type(instance) is not int or instance not in _REGISTERED_IDENTITIES:
        raise ValueError("instance is not a registered ownership world")
    registered_prefix, registered_checksum, registered_digest = _REGISTERED_IDENTITIES[
        instance
    ]
    world = intent_packet.load()[instance]
    source = bp._Snapshot()
    directory, result = bp._registered_trials(source, archive)[instance]
    try:
        bp._check_selected_metadata(source, directory, result)
        lock = bp._decode(source.read(directory / "lock.json"))
        retained = result["task_checksum"], lock["task"]["digest"]
        timing = bp._timing(result)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, RecursionError):
        raise ValueError(
            "cannot verify the retained ownership trial metadata"
        ) from None

    task = (tasks / bp._task_name(instance)).absolute()
    current = _task_identity(task)
    _require_task_identity(current, retained)
    if retained[0] != registered_checksum:
        raise ValueError("retained legacy checksum differs from the registered task")
    if retained[1] != registered_digest:
        raise ValueError("retained package digest differs from the registered task")
    try:
        raw = source.read(directory / "verifier/events.jsonl")
        opens = bp._service(raw, timing)
        _, _, anchor = bp._cut(opens, bp._SEATS[(instance - 1) % len(bp._SEATS)])
        actions = _prefix(raw, opens, anchor)
    except (KeyError, OSError, TypeError, ValueError, RecursionError):
        raise ValueError("cannot verify the retained ownership events") from None
    _verify_world(actions, world)
    prefix = prefix_sha256(actions)
    if prefix != registered_prefix:
        raise ValueError("retained ownership prefix differs from the registered digest")
    source.check()
    if _task_identity(task) != current:
        raise ValueError("native ownership task source changed during preparation")

    baseline_flags = len(
        {
            action.response["box"]
            for action in actions
            if action.kind == "open" and action.response["flag"] is True
        }
    )
    return Cut(instance, task, actions, *current, prefix, baseline_flags)
