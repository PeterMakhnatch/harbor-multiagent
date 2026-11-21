from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from .calendar_case import _request
from .calendar_notices import protocol as p
from .calendar_notices import team_views
from .receiver_panel import _MAX_REPLY_BYTES
from .visibility import calendar_packet as packet
from .visibility import calendar_protocol

_SCHEMA = "seatlab.calendar-inquiry-source/v1"
_Stage = Literal["first", "second", "book"]


@dataclass(frozen=True, slots=True)
class Source:
    trial_uuid: str
    result_sha256: str
    stock_semantics_sha256: str
    world: str
    arm: str
    receiver_table: packet.Table
    initial: tuple[int, ...]
    revision: tuple[int, ...]
    native_days: tuple[int, ...]
    native_opened_sha256: str
    native_final_prompt_sha256: str
    target: int
    _notice_wire: str
    _commitment: str


def _native_cell(world: object, arm: object) -> team_views.Cell:
    if not isinstance(world, str) or not isinstance(arm, str):
        raise TypeError("invalid native world or arm")
    if arm in team_views.JOIN_ARMS:
        return team_views.JoinCell(world, arm)
    if arm in team_views.ROLE_ARMS:
        return team_views.RoleCell(world, arm)
    return team_views.Cell(world, arm)


def parse_source(value: object) -> Source:
    record = packet._object(
        value,
        "schema",
        "trial_uuid",
        "result_sha256",
        "stock_semantics_sha256",
        "world",
        "arm",
        "receiver_table",
        "notice",
        "revision",
        "native_days",
        "native_opened_sha256",
        "native_final_prompt_sha256",
        "target",
    )
    if record["schema"] != _SCHEMA:
        raise ValueError("unsupported inquiry source schema")
    trial_uuid = record["trial_uuid"]
    if (
        not isinstance(trial_uuid, str)
        or len(trial_uuid) != 36
        or str(UUID(trial_uuid)) != trial_uuid
    ):
        raise ValueError("expected a canonical trial UUID")
    result_sha = packet._check_digest(record["result_sha256"])
    stock_sha = packet._check_digest(record["stock_semantics_sha256"])
    opened_sha = packet._check_digest(record["native_opened_sha256"])
    final_sha = packet._check_digest(record["native_final_prompt_sha256"])
    cell = _native_cell(record["world"], record["arm"])
    table = packet._table(record["receiver_table"])
    target = calendar_protocol._checked_slot(record["target"])
    if target not in packet.starts(table):
        raise ValueError("stock target does not fit the receiver calendar")
    notice = packet._object(record["notice"], "initial", "count", "changed")
    initial = p._starts(packet._list(notice["initial"]))
    revision = p._starts(packet._list(record["revision"]))
    p._days(packet._list(notice["changed"]))
    notice_wire = p.index_wire(initial, revision, "named")
    if type(notice["count"]) is not int or p.wire(notice) != notice_wire:
        raise ValueError("notice does not match the sender revision")
    native_days = p._picked_days(packet._list(record["native_days"]))
    native_opened = p.read_wire(revision, native_days)
    if p.digest(native_opened) != opened_sha:
        raise ValueError("native opened report digest mismatch")
    native_final = team_views.choice_prompt(
        table, initial, packet._json(native_opened), cell.arm
    )
    if p.digest(native_final) != final_sha:
        raise ValueError("native final prompt digest mismatch")
    return Source(
        trial_uuid=trial_uuid,
        result_sha256=result_sha,
        stock_semantics_sha256=stock_sha,
        world=cell.world,
        arm=cell.arm,
        receiver_table=table,
        initial=initial,
        revision=revision,
        native_days=tuple(native_days),
        native_opened_sha256=opened_sha,
        native_final_prompt_sha256=final_sha,
        target=target,
        _notice_wire=notice_wire,
        _commitment=p.digest(p.wire([trial_uuid, result_sha])),
    )


def _pick_prompt(source: Source, days: list[int], *, show_report: bool) -> str:
    prompt = (
        team_views._view(source.receiver_table, "exact")
        + f"Notice from the other calendar: {source._notice_wire}\n"
        + "The notice contains the full initial list, the number of weekdays "
        "whose list changed, and the IDs of those weekdays. A weekday changed if any "
        "start was added or removed.\n"
    )
    if not days:
        prompt += "Choose the first of 2 weekday reports to help find a joint start.\n"
    else:
        first = days[0]
        prompt += f"First requested day ID: {first}.\n"
        if show_report:
            report = {
                "day": first,
                "starts": [slot for slot in source.revision if p._day(slot) == first],
            }
            prompt += f"First weekday report: {p.wire(report)}\n"
        else:
            prompt += "First weekday report: pending until after the second request.\n"
        prompt += (
            "Choose a second weekday report to help find a joint start. "
            "The second day ID must be distinct from the first.\n"
        )
    return prompt + 'Return only JSON: {"day":d}. Replace d with 1 day ID.'


def _stage_request(
    source: Source, stage: _Stage, days: list[int], prompt: str
) -> dict[str, object]:
    state = p.digest(p.wire([source._commitment, stage, days, p.digest(prompt)]))
    return dict(_request(f"{stage}/{state}", prompt))


def _raw_reply(record: object, request: dict[str, object]) -> str:
    envelope = packet._object(record, "id", "prompt_sha256", "reply")
    if (
        not isinstance(envelope["id"], str)
        or envelope["id"] != request["id"]
        or packet._check_digest(envelope["prompt_sha256"]) != request["prompt_sha256"]
    ):
        raise ValueError("reply does not match the expected request")
    reply = envelope["reply"]
    if not isinstance(reply, str):
        raise TypeError("reply must be text")
    if len(reply) > _MAX_REPLY_BYTES or len(reply.encode("utf-8")) > _MAX_REPLY_BYTES:
        raise ValueError("reply exceeds the byte limit")
    return reply


def _terminal(
    stage: _Stage, correct: int | None, days: list[int], target_visible: bool | None
) -> dict[str, object]:
    return {
        "status": "invalid" if correct is None else "scored",
        "stage": stage,
        "correct": correct,
        "days": days,
        "target_visible": target_visible,
    }


def advance(
    source: Source, timing: str, replies: list[dict[str, object]]
) -> dict[str, object]:
    if timing not in ("immediate", "delayed"):
        raise ValueError("invalid inquiry timing")
    if not isinstance(replies, list) or len(replies) > 3:
        raise ValueError("invalid inquiry history")
    days: list[int] = []
    stages: tuple[_Stage, _Stage] = ("first", "second")
    for index, stage in enumerate(stages):
        prompt = _pick_prompt(source, days, show_report=timing == "immediate")
        request = _stage_request(source, stage, days, prompt)
        if len(replies) == index:
            return request
        reply = _raw_reply(replies[index], request)
        try:
            day = packet._object(packet._json(reply), "day")["day"]
            days = p._days([*days, day])
        except (RecursionError, ValueError):
            if len(replies) != index + 1:
                raise ValueError("unexpected reply after a terminal inquiry") from None
            return _terminal(stage, None, days, None)
    opened = packet._json(p.read_wire(source.revision, days))
    prompt = team_views.choice_prompt(
        source.receiver_table, source.initial, opened, "checked"
    )
    request = _stage_request(source, "book", days, prompt)
    if len(replies) == 2:
        return request
    reply = _raw_reply(replies[2], request)
    starts = source.revision if p._day(source.target) in days else source.initial
    target_visible = source.target in starts
    try:
        slot = calendar_protocol.decode_slot(reply)
    except (RecursionError, ValueError):
        return _terminal("book", None, days, target_visible)
    return _terminal("book", int(slot == source.target), days, target_visible)
