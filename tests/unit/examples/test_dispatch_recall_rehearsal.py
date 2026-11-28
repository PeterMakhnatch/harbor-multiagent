import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from examples.seatlab.dispatch_recall.rehearse import rehearse

ROOT = Path(__file__).resolve().parents[3]
PREFIX = [
    ("loader", "stage"),
    ("depot", "report"),
    ("loader", "commit"),
    ("loader", "close"),
]
POLL_SECONDS = 0.05
WAIT_SECONDS = 30.0
HOLD = """\
import signal
import sys
from pathlib import Path

from examples.seatlab.dispatch_recall import rehearse

root, pids = Path(sys.argv[1]), Path(sys.argv[2])
rehearse.SERVER_LIFETIME_SECONDS = float(sys.argv[3])
spawn = rehearse._Fleet.spawn


def record(fleet, *args):
    child = spawn(fleet, *args)
    with pids.open("a") as out:
        out.write(f"{child.process.pid}\\n")
    return child


for number in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(number, signal.SIG_DFL)
rehearse.SHORT_ROOT = root
rehearse.ROLES = ("depot",)
rehearse._Fleet.spawn = record
rehearse.rehearse(7, "move", "immediate")
"""


def test_late_report_leaves_the_scripted_loader_on_the_stale_bay() -> None:
    immediate = rehearse(7, "move", "immediate")
    deferred = rehearse(7, "move", "deferred")

    for run in (immediate, deferred):
        cut = run["cut"]
        assert run["summary"]["fixture"] == "scripted-reference"
        assert run["summary"]["harbor_trial"] is False
        assert [(a["role"], a["op"]) for a in cut["actions"]] == PREFIX
        assert cut["actions"][1]["bay"] == cut["current_bay"] != cut["initial_bay"]
        assert run["summary"]["verdict"]["disposition"] == "committed"
        assert run["summary"]["verdict"]["closed"] is True
    assert immediate["cut"]["actions"][1] == deferred["cut"]["actions"][1]

    now = immediate["cut"]
    assert now["windows"]["commit"]["notice"] == now["current_bay"]
    assert now["actions"][2]["bay"] == now["current_bay"]
    assert immediate["summary"]["verdict"]["reward"] == 1

    late = deferred["cut"]
    assert late["windows"]["commit"]["notice"] == "--"
    assert late["windows"]["close"]["notice"] == late["current_bay"]
    assert late["actions"][2]["bay"] == late["initial_bay"]
    assert deferred["summary"]["verdict"]["reward"] == 0


def until[T](condition: Callable[[], T | None], what: str) -> T:
    deadline = time.monotonic() + WAIT_SECONDS
    for _ in range(int(WAIT_SECONDS / POLL_SECONDS) + 1):
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"timed out waiting for {what}")


def recorded(pids: Path) -> list[int] | None:
    if not pids.exists():
        return None
    lines = [line for line in pids.read_text().splitlines(True) if line.endswith("\n")]
    return [int(line) for line in lines] if len(lines) >= 2 else None


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def start(root: Path, lifetime: float) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            HOLD,
            str(root),
            str(root / "pids"),
            str(lifetime),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def bound(root: Path) -> tuple[int, int, Path]:
    server, depot = until(lambda: recorded(root / "pids"), "a bound server and seat")
    (sandbox,) = root.glob("rr-*")
    assert (sandbox / "c" / "rpc.sock").is_socket()
    assert (sandbox / "s" / "loader" / "rpc.sock").is_socket()
    assert not gone(server)
    return server, depot, sandbox


def stopped(holder: subprocess.Popen[bytes], number: signal.Signals) -> None:
    holder.send_signal(number)
    _, error = holder.communicate(timeout=WAIT_SECONDS)
    assert holder.returncode == -number, error.decode(errors="replace")


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP])
def test_signalling_the_caller_stops_the_server_and_removes_the_sandbox(
    number: signal.Signals,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="rt-", dir="/tmp"))
    holder = start(root, 65.0)
    try:
        server, depot, sandbox = bound(root)
        stopped(holder, number)
        assert gone(server) and gone(depot)
        assert not sandbox.exists()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        shutil.rmtree(root, ignore_errors=True)


def test_killed_caller_leaves_the_sandbox_and_a_server_bounded_by_its_lifetime() -> (
    None
):
    root = Path(tempfile.mkdtemp(prefix="rt-", dir="/tmp"))
    holder = start(root, 5.0)
    try:
        server, depot, sandbox = bound(root)
        stopped(holder, signal.SIGKILL)
        assert not gone(server)
        until(lambda: gone(server) and gone(depot), "the server lifetime to end")
        assert sandbox.is_dir()
        assert not (sandbox / "c" / "rpc.sock").exists()
        assert not (sandbox / "s" / "depot" / "rpc.sock").exists()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        shutil.rmtree(root, ignore_errors=True)
