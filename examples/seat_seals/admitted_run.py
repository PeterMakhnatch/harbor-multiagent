import argparse
import asyncio
import json
import os
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..native_study import as_text, require
from ..shift_board.plan import TICKET
from .admission_wire import make_request, read_message, validate_reply, wire_bytes
from .worker_layout import digest, load_plan
from .worker_run import prepare_ticket, ticket_custody

REPORT_SCHEMA = "harbor.seat-seal.admitted-run/v1"
REQUEST_SCHEMA = "harbor.seat-seal.admission-request/v1"
REPLY_SCHEMA = "harbor.seat-seal.admission-reply/v1"
GRANTED = "granted"
ALREADY_ISSUED = "already_issued"
NONCE_BYTES = 16
MAX_ENVELOPE_BYTES = 4096
MAX_STDERR_BYTES = 16 << 10
MAX_READ_BYTES = 4096
MAX_PUMP_STEPS = 1 << 16
EXCHANGE_SECONDS = 60.0
TRANSPORT_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "SSH_AUTH_SOCK",
)
EXIT_SUCCESS = 0
EXIT_NATIVE_FAILURE = 1
EXIT_REFUSED = 2
EXIT_TAKEN = 3
STATUSES = {
    "completed": EXIT_SUCCESS,
    "native_failed": EXIT_NATIVE_FAILURE,
    "refused": EXIT_REFUSED,
    "admission_failed": EXIT_REFUSED,
    "blocked_after_grant": EXIT_REFUSED,
    "already_issued": EXIT_TAKEN,
    "occupied": EXIT_TAKEN,
}
NATIVE_STARTS = ("not_attempted", "blocked", "returned", "raised")

Record = dict[str, Any]


def trusted_via(path: Path) -> Path:
    require(isinstance(path, Path) and path.is_absolute(), "--via must be absolute")
    try:
        real = path.resolve(strict=True)
        info = os.stat(real)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"--via is unusable: {error}") from None
    require(stat.S_ISREG(info.st_mode), "--via is not a regular file")
    require(info.st_mode & 0o022 == 0, "--via is writable by group or others")
    require(info.st_uid in (0, os.geteuid()), "--via is owned by another user")
    require(os.access(real, os.X_OK), "--via is not executable")
    return real


def _transport_env() -> dict[str, str]:
    return {name: os.environ[name] for name in TRANSPORT_ENV if name in os.environ}


def _stop(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    if process.poll() is None:
        process.kill()
    process.wait()


def _feed(sink: int, pending: memoryview) -> memoryview:
    try:
        written = os.write(sink, pending)
    except BlockingIOError:
        return pending
    except BrokenPipeError:
        raise ValueError("transporter closed its input before the request") from None
    assert 0 <= written <= len(pending)
    return pending[written:]


def _pump(
    process: subprocess.Popen[bytes],
    request: bytes,
    deadline: float,
    received: dict[int, bytearray],
    caps: dict[int, int],
) -> None:
    assert process.stdin is not None and set(received) == set(caps)
    sink = process.stdin.fileno()
    pending = memoryview(request)
    os.set_blocking(sink, False)
    with selectors.DefaultSelector() as selector:
        selector.register(sink, selectors.EVENT_WRITE)
        for fd in caps:
            selector.register(fd, selectors.EVENT_READ)
        for _ in range(MAX_PUMP_STEPS):
            if not selector.get_map():
                break
            remaining = deadline - time.monotonic()
            require(remaining > 0, f"transporter exceeded {EXCHANGE_SECONDS} seconds")
            for key, _ in selector.select(remaining):
                if key.fd == sink:
                    pending = _feed(sink, pending)
                    if not pending:
                        selector.unregister(sink)
                        process.stdin.close()
                    continue
                chunk = os.read(key.fd, MAX_READ_BYTES)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                received[key.fd] += chunk
                require(
                    len(received[key.fd]) <= caps[key.fd],
                    f"transporter output exceeds {caps[key.fd]} bytes",
                )
        else:
            raise ValueError("transporter exchange exceeded its step bound")
    assert not pending


def _forward(stderr: bytes) -> None:
    if not stderr:
        return
    text = stderr.decode("utf-8", "replace")
    shown = "".join(c if c.isprintable() or c in "\n\t" else "?" for c in text)
    print(f"admitted_run: transporter stderr:\n{shown}", file=sys.stderr)


def exchange(via: Path, request: bytes) -> bytes:
    require(0 < len(request) <= MAX_ENVELOPE_BYTES, "request envelope is oversized")
    deadline = time.monotonic() + EXCHANGE_SECONDS
    try:
        process = subprocess.Popen(
            [os.fspath(via)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=_transport_env(),
            close_fds=True,
            start_new_session=True,
        )
    except OSError as error:
        raise ValueError(f"transporter did not start: {error.strerror}") from None
    assert process.stdout is not None and process.stderr is not None
    out, err = process.stdout.fileno(), process.stderr.fileno()
    received = {out: bytearray(), err: bytearray()}
    caps = {out: MAX_ENVELOPE_BYTES, err: MAX_STDERR_BYTES}
    finished = False
    try:
        _pump(process, request, deadline, received, caps)
        remaining = deadline - time.monotonic()
        require(remaining > 0, f"transporter exceeded {EXCHANGE_SECONDS} seconds")
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ValueError(
                f"transporter exceeded {EXCHANGE_SECONDS} seconds"
            ) from None
        finished = True
    finally:
        if not finished:
            _stop(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        _forward(bytes(received[err]))
    require(code == 0, f"transporter exited with status {code}")
    return bytes(received[out])


def admit(via: Path, plan_digest: str, worker: int, ticket: str, nonce: str) -> str:
    request = make_request(plan_digest, worker, ticket, nonce)
    binding = {"plan": plan_digest, "worker": worker, "ticket": ticket, "nonce": nonce}
    assert request == {"schema": REQUEST_SCHEMA, **binding}
    sent = wire_bytes(request)
    assert read_message(sent) == request
    data = exchange(via, sent)
    require(0 < len(data) <= MAX_ENVELOPE_BYTES, "coordinator reply is empty")
    reply = read_message(data)
    status = validate_reply(reply, request)
    require(status in (GRANTED, ALREADY_ISSUED), "coordinator status is unknown")
    receipt = {"schema": REPLY_SCHEMA, **binding, "status": status}
    require(
        reply == receipt and data == wire_bytes(receipt),
        "coordinator reply is not the literal receipt for this request",
    )
    return status


def _end(report: Record, status: str, reason: str | None = None) -> int:
    assert status in STATUSES and report["native_start"] in NATIVE_STARTS
    report["status"] = status
    report["reason"] = reason
    if reason is not None:
        print(f"admitted_run: {reason}", file=sys.stderr)
    return STATUSES[status]


def _failure(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _entry_absent(trials: Path, ticket: str) -> bool:
    try:
        os.lstat(trials / ticket)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def start(
    args: argparse.Namespace, plan: Record, native: argparse.Namespace, report: Record
) -> int:
    assert report["admission"] == GRANTED and native.ticket == args.ticket
    report["native_start"] = "blocked"
    try:
        observed = ticket_custody(
            args.registration, plan, args.worker, args.trials, args.ticket,
            bind_root=True,
        )
    except Exception as error:
        return _end(report, "blocked_after_grant", _failure(error))
    if observed != "absent":
        return _end(report, "occupied", f"ticket entry is {observed} after the grant")
    from . import run as native_run

    try:
        ok = asyncio.run(native_run.run(native, summary_to_stderr=True))
    except native_run.AlreadyStarted as error:
        return _end(report, "occupied", str(error))
    except Exception as error:
        if _entry_absent(args.trials, args.ticket):
            return _end(report, "blocked_after_grant", _failure(error))
        report["native_start"] = "raised"
        return _end(report, "native_failed", _failure(error))
    report["native_start"] = "returned"
    report["native_runner_ok"] = ok is True
    return _end(report, "completed" if ok is True else "native_failed")


def attempt(args: argparse.Namespace, report: Record) -> int:
    try:
        via = trusted_via(args.via)
        as_text(args.ticket, "ticket", TICKET)
        plan = load_plan(args.plan, expected=args.expected_plan)
        native = prepare_ticket(
            args.registration, plan, args.worker, args.task, args.trials,
            args.ticket, allow_named_seats=args.allow_named_seats,
        )
        observed = ticket_custody(
            args.registration, plan, args.worker, args.trials, args.ticket,
            bind_root=False,
        )
    except Exception as error:
        return _end(report, "refused", _failure(error))
    report["plan"] = digest(plan)
    if observed != "absent":
        return _end(report, "occupied", f"ticket entry is {observed}; nothing requested")
    nonce = secrets.token_hex(NONCE_BYTES)
    report["nonce"] = nonce
    try:
        status = admit(via, report["plan"], args.worker, args.ticket, nonce)
    except Exception as error:
        return _end(report, "admission_failed", _failure(error))
    report["admission"] = status
    if status == ALREADY_ISSUED:
        return _end(report, "already_issued", "coordinator already issued this ticket")
    return start(args, plan, native, report)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="python -B -m examples.seat_seals.admitted_run",
        description=(
            "Ask the trusted coordinator once for one assigned ticket and run it "
            "natively only on a fresh grant. Never retries, scans or releases."
        ),
    )
    cli.add_argument("registration", type=Path)
    cli.add_argument("plan", type=Path)
    cli.add_argument("task", type=Path)
    cli.add_argument("trials", type=Path)
    cli.add_argument("--worker", type=int, required=True)
    cli.add_argument("--ticket", required=True)
    cli.add_argument("--expected-plan", required=True, metavar="DIGEST")
    cli.add_argument(
        "--via", type=Path, required=True, metavar="ABS_EXEC",
        help="operator-configured local executable that carries one envelope",
    )
    cli.add_argument("--allow-named-seats", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report: Record = {
        "schema": REPORT_SCHEMA,
        "status": None,
        "ticket": args.ticket,
        "plan": args.expected_plan,
        "worker": args.worker,
        "nonce": None,
        "admission": None,
        "native_start": "not_attempted",
        "native_runner_ok": None,
        "reason": None,
    }
    code = attempt(args, report)
    assert code == STATUSES[report["status"]]
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
