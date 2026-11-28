import asyncio
import json
import logging
import re
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any, override

from examples.native_study import normalize, require
from examples.seatlab.exhibit_owner import _exchange, _identity
from examples.seatlab.exhibit_wire import encode_request, request_stamp
from examples.seatlab.visibility import local_seat
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

IMPORT_PATH = "examples.seatlab.dispatch_recall.participant:RecallParticipant"
ROLES = ("loader", "depot")
TURNS = ("stage", "report", "commit", "close")
DECISIONS = {"loader": ("stage", "commit", "close"), "depot": ("report",)}
NEXT = {"stage": "report", "report": "commit", "commit": "close", "close": "done"}
CLIENT = ("python3", "/app/recall.py")
SILENT = "--"
WAIT_SECONDS = 310
ACT_SECONDS = 10
LIMIT = 65536
BAY = re.compile(r"[A-Z0-9]{2}")
TICKET = re.compile(r"[0-9a-f]{32}")
_SEEN = frozenset({"role", "turn", "active", "bays"})
_LOADER = _SEEN | {"initial_bay"}
_WINDOW = _LOADER | {"notice", "ticket"}
VIEW_KEYS = {
    "stage": _LOADER,
    "report": _SEEN | {"current_bay"},
    "commit": _WINDOW,
    "close": _WINDOW,
}
RULES = {
    "stage": '{"op":"stage","bay":"<one bay from view.bays>"}',
    "report": (
        '{"op":"report","bay":"<one bay from view.bays>"} to name a bay, '
        'or {"op":"report","silent":true} to send no bay'
    ),
    "commit": (
        '{"op":"commit","bay":"<one bay from view.bays>"}. '
        "This participant attaches the commit ticket; do not include one"
    ),
    "close": (
        '{"op":"close"}. This participant attaches the close ticket; '
        "the close step does not change the result"
    ),
}


def kwargs(role: str, endpoints: dict[str, str]) -> dict[str, Any]:
    require(role in DECISIONS, "unknown dispatch-recall role")
    require(
        type(endpoints) is dict and set(endpoints) == set(TURNS),
        "expected one owner endpoint per decision turn",
    )
    return {
        "role": role,
        "owner_sockets": {turn: endpoints[turn] for turn in DECISIONS[role]},
    }


def roster(harbor: SimpleNamespace, endpoints: dict[str, str]) -> list[dict[str, Any]]:
    require(
        type(endpoints) is dict
        and all(type(path) is str for path in endpoints.values())
        and all(Path(path).is_absolute() for path in endpoints.values())
        and len(set(endpoints.values())) == len(endpoints),
        "owner endpoints must be distinct absolute paths",
    )
    team = []
    for role in ROLES:
        binding = kwargs(role, endpoints)
        member = normalize(
            harbor, {"seat": role, "import_path": IMPORT_PATH, "kwargs": binding}
        )
        require(
            member.get("seat") == role
            and member.get("import_path") == IMPORT_PATH
            and member.get("kwargs") == binding
            and member.get("name") is None
            and member.get("model_name") is None
            and not member.get("env"),
            "native participant changed the recall binding",
        )
        team.append(member)
    return team


def check_view(
    view: dict[str, Any], role: str, turn: str
) -> tuple[dict[str, Any], str | None]:
    require(
        type(view) is dict and set(view) == VIEW_KEYS[turn],
        f"{turn} view has unexpected fields",
    )
    require(
        view["role"] == role and view["turn"] == turn and view["active"] is True,
        f"{turn} is not an active {role} decision",
    )
    bays = view["bays"]
    require(
        type(bays) is list
        and len(bays) == 2
        and all(type(bay) is str and BAY.fullmatch(bay) for bay in bays)
        and bays[0] < bays[1],
        "view must list two distinct bays",
    )
    for field in ("initial_bay", "current_bay"):
        require(field not in view or view[field] in bays, f"invalid {field}")
    require(
        "notice" not in view or view["notice"] == SILENT or view["notice"] in bays,
        "invalid notice",
    )
    ticket = view.get("ticket")
    require(
        ticket is None
        or (type(ticket) is str and TICKET.fullmatch(ticket) is not None),
        "invalid ticket",
    )
    return {key: value for key, value in view.items() if key != "ticket"}, ticket


def _held(ticket: str | None) -> str:
    if ticket is None:
        raise ValueError("window view lacks its ticket")
    return ticket


def render(
    instruction: str, turn: str, view: dict[str, Any], history: list[dict[str, Any]]
) -> str:
    payload = {"instruction": instruction, "view": view, "history": history}
    return (
        f"You hold the {view['role']} seat. This participant runs the relay commands "
        "named in your instruction for you. Decide your "
        f"{turn} step using only the supplied information; history lists your own "
        "earlier views and decisions. Return one JSON object without markdown, in this "
        "form: "
        + RULES[turn]
        + ".\nInput JSON: "
        + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )


def arguments(
    action: dict[str, Any], turn: str, view: dict[str, Any], ticket: str | None
) -> list[str]:
    require(
        type(action) is dict and action.get("op") == turn,
        f"owner must return {turn}",
    )
    if turn == "close":
        require(set(action) == {"op"}, "close takes no fields")
        return ["close", _held(ticket)]
    if turn == "report" and set(action) == {"op", "silent"}:
        require(action["silent"] is True, "silent must be true")
        return ["report", "--silent"]
    require(
        set(action) == {"op", "bay"}
        and type(action["bay"]) is str
        and action["bay"] in view["bays"],
        f"owner {turn} must name one bay from the view",
    )
    if turn == "commit":
        return ["commit", action["bay"], _held(ticket)]
    return [turn, action["bay"]]


async def seat_command(
    environment: BaseEnvironment, args: list[str], timeout: int
) -> dict[str, Any]:
    result = await environment.exec(shlex.join([*CLIENT, *args]), timeout_sec=timeout)
    require(
        result.return_code == 0 and result.stdout is not None,
        f"recall {args[0]} failed",
    )
    return local_seat._load_line(result.stdout.strip().encode() + b"\n", LIMIT)


class RecallParticipant(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        owner_sockets: dict[str, str],
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        if model_name is not None or extra_env or extra:
            raise ValueError(
                "RecallParticipant accepts only a role and its owner sockets"
            )
        require(role in DECISIONS, "unknown dispatch-recall role")
        require(
            type(owner_sockets) is dict
            and set(owner_sockets) == set(DECISIONS[role])
            and all(type(path) is str for path in owner_sockets.values()),
            f"{role} needs one owner socket per decision: {list(DECISIONS[role])}",
        )
        endpoints: dict[str, tuple[str, tuple[int, int]]] = {}
        for turn in DECISIONS[role]:
            try:
                path = local_seat._socket_path(owner_sockets[turn])
            except local_seat._Fail as error:
                raise ValueError(f"{turn}: {error.message}") from None
            require(
                path == owner_sockets[turn],
                f"{turn}: owner socket path is not canonical",
            )
            endpoints[turn] = (path, _identity(path))
        pins = [pin for _, pin in endpoints.values()]
        require(
            len(set(owner_sockets.values())) == len(pins) == len(set(pins)),
            "owner sockets must be distinct per decision",
        )
        super().__init__(logs_dir=logs_dir, logger=logger)
        self._role = role
        self._endpoints = endpoints
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-dispatch-recall-participant"

    @override
    def version(self) -> str:
        return "1.0.0"

    def _bound(self, environment: BaseEnvironment) -> bool:
        return (
            isinstance(environment, SeatEnvironment)
            and environment.service == self._role
        )

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        require(self._bound(environment), "participant bound to the wrong seat")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        require(
            not self._started and self._bound(environment),
            "invalid or repeated participant run",
        )
        require(
            type(instruction) is str and instruction.strip() != "",
            "empty instruction",
        )
        self._started = True
        metadata: dict[str, Any] = {
            "role": self._role,
            "accepted_actions": [],
            "accepted_requests": [],
            "phase": None,
        }
        context.metadata = metadata
        history: list[dict[str, Any]] = []
        tickets: list[str] = []
        for turn in DECISIONS[self._role]:
            metadata["phase"] = turn
            view = await seat_command(environment, ["wait", turn], WAIT_SECONDS)
            shown, ticket = check_view(view, self._role, turn)
            if ticket is not None:
                tickets.append(ticket)
            request = encode_request(render(instruction, turn, shown, history))
            require(
                not any(seen.encode() in request for seen in tickets),
                "owner request carries a relay ticket",
            )
            path, pin = self._endpoints.pop(turn)
            action = await asyncio.to_thread(_exchange, path, pin, request)
            args = arguments(action, turn, shown, ticket)
            accepted = await seat_command(environment, args, ACT_SECONDS)
            require(
                accepted == {"op": turn, "turn": NEXT[turn]},
                f"relay did not accept {turn}",
            )
            metadata["accepted_actions"].append(action)
            metadata["accepted_requests"].append(request_stamp(request))
            history.append({"view": shown, "action": action})
        metadata["phase"] = "finished"
