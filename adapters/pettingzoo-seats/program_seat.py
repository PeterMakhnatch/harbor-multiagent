"""Host agent that runs one absolute program per seat decision. No model and no shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_HELPER = Path("/tmp/pz-program-seat/helper.py")
_COMMIT = Path("/tmp/pz-program-seat/commit.json")
_POLL_SEC = 0.05
_MAX_PROGRAM_BYTES = 4096
_MAX_FRAME_BYTES = 1_048_576
_PATH = "/usr/bin:/bin"
_SCHEMA = "seatlab.program-seat/v1"


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not finite canonical JSON") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class _Wire:
    """Same canonical JSON as runtime/wire.py. Stdout is capped in memory, not on disk."""

    canonical_bytes = staticmethod(canonical_bytes)
    canonical_digest = staticmethod(canonical_digest)


_WIRE = _Wire()


def _argv(argv: list[str]) -> tuple[str, ...]:
    if (
        not isinstance(argv, list)
        or not argv
        or any(type(item) is not str or not item for item in argv)
    ):
        raise ValueError("argv must be a nonempty list of strings")
    executable = Path(argv[0])
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise ValueError("argv[0] must be an absolute executable")
    return tuple(argv)


class ProgramSeat(BaseAgent):
    """One fresh process per decision. The process sees only the current view."""

    @staticmethod
    @override
    def name() -> str:
        return "program-seat"

    @override
    def version(self) -> str:
        return "1"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        argv: list[str],
        call_timeout_sec: int = 10,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name is not None:
            raise ValueError("ProgramSeat does not support model_name")
        if extra_env:
            raise ValueError("ProgramSeat does not support extra_env")
        if type(call_timeout_sec) is not int or call_timeout_sec < 1:
            raise ValueError("call_timeout_sec must be a positive int")
        self._argv = _argv(argv)
        self._timeout = call_timeout_sec
        self._calls = 0
        super().__init__(logs_dir, None, extra_env=None, **kwargs)

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        del instruction  # The program does not receive the task text.
        if not isinstance(environment, SeatEnvironment):
            raise TypeError("ProgramSeat requires the bound SeatEnvironment")
        role = environment.service
        self._meta(context, role, "waiting")
        try:
            await self._install(environment)
            last = 0
            while True:
                frame = await self._next(environment, last)
                if frame is None:
                    await asyncio.sleep(_POLL_SEC)
                    continue
                if frame.get("done") is True:
                    self._meta(context, role, "done")
                    return
                await self._decide(environment, context, role, frame)
                last = frame["step"]
        except Exception:
            self._meta(context, role, "failed")
            raise

    def _meta(self, context: AgentContext, role: str, stage: str) -> None:
        context.metadata = {
            "schema": _SCHEMA,
            "role": role,
            "program_calls": self._calls,
            "stage": stage,
        }

    async def _install(self, environment: SeatEnvironment) -> None:
        result = await environment.exec(
            "mkdir -p /tmp/pz-program-seat", timeout_sec=self._timeout
        )
        self._ok(result, "mkdir")
        source = Path(__file__).resolve().with_name("program_seat_helper.py")
        await environment.upload_file(source, str(_HELPER))

    async def _next(
        self, environment: SeatEnvironment, after: int
    ) -> dict[str, Any] | None:
        result = await environment.exec(
            f"python {_HELPER} next {after}", timeout_sec=self._timeout
        )
        if result.return_code == 3:
            return None
        self._ok(result, "next")
        raw = result.stdout or ""
        if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
            raise ValueError("seat frame exceeds the output bound")
        frame = _object(raw)
        if frame.get("done") is True and set(frame) == {"done"}:
            return frame
        view = frame.get("view")
        if type(frame.get("step")) is int and frame["step"] <= after:
            return None
        if (
            set(frame) != {"step", "view", "view_sha256"}
            or type(frame.get("step")) is not int
            or not isinstance(view, dict)
            or frame.get("view_sha256") != _WIRE.canonical_digest(view)
        ):
            raise ValueError("seat frame is invalid")
        return frame

    async def _decide(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        role: str,
        frame: dict[str, Any],
    ) -> None:
        self._calls += 1
        self._meta(context, role, "acting")
        action = await asyncio.to_thread(self._program, frame["view"])
        legal = frame["view"].get("legal_actions")
        if type(action) is not int or not isinstance(legal, list) or action not in legal:
            raise ValueError("program action is not legal")
        payload = {
            "step": frame["step"],
            "view_sha256": frame["view_sha256"],
            "action": action,
        }
        with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as handle:
            handle.write(_WIRE.canonical_bytes(payload) + b"\n")
            local = Path(handle.name)
        try:
            await environment.upload_file(local, str(_COMMIT))
        finally:
            local.unlink(missing_ok=True)
        result = await environment.exec(f"python {_HELPER} commit", timeout_sec=self._timeout)
        self._ok(result, "commit")

    def _program(self, view: dict[str, Any]) -> int:
        # Stderr is discarded. Stdout is capped in memory at 4097 bytes, not spooled to disk.
        home = tempfile.TemporaryDirectory(prefix="pz-program-home-")
        work = tempfile.TemporaryDirectory(prefix="pz-program-cwd-")
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=work.name,
                env={"PATH": _PATH, "HOME": home.name, "TMPDIR": home.name, "LANG": "C"},
                start_new_session=True,
                bufsize=0,
            )
            out = _read_bounded(process, canonical_bytes(view) + b"\n", monotonic() + self._timeout)
        finally:
            if process is not None:
                _reap(process)
            home.cleanup()
            work.cleanup()
        body = _object(out.decode("utf-8"))
        action = body.get("action")
        if set(body) != {"action"} or type(action) is not int:
            raise ValueError("program stdout must be one action object")
        return action

    @staticmethod
    def _ok(result: ExecResult, label: str) -> None:
        if result.return_code != 0:
            raise RuntimeError(f"seat helper {label} failed")


def _reap(process: subprocess.Popen[bytes]) -> None:
    # Kill only the session we started, and only before its leader is reaped.
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    for pipe in (process.stdin, process.stdout):
        if pipe is not None and not pipe.closed:
            pipe.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _read_bounded(process: subprocess.Popen[bytes], data: bytes, deadline: float) -> bytes:
    assert process.stdin is not None and process.stdout is not None
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    pending, output = memoryview(data), bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("program timed out")
                for key, event in selector.select(remaining):
                    try:
                        if event & selectors.EVENT_WRITE:
                            pending = pending[os.write(key.fd, pending) :]
                            if not pending:
                                selector.unregister(key.fd)
                                process.stdin.close()
                        else:
                            room = _MAX_PROGRAM_BYTES + 1 - len(output)
                            chunk = os.read(key.fd, room)
                            if not chunk:
                                selector.unregister(key.fd)
                                continue
                            output.extend(chunk)
                            if len(output) > _MAX_PROGRAM_BYTES:
                                raise ValueError("program failed or exceeded the output bound")
                    except BlockingIOError:
                        continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("program timed out")
        if process.wait(timeout=remaining) != 0:
            raise ValueError("program failed or exceeded the output bound")
    except subprocess.TimeoutExpired:
        raise TimeoutError("program timed out") from None
    return bytes(output)


def _object(text: str) -> dict[str, Any]:
    line = text[:-1] if text.endswith("\n") else text
    if not line or "\n" in line:
        raise ValueError("expected one JSON object")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("expected one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("expected one JSON object")
    return value
