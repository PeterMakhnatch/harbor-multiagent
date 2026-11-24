from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from examples.seatlab.visibility import agy_seat
from examples.seatlab.wait_peer import receipt as owner_receipt

ROLES = ("sales", "finance-price", "finance-receipt")
SEAT = Path(agy_seat.__file__).resolve(strict=True)
NOTE = "admission.json"
TURN_SCHEMA = "seatlab.seat-turn/v1"
ADMISSION_KEYS = {"schema", "outcome", "stage", "cause"}
MAX_FRAME = 65536
MAX_STDOUT = 65536
MAX_NOTE = 512
TURN_SECONDS = 90
MAX_TURNS = 256
HOST_KEYS = ("HOME", "PATH", "LANG", "TMPDIR")
META_KEYS = (
    "session_sha256",
    "prompt_sha256",
    "answer_sha256",
    "requested_model",
    "served_model",
    "client_tokens",
    "tool_steps",
    "credit_fallback",
)
CATEGORIES = frozenset(
    {
        "contract",
        "admission_missing",
        "admission_refused",
        "admission_unavailable",
        "turn_unanswered",
        "turn_timeout",
        "turn_unverified",
    }
)
READ_ONLY = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

_host: dict[str, str] | None = None
_sessions: set[str] = set()


class SeatFailure(ValueError):
    def __init__(self, category: str, *, sent: bool) -> None:
        if category not in CATEGORIES or type(sent) is not bool:
            raise ValueError("unknown seat failure")
        if sent and category.startswith("admission_"):
            raise ValueError("admission failure after request")
        super().__init__(category)
        self.category = category
        self.sent = sent


def contract(test: object) -> None:
    if not test:
        raise SeatFailure("contract", sent=False)


def bind_host(env: Mapping[str, str]) -> None:
    global _host
    host = {key: env[key] for key in HOST_KEYS if env.get(key)}
    contract(all(type(value) is str for value in host.values()))
    home = host.get("HOME")
    contract(
        home is not None
        and "PATH" in host
        and Path(home).is_absolute()
        and Path(home).is_dir()
    )
    contract(_host is None or _host == host)
    _host = host


def private_parent(path: Path) -> bool:
    try:
        info = path.lstat()
        return (
            path.resolve(strict=True) == path
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700
        )
    except OSError:
        return False


def turn_frame(client: Path, owner: Path, prompt: str, role: str) -> bytes:
    contract(_host is not None and len(_sessions) < MAX_TURNS)
    contract(role in ROLES and type(prompt) is str and prompt)
    contract(
        isinstance(client, Path)
        and client.is_absolute()
        and client.is_file()
        and os.access(client, os.X_OK)
    )
    contract(
        isinstance(owner, Path)
        and owner.is_absolute()
        and owner.name not in ("", ".", "..")
        and not os.path.lexists(owner)
        and private_parent(owner.parent)
    )
    contract(Path(sys.executable).is_absolute())
    try:
        frame = agy_seat.line({"schema": TURN_SCHEMA, "prompt": prompt})
    except (UnicodeError, ValueError):
        raise SeatFailure("contract", sent=False) from None
    contract(len(frame) <= MAX_FRAME and frame.count(b"\n") == 1)
    return frame


def claim_owner(owner: Path) -> None:
    try:
        os.mkdir(owner, 0o700)
        os.chmod(owner, 0o700)
    except OSError:
        raise SeatFailure("contract", sent=False) from None
    contract(private_parent(owner))


def seat_argv(client: Path, owner: Path, role: str) -> list[str]:
    return [
        sys.executable,
        "-IS",
        str(SEAT),
        "--personal-quota",
        "--agy",
        str(client),
        "--record",
        str(owner / role),
        "--admission-note",
        str(owner / NOTE),
    ]


def stop_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def run_seat(argv: list[str], frame: bytes, owner: Path) -> tuple[int | None, bytes]:
    assert _host is not None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=owner,
            env=dict(_host),
            start_new_session=True,
        )
    except OSError:
        return 127, b""
    try:
        out, _ = proc.communicate(frame, timeout=TURN_SECONDS)
    except subprocess.TimeoutExpired:
        return None, b""
    finally:
        stop_group(proc)
    return proc.returncode, out


def admission(owner: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(owner / NOTE, READ_ONLY)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, MAX_NOTE + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        note = json.loads(raw)
    except ValueError:
        return None
    if (
        len(raw) > MAX_NOTE
        or not isinstance(note, dict)
        or note.keys() != ADMISSION_KEYS
        or note["schema"] != agy_seat.ADMISSION_SCHEMA
        or raw
        != json.dumps(note, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    ):
        return None
    return note


def failure(owner: Path, role: str, code: int | None) -> SeatFailure:
    record = owner / role
    sent = os.path.lexists(record / "request.json")
    if code is None:
        return SeatFailure("turn_timeout", sent=sent)
    if sent:
        replied = os.path.lexists(record / "reply.json") or os.path.lexists(
            record / "receipt.json"
        )
        return SeatFailure(
            "turn_unverified" if replied else "turn_unanswered", sent=True
        )
    note = admission(owner)
    if note is None:
        return SeatFailure("admission_missing", sent=False)
    if note["outcome"] == "refused":
        return SeatFailure("admission_refused", sent=False)
    if note["outcome"] == "unavailable":
        return SeatFailure("admission_unavailable", sent=False)
    return SeatFailure("turn_unanswered", sent=False)


def verified(
    owner: Path, role: str, prompt: str, out: bytes
) -> tuple[str, dict[str, Any]]:
    reply = json.loads(out)
    note = admission(owner)
    owned = owner_receipt.owner_turn(owner, role, prompt, None)
    if not (
        len(out) <= MAX_STDOUT
        and isinstance(reply, dict)
        and reply.keys() == {"text"}
        and out == agy_seat.line(reply)
        and note is not None
        and (note["outcome"], note["stage"], note["cause"])
        == ("passed", "complete", "none")
        and owned["status"] == "answered_unused"
        and owned["text"] == reply["text"]
    ):
        raise ValueError("unbound reply")
    seat = owned["receipt"]
    meta = {
        "session_sha256": seat["session_sha256"],
        "prompt_sha256": seat["prompt_sha256"],
        "answer_sha256": seat["answer_sha256"],
        "requested_model": seat["requested_model"],
        "served_model": seat["server_model"],
        "client_tokens": seat["usage"]["total_tokens"],
        "tool_steps": seat["observed_tool_steps"],
        "credit_fallback": seat["ai_credit_fallback"],
    }
    if not (
        tuple(meta) == META_KEYS
        and meta["served_model"] is None
        and meta["tool_steps"] == 0
        and meta["credit_fallback"] is False
        and seat["api_key_passed"] is False
        and meta["prompt_sha256"] == agy_seat.sha(prompt)
        and meta["answer_sha256"] == agy_seat.sha(owned["text"])
        and meta["session_sha256"] not in _sessions
    ):
        raise ValueError("unattested turn")
    return owned["text"], meta


def call(
    client: Path, owner: Path, prompt: str, role: str
) -> tuple[str, dict[str, Any]]:
    frame = turn_frame(client, owner, prompt, role)
    claim_owner(owner)
    code, out = run_seat(seat_argv(client, owner, role), frame, owner)
    if code != 0:
        raise failure(owner, role, code)
    try:
        text, meta = verified(owner, role, prompt, out)
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        raise SeatFailure("turn_unverified", sent=True) from None
    _sessions.add(meta["session_sha256"])
    return text, meta
