import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from .design import TIMINGS, WORLDS
from .native import ENVIRONMENT, layout

TASK = ENVIRONMENT.parent
SERVER = ENVIRONMENT / "recall_server.py"
SEAT = ENVIRONMENT / "seat"
REFERENCE = TASK / "solution" / "reference.py"
VERIFIER = TASK / "tests" / "verify.py"
SEAT_SHADOW = Path("/app/recall.py")
SHORT_ROOT = Path("/tmp")
SCHEMA = "harbor.dispatch-recall.rehearsal/v1"
FIXTURE = "scripted-reference"
PARTICIPANTS = (
    "solution/reference.py run as one OS process per role; "
    "a scripted fixture, not a free model team"
)
ISOLATION = (
    "separate OS processes under one user, each given only its own role socket "
    "path; the same UID can still reach every socket"
)
ROLES = ("loader", "depot")
OUTPUTS = ("verdict.json", "diagnostics.json", "reward.json")
PRIVATE_CUT = "private-recall-cut.json"
FORWARDED = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
DEADLINE_SECONDS = 60.0
READY_SECONDS = 10.0
POLL_SECONDS = 0.05
STOP_SECONDS = 5.0
SERVER_LIFETIME_SECONDS = DEADLINE_SECONDS + STOP_SECONDS
OUTPUT_LIMIT = 2048
TEARDOWN = (
    "SIGTERM, SIGHUP or SIGINT to the caller stops every started child and "
    "removes the private sandbox before the signal is re-delivered. SIGKILL to "
    "the caller skips all cleanup: the task server exits by itself after "
    f"{SERVER_LIFETIME_SECONDS:g} s, seat clients then fail, and the private "
    "sandbox under the temp directory stays on disk until the operator removes it"
)


class RehearsalInterrupted(BaseException):
    def __init__(self, number: int) -> None:
        super().__init__(f"dispatch-recall rehearsal interrupted by signal {number}")
        self.number = number


class _Signals:
    def __init__(self) -> None:
        self.received: int | None = None
        self.armed = True
        self.previous: dict[int, Any] = {}

    def _handle(self, number: int, frame: FrameType | None) -> None:
        if self.received is None:
            self.received = number
        self.check()

    def check(self) -> None:
        if self.armed and self.received is not None:
            self.armed = False
            raise RehearsalInterrupted(self.received)

    @contextmanager
    def held(self) -> Iterator[None]:
        self.armed = False
        try:
            yield
        finally:
            self.armed = True
        self.check()

    @contextmanager
    def installed(self) -> Iterator[None]:
        if threading.current_thread() is threading.main_thread():
            for number in FORWARDED:
                current = signal.getsignal(number)
                if current is None or current is signal.SIG_IGN:
                    continue
                self.previous[number] = current
                signal.signal(number, self._handle)
        try:
            yield
        finally:
            self.armed = False
            for number, handler in self.previous.items():
                signal.signal(number, handler)
            if self.received is not None:
                signal.raise_signal(self.received)


@dataclass(frozen=True)
class _Child:
    name: str
    home: Path
    process: subprocess.Popen[bytes]


def _check(seed: int, transition: str, timing: str) -> None:
    layout(seed)
    if transition not in WORLDS:
        raise ValueError(f"transition must be one of {list(WORLDS)}")
    if timing not in TIMINGS:
        raise ValueError(f"timing must be one of {list(TIMINGS)}")
    if SEAT_SHADOW.exists():
        raise RuntimeError(f"{SEAT_SHADOW} would shadow the task seat client")


def _tail(child: _Child) -> str:
    data = (child.home / "output").read_bytes()
    return data[-OUTPUT_LIMIT:].decode("utf-8", "replace").strip()


def _signal(child: _Child, number: int) -> None:
    try:
        os.killpg(child.process.pid, number)
    except ProcessLookupError:
        pass


def _stop(child: _Child) -> None:
    if child.process.poll() is None:
        _signal(child, signal.SIGTERM)
        try:
            child.process.wait(STOP_SECONDS)
        except subprocess.TimeoutExpired:
            _signal(child, signal.SIGKILL)
    child.process.wait()


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("dispatch-recall rehearsal exceeded its deadline")
    return left


class _Fleet:
    def __init__(self, signals: _Signals) -> None:
        self.signals = signals
        self.sandbox: Path | None = None
        self.children: list[_Child] = []

    def open(self) -> Path:
        base = SHORT_ROOT if SHORT_ROOT.is_dir() else Path(tempfile.gettempdir())
        with self.signals.held():
            self.sandbox = Path(tempfile.mkdtemp(prefix="rr-", dir=base))
        return self.sandbox

    def spawn(
        self, name: str, home: Path, argv: list[str], extra: dict[str, str], cwd: Path
    ) -> _Child:
        home.mkdir(mode=0o700)
        env = {
            "PATH": os.defpath,
            "HOME": str(home),
            "TMPDIR": str(home),
            "LC_ALL": "C.UTF-8",
            **extra,
        }
        with self.signals.held(), (home / "output").open("wb") as output:
            process = subprocess.Popen(
                [sys.executable, *argv],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
            child = _Child(name, home, process)
            self.children.append(child)
        return child

    def finish(self, child: _Child, deadline: float) -> None:
        try:
            code = child.process.wait(_remaining(deadline))
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"{child.name} did not finish in time") from error
        if code != 0:
            raise RuntimeError(f"{child.name} exited {code}: {_tail(child)}")

    def close(self) -> None:
        self.signals.armed = False
        for child in reversed(self.children):
            _stop(child)
        if self.sandbox is not None:
            shutil.rmtree(self.sandbox, ignore_errors=True)

    def closed(self) -> None:
        if self.sandbox is None or self.sandbox.exists():
            raise RuntimeError(f"rehearsal sandbox was not removed: {self.sandbox}")
        stopped = self.children[0].process.returncode
        if stopped != 0:
            raise RuntimeError(f"task server stopped with exit {stopped}")


def _await_server(server: _Child, sockets: list[Path], deadline: float) -> None:
    ready = min(deadline, time.monotonic() + READY_SECONDS)
    for _ in range(int(READY_SECONDS / POLL_SECONDS) + 1):
        code = server.process.poll()
        if code is not None:
            raise RuntimeError(f"{server.name} exited {code}: {_tail(server)}")
        if all(path.is_socket() for path in sockets):
            return
        if time.monotonic() >= ready:
            break
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"{server.name} did not bind its role and control sockets")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} is not a JSON object")
    return value


def _collect(logs: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    verdict, diagnosis, reward = (_read(logs / name) for name in OUTPUTS)
    private = logs / PRIVATE_CUT
    if stat.S_IMODE(private.lstat().st_mode) != 0o600:
        raise RuntimeError("verifier retained the private cut without mode 0600")
    if reward != {"reward": verdict["reward"]}:
        raise RuntimeError("reward.json disagrees with verdict.json")
    return verdict, diagnosis, _read(private)


def _run(
    fleet: _Fleet, condition: dict[str, str], deadline: float
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    sandbox = fleet.open()
    root, control, logs = sandbox / "s", sandbox / "c" / "rpc.sock", sandbox / "logs"
    knobs = {
        **condition,
        "RECALL_SOCKET_ROOT": str(root),
        "RECALL_CONTROL_DIR": str(control.parent),
    }
    server = fleet.spawn(
        "task server",
        sandbox / "server",
        ["-E", "-s", "-B", str(SERVER)]
        + ["--lifetime-seconds", str(SERVER_LIFETIME_SECONDS)],
        knobs,
        ENVIRONMENT,
    )
    seats = {role: root / role / "rpc.sock" for role in ROLES}
    _await_server(server, [*seats.values(), control], deadline)
    clients = [
        fleet.spawn(
            f"{role} client",
            sandbox / role,
            ["-s", "-B", str(REFERENCE)],
            {"RECALL_SEAT_SOCKET": str(seats[role]), "PYTHONPATH": str(SEAT)},
            sandbox / role,
        )
        for role in ROLES
    ]
    for client in clients:
        fleet.finish(client, deadline)
    verifier = fleet.spawn(
        "verifier",
        sandbox / "verifier",
        ["-I", "-B", str(VERIFIER), "--control", str(control)]
        + ["--logs", str(logs), "--app", str(ENVIRONMENT)],
        {"RECALL_RETAIN_CUT": "1"},
        VERIFIER.parent,
    )
    fleet.finish(verifier, deadline)
    return _collect(logs)


def rehearse(seed: int, transition: str, timing: str) -> dict[str, Any]:
    _check(seed, transition, timing)
    condition = {
        "RECALL_SEED": str(seed),
        "RECALL_TRANSITION": transition,
        "RECALL_TIMING": timing,
    }
    deadline = time.monotonic() + DEADLINE_SECONDS
    signals = _Signals()
    with signals.installed():
        fleet = _Fleet(signals)
        try:
            verdict, diagnosis, cut = _run(fleet, condition, deadline)
        finally:
            fleet.close()
    fleet.closed()
    requested = (seed, transition, timing)
    if (cut.get("seed"), cut.get("transition"), cut.get("timing")) != requested:
        raise RuntimeError("retained cut does not match the requested condition")
    return {
        "schema": SCHEMA,
        "summary": {
            "fixture": FIXTURE,
            "harbor_trial": False,
            "participants": PARTICIPANTS,
            "isolation": ISOLATION,
            "teardown": TEARDOWN,
            "verdict": verdict,
            "diagnosis": diagnosis,
        },
        "cut": cut,
    }
