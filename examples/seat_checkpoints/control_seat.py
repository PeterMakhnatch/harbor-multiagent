import asyncio
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ..native_seat_http import SeatFailure, seat_request
from ..shift_board.owner_game import LETTERS, play
from ..shift_board.owner_seat import (
    GAME_PATHS,
    MAX_DECISIONS,
    MAX_POLLS,
    POLL_SECONDS,
    ROUTES,
    STATUS_KEYS,
    WAIT_FLAGS,
    WAIT_SECONDS,
    _body,
    _checked_summary,
    _receipt_reason,
)

RECEIPT_SCHEMA = "harbor.seat-cut.control-receipt/v1"
MARKER_SCHEMA = "harbor.seat-cut.control-marker/v1"
RECEIPT_NAME = "control-receipt.json"
METADATA_KEY = "seat_cut_control"
CONTROL_DIR = "seat-cut-control"
ROLE_POLICIES: dict[str, frozenset[str]] = {
    "planner": frozenset({"publish"}),
    "dispatcher": frozenset({"preferred", "leave"}),
}
ROLE_STAGES: dict[str, tuple[str, ...]] = {
    "planner": ("after_publish",),
    "dispatcher": ("before_decide", "after_decide"),
}
FINAL_STAGE = {"planner": "after_publish", "dispatcher": "after_decide"}
TERMINAL = {"publish": "publish", "preferred": "publish", "leave": "leave"}
SEALED_ROLES = frozenset({"dispatcher"})
FRAMEWORK_KWARGS = frozenset(
    {"logger", "mcp_servers", "skills_dir", "override_setup_timeout_sec"}
)
GATE_SECONDS = 60.0
GATE_POLL_SECONDS = 0.25
MAX_GATE_POLLS = int(GATE_SECONDS / GATE_POLL_SECONDS) + 1

assert set(ROLE_POLICIES) == set(ROLE_STAGES) == set(FINAL_STAGE)
assert all(FINAL_STAGE[role] in ROLE_STAGES[role] for role in ROLE_STAGES)
assert 0 < GATE_POLL_SECONDS < GATE_SECONDS and MAX_GATE_POLLS <= 1024


def _checked_gates(role: str, gates: object) -> tuple[str, ...]:
    if type(gates) is not list and type(gates) is not tuple:
        raise ValueError("ControlSeat gates must be a list of stage names")
    listed = tuple(gates)
    if not all(type(gate) is str and gate in ROLE_STAGES[role] for gate in listed):
        raise ValueError(f"ControlSeat {role} gates must be among {ROLE_STAGES[role]}")
    if len(set(listed)) != len(listed):
        raise ValueError("ControlSeat gates must not repeat")
    return tuple(stage for stage in ROLE_STAGES[role] if stage in listed)


def _control_dir(logs_dir: Path, role: str) -> Path:
    agent_dir = Path(logs_dir)
    seat_dir = agent_dir.parent
    if (
        agent_dir.name != "agent"
        or seat_dir.name != role
        or seat_dir.parent.name != "seats"
    ):
        raise ValueError("ControlSeat logs_dir must be <trial>/seats/<role>/agent")
    return seat_dir / CONTROL_DIR


def _present(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError:
        raise SeatFailure("gate_unreadable", path.name) from None
    if not stat.S_ISREG(mode):
        raise SeatFailure("gate_file_malformed", path.name)
    return True


async def _world_wait(environment: BaseEnvironment, flag: str) -> None:
    if flag not in WAIT_FLAGS:
        raise SeatFailure("wait_flag_refused")
    deadline = time.monotonic() + WAIT_SECONDS
    for _ in range(MAX_POLLS):
        status, reply = await seat_request(environment, ROUTES, "GET", "/status", None)
        if status != 200 or set(reply) != STATUS_KEYS:
            raise SeatFailure("status_unavailable", f"HTTP {status}")
        if not all(type(reply[key]) is bool for key in STATUS_KEYS):
            raise SeatFailure("status_malformed")
        if reply[flag]:
            return
        if reply["closed"]:
            raise SeatFailure("world_closed", flag)
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(POLL_SECONDS)
    raise SeatFailure("wait_timeout", flag)


class ControlSeat(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "seat-cut-control"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        policy: str,
        gates: list[str],
        skip_model_parsing: bool = False,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if type(role) is not str or role not in ROLE_POLICIES:
            raise ValueError("ControlSeat role must be planner or dispatcher")
        if type(policy) is not str or policy not in ROLE_POLICIES[role]:
            raise ValueError(
                f"ControlSeat {role} policy must be one of {sorted(ROLE_POLICIES[role])}"
            )
        if model_name is not None:
            raise ValueError("ControlSeat is scripted and takes no model_name")
        if extra_env:
            raise ValueError("ControlSeat forwards no environment into its seat")
        if unknown := sorted(set(kwargs) - FRAMEWORK_KWARGS):
            raise ValueError(f"ControlSeat refuses kwargs {unknown}")
        self.role = role
        self.policy = policy
        self.gates = _checked_gates(role, gates)
        self.control_dir = _control_dir(logs_dir, role)
        super().__init__(
            logs_dir=logs_dir,
            model_name=None,
            skip_model_parsing=skip_model_parsing,
            **kwargs,
        )

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        receipt: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "role": self.role,
            "policy": self.policy,
            "gates": list(self.gates),
            "decisions": [],
            "stages": [],
            "summary": None,
            "outcome": "incomplete",
            "failure": None,
        }
        self._record(context, receipt)
        try:
            await self._play(environment, context, receipt)
        except BaseException as exc:
            receipt["outcome"] = "failed"
            receipt["failure"] = _receipt_reason(exc)
            raise
        else:
            receipt["outcome"] = "complete"
        finally:
            self._record(context, receipt)
            self._write_receipt(receipt)

    async def _play(
        self,
        environment: BaseEnvironment,
        context: AgentContext,
        receipt: dict[str, Any],
    ) -> None:
        self._open_control_dir()
        status, brief = await seat_request(environment, ROUTES, "GET", "/brief", None)
        if status != 200 or brief.get("role") != self.role:
            raise SeatFailure("brief_role_mismatch", f"HTTP {status}")

        async def request(
            path: str, body: dict[str, object] | None
        ) -> tuple[int, dict[str, object]]:
            if path not in GAME_PATHS:
                raise SeatFailure("request_path_refused")
            method = "GET" if body is None else "POST"
            if (method, path) not in ROUTES:
                raise SeatFailure("request_method_refused")
            return await seat_request(
                environment, ROUTES, method, path, _body(path, body)
            )

        async def decide(view: dict[str, object]) -> str:
            if len(receipt["decisions"]) >= MAX_DECISIONS:
                raise SeatFailure("decision_bound_exceeded")
            reached = {entry["stage"] for entry in receipt["stages"]}
            if view.get("stage") == "decide" and "before_decide" not in reached:
                await self._stage("before_decide", context, receipt)
            choice = self._choice(view)
            receipt["decisions"].append(
                {
                    "turn": view["turn"],
                    "stage": view["stage"],
                    "kind": choice.split(":", 1)[0],
                }
            )
            self._record(context, receipt)
            return choice

        async def wait_for(flag: str) -> None:
            await _world_wait(environment, flag)

        summary = await play(dict(brief), request, decide, wait_for)
        receipt["summary"] = _checked_summary(summary, self.role)
        self._record(context, receipt)
        terminal = receipt["summary"]["terminal"]
        sealed = terminal == "sealed" and self.role in SEALED_ROLES
        if terminal != TERMINAL[self.policy] and not sealed:
            raise SeatFailure("control_terminal_mismatch")
        await self._stage(FINAL_STAGE[self.role], context, receipt)
        assert [entry["stage"] for entry in receipt["stages"]] == list(
            ROLE_STAGES[self.role]
        )

    def _choice(self, view: dict[str, object]) -> str:
        stage, preferred = view.get("stage"), view.get("preferred")
        board, choices = view.get("board"), view.get("choices")
        if type(preferred) is not str or preferred not in LETTERS:
            raise SeatFailure("control_view_malformed")
        if self.role == "planner" and stage == "plan":
            choice = f"publish:{preferred}@draft"
        elif self.role == "dispatcher" and stage == "prepare":
            choice = f"prepare:{preferred}"
        elif self.role == "dispatcher" and stage == "decide":
            if board is None:
                choice = "read"
            elif self.policy == "leave":
                choice = "leave"
            else:
                choice = f"publish:{preferred}@current"
        else:
            raise SeatFailure("control_stage_unexpected")
        if type(choices) is not list or choice not in choices:
            raise SeatFailure("control_choice_unavailable", choice)
        return choice

    def _open_control_dir(self) -> None:
        try:
            self.control_dir.mkdir(mode=0o755, exist_ok=True)
            mode = self.control_dir.lstat().st_mode
        except OSError:
            raise SeatFailure("gate_dir_unwritable") from None
        if not stat.S_ISDIR(mode):
            raise SeatFailure("gate_dir_malformed")

    async def _stage(
        self, stage: str, context: AgentContext, receipt: dict[str, Any]
    ) -> None:
        assert stage in ROLE_STAGES[self.role]
        gated = stage in self.gates
        self._arrive(stage, gated)
        entry: dict[str, Any] = {"stage": stage, "gated": gated, "released": None}
        receipt["stages"].append(entry)
        self._record(context, receipt)
        if not gated:
            return
        entry["released"] = False
        await self._await_release(stage)
        entry["released"] = True
        self._record(context, receipt)

    def _arrive(self, stage: str, gated: bool) -> None:
        if _present(self.control_dir / f"release-{stage}"):
            raise SeatFailure("gate_released_early", stage)
        marker = {
            "schema": MARKER_SCHEMA,
            "role": self.role,
            "stage": stage,
            "gated": gated,
        }
        data = json.dumps(marker, separators=(",", ":")) + "\n"
        staged = self.control_dir / f".arrived-{stage}.tmp"
        target = self.control_dir / f"arrived-{stage}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor = os.open(staged, flags, 0o644)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(staged, target)
            finally:
                staged.unlink()
        except FileExistsError:
            raise SeatFailure("gate_marker_exists", stage) from None
        except OSError:
            raise SeatFailure("gate_marker_unwritable", stage) from None

    async def _await_release(self, stage: str) -> None:
        release = self.control_dir / f"release-{stage}"
        deadline = time.monotonic() + GATE_SECONDS
        for _ in range(MAX_GATE_POLLS):
            if _present(release):
                return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(GATE_POLL_SECONDS)
        raise SeatFailure("gate_timeout", stage)

    def _record(self, context: AgentContext, receipt: dict[str, Any]) -> None:
        snapshot = json.loads(json.dumps(receipt, allow_nan=False))
        context.metadata = {**(context.metadata or {}), METADATA_KEY: snapshot}

    def _write_receipt(self, receipt: dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        target = self.logs_dir / RECEIPT_NAME
        staged = self.logs_dir / f".{RECEIPT_NAME}.tmp"
        data = json.dumps(receipt, allow_nan=False, indent=2) + "\n"
        staged.write_text(data, encoding="utf-8")
        os.replace(staged, target)
