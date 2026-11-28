import os
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from .. import wire_policy
from .manifest import load, pick

CHECKOUT = Path(__file__).resolve().parents[4]
DEADLINE = 60.0


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


def _one(root: Path, expected: str, ticket: str) -> None:
    operator = [
        sys.executable,
        "-BS",
        "-m",
        "examples.seatlab.receiver_memory.study",
        "serve",
        str(root.resolve()),
        ticket,
        "--expect",
        expected,
    ]
    receiver = [
        sys.executable,
        "-IS",
        str(Path(wire_policy.__file__).resolve()),
        "by-tag",
    ]
    deadline = time.monotonic() + DEADLINE
    with (
        tempfile.TemporaryDirectory(prefix="receiver-control-") as scratch,
        ExitStack() as stack,
    ):
        a, b = socket.socketpair()
        with a, b:
            session = subprocess.Popen(
                operator,
                cwd=CHECKOUT,
                stdin=a.fileno(),
                stdout=a.fileno(),
                env={"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": scratch},
                close_fds=True,
            )
            stack.callback(_stop, session)
            owner = subprocess.Popen(
                receiver,
                cwd=scratch,
                stdin=b.fileno(),
                stdout=b.fileno(),
                env={"LC_ALL": "C"},
                close_fds=True,
            )
            stack.callback(_stop, owner)
        for child in (session, owner):
            try:
                status = child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError("scripted receiver deadline elapsed") from None
            if status != 0:
                raise ValueError("scripted receiver or session did not finish")


def run_controls(
    root: Path, expected: str, tickets: list[str] | None, workers: int
) -> dict[str, object]:
    plan = load(root, expected)
    if (
        plan["source"] != "scripted_control"
        or type(workers) is not int
        or workers not in (1, 2)
    ):
        raise ValueError("use a scripted-control plan and at most two local workers")
    chosen = (
        [row["ticket"] for row in plan["assignments"]] if tickets is None else tickets
    )
    if not 1 <= len(chosen) <= len(plan["assignments"]) or len(chosen) != len(
        set(chosen)
    ):
        raise ValueError("invalid control assignment")
    for ticket in chosen:
        pick(plan, ticket)
        for kind in ("start", "result"):
            if os.path.lexists(root / "attempts" / f"{ticket}.{kind}.json"):
                raise ValueError("ticket already attempted")
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        jobs = [pool.submit(_one, root, expected, ticket) for ticket in chosen]
        for job in jobs:
            job.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return {"complete": len(chosen), "policy": "by-tag", "model_calls": 0, "trials": 0}
