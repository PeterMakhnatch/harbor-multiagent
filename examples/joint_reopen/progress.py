import argparse
import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Never, cast, override

from ..native_evidence import Incomplete, Invalid, Record, read_bytes, strict_object
from .observation import KINDS, ROLES

SCHEMA = "harbor.joint-reopen.progress/v1"
VIEW_SCHEMA = "harbor.joint-reopen.progress-view/v1"
MARKER = "joint-owner-progress.json"
STAGED = ".joint-owner-progress.json.staged"
RESULT = "result.json"
FIELDS = ("schema", "role", "serial", "state", "step", "confirmed")
PUBLIC = ("serial", "state", "step", "confirmed")
TERMINAL = ("voted", "stopped", "failed")
TRIAL_STATES = ("present", "absent", "invalid")
STATUSES = ("observed", "not_observed", "invalid")
MAX_SERIAL = 64
MAX_BYTES = 512
MODE = 0o600
AFTER: dict[str, tuple[str, ...]] = {
    "endorse": (),
    "card": ("endorse",),
    "report": ("endorse",),
    "inbox": ("report",),
    "vote": ("report",),
}
NEEDS: dict[tuple[str, str | None], tuple[tuple[str, ...], tuple[str, ...]]] = {
    ("owner_turn", "endorse"): ((), ("endorse",)),
    ("owner_turn", "report"): (("endorse",), ("report",)),
    ("owner_turn", "vote"): (("report",), ("vote",)),
    ("barrier", "review"): (("endorse",), ("report",)),
    ("barrier", "vote"): (("report",), ("vote",)),
    ("request", "endorse"): ((), ("endorse",)),
    ("request", "card"): (("endorse",), ("vote",)),
    ("request", "report"): (("endorse",), ("report",)),
    ("request", "inbox"): (("report",), ("vote",)),
    ("request", "vote"): (("report",), ("vote",)),
    ("voted", None): (("vote",), ()),
    ("stopped", None): ((), ("vote",)),
    ("failed", None): ((), ("vote",)),
}


class ProgressError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProgressError(reason)


def _check_confirmed(raw: object) -> list[str]:
    if type(raw) is not list or len(raw) > len(KINDS):
        raise ProgressError("confirmed is not a bounded list")
    confirmed: list[str] = []
    for kind in raw:
        if type(kind) is not str or kind not in KINDS:
            raise ProgressError("confirmed has an unknown kind")
        _require(kind not in confirmed, "confirmed repeats a route kind")
        _require("vote" not in confirmed, "confirmed continues after the vote")
        _require(
            all(prior in confirmed for prior in AFTER[kind]),
            "confirmed breaks the world order",
        )
        confirmed.append(kind)
    assert len(confirmed) == len(set(confirmed)) <= len(KINDS)
    return confirmed


def _check(raw: object, role: str) -> Record:
    assert role in ROLES
    if type(raw) is not dict or set(raw) != set(FIELDS):
        raise ProgressError("fields do not match")
    fields = cast(dict[str, object], raw)
    _require(fields["schema"] == SCHEMA, "bad schema")
    _require(fields["role"] == role, "role mismatch")
    serial, state, step = fields["serial"], fields["state"], fields["step"]
    if type(serial) is not int or not 1 <= serial <= MAX_SERIAL:
        raise ProgressError("serial is out of range")
    if type(state) is not str or (step is not None and type(step) is not str):
        raise ProgressError("state and step are not text")
    _require((state, step) in NEEDS, "state and step do not pair")
    confirmed = _check_confirmed(fields["confirmed"])
    present, absent = NEEDS[(state, step)]
    _require(
        all(kind in confirmed for kind in present)
        and not any(kind in confirmed for kind in absent),
        "state contradicts confirmed route kinds",
    )
    snapshot: Record = {
        "schema": SCHEMA,
        "role": role,
        "serial": serial,
        "state": state,
        "step": step,
        "confirmed": confirmed,
    }
    assert tuple(snapshot) == FIELDS
    return snapshot


def _encode(snapshot: Record) -> bytes:
    assert tuple(snapshot) == FIELDS
    text = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
    data = text.encode("ascii") + b"\n"
    _require(len(data) <= MAX_BYTES, "marker exceeds the byte limit")
    return data


def _parse(data: bytes, role: str) -> Record:
    try:
        raw = strict_object(data, MARKER)
    except Invalid:
        raise ProgressError("marker is not strict JSON") from None
    snapshot = _check(raw, role)
    _require(_encode(snapshot) == data, "marker is not canonical")
    return snapshot


def _previous(logs_dir: Path, role: str) -> Record | None:
    try:
        data = read_bytes(logs_dir, (MARKER,), MAX_BYTES)
    except Incomplete:
        return None
    except (Invalid, OSError):
        raise ProgressError("existing marker is unreadable") from None
    return _parse(data, role)


def _follows(previous: Record | None, snapshot: Record) -> None:
    if previous is None:
        _require(snapshot["serial"] == 1, "first serial is not 1")
        return
    _require(previous["state"] not in TERMINAL, "progress already ended")
    _require(snapshot["serial"] == previous["serial"] + 1, "serial does not follow")
    old: list[str] = previous["confirmed"]
    _require(
        snapshot["confirmed"][: len(old)] == old,
        "confirmed rewrites earlier route kinds",
    )


def _publish(directory: int, logs_dir: Path, snapshot: Record) -> None:
    data = _encode(snapshot)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        staged = os.open(STAGED, flags, MODE, dir_fd=directory)
    except FileExistsError:
        raise ProgressError("another writer holds the marker") from None
    except OSError:
        raise ProgressError("marker could not be staged") from None
    published = False
    try:
        with open(staged, "wb") as handle:
            os.fchmod(handle.fileno(), MODE)
            _follows(_previous(logs_dir, snapshot["role"]), snapshot)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(STAGED, MARKER, src_dir_fd=directory, dst_dir_fd=directory)
        published = True
        os.fsync(directory)
    except OSError:
        raise ProgressError("marker could not be published") from None
    finally:
        if not published:
            with contextlib.suppress(OSError):
                os.unlink(STAGED, dir_fd=directory)


def record(
    logs_dir: Path,
    *,
    role: str,
    serial: int,
    state: str,
    step: str | None,
    confirmed: list[str],
) -> None:
    _require(isinstance(logs_dir, Path), "logs_dir is not a path")
    _require(type(role) is str and role in ROLES, "role is unknown")
    snapshot = _check(
        {
            "schema": SCHEMA,
            "role": role,
            "serial": serial,
            "state": state,
            "step": step,
            "confirmed": confirmed,
        },
        role,
    )
    try:
        directory = os.open(logs_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ProgressError("logs_dir is not a plain directory") from None
    try:
        _publish(directory, logs_dir, snapshot)
    finally:
        os.close(directory)


def _trial_state(trial: Path) -> str:
    try:
        mode = os.lstat(trial).st_mode
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "invalid"
    return "present" if stat.S_ISDIR(mode) else "invalid"


def _result_present(trial: Path) -> bool:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory = os.open(trial, flags)
    except OSError:
        return False
    try:
        mode = os.stat(RESULT, dir_fd=directory, follow_symlinks=False).st_mode
    except OSError:
        return False
    finally:
        os.close(directory)
    return stat.S_ISREG(mode)


def _role_view(trial: Path, role: str) -> Record:
    blank: Record = {"status": "not_observed", **dict.fromkeys(PUBLIC)}
    try:
        data = read_bytes(trial, ("seats", role, "agent", MARKER), MAX_BYTES)
        snapshot = _parse(data, role)
    except Incomplete:
        return blank
    except (Invalid, ProgressError, OSError):
        return {**blank, "status": "invalid"}
    return {"status": "observed", **{key: snapshot[key] for key in PUBLIC}}


def progress(trial: Path) -> Record:
    state = _trial_state(trial)
    assert state in TRIAL_STATES
    present = state == "present"
    roles: Record = {}
    for role in ROLES:
        roles[role] = (
            _role_view(trial, role)
            if present
            else {"status": "not_observed", **dict.fromkeys(PUBLIC)}
        )
        assert roles[role]["status"] in STATUSES
    return {
        "schema": VIEW_SCHEMA,
        "trial": state,
        "native_result_present_unverified": present and _result_present(trial),
        "roles": roles,
    }


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if type(value) is list:
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def render(view: Record) -> str:
    present = "true" if view["native_result_present_unverified"] else "false"
    lines = [
        f"trial\t{view['trial']}",
        f"native_result_present_unverified\t{present}",
        "\t".join(("role", "status", *PUBLIC)),
    ]
    for role, row in view["roles"].items():
        cells = (_cell(row[key]) for key in PUBLIC)
        lines.append("\t".join((role, row["status"], *cells)))
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        raise ProgressError("usage")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="python3 -BS -m examples.joint_reopen.progress")
    parser.add_argument("trial", type=Path, metavar="NATIVE_TRIAL")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        view = progress(args.trial)
        text = json.dumps(view, indent=2) if args.json else render(view)
    except ProgressError:
        category = "usage"
    except Exception:
        category = "internal"
    else:
        print(text)
        return 0
    print(f"progress: {category}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
