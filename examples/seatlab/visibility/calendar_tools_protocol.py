"""Replay native calendar prompts and offer a seat-local operation."""

import json
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, TypedDict

from . import calendar_packet, calendar_protocol

_BLOCKS = frozenset(calendar_protocol.blocks())
_SEATS = ("p1", "p2")
_AVAILABILITY = ("off", "on")
_ROLE_ACCESS = ("sender", "receiver")


class _Returned(TypedDict):
    status: Literal["returned"]
    starts: list[int]


class _NoList(TypedDict):
    status: Literal["unavailable", "not_requested", "invalid"]


ToolResult = _Returned | _NoList


@dataclass(frozen=True, slots=True)
class View:
    seat: str
    role: str
    base: str
    table: calendar_packet.Table


def _check_block(block: calendar_protocol.Block) -> None:
    if (
        not isinstance(block, calendar_protocol.Block)
        or type(block.world) is not int
        or type(block.direction) is not int
        or type(block.repetition) is not int
        or block not in _BLOCKS
    ):
        raise ValueError("invalid calendar block")


def native_id(block: calendar_protocol.Block) -> str:
    _check_block(block)
    return f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"


def _design(design: str) -> tuple[tuple[str, str], str]:
    if design == "availability":
        return _AVAILABILITY, "tl-"
    if design == "role-access":
        return _ROLE_ACCESS, "ra-"
    raise ValueError("invalid calendar tool design")


def design_arms(design: str) -> tuple[str, str]:
    return _design(design)[0]


def spend_prefix(design: str) -> str:
    return _design(design)[1]


def policies(
    block: calendar_protocol.Block, *, design: str = "availability"
) -> tuple[str, str]:
    _check_block(block)
    first, second = design_arms(design)
    if (block.world // 2 + block.direction + block.repetition) % 2 == 0:
        return first, second
    return second, first


def _policy_design(policy: str) -> str:
    if policy in _AVAILABILITY:
        return "availability"
    if policy in _ROLE_ACCESS:
        return "role-access"
    raise ValueError("invalid calendar tool policy")


def access_for(block: calendar_protocol.Block, policy: str, seat: str) -> str:
    _check_block(block)
    if seat not in _SEATS:
        raise ValueError("invalid calendar seat")
    if _policy_design(policy) == "availability":
        return policy
    allowed = block.sender if policy == "sender" else block.receiver
    return "on" if seat == allowed else "off"


def _check_policy(policy: str) -> None:
    if policy not in _AVAILABILITY:
        raise ValueError("invalid calendar tool policy")


def _actions(run: str, arms: tuple[str, str]) -> dict[str, str]:
    actions = {
        f"{run}:cal_initial_p1": "seed1",
        f"{run}:cal_initial_p2": "seed2",
    }
    for arm in arms:
        actions[f"{run}-{arm}:cal_recheck"] = f"{arm}-s"
        actions[f"{run}-{arm}:cal_decide"] = f"{arm}-r"
    return actions


def action_id(block: calendar_protocol.Block, policy: str, identifier: str) -> str:
    prefix = spend_prefix(_policy_design(policy))
    run = native_id(block)
    suffix = _actions(run, block.arms).get(identifier)
    if suffix is None:
        raise ValueError("invalid native turn identifier")
    return f"{prefix}{run.removeprefix('rx-')}-{policy}-{suffix}"


class Session:
    """Bind one policy's native turn sequence and decoded replies."""

    def __init__(self, block: calendar_protocol.Block) -> None:
        run = native_id(block)
        self._pending = deque(_actions(run, block.arms))
        self._tables = calendar_packet.load(
            calendar_packet.PACKET, calendar_packet.PACKET_SHA256
        )[block.world].tables
        self._sender = block.direction
        self._seeds: list[tuple[int, ...] | None] = []
        self._revised: tuple[int, ...] | None = None

    def _role(self, identifier: str) -> str:
        if not self._pending or self._pending[0] != identifier:
            raise ValueError("unexpected native turn identifier")
        return identifier.split(":", 1)[1]

    def view(self, identifier: str, base_prompt: str | None = None) -> View:
        role = self._role(identifier)
        if role in ("cal_initial_p1", "cal_initial_p2"):
            index = int(role == "cal_initial_p2")
            prompt = calendar_protocol.initial_prompt(self._tables[index])
        else:
            sender, receiver = self._sender, 1 - self._sender
            if role == "cal_recheck":
                index = sender
                own = self._seeds[index]
                if own is None:
                    raise ValueError("recheck without valid seeds")
                peer = (
                    self._seeds[receiver]
                    if identifier.endswith("-peer:cal_recheck")
                    else None
                )
                prompt = calendar_protocol.recheck_prompt(
                    self._tables[index], own, peer
                )
            else:
                index = receiver
                own = self._seeds[index]
                if own is None or self._revised is None:
                    raise ValueError("decision without valid lists")
                prompt = calendar_protocol.decision_prompt(
                    self._tables[index], own, self._revised
                )
        if base_prompt is not None and base_prompt != prompt:
            raise ValueError("native role prompt mismatch")
        return View(_SEATS[index], role, prompt, self._tables[index])

    def accept(self, identifier: str, final_text: str) -> None:
        role = self._role(identifier)
        if not isinstance(final_text, str):
            raise TypeError("native response must be text")
        self._pending.popleft()
        if role == "cal_decide":
            # A decision never changes the remaining plan. Stock owns its score.
            self._revised = None
            return
        try:
            starts = calendar_protocol.decode_list(final_text)
        except ValueError:
            starts = None
        if role == "cal_recheck":
            self._revised = starts
            if starts is None:
                self._pending.popleft()  # No decision in this room.
        else:
            self._seeds.append(starts)
            if role == "cal_initial_p2" and None in self._seeds:
                self._pending.clear()  # Both seeds run, even if the first is invalid.


def select_prompt(view: View) -> str:
    return (
        "Choose an operation for the calendar task below. Do not answer the task "
        "in this call.\n\n"
        f"{view.base}\n"
        "The optional local_starts operation lists every feasible 2-hour start "
        "for your own calendar. It takes no arguments.\n"
        'Return only {"tool":"local_starts"} or {"tool":"none"}.\n'
    )


def tool_result(view: View, policy: str, select_text: str) -> ToolResult:
    _check_policy(policy)
    try:
        choice = calendar_protocol._json(select_text)
    except ValueError:
        return {"status": "invalid"}
    if choice == {"tool": "none"}:
        return {"status": "not_requested"}
    if choice != {"tool": "local_starts"}:
        return {"status": "invalid"}
    if policy == "off":
        return {"status": "unavailable"}
    return {"status": "returned", "starts": list(calendar_packet.starts(view.table))}


def _result_json(result: ToolResult) -> str:
    return json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)


def final_prompt(view: View, result: ToolResult) -> str:
    return f"Local operation result (JSON):\n{_result_json(result)}\n\n{view.base}"


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def result_digest(result: ToolResult) -> str:
    return digest(_result_json(result))
