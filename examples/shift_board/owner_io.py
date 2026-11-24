import asyncio
import json
import os
import re
import signal
from typing import Any

REQUEST_SCHEMA = "harbor.shift-board.turn/v1"
REPLY_SCHEMA = "harbor.shift-board.choice/v1"
RECEIPT_SCHEMA = "harbor.shift-board.owner-receipt/v1"
METADATA_KEY = "shift_board_owner"
OWNER_VARIABLES = {
    "planner": "SHIFT_BOARD_PLANNER_OWNER",
    "dispatcher": "SHIFT_BOARD_DISPATCHER_OWNER",
}
INHERITED_ENV = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
VIEW_FIELDS = frozenset(
    {
        "role",
        "turn",
        "stage",
        "available",
        "preferred",
        "draft",
        "board",
        "last",
        "choices",
    }
)
REPLY_FIELDS = frozenset({"schema", "requested", "choice", "served_model"})
STAGES = frozenset({"plan", "prepare", "decide"})
KINDS = frozenset({"prepare", "publish", "read", "leave", "stop"})
CHOICE = re.compile(r"prepare:[A-D]|publish:[A-D]@(?:draft|current)|read|leave|stop")
OWNER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
MAX_TURN = 5
MAX_CHOICES = 16
DEADLINE_SECONDS = 90.0
REAP_SECONDS = 5.0
REAP_POLL_SECONDS = 0.05
REAP_POLLS = 100
PUBLIC_INSTRUCTIONS = (
    "Two seats, planner and dispatcher, share one shift board. "
    "Score: 1 only if the final published shift fits both calendars, otherwise 0. "
    "Preferences do not count toward the score. "
    "Each seat sees only its own calendar; the partner calendar is private. "
    "The dispatcher prepares a draft, then the planner publishes. "
    "publish:X@draft writes against the original empty-board revision; "
    "publish:X@current writes against the revision returned by an explicit read. "
    "A stale write may succeed or be refused; a refusal does not finish play. "
    "leave and stop preserve the standing answer. "
    "Reply with exactly one of the offered choices."
)
STREAM_LIMIT = 16 << 10
REQUEST_LIMIT = 16 << 10
CHUNK_BYTES = 4096

assert PUBLIC_INSTRUCTIONS.isascii() and len(PUBLIC_INSTRUCTIONS) <= 700


class OwnerFailure(Exception):
    def __init__(self, role: str, reason: str) -> None:
        assert role in OWNER_VARIABLES
        assert re.fullmatch(r"[a-z_]{1,48}", reason)
        super().__init__(f"{role} owner failed: {reason}")
        self.role = role
        self.reason = reason


class _Oversized(Exception):
    pass


def _owner_id(value: object) -> bool:
    return (
        type(value) is str
        and OWNER_ID.fullmatch(value) is not None
        and "://" not in value
    )


def _check_view(view: dict[str, Any]) -> str:
    if type(view) is not dict or set(view) != VIEW_FIELDS:
        raise ValueError("owner view fields differ")
    role = view["role"]
    if role not in OWNER_VARIABLES:
        raise ValueError("owner view role is unknown")
    turn = view["turn"]
    if type(turn) is not int or not 1 <= turn <= MAX_TURN:
        raise ValueError("owner view turn is out of range")
    if view["stage"] not in STAGES:
        raise ValueError("owner view stage is unknown")
    choices = view["choices"]
    if type(choices) is not list or not 1 <= len(choices) <= MAX_CHOICES:
        raise ValueError("owner view choices are not a bounded list")
    if len(set(choices)) != len(choices) or not all(
        type(choice) is str and CHOICE.fullmatch(choice) for choice in choices
    ):
        raise ValueError("owner view choices are malformed")
    return role


def _executable(role: str) -> str:
    path = os.environ.get(OWNER_VARIABLES[role], "")
    if not path:
        raise OwnerFailure(role, "owner_unconfigured")
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise OwnerFailure(role, "owner_path_not_absolute")
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise OwnerFailure(role, "owner_not_executable")
    return path


def _request(view: dict[str, Any], requested: str) -> bytes:
    body = {
        "schema": REQUEST_SCHEMA,
        "requested": requested,
        "instructions": PUBLIC_INSTRUCTIONS,
        "view": view,
    }
    text = json.dumps(body, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    payload = (text + "\n").encode("ascii")
    assert len(payload) <= REQUEST_LIMIT
    return payload


async def _drain(stream: asyncio.StreamReader) -> bytes:
    data = bytearray()
    while True:
        chunk = await stream.read(min(CHUNK_BYTES, STREAM_LIMIT + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > STREAM_LIMIT:
            raise _Oversized()


async def _feed(stream: asyncio.StreamWriter, payload: bytes) -> None:
    try:
        stream.write(payload)
        await stream.drain()
        stream.close()
        await stream.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        return


def _group_alive(role: str, group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        raise OwnerFailure(role, "owner_reap_failed") from None
    return True


async def _reap(role: str, process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise OwnerFailure(role, "owner_reap_failed") from None
    try:
        await asyncio.wait_for(process.wait(), REAP_SECONDS)
    except TimeoutError:
        raise OwnerFailure(role, "owner_reap_failed") from None
    for _ in range(REAP_POLLS):
        if not _group_alive(role, process.pid):
            return
        await asyncio.sleep(REAP_POLL_SECONDS)
    raise OwnerFailure(role, "owner_reap_failed")


async def _run(role: str, executable: str, payload: bytes) -> bytes:
    env = {name: os.environ[name] for name in INHERITED_ENV if name in os.environ}
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/",
            env=env,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        raise OwnerFailure(role, "owner_spawn_failed") from None
    try:
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                stdout, code = await _exchange(role, process, payload)
        except BaseException:
            await _reap(role, process)
            raise
    except TimeoutError:
        raise OwnerFailure(role, "owner_timeout") from None
    if code != 0:
        raise OwnerFailure(role, "owner_exit_nonzero")
    return stdout


async def _exchange(
    role: str, process: asyncio.subprocess.Process, payload: bytes
) -> tuple[bytes, int]:
    assert process.stdin and process.stdout and process.stderr
    try:
        async with asyncio.TaskGroup() as group:
            stdout = group.create_task(_drain(process.stdout))
            group.create_task(_drain(process.stderr))
            group.create_task(_feed(process.stdin, payload))
    except ExceptionGroup as failures:
        if failures.subgroup(_Oversized) is not None:
            raise OwnerFailure(role, "owner_output_oversized") from None
        if failures.subgroup(OSError) is not None:
            raise OwnerFailure(role, "owner_stream_failed") from None
        raise
    return stdout.result(), await process.wait()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def _constant(name: str) -> None:
    raise ValueError("non-finite JSON number")


def _reply(role: str, data: bytes) -> dict[str, Any]:
    body = data[:-1] if data.endswith(b"\n") else data
    if not body.startswith(b"{") or not body.endswith(b"}"):
        raise OwnerFailure(role, "owner_reply_not_one_object")
    try:
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OwnerFailure(role, "owner_reply_not_strict_json") from None
    if type(value) is not dict or set(value) != REPLY_FIELDS:
        raise OwnerFailure(role, "owner_reply_fields_differ")
    if value["schema"] != REPLY_SCHEMA:
        raise OwnerFailure(role, "owner_reply_schema_differs")
    return value


async def choose(view: dict[str, Any], requested: str) -> tuple[str, dict[str, Any]]:
    role = _check_view(view)
    if not _owner_id(requested):
        raise OwnerFailure(role, "requested_invalid")
    executable = _executable(role)
    reply = _reply(role, await _run(role, executable, _request(view, requested)))
    if type(reply["requested"]) is not str or reply["requested"] != requested:
        raise OwnerFailure(role, "owner_requested_mismatch")
    choice = reply["choice"]
    if type(choice) is not str or choice not in view["choices"]:
        raise OwnerFailure(role, "owner_choice_not_offered")
    served = reply["served_model"]
    if served is not None and not _owner_id(served):
        raise OwnerFailure(role, "owner_served_model_invalid")
    kind = choice.split(":", 1)[0]
    assert kind in KINDS
    receipt = {
        "turn": view["turn"],
        "stage": view["stage"],
        "kind": kind,
        "served_model": served,
    }
    return choice, receipt
