from __future__ import annotations

import os
import re
from contextlib import ExitStack
from pathlib import Path

from . import (
    calendar_client,
    calendar_list_run,
    calendar_query_run,
    calendar_replica_report,
)
from . import calendar_tools_evidence as evidence
from . import calendar_tools_protocol as tools
from .calendar_protocol import Block, decode_list, decode_slot
from .calendar_report import _ARMS, _SEATS, _Row, _check

_SCHEMA = "seatlab.calendar-query-turn/v1"
_ACTION_BYTES = 1024
_DIGEST = re.compile(r"[a-f0-9]{64}")
_TERMINAL = {"complete", "invalid_seed"}


def _empty(block: Block, status: str) -> _Row:
    return {
        "world": block.world,
        "direction": block.direction,
        "repetition": block.repetition,
        "status": status,
        "receiver": block.receiver,
        "starts": dict.fromkeys(_SEATS),
        "revised": dict.fromkeys(_ARMS),
        "arms": {
            arm: dict.fromkeys(("reward", "slot", "trial_id_sha256")) for arm in _ARMS
        },
        "turns": [],
    }


def _entries(fd: int, allowed: set[str]) -> set[str]:
    found: set[str] = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            _check(entry.name in allowed, "unregistered_query_evidence")
            found.add(entry.name)
    return found


def _filename(identifier: str) -> str:
    return identifier.replace(":", "_") + ".json"


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _check_native(native: _Row, block: Block) -> None:
    status = native["status"]
    _check(status in _TERMINAL | {"not_started", "partial"}, "native_query_status")
    coords = (native["world"], native["direction"], native["repetition"])
    if coords == (None, None, None) and status not in _TERMINAL:
        _check(not native["turns"], "unbound_native_query")
        return
    _check(
        coords == (block.world, block.direction, block.repetition)
        and all(type(value) is int for value in coords),
        "native_query_coordinates",
    )
    if native["turns"] or status in _TERMINAL:
        _check(
            _digest(native["native_sha256"]) and _digest(native["semantics_sha256"]),
            "native_query_source",
        )


def _decode_list(text: str) -> list[int] | None:
    try:
        return list(decode_list(text))
    except ValueError:
        return None


def _decode_slot(text: str) -> int | None:
    try:
        return decode_slot(text)
    except ValueError:
        return None


def _bind_turn(
    action_fd: int, identifier: str, view: tools.View, block: Block, text: str
) -> _Row:
    public = {
        "id": identifier,
        "role": "sender" if view.seat == block.sender else "receiver",
        "prompt_sha256": tools.digest(
            calendar_query_run.model_prompt(view.role, view.base)
        ),
        "response_sha256": tools.digest(text),
    }
    receipt = evidence._read(action_fd, _filename(identifier), _ACTION_BYTES)
    _check(
        receipt
        == {
            **public,
            "schema": _SCHEMA,
            "seat": view.seat,
            "base_sha256": tools.digest(view.base),
        },
        "query_turn_binding",
    )
    return public


def _replay(
    row: _Row,
    block: Block,
    ids: list[str],
    hashes: dict[str, _Row],
    turn_fd: int,
    action_fd: int,
) -> dict[str, int | None]:
    session = tools.Session(block)
    slots: dict[str, int | None] = {}
    for identifier in ids:
        view = session.view(identifier)
        text = evidence._native_reply(turn_fd, identifier, view, hashes[identifier])
        row["turns"].append(_bind_turn(action_fd, identifier, view, block, text))
        session.accept(identifier, text)
        if view.role.startswith("cal_initial_"):
            row["starts"][view.seat] = _decode_list(text)
        else:
            arm = identifier.split(":", 1)[0].rsplit("-", 1)[1]
            if view.role == "cal_recheck":
                revised = _decode_list(text)
                row["revised"][arm] = revised
                if revised is None:
                    slots[arm] = None
            else:
                slots[arm] = _decode_slot(text)
    return slots


def _finish(row: _Row, native: _Row, slots: dict[str, int | None]) -> None:
    if native["status"] == "invalid_seed":
        _check(
            len(row["turns"]) == len(_SEATS) and None in row["starts"].values(),
            "unbound_invalid_query_seed",
        )
        row["status"] = "invalid_seed"
    if native["status"] != "complete":
        return
    _check(slots.keys() == set(_ARMS), "missing_query_decisions")
    for arm, slot in slots.items():
        proof = native["arms"][arm]
        status = "complete" if slot is not None else "model_invalid_scored_zero"
        reward, trial = proof["reward"], proof["trial_id_sha256"]
        _check(
            proof["status"] == status
            and proof["native_timeline_verified"] is True
            and proof["slot"] == slot
            and type(proof["slot"]) is type(slot)
            and _digest(trial)
            and type(reward) is int
            and reward in (0, 1)
            and (slot is not None or reward == 0),
            "unverified_native_query_arm",
        )
        row["arms"][arm] = {"reward": reward, "slot": slot, "trial_id_sha256": trial}
    _check(
        row["arms"]["blind"]["trial_id_sha256"]
        != row["arms"]["peer"]["trial_id_sha256"],
        "reused_native_query_trial",
    )
    row["status"] = "complete"


def _read_run(directory: Path, fd: int, block: Block, row: _Row) -> None:
    plan = {
        identifier: _filename(identifier)
        for identifier in calendar_client._request_plan(block)
    }
    names = _entries(fd, {"actions", "block"})
    with ExitStack() as stack:
        if "actions" in names:
            action_fd = stack.enter_context(evidence._directory("actions", fd))
            actions = _entries(action_fd, set(plan.values()))
        else:
            action_fd = None
            actions = set()
        if "block" not in names:
            _check(not actions, "query_actions_without_native_block")
            return
        native_fd = stack.enter_context(evidence._directory("block", fd))
        native = calendar_replica_report.analyze(directory / "block")
        _check_native(native, block)
        hashes = native["turns"]
        _check(hashes.keys() <= plan.keys(), "unregistered_native_query_turn")
        ids = [identifier for identifier in plan if identifier in hashes]
        expected = {plan[identifier] for identifier in ids}
        _check(actions <= expected, "query_actions_without_native_turn")
        if not ids or action_fd is None or actions != expected:
            return
        turn_fd = stack.enter_context(evidence._directory("turns", native_fd))
        _check(_entries(turn_fd, expected) == expected, "native_query_turn_set")
        _finish(row, native, _replay(row, block, ids, hashes, turn_fd, action_fd))


def read_block(study: Path, block: Block) -> _Row:
    run = tools.native_id(block)
    if block not in calendar_query_run.blocks():
        raise ValueError("unregistered calendar query block")
    row = _empty(block, "not_started")
    study = study.absolute()
    try:
        with (
            calendar_list_run._root(study) as root,
            evidence._directory(run, root) as fd,
        ):
            row["status"] = "partial"
            _read_run(study / run, fd, block, row)
    except FileNotFoundError:
        return _empty(block, row["status"])
    except calendar_replica_report._REPLAY_ERRORS:
        return _empty(block, "invalid_evidence")
    return row
