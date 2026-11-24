import re
from typing import Any, cast

from ..native_owner_process import OWNER_ID
from .owner_game import MAX_DISPATCHER_TURNS, PLANNER_TURNS
from .owner_io import (
    KINDS,
    METADATA_KEY,
    RECEIPT_SCHEMA,
    REPLY_SCHEMA,
    REQUEST_SCHEMA,
    STAGES,
)

SCHEMA = "harbor.shift-board.owner-evidence/v1"
IMPORT_PATH = "examples.shift_board.owner_seat:OwnerSeat"
IDENTITY = "owner_declared"
ROLES = ("planner", "dispatcher")
STATUSES = ("matched", "missing", "malformed", "inconsistent")
REASONS = {
    "matched": (None,),
    "missing": ("seat_absent", "agent_result_absent", "receipt_absent"),
    "malformed": (
        "registration_invalid",
        "metadata_malformed",
        "receipt_fields",
        "receipt_schema",
        "outcome_malformed",
        "decisions_malformed",
        "summary_malformed",
    ),
    "inconsistent": (
        "requested_mismatch",
        "owner_not_complete",
        "role_mismatch",
        "sequence_invalid",
        "summary_mismatch",
        "world_mismatch",
    ),
}
OUTCOMES = ("incomplete", "complete", "failed")
TERMINALS = ("publish", "leave", "stop")
WORLD_TERMINALS = ("publish", "leave")
WORLD_ACTIONS = ("prepare", "read", "publish_accepted", "publish_rejected", "leave")
PUBLISH_OUTCOMES = ("accepted", "rejected")
RECEIPT_KEYS = frozenset(
    {
        "schema",
        "role",
        "requested",
        "request_schema",
        "reply_schema",
        "decisions",
        "summary",
        "outcome",
        "failure",
    }
)
DECISION_KEYS = frozenset({"turn", "stage", "kind", "served_model"})
SUMMARY_KEYS = frozenset({"role", "turns", "terminal", "reads", "conflicts"})
COUNT_KEYS = ("turns", "reads", "conflicts")
FAILURE = re.compile(r"[a-z_]{1,48}")
MAX_DECISIONS = MAX_DISPATCHER_TURNS
MAX_EVENTS = 24
MAX_SEAT_ROWS = 16

Record = dict[str, object]
World = tuple[bool, list[str], str | None]


def _owner_id(value: object) -> bool:
    return (
        type(value) is str
        and OWNER_ID.fullmatch(value) is not None
        and "://" not in value
    )


def _member(team: object, role: str) -> Record:
    if type(team) is not dict or set(team) != set(ROLES):
        raise ValueError("owner evidence team does not bind planner and dispatcher")
    member = cast(Record, team)[role]
    if type(member) is not dict:
        raise ValueError(f"owner evidence team {role} is not an object")
    return cast(Record, member)


def _world(events: object, role: str) -> World:
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise ValueError("owner evidence cut events are not a bounded list")
    transport = False
    actions: list[str] = []
    terminal: str | None = None
    for raw in cast(list[object], events):
        if type(raw) is not dict:
            raise ValueError("owner evidence cut event is malformed")
        event = cast(Record, raw)
        seat = event.get("seat")
        if type(seat) is not str or seat not in ROLES:
            raise ValueError("owner evidence cut event is malformed")
        if seat != role:
            continue
        kind, outcome, rev = event.get("type"), event.get("outcome"), event.get("rev")
        if kind == "view" and type(rev) is int and rev >= 0:
            opening = rev == 0 and not transport and not actions
            transport = transport or opening
            if not opening:
                actions.append("read")
        elif kind == "publish" and type(outcome) is str and outcome in PUBLISH_OUTCOMES:
            actions.append(f"publish_{outcome}")
            terminal = "publish" if outcome == "accepted" else terminal
        elif kind in ("prepare", "leave"):
            actions.append(cast(str, kind))
            terminal = "leave" if kind == "leave" else terminal
        else:
            raise ValueError("owner evidence cut event is malformed")
    assert all(action in WORLD_ACTIONS for action in actions)
    assert terminal is None or terminal in WORLD_TERMINALS
    return transport, actions, terminal


def _receipt(result: Record, role: str) -> tuple[str | None, object]:
    rows = result.get("seat_results")
    if type(rows) is not list:
        return "seat_absent", None
    if len(rows) > MAX_SEAT_ROWS:
        raise ValueError("owner evidence result has too many seat rows")
    found = [
        row
        for row in cast(list[object], rows)
        if type(row) is dict and row.get("seat") == role
    ]
    if len(found) > 1:
        raise ValueError(f"owner evidence result repeats the {role} seat")
    if not found:
        return "seat_absent", None
    agent_result = cast(Record, found[0]).get("agent_result")
    if agent_result is None:
        return "agent_result_absent", None
    if type(agent_result) is not dict:
        return "metadata_malformed", None
    metadata = cast(Record, agent_result).get("metadata")
    if metadata is None or (type(metadata) is dict and METADATA_KEY not in metadata):
        return "receipt_absent", None
    if type(metadata) is not dict:
        return "metadata_malformed", None
    return None, cast(Record, metadata)[METADATA_KEY]


def _constant(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _header(receipt: object) -> str | None:
    if type(receipt) is not dict or set(receipt) != RECEIPT_KEYS:
        return "receipt_fields"
    fields = cast(Record, receipt)
    if not (
        _constant(fields["schema"], RECEIPT_SCHEMA)
        and _constant(fields["request_schema"], REQUEST_SCHEMA)
        and _constant(fields["reply_schema"], REPLY_SCHEMA)
    ):
        return "receipt_schema"
    role = fields["role"]
    if not _owner_id(fields["requested"]) or not (
        role is None or (type(role) is str and role in ROLES)
    ):
        return "receipt_fields"
    outcome, failure = fields["outcome"], fields["failure"]
    if type(outcome) is not str or outcome not in OUTCOMES:
        return "outcome_malformed"
    if (outcome == "failed") != (failure is not None):
        return "outcome_malformed"
    if failure is not None and (
        type(failure) is not str or FAILURE.fullmatch(failure) is None
    ):
        return "outcome_malformed"
    if (outcome == "complete") != (fields["summary"] is not None):
        return "outcome_malformed"
    return None


def _decisions(decisions: object) -> list[Record] | None:
    if type(decisions) is not list or len(decisions) > MAX_DECISIONS:
        return None
    checked: list[Record] = []
    for turn, decision in enumerate(cast(list[object], decisions), start=1):
        if type(decision) is not dict or set(decision) != DECISION_KEYS:
            return None
        fields = cast(Record, decision)
        number, stage, kind = fields["turn"], fields["stage"], fields["kind"]
        served = fields["served_model"]
        if type(number) is not int or number != turn:
            return None
        if type(stage) is not str or stage not in STAGES:
            return None
        if type(kind) is not str or kind not in KINDS:
            return None
        if served is not None and not _owner_id(served):
            return None
        checked.append({"stage": stage, "kind": kind, "served_model": served})
    return checked


def _summary(summary: object) -> Record | None:
    if type(summary) is not dict or set(summary) != SUMMARY_KEYS:
        return None
    fields = cast(Record, summary)
    role, terminal = fields["role"], fields["terminal"]
    if type(role) is not str or role not in ROLES:
        return None
    if type(terminal) is not str or terminal not in TERMINALS:
        return None
    for key in COUNT_KEYS:
        count = fields[key]
        if type(count) is not int or not 0 <= count <= MAX_DISPATCHER_TURNS:
            return None
    return {key: fields[key] for key in ("role", "terminal", *COUNT_KEYS)}


def _parse(receipt: object) -> tuple[str | None, Record | None]:
    reason = _header(receipt)
    if reason is not None:
        return reason, None
    fields = cast(Record, receipt)
    decisions = _decisions(fields["decisions"])
    if decisions is None:
        return "decisions_malformed", None
    summary = None
    if fields["summary"] is not None:
        summary = _summary(fields["summary"])
        if summary is None:
            return "summary_malformed", None
    served: list[object] = []
    for decision in decisions:
        if decision["served_model"] not in served:
            served.append(decision["served_model"])
    owner: Record = {
        "requested": fields["requested"],
        "role": fields["role"],
        "outcome": fields["outcome"],
        "stages": [decision["stage"] for decision in decisions],
        "kinds": [decision["kind"] for decision in decisions],
        "served": served,
        "summary": summary,
    }
    return None, owner


def _sequence_valid(role: str, stages: list[str], kinds: list[str]) -> bool:
    if role == "planner":
        return (
            stages == ["plan"] * PLANNER_TURNS and kinds == ["publish"] * PLANNER_TURNS
        )
    turns = len(kinds)
    if not 2 <= turns <= MAX_DISPATCHER_TURNS:
        return False
    if stages != ["prepare"] + ["decide"] * (turns - 1) or kinds[0] != "prepare":
        return False
    middle = kinds[1:-1]
    return (
        kinds[-1] in TERMINALS
        and all(kind in ("read", "publish") for kind in middle)
        and middle.count("read") <= 1
        and middle.count("publish") <= 1
    )


def _summary_matches(summary: Record, role: str, kinds: list[str]) -> bool:
    return (
        summary["role"] == role
        and summary["turns"] == len(kinds)
        and summary["terminal"] == kinds[-1]
        and summary["reads"] == kinds.count("read")
        and summary["conflicts"] == kinds[:-1].count("publish")
    )


def _expected_world(kinds: list[str]) -> tuple[list[str], str | None]:
    last = len(kinds) - 1
    actions = [
        ("publish_accepted" if turn == last else "publish_rejected")
        if kind == "publish"
        else kind
        for turn, kind in enumerate(kinds)
        if kind != "stop"
    ]
    terminal = kinds[-1] if kinds[-1] in WORLD_TERMINALS else None
    return actions, terminal


def _judge(owner: Record, role: str, requested: str, world: World) -> str | None:
    stages = cast(list[str], owner["stages"])
    kinds = cast(list[str], owner["kinds"])
    if owner["requested"] != requested:
        return "requested_mismatch"
    if owner["outcome"] != "complete":
        return "owner_not_complete"
    if owner["role"] != role:
        return "role_mismatch"
    if not _sequence_valid(role, stages, kinds):
        return "sequence_invalid"
    if not _summary_matches(cast(Record, owner["summary"]), role, kinds):
        return "summary_mismatch"
    transport, actions, terminal = world
    if not transport or (actions, terminal) != _expected_world(kinds):
        return "world_mismatch"
    return None


def _owner_fields(owner: Record | None) -> Record:
    if owner is None:
        return {
            "served_models": None,
            "owner_outcome": None,
            "owner_stages": None,
            "owner_actions": None,
            "owner_terminal": None,
            "owner_summary": None,
        }
    summary = cast(Record | None, owner["summary"])
    return {
        "served_models": list(cast(list[object], owner["served"])),
        "owner_outcome": owner["outcome"],
        "owner_stages": list(cast(list[str], owner["stages"])),
        "owner_actions": list(cast(list[str], owner["kinds"])),
        "owner_terminal": None if summary is None else summary["terminal"],
        "owner_summary": None
        if summary is None
        else {key: summary[key] for key in COUNT_KEYS},
    }


def _classify(
    requested: str | None, result: Record, role: str, world: World
) -> tuple[str, str | None, Record | None]:
    if requested is None:
        return "malformed", "registration_invalid", None
    reason, receipt = _receipt(result, role)
    if reason is not None:
        status = "malformed" if reason == "metadata_malformed" else "missing"
        return status, reason, None
    reason, owner = _parse(receipt)
    if owner is None:
        return "malformed", reason, None
    reason = _judge(owner, role, requested, world)
    return ("matched" if reason is None else "inconsistent"), reason, owner


def _entry(member: Record, result: Record, role: str, world: World) -> Record:
    model = member.get("model_name")
    requested = cast(str, model) if _owner_id(model) else None
    status, reason, owner = _classify(requested, result, role, world)
    assert status in STATUSES and reason in REASONS[status]
    transport, actions, terminal = world
    entry: Record = {
        "status": status,
        "reason": reason,
        "identity": IDENTITY,
        "requested": requested,
        "world_transport": transport,
        "world_actions": list(actions),
        "world_terminal": terminal,
    }
    entry.update(_owner_fields(owner))
    return entry


def inspect_owners(
    team: dict[str, dict[str, Any]], result: dict[str, Any], cut: dict[str, Any]
) -> dict[str, object] | None:
    if type(result) is not dict:
        raise ValueError("owner evidence result is not an object")
    if type(cut) is not dict or "events" not in cut:
        raise ValueError("owner evidence cut has no events")
    members = {role: _member(team, role) for role in ROLES}
    owners = [role for role in ROLES if members[role].get("import_path") == IMPORT_PATH]
    if not owners:
        return None
    roles = {
        role: _entry(members[role], result, role, _world(cut["events"], role))
        for role in owners
    }
    return {"schema": SCHEMA, "roles": roles}
