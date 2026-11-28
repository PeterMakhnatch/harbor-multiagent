import json
import re
from typing import Any

from examples.native_study import require
from examples.seatlab.exhibit_wire import encode_request, request_stamp

from .native import Session, diagnose
from .participant import DECISIONS, NEXT, ROLES, SILENT, arguments, check_view, render

SCHEMA = "harbor.dispatch-recall.owner-evidence/v1"
REPLAY_TICKETS = ("0123456789abcdef" * 2, "fedcba9876543210" * 2)
METADATA_KEYS = frozenset({"role", "phase", "accepted_actions", "accepted_requests"})
STAMP_KEYS = frozenset({"bytes", "sha256"})
SHA256 = re.compile(r"[0-9a-f]{64}")
CAVEAT = (
    "A confirmed turn means the participant adapter logged an accepted owner decision "
    "whose exact request bytes match the request rebuilt from the native cut and the "
    "role instruction; a confirmed commit or close request with a bay notice carried "
    "the delivered report. It does not show that any provider or model received, read "
    "or used a request or its notice. Unconfirmed means the adapter log is absent or "
    "shorter than the accepted native actions; it never changes the physical grade."
)

Expected = dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _owner_action(record: dict[str, Any]) -> dict[str, Any]:
    op = record["op"]
    if op == "close":
        return {"op": "close"}
    if op == "report" and record["bay"] is None:
        return {"op": "report", "silent": True}
    return {"op": op, "bay": record["bay"]}


def _native_request(args: list[str]) -> dict[str, Any]:
    op = args[0]
    if op == "close":
        return {"op": "close", "ticket": args[1]}
    if op == "commit":
        return {"op": "commit", "bay": args[1], "ticket": args[2]}
    return {"op": op, "bay": None if args[1] == "--silent" else args[1]}


def _replay(cut: dict[str, Any], instructions: dict[str, str]) -> Expected:
    session = Session(
        cut["seed"], cut["transition"], cut["timing"], tickets=REPLAY_TICKETS
    )
    history: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    expected: Expected = {role: [] for role in ROLES}
    for record in cut["actions"]:
        role, op = record["role"], record["op"]
        shown, ticket = check_view(session.view(role), role, op)
        if op in ("commit", "close"):
            require(
                shown["notice"] == cut["windows"][op]["notice"],
                f"replayed {op} notice differs from the cut",
            )
        action = _owner_action(record)
        request = encode_request(render(instructions[role], op, shown, history[role]))
        require(
            not any(issued.encode() in request for issued in REPLAY_TICKETS),
            "rebuilt request carries a relay ticket",
        )
        args = arguments(action, op, shown, ticket)
        accepted = session.apply(role, _native_request(args))
        require(accepted == {"op": op, "turn": NEXT[op]}, f"replay rejected {op}")
        expected[role].append((action, request_stamp(request)))
        history[role].append({"view": shown, "action": action})
    replayed = session.snapshot()
    require(
        all(
            replayed[key] == cut[key]
            for key in ("bays", "initial_bay", "current_bay", "actions", "reward")
        ),
        "replayed session differs from the cut",
    )
    return expected


def _stamp(value: Any) -> bool:
    return (
        type(value) is dict
        and set(value) == STAMP_KEYS
        and type(value["bytes"]) is int
        and value["bytes"] > 0
        and type(value["sha256"]) is str
        and SHA256.fullmatch(value["sha256"]) is not None
    )


def _logged(role: str, entry: Any) -> list[tuple[Any, Any]] | None:
    if entry is None:
        return None
    require(
        type(entry) is dict and set(entry) == METADATA_KEYS and entry["role"] == role,
        f"{role} metadata is not a recall participant log",
    )
    actions, stamps = entry["accepted_actions"], entry["accepted_requests"]
    turns = DECISIONS[role]
    require(
        type(actions) is list
        and type(stamps) is list
        and len(actions) == len(stamps) <= len(turns),
        f"{role} metadata lists unmatched or extra decisions",
    )
    count = len(actions)
    phase = turns[count] if count < len(turns) else "finished"
    require(
        entry["phase"] == phase or (count == 0 and entry["phase"] is None),
        f"{role} metadata phase contradicts its accepted decisions",
    )
    require(
        all(type(action) is dict for action in actions) and all(map(_stamp, stamps)),
        f"{role} metadata holds malformed decisions or request stamps",
    )
    return list(zip(actions, stamps, strict=True))


def _confirm(role: str, logged: list[tuple[Any, Any]], expected: Expected) -> None:
    native = expected[role]
    require(
        len(logged) <= len(native),
        f"{role} metadata records a decision the native cut lacks",
    )
    for index, ((action, stamp), (want, wanted)) in enumerate(
        zip(logged, native, strict=False)
    ):
        turn = DECISIONS[role][index]
        require(
            _canonical(action) == _canonical(want),
            f"{role} {turn} decision contradicts the native cut",
        )
        require(
            _canonical(stamp) == _canonical(wanted),
            f"{role} {turn} request differs from the rebuilt request",
        )


def _window(cut: dict[str, Any], name: str, status: str | None) -> dict[str, Any]:
    window = cut["windows"][name]
    notice = None
    if window is not None:
        notice = "blank" if window["notice"] == SILENT else "bay"
    return {"status": status or "not_accepted", "notice": notice}


def owner_evidence(
    cut: dict[str, Any],
    instructions: dict[str, str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    diagnose(cut)
    require(
        type(instructions) is dict
        and set(instructions) == set(ROLES)
        and all(type(text) is str and text.strip() for text in instructions.values()),
        "expected one instruction per role",
    )
    require(
        type(metadata) is dict and set(metadata) == set(ROLES),
        "expected metadata or None per role",
    )
    expected = _replay(cut, instructions)
    logs = {role: _logged(role, metadata[role]) for role in ROLES}
    for role, logged in logs.items():
        if logged is not None:
            _confirm(role, logged, expected)
    seen = dict.fromkeys(ROLES, 0)
    turns = []
    for seq, record in enumerate(cut["actions"], 1):
        role = record["role"]
        logged = logs[role]
        confirmed = logged is not None and seen[role] < len(logged)
        seen[role] += 1
        status = "confirmed" if confirmed else "unconfirmed"
        turns.append({"seq": seq, "role": role, "turn": record["op"], "status": status})
    statuses = {row["turn"]: row["status"] for row in turns}
    return {
        "schema": SCHEMA,
        "turns": turns,
        "counts": {
            label: sum(row["status"] == label for row in turns)
            for label in ("confirmed", "unconfirmed")
        },
        "roles": {
            role: {
                "metadata": "absent" if logs[role] is None else "present",
                "logged": len(logs[role] or []),
                "accepted": len(expected[role]),
            }
            for role in ROLES
        },
        "commit_request": _window(cut, "commit", statuses.get("commit")),
        "close_request": _window(cut, "close", statuses.get("close")),
        "caveat": CAVEAT,
    }
