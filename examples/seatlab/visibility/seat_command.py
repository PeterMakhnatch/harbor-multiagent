"""One POSIX process per turn for trusted seat programs, not a sandbox."""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

_MAX_CONFIG_BYTES = 64 * 1024
_MAX_TEXT_BYTES = 8192
# Allow six bytes per escaped byte of text, plus JSON framing and padding.
_MAX_REPLY_BYTES = 6 * _MAX_TEXT_BYTES + 256
_TRACING_OFF = {
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "OPENAI_AGENTS_DISABLE_TRACING": "1",
    "OTEL_SDK_DISABLED": "true",
}


def _allowed_env_name(name: str) -> bool:
    upper = name.upper()
    return (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None
        and upper not in {"PATH", "HOME"}
        and upper not in _TRACING_OFF
        and not upper.startswith(("PYTHON", "DYLD", "LD_"))
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("seat command timed out")
    return seconds


def _exchange_line(
    process: subprocess.Popen[bytes],
    request: bytes,
    deadline: float,
    *,
    max_reply_bytes: int,
) -> bytearray:
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin.fileno(), process.stdout.fileno()
    os.set_blocking(stdin, False)
    os.set_blocking(stdout, False)
    response = bytearray()
    sent = 0
    with memoryview(request) as payload, selectors.DefaultSelector() as ready:
        ready.register(stdin, selectors.EVENT_WRITE)
        ready.register(stdout, selectors.EVENT_READ)
        while ready.get_map():
            for key, _ in ready.select(min(_remaining(deadline), 3600.0)):
                if key.fd == stdin:
                    try:
                        sent += os.write(stdin, payload[sent:])
                    except BlockingIOError:
                        continue
                    if sent == len(request):
                        ready.unregister(stdin)
                        process.stdin.close()
                    continue
                try:
                    chunk = os.read(
                        stdout, min(8192, max_reply_bytes - len(response) + 1)
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    ready.unregister(stdout)
                    continue
                if len(response) + len(chunk) > max_reply_bytes:
                    raise ValueError("seat command reply is too large")
                if response.endswith(b"\n") or chunk.find(b"\n") not in (
                    -1,
                    len(chunk) - 1,
                ):
                    raise ValueError("invalid seat command reply")
                response.extend(chunk)

    if not response.endswith(b"\n"):
        raise ValueError("invalid seat command reply")
    return response


@contextmanager
def _command_line(
    argv: Sequence[str],
    request: bytes,
    *,
    deadline: float,
    max_reply_bytes: int,
    search_path: str,
    environ: Mapping[str, str],
) -> Iterator[bytearray]:
    with (
        TemporaryDirectory(prefix="seatlab-turn-") as home,
        subprocess.Popen(
            argv,
            cwd=home,
            env={"PATH": search_path, "HOME": home, **_TRACING_OFF, **environ},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process,
    ):
        try:
            yield _exchange_line(
                process, request, deadline, max_reply_bytes=max_reply_bytes
            )
            if process.wait(timeout=_remaining(deadline)) != 0:
                raise ValueError("seat command failed")
        finally:
            _reap_and_kill_group(process)


def _reap_and_kill_group(process: subprocess.Popen[bytes]) -> None:
    process.poll()
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            process.kill()
            raise


def _decode_text(response: bytearray) -> str:
    try:
        frame = json.loads(
            response.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if (
            not isinstance(frame, dict)
            or set(frame) != {"text"}
            or not isinstance(frame["text"], str)
        ):
            raise ValueError
        text = frame["text"]
        size = len(text.encode("utf-8"))
    except (ValueError, RecursionError):
        raise ValueError("invalid seat command reply") from None
    if size > _MAX_TEXT_BYTES:
        raise ValueError("seat command reply is too large")
    return text


def _process_text(value: object) -> bool:
    return isinstance(value, str) and "\0" not in value


class CommandSeat:
    """Call a trusted program with an explicit environment and a private working directory.

    The executable can still use the network and access same-user files. Bad turns
    raise ValueError or TimeoutError without exposing stderr, argv, or the reply.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        environ: Mapping[str, str] | None = None,
        timeout: float = 90.0,
    ) -> None:
        args = () if isinstance(argv, (str, bytes)) else tuple(argv)
        if (
            not args
            or not all(_process_text(arg) for arg in args)
            or not Path(args[0]).is_absolute()
        ):
            raise ValueError("invalid seat command argv")
        try:
            valid_timeout = (
                not isinstance(timeout, bool) and math.isfinite(timeout) and timeout > 0
            )
        except (TypeError, OverflowError):
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("invalid seat command timeout")
        self._argv = args
        self._timeout = timeout
        self._environ = dict(environ) if environ is not None else {}
        if any(
            not _process_text(name)
            or not _allowed_env_name(name)
            or not _process_text(value)
            for name, value in self._environ.items()
        ):
            raise ValueError("invalid seat command environment")

    def __call__(self, prompt: str) -> str:
        deadline = time.monotonic() + self._timeout
        try:
            if not isinstance(prompt, str):
                raise TypeError
            request = (
                json.dumps(
                    {"schema": "seatlab.seat-turn/v1", "prompt": prompt},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, UnicodeError):
            raise ValueError("invalid seat command prompt") from None
        try:
            with _command_line(
                self._argv,
                request,
                deadline=deadline,
                max_reply_bytes=_MAX_REPLY_BYTES,
                search_path="/usr/bin:/bin",
                environ=self._environ,
            ) as response:
                return _decode_text(response)
        except (TimeoutError, subprocess.TimeoutExpired):
            raise TimeoutError("seat command timed out") from None
        except (OSError, UnicodeError):
            raise ValueError("seat command could not complete") from None


def _string_argv(values: list[Any]) -> list[str]:
    argv = [value for value in values if isinstance(value, str)]
    if len(argv) != len(values):
        raise ValueError("invalid seat command argv")
    return argv


def _host_environ(names: list[Any]) -> dict[str, str]:
    environ: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str) or not _allowed_env_name(name) or name in environ:
            raise ValueError("invalid seat command environment name")
        value = os.environ.get(name)
        if value is None:
            raise ValueError("required seat command environment variable is missing")
        environ[name] = value
    return environ


def _configured_seat(spec: object) -> CommandSeat:
    if not isinstance(spec, dict) or set(spec) != {"argv", "env"}:
        raise ValueError("invalid seat command role")
    fields = cast("dict[str, object]", spec)
    match fields["argv"], fields["env"]:
        case list() as values, list() as names:
            environ = _host_environ(names)
            return CommandSeat(_string_argv(values), environ=environ)
        case _:
            raise ValueError("invalid seat command role")


def _config_text(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_CONFIG_BYTES:
            raise ValueError("invalid seat command config")
        data = bytearray()
        while len(data) <= _MAX_CONFIG_BYTES:
            chunk = os.read(fd, _MAX_CONFIG_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
    finally:
        os.close(fd)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("invalid seat command config")
    return data.decode("utf-8", errors="strict")


def load_commands(
    path: Path, *, roles: tuple[str, str] = ("reviewer", "decider")
) -> tuple[CommandSeat, CommandSeat]:
    """Load two distinct named seats in their requested order."""
    first, second = roles
    if not first or not second or first == second:
        raise ValueError("expected two different seat roles")
    try:
        config = json.loads(
            _config_text(path),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid seat command config") from None
    if not isinstance(config, dict) or set(config) != {first, second}:
        raise ValueError("invalid seat command config")
    return _configured_seat(config[first]), _configured_seat(config[second])
