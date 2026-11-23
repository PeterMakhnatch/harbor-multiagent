from __future__ import annotations

import argparse
import errno
import fcntl
import os
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path
from types import FrameType
from typing import Any

from examples.seatlab.market_handoff import (
    decisions,
    native_panel,
    native_receipt,
    native_stage,
)

SCHEMA = "seatlab.market-native-panel-work/v1"
ROOT = Path(__file__).resolve().parents[3]
REUSED = native_panel.PUBLISHED
CASES = tuple(native_panel.NAMES[cell] for cell in native_panel.CELLS)
LOCK, LOG, EXIT = "native-panel.lock", "console.log", "worker-exit.json"
BUSY = frozenset({errno.EACCES, errno.EAGAIN})
CHILD_TIMEOUT = 7200
STOP_GRACE = ((signal.SIGTERM, 120), (signal.SIGKILL, 30))
FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    RecursionError,
    subprocess.SubprocessError,
)
COMPARED = (
    "trial_name",
    "kind",
    "world",
    "arm",
    "choices",
    "grade",
    "multiple",
    "prefix_fingerprint",
)
Record = dict[str, Any]

assert len(CASES) == 24 and set(REUSED) <= set(CASES)


fail = native_stage.fail


def expected(root: Path) -> dict[str, Record]:
    _, originals = native_panel.originals(root)
    table: dict[str, Record] = {}
    for cell, row in originals.items():
        if row["choices"]["finance"] != row["archived_pick"]:
            fail(
                f"{row['trial_name']}: sealed replies disagree with the archived decision"
            )
        table[native_panel.NAMES[cell]] = {key: row[key] for key in COMPARED}
    reused = native_panel.published(root)
    if any(divergence(reused[name], table[name]) for name in REUSED):
        fail("native.json: published replay receipts differ from the registered reuse")
    return table


def divergence(receipt: Record, want: Record) -> list[str]:
    return [key for key in COMPARED if receipt.get(key) != want[key]]


def private_campaign(path: Path) -> Path:
    path = path.expanduser().absolute()
    if not os.path.lexists(path):
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    details, resolved = path.lstat(), path.resolve(strict=True)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
        or resolved.is_relative_to(ROOT)
    ):
        fail("CAMPAIGN must be an owner-only 0700 real directory outside the fork")
    return resolved


def executable(path: Path, label: str) -> Path:
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        fail(f"{label} must be an absolute executable file")
    return path


def child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    host = os.environ.get("DOCKER_HOST")
    if host is not None:
        if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
            fail("DOCKER_HOST must name a local Unix socket")
        socket = Path(host.removeprefix("unix://")).resolve(strict=True)
        if not stat.S_ISSOCK(socket.stat().st_mode):
            fail("DOCKER_HOST must name a local Unix socket")
        env["DOCKER_HOST"] = f"unix://{socket}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def claim(fd: int, index: int) -> bool:
    assert fd >= 0 and 0 <= index < len(CASES)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, index, os.SEEK_SET)
    except OSError as error:
        if error.errno in BUSY:
            return False
        raise
    return True


def stop(child: subprocess.Popen[bytes]) -> None:
    for sig, grace in STOP_GRACE:
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def record_exit(out: Path, status: Record) -> None:
    native_stage.create(out / EXIT, native_stage.canonical(status) + b"\n", 0o600)


def launch(out: Path, command: list[str], env: dict[str, str]) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    log = os.open(out / LOG, flags, 0o600)
    try:
        child = subprocess.Popen(
            [*command, str(out)],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        record_exit(out, {"returncode": None, "stopped": False, "spawned": False})
        raise
    finally:
        os.close(log)
    stopped = True
    try:
        child.wait(timeout=CHILD_TIMEOUT)
        stopped = False
    finally:
        if stopped:
            stop(child)
        record_exit(
            out, {"returncode": child.returncode, "stopped": stopped, "spawned": True}
        )
    assert child.returncode is not None
    return child.returncode


def progress(out: Path) -> Record:
    found = [
        step
        for step, name in (
            ("staged", "config.json"),
            ("launched", LOG),
            ("trial_started", "trials"),
        )
        if (out / name).exists()
    ]
    state: Record = {"progress": found[-1] if found else "reserved", "exit": None}
    if (out / EXIT).exists():
        try:
            state["exit"] = decisions.decode(decisions.read_text(out / EXIT))
        except FAILURES:
            state["exit"] = "unreadable"
    state["child_may_survive"] = state["exit"] is None and "launched" in found
    return state


def settle(out: Path, want: Record) -> tuple[str, Any]:
    try:
        receipt = native_receipt.receipt(out)
    except FAILURES as error:
        return "incomplete", {
            **progress(out),
            "receipt_error": clean(error, [(out, out.name)]),
        }
    wrong = divergence(receipt, want)
    if wrong:
        return "divergent", {
            "fields": wrong,
            "observed": {k: receipt.get(k) for k in wrong},
            "expected": {k: want[k] for k in wrong},
        }
    return "complete", receipt


def attempt(
    out: Path, want: Record, data: Path, command: list[str], env: dict[str, str]
) -> tuple[str, Any]:
    staged = native_stage.stage(
        argparse.Namespace(
            data=data,
            out=out,
            world=want["world"],
            arm=want["arm"],
            mode="replay",
            sales_replies=data / "sales-first.jsonl",
            finance_replies=data / "finance-first.jsonl",
        )
    )
    if staged["picks"] != want["choices"]:
        return "divergent", {
            "fields": ["choices"],
            "observed": staged["picks"],
            "expected": want["choices"],
            "launched": False,
        }
    code = launch(out, command, env)
    if code != 0:
        return "failed", {"returncode": code}
    outcome, detail = settle(out, want)
    if outcome == "incomplete":
        return "failed", {"returncode": 0, "receipt_error": detail["receipt_error"]}
    return outcome, detail


def visit(
    campaign: Path,
    fd: int,
    name: str,
    want: Record,
    data: Path,
    command: list[str],
    env: dict[str, str],
) -> tuple[str, Any, bool]:
    index = CASES.index(name)
    if not claim(fd, index):
        return "busy", None, False
    try:
        out = campaign / name
        try:
            out.mkdir(mode=0o700)
        except FileExistsError:
            if out.is_symlink() or not out.is_dir():
                return "incomplete", {"progress": "not_a_directory"}, False
            outcome, detail = settle(out, want)
            return (
                ("already_complete" if outcome == "complete" else outcome),
                detail,
                False,
            )
        outcome, detail = attempt(out, want, data, command, env)
        return ("completed" if outcome == "complete" else outcome), detail, True
    finally:
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, index, os.SEEK_SET)


def clean(error: BaseException, roots: list[tuple[Path, str]]) -> str:
    text = str(error)
    if isinstance(error, OSError) and error.filename:
        text = f"{error.strerror or 'error'}: {Path(str(error.filename)).name}"
    for root, label in sorted(roots, key=lambda item: -len(str(item[0]))):
        text = text.replace(str(root), label)
    return f"{type(error).__name__}: {text}"


def work(
    args: argparse.Namespace, report: Record, roots: list[tuple[Path, str]]
) -> None:
    data = native_panel.pinned(args.data)
    roots.append((data, "DATA"))
    table = expected(data)
    env = child_env()
    command = [
        str(executable(args.slot_wrapper, "--slot-wrapper")),
        str(executable(args.harbor_python, "--harbor-python")),
        "-B",
        "-m",
        "examples.seatlab.market_handoff.native_run",
    ]
    campaign = private_campaign(args.campaign)
    roots.append((campaign, "CAMPAIGN"))
    shard, count = args.shard
    mine = [name for index, name in enumerate(CASES) if index % count == shard]
    report["reused"] = [name for name in mine if name in REUSED]
    report["assigned"] = [name for name in mine if name not in REUSED]
    report["unvisited"] = list(report["assigned"])
    fd = os.open(
        campaign / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            fail("CAMPAIGN lock is not a regular file")
        started = 0
        for name in report["assigned"]:
            if started >= args.limit:
                break
            report["unvisited"].remove(name)
            report["current"] = name
            try:
                outcome, detail, fresh = visit(
                    campaign, fd, name, table[name], data, command, env
                )
            except FAILURES as error:
                outcome, detail, fresh = "failed", {"error": clean(error, roots)}, True
            report["current"] = None
            started += fresh
            report[outcome][name] = detail
            if fresh and outcome != "completed":
                break
    finally:
        os.close(fd)


def shard_spec(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", text)
    if not match or not 0 <= int(match[1]) < int(match[2]) <= len(CASES):
        raise argparse.ArgumentTypeError("use K/N with 0 <= K < N <= 24")
    return int(match[1]), int(match[2])


def terminate(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.seatlab.market_handoff.native_panel_work"
    )
    parser.add_argument("data", type=Path)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--harbor-python", type=Path, required=True)
    parser.add_argument("--slot-wrapper", type=Path, required=True)
    parser.add_argument("--shard", type=shard_spec, required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=len(CASES),
        choices=range(1, len(CASES) + 1),
        metavar="1..24",
    )
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    report: Record = {
        "schema": SCHEMA,
        "shard": "{}/{}".format(*args.shard),
        "limit": args.limit,
        "assigned": [],
        "reused": [],
        "unvisited": [],
        "current": None,
        "completed": {},
        "already_complete": {},
        "busy": {},
        "incomplete": {},
        "divergent": {},
        "failed": {},
        "error": None,
    }
    roots = [(ROOT, "ROOT"), (Path.home(), "HOME")]
    code = 0
    try:
        work(args, report, roots)
    except FAILURES as error:
        report["error"], code = clean(error, roots), 2
    except (KeyboardInterrupt, SystemExit) as error:
        report["error"] = f"interrupted: {type(error).__name__}"
        code = 130
    done = set(report["completed"]) | set(report["already_complete"])
    report["complete"] = bool(report["assigned"]) and done == set(report["assigned"])
    if not code and (report["incomplete"] or report["divergent"] or report["failed"]):
        code = 1
    sys.stdout.write(native_stage.canonical(report).decode("ascii") + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
