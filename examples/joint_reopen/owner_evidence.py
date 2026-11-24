from typing import cast

from ..native_owner_process import REASON, owner_id
from .observation import CODES, ROLES
from .observation import KINDS as ACTIONS
from .study import OWNER_IMPORT_PATH

SCHEMA = "harbor.joint-reopen.owner-evidence/v1"
RECEIPT_SCHEMA = "harbor.joint-reopen.owner-receipt/v1"
METADATA_KEY = "joint_reopen_owner"
IDENTITY = "owner_declared"
REPORTED_FAILURE = "reported_failure"
STATUSES = ("matched", "missing", "malformed", "inconsistent", "failed", "unjudged")
REASONS = {
    "matched": (None,),
    "missing": ("seat_absent", "agent_result_absent", "receipt_absent"),
    "malformed": (
        "seat_malformed",
        "metadata_malformed",
        "receipt_fields",
        "receipt_schema",
        "outcome_malformed",
        "confirmed_malformed",
        "decisions_malformed",
    ),
    "inconsistent": (
        "requested_mismatch",
        "owner_incomplete",
        "role_mismatch",
        "sequence_invalid",
        "confirmed_mismatch",
        "world_mismatch",
    ),
    "failed": (
        "native_trial_error",
        "native_seat_error",
        "owner_failed",
        "terminal_failure",
    ),
    "unjudged": ("world_unavailable",),
}
OUTCOMES = ("incomplete", "voted", "stopped", "failed")
TERMINALS = {"voted": "vote", "stopped": "stop"}
STAGES = ("endorse", "report", "vote")
STAGE_KINDS = {
    "endorse": ("endorse", "stop"),
    "report": ("card", "report", "stop"),
    "vote": ("inbox", "card", "vote", "stop"),
}
NEXT_STAGE = {"endorse": "report", "report": "vote"}
FINAL_KINDS = ("vote", "stop")
KINDS = (*ACTIONS, "stop")
RECEIPT_KEYS = frozenset(
    {"schema", "role", "requested", "outcome", "confirmed", "decisions", "failure"}
)
DECISION_KEYS = frozenset({"turn", "stage", "kind", "served_model"})
MAX_DECISIONS = len(ACTIONS)
MAX_SEAT_ROWS = 16

Record = dict[str, object]


def owner_roles(team: dict[str, Record]) -> tuple[str, ...]:
    if type(team) is not dict or set(team) != set(ROLES):
        raise ValueError("owner evidence team does not bind east and west")
    return tuple(
        role for role in ROLES if team[role].get("import_path") == OWNER_IMPORT_PATH
    )


def _world(timeline: object, role: str) -> list[str]:
    bound = len(ROLES) * len(ACTIONS)
    if type(timeline) is not list or len(cast(list[object], timeline)) > bound:
        raise ValueError("owner evidence timeline is not a validated bounded list")
    actions: list[str] = []
    for entry in cast(list[Record], timeline):
        if entry["role"] == role:
            actions.append(CODES[cast(str, entry["kind"])])
    assert len(set(actions)) == len(actions) <= len(ACTIONS)
    return actions


def _seat(result: Record, role: str) -> tuple[str | None, Record | None]:
    rows = result.get("seat_results")
    if type(rows) is not list:
        return "seat_absent", None
    if len(cast(list[object], rows)) > MAX_SEAT_ROWS:
        return "seat_malformed", None
    found = [
        row
        for row in cast(list[object], rows)
        if type(row) is dict and cast(Record, row).get("seat") == role
    ]
    if len(found) > 1:
        return "seat_malformed", None
    if not found:
        return "seat_absent", None
    return None, cast(Record, found[0])


def _native(result: Record, seat: Record | None) -> str | None:
    if result.get("exception_info") is not None:
        return "native_trial_error"
    if seat is not None and seat.get("exception_info") is not None:
        return "native_seat_error"
    return None


def _receipt(seat: Record) -> tuple[str | None, object]:
    agent_result = seat.get("agent_result")
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


def _header(receipt: object) -> str | None:
    if type(receipt) is not dict or set(receipt) != RECEIPT_KEYS:
        return "receipt_fields"
    fields = cast(Record, receipt)
    schema, role = fields["schema"], fields["role"]
    if type(schema) is not str or schema != RECEIPT_SCHEMA:
        return "receipt_schema"
    if not owner_id(fields["requested"]) or not (
        role is None or (type(role) is str and role in ROLES)
    ):
        return "receipt_fields"
    outcome, failure = fields["outcome"], fields["failure"]
    if type(outcome) is not str or outcome not in OUTCOMES:
        return "outcome_malformed"
    if (outcome == "failed") != (failure is not None) and outcome not in TERMINALS:
        return "outcome_malformed"
    if failure is not None and (
        type(failure) is not str or REASON.fullmatch(failure) is None
    ):
        return "outcome_malformed"
    return None


def _confirmed(value: object) -> list[str] | None:
    if type(value) is not list or len(cast(list[object], value)) > len(ACTIONS):
        return None
    confirmed = cast(list[object], value)
    if not all(type(kind) is str and kind in ACTIONS for kind in confirmed):
        return None
    return list(cast(list[str], confirmed))


def _decisions(value: object) -> tuple[list[Record], int] | None:
    if type(value) is not list or len(cast(list[object], value)) > MAX_DECISIONS:
        return None
    rows: list[Record] = []
    declared = 0
    for turn, decision in enumerate(cast(list[object], value), start=1):
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
        if served is not None and not owner_id(served):
            return None
        if served is not None:
            declared += 1
        rows.append({"turn": turn, "stage": stage, "kind": kind})
    return rows, declared


def _parse(receipt: object) -> tuple[str | None, Record | None]:
    reason = _header(receipt)
    if reason is not None:
        return reason, None
    fields = cast(Record, receipt)
    confirmed = _confirmed(fields["confirmed"])
    if confirmed is None:
        return "confirmed_malformed", None
    parsed = _decisions(fields["decisions"])
    if parsed is None:
        return "decisions_malformed", None
    rows, declared = parsed
    owner: Record = {
        "requested": fields["requested"],
        "role": fields["role"],
        "outcome": fields["outcome"],
        "failure": None if fields["failure"] is None else REPORTED_FAILURE,
        "confirmed": confirmed,
        "decisions": rows,
        "declared": declared,
    }
    return None, owner


def _sequence_valid(decisions: list[Record], outcome: str) -> bool:
    if not decisions or decisions[-1]["kind"] != TERMINALS[outcome]:
        return False
    stage = "endorse"
    seen: set[str] = set()
    last = len(decisions) - 1
    for position, decision in enumerate(decisions):
        kind = cast(str, decision["kind"])
        if decision["stage"] != stage or kind not in STAGE_KINDS[stage]:
            return False
        if kind in seen or (kind in FINAL_KINDS and position != last):
            return False
        seen.add(kind)
        stage = NEXT_STAGE.get(kind, stage)
    return True


def _judge(
    owner: Record, role: str, requested: str, world: list[str] | None
) -> tuple[str, str | None]:
    outcome = cast(str, owner["outcome"])
    if owner["requested"] != requested:
        return "inconsistent", "requested_mismatch"
    if outcome == "failed":
        return "failed", "owner_failed"
    if owner["failure"] is not None:
        return "failed", "terminal_failure"
    if world is None:
        return "unjudged", "world_unavailable"
    if outcome == "incomplete":
        return "inconsistent", "owner_incomplete"
    if owner["role"] != role:
        return "inconsistent", "role_mismatch"
    decisions = cast(list[Record], owner["decisions"])
    if not _sequence_valid(decisions, outcome):
        return "inconsistent", "sequence_invalid"
    expected = [row["kind"] for row in decisions if row["kind"] != "stop"]
    if owner["confirmed"] != expected:
        return "inconsistent", "confirmed_mismatch"
    if world != expected:
        return "inconsistent", "world_mismatch"
    return "matched", None


def _classify(
    requested: str, result: Record, role: str, world: list[str] | None
) -> tuple[str, str | None, Record | None]:
    error, seat = _seat(result, role)
    native = _native(result, seat)
    owner: Record | None = None
    if seat is not None:
        error, receipt = _receipt(seat)
        if error is None:
            error, owner = _parse(receipt)
    if native is not None:
        return "failed", native, owner
    if error is not None:
        status = "missing" if error in REASONS["missing"] else "malformed"
        return status, error, None
    assert owner is not None
    status, reason = _judge(owner, role, requested, world)
    return status, reason, owner


def _owner_fields(owner: Record | None) -> Record:
    if owner is None:
        return {
            "owner_outcome": None,
            "owner_failure": None,
            "owner_decisions": None,
            "owner_confirmed": None,
            "served_declared": None,
        }
    return {
        "owner_outcome": owner["outcome"],
        "owner_failure": owner["failure"],
        "owner_decisions": [
            dict(row) for row in cast(list[Record], owner["decisions"])
        ],
        "owner_confirmed": list(cast(list[str], owner["confirmed"])),
        "served_declared": owner["declared"],
    }


def _entry(
    member: Record, result: Record, role: str, world: list[str] | None
) -> Record:
    requested = member.get("model_name")
    if not owner_id(requested):
        raise ValueError(f"registered {role} owner seat names no owner")
    status, reason, owner = _classify(cast(str, requested), result, role, world)
    assert status in STATUSES and reason in REASONS[status]
    entry: Record = {
        "status": status,
        "reason": reason,
        "identity": IDENTITY,
        "world_actions": None if world is None else list(world),
    }
    entry.update(_owner_fields(owner))
    return entry


def inspect_owners(
    team: dict[str, Record], result: Record, timeline: object
) -> Record | None:
    if type(result) is not dict:
        raise ValueError("owner evidence result is not an object")
    owners = owner_roles(team)
    if not owners:
        return None
    roles = {
        role: _entry(
            team[role],
            result,
            role,
            None if timeline is None else _world(timeline, role),
        )
        for role in owners
    }
    return {"schema": SCHEMA, "roles": roles}
