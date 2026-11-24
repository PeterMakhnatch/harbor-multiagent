import asyncio
import json
import os
import re
import signal
from typing import Any

ROLE = re.compile(r"[a-z][a-z_]{0,31}")
VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
SCHEMA = re.compile(r"[a-z][a-z0-9.-]{0,63}/v[0-9]{1,3}")
REASON = re.compile(r"[a-z_]{1,48}")
OWNER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
CHOICE = re.compile(r"[a-z]{1,16}(?::[A-Za-z0-9@_-]{1,32})?")
INHERITED_ENV = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
REPLY_FIELDS = frozenset({"schema", "requested", "choice", "served_model"})
MAX_CHOICES = 64
MAX_INSTRUCTIONS = 4096
DEADLINE_SECONDS = 90.0
REAP_SECONDS = 5.0
REAP_POLL_SECONDS = 0.05
REAP_POLLS = 100
STREAM_LIMIT = 16 << 10
REQUEST_LIMIT = 16 << 10
CHUNK_BYTES = 4096


class OwnerFailure(Exception):
    def __init__(self, role: str, reason: str) -> None:
        assert ROLE.fullmatch(role)
        assert REASON.fullmatch(reason)
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


def _check_call(
    role: str,
    variable: str,
    request_schema: str,
    reply_schema: str,
    instructions: str,
) -> None:
    if type(role) is not str or ROLE.fullmatch(role) is None:
        raise ValueError("owner role is not an identifier")
    if type(variable) is not str or VARIABLE.fullmatch(variable) is None:
        raise ValueError("owner variable is not an environment name")
    for schema in (request_schema, reply_schema):
        if type(schema) is not str or SCHEMA.fullmatch(schema) is None:
            raise ValueError("owner schema is not a versioned identifier")
    if request_schema == reply_schema:
        raise ValueError("owner request and reply schemas must differ")
    if (
        type(instructions) is not str
        or not instructions.isascii()
        or not 1 <= len(instructions) <= MAX_INSTRUCTIONS
    ):
        raise ValueError("owner instructions are not bounded ASCII")


def _offered(view: dict[str, Any]) -> list[str]:
    if type(view) is not dict:
        raise ValueError("owner view is not an object")
    choices = view.get("choices")
    if type(choices) is not list or not 1 <= len(choices) <= MAX_CHOICES:
        raise ValueError("owner view choices are not a bounded list")
    if len(set(choices)) != len(choices) or not all(
        type(choice) is str and CHOICE.fullmatch(choice) for choice in choices
    ):
        raise ValueError("owner view choices are malformed")
    return choices


def _executable(role: str, variable: str) -> str:
    path = os.environ.get(variable, "")
    if not path:
        raise OwnerFailure(role, "owner_unconfigured")
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise OwnerFailure(role, "owner_path_not_absolute")
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise OwnerFailure(role, "owner_not_executable")
    return path


def _request(
    request_schema: str, requested: str, instructions: str, view: dict[str, Any]
) -> bytes:
    body = {
        "schema": request_schema,
        "requested": requested,
        "instructions": instructions,
        "view": view,
    }
    text = json.dumps(body, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    payload = (text + "\n").encode("ascii")
    if len(payload) > REQUEST_LIMIT:
        raise ValueError("owner request exceeds its bound")
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


def _reply(role: str, reply_schema: str, data: bytes) -> dict[str, Any]:
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
    if value["schema"] != reply_schema:
        raise OwnerFailure(role, "owner_reply_schema_differs")
    return value


async def call_owner(
    *,
    role: str,
    variable: str,
    requested: str,
    request_schema: str,
    reply_schema: str,
    instructions: str,
    view: dict[str, Any],
) -> tuple[str, str | None]:
    _check_call(role, variable, request_schema, reply_schema, instructions)
    offered = _offered(view)
    if not _owner_id(requested):
        raise OwnerFailure(role, "requested_invalid")
    executable = _executable(role, variable)
    payload = _request(request_schema, requested, instructions, view)
    reply = _reply(role, reply_schema, await _run(role, executable, payload))
    if type(reply["requested"]) is not str or reply["requested"] != requested:
        raise OwnerFailure(role, "owner_requested_mismatch")
    choice = reply["choice"]
    if type(choice) is not str or choice not in offered:
        raise OwnerFailure(role, "owner_choice_not_offered")
    served = reply["served_model"]
    if served is not None and not _owner_id(served):
        raise OwnerFailure(role, "owner_served_model_invalid")
    return choice, served
