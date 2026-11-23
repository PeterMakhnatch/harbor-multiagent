from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import sys
from decimal import Decimal
from fractions import Fraction
from typing import Any, NoReturn

from . import mark_selector as ms

SCHEMA = "seatlab.continuation-owner-mark/v1"
PROG = "python3.12 -BS -m examples.seatlab.continuation.owner_mark"
MAX_BYTES = 512 * 1024
MARK = "h03"
FINAL_MARK = "h04"
REASON = f"mark {MARK}"
MARK_S = Decimal(10800)
TIMEOUT_S = Decimal(14400)
FORMATS = (1, 2)
GPU_FORMAT = 2
TASK_MAX = 256
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
TEAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RESUME_KEYS = ("resumed", "regraded_after_resume")
MISSING = "manifest_missing"
AFTER_WINDOW = "mark_pinned_after_window"
PENDING = {
    "queued": "mark_queued",
    "grading": "mark_grading",
    "failed": "mark_failed",
    "timeout": "mark_timeout",
    "cancelled": "mark_cancelled",
}
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
ATTESTATION = (
    "--task is the owner's attestation of the environment identifier; "
    "matching it proves only that both manifests carry that string, not which task code ran"
)
LIMITATIONS = (
    "the nominal 3h label is not the actual receipt time; graded rows publish elapsed_s, the harness agent-phase clock at the h03 pin",
    "agent time only: elapsed_s is not wall-clock time and not when the owner received the mark",
    "a mark pinned at elapsed_s >= 14400 is not an actionable pre-terminal snapshot and withholds selection",
    "cannot certify that the mark reached the owner before minute 240; this tool sets no deadline and cancels nothing",
    "the h03 mark is the live verifier grade of main pinned at 3h while the agents keep working, not a terminal grade",
    "resumed runs are rejected because the ancestry of their marks cannot be certified",
    "reads and hashes each entire owner-supplied manifest and opens no neighboring file",
    "does not use or output the manifests' model, cost, spend, agent names or paths; raw manifests are not published",
)

_require = ms._require


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def _number(value: Any, label: str) -> Decimal:
    _require(
        type(value) in (int, Decimal), f"{label} must be a non-boolean JSON number"
    )
    number = Decimal(value)
    _require(number.is_finite(), f"{label} must be finite")
    return number


def _read(team: str, path: str) -> tuple[bytes, tuple[int, int]] | None:
    try:
        fd = os.open(path, OPEN_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        reason = (
            "is a symbolic link" if exc.errno == errno.ELOOP else "cannot be opened"
        )
        raise ValueError(f"{team}: manifest {reason}") from None
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode), f"{team}: manifest is not a regular file")
        chunks: list[bytes] = []
        size = 0
        while size <= MAX_BYTES:
            chunk = os.read(fd, MAX_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise ValueError(f"{team}: manifest cannot be read") from None
    finally:
        os.close(fd)
    _require(size <= MAX_BYTES, f"{team}: manifest exceeds {MAX_BYTES} bytes")
    return b"".join(chunks), (info.st_dev, info.st_ino)


def _parse(team: str, raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=Decimal,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"{team}: manifest is not strict finite JSON") from None
    _require(type(document) is dict, f"{team}: manifest is not a JSON object")
    return document


def _header(team: str, document: dict[str, Any], task: str) -> int:
    fmt = document.get("format")
    if type(fmt) is not int or fmt not in FORMATS:
        raise ValueError(f"{team}: format must be 1 or 2")
    gpu = document.get("gpu_allocation")
    _require(
        type(gpu) is dict if fmt == GPU_FORMAT else gpu is None or type(gpu) is dict,
        f"{team}: format {fmt} gpu_allocation is invalid",
    )
    _require(
        type(document.get("task")) is str and document["task"] == task,
        f"{team}: manifest task differs from --task",
    )
    _require(
        "resumed_from" in document and document["resumed_from"] is None,
        f"{team}: resumed run; mark ancestry cannot be certified",
    )
    _require(
        document.get("mark") == MARK,
        f"{team}: manifest is not the {MARK} mark checkpoint",
    )
    _require(document.get("reason") == REASON, f"{team}: reason is not '{REASON}'")
    _require(document.get("final") is False, f"{team}: final must be false")
    timeout = _number(document.get("agent_timeout_sec"), f"{team}: agent_timeout_sec")
    _require(timeout == TIMEOUT_S, f"{team}: agent_timeout_sec is not 4h")
    grade_at = document.get("grade_at")
    _require(
        type(grade_at) is list
        and all(type(label) is str for label in grade_at)
        and MARK in grade_at
        and FINAL_MARK in grade_at,
        f"{team}: grade_at must list {MARK} and {FINAL_MARK}",
    )
    return fmt


def _mark_record(team: str, document: dict[str, Any]) -> dict[str, Any]:
    records = document.get("marks")
    if not isinstance(records, list) or not all(
        type(record) is dict for record in records
    ):
        raise ValueError(f"{team}: marks must be a list of objects")
    _require(
        not any(key in record for record in records for key in RESUME_KEYS),
        f"{team}: resumed or regraded mark; ancestry cannot be certified",
    )
    found = [record for record in records if record.get("mark") == MARK]
    _require(len(found) == 1, f"{team}: marks must hold exactly one {MARK} record")
    return found[0]


def _grade(
    team: str, record: dict[str, Any]
) -> tuple[str | None, dict[str, Any], Fraction | None]:
    at = f"{team}: marks[{MARK}]"
    _require(
        _number(record.get("mark_s"), f"{at}.mark_s") == MARK_S,
        f"{at}.mark_s is not 10800",
    )
    elapsed = _number(record.get("elapsed_s"), f"{at}.elapsed_s")
    _require(elapsed >= MARK_S, f"{at}.elapsed_s is before 10800")
    commit = record.get("commit")
    if not isinstance(commit, str) or SHA.fullmatch(commit) is None:
        raise ValueError(f"{at}.commit is not a git SHA")
    _require(record.get("final") is False, f"{at}.final must be false")
    status = record.get("status")
    if type(status) is str and status in PENDING:
        _require(
            record.get("reward") is None, f"{at}.reward must be null while {status}"
        )
        return AFTER_WINDOW if elapsed >= TIMEOUT_S else PENDING[status], {}, None
    _require(status == "graded", f"{at}.status is not recognised")
    reward = ms._unit(record.get("reward"), f"{at}.reward")
    if elapsed >= TIMEOUT_S:
        return AFTER_WINDOW, {}, None
    projected = {
        "commit": commit,
        "elapsed_s": float(elapsed),
        "elapsed_s_exact": str(elapsed),
    }
    return None, projected | ms._num("reward", reward), reward


def _team(
    team: str, path: str, task: str
) -> tuple[dict[str, Any], Fraction | None, tuple[int, int] | None]:
    loaded = _read(team, path)
    if loaded is None:
        row = {"team": team, "status": "incomplete", "reason": MISSING}
        return (
            row | {"manifest_sha256": None, "manifest_bytes": None, "format": None},
            None,
            None,
        )
    raw, identity = loaded
    document = _parse(team, raw)
    fmt = _header(team, document, task)
    reason, projected, mark = _grade(team, _mark_record(team, document))
    row = {
        "team": team,
        "status": "incomplete" if reason else "graded",
        "reason": reason,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_bytes": len(raw),
        "format": fmt,
    }
    return row | projected, mark, identity


def evaluate(task: str, entries: list[tuple[str, str]]) -> dict[str, Any]:
    _require(len(entries) == 2, "exactly 2 teams are required")
    _require(
        0 < len(task) <= TASK_MAX and all("!" <= char <= "~" for char in task),
        f"--task must be 1-{TASK_MAX} printable ASCII characters without spaces",
    )
    teams = [team for team, _ in entries]
    _require(
        all(TEAM.fullmatch(team) for team in teams),
        "team ids must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}",
    )
    _require(teams[0] != teams[1], "team ids must differ")
    _require(
        os.path.abspath(entries[0][1]) != os.path.abspath(entries[1][1]),
        "the same manifest path is supplied twice",
    )
    loaded = [_team(team, path, task) for team, path in entries]
    identities = [identity for _, _, identity in loaded if identity is not None]
    _require(
        len(set(identities)) == len(identities),
        "the same manifest file is supplied twice",
    )
    hashes = [row["manifest_sha256"] for row, _, _ in loaded if row["manifest_sha256"]]
    _require(
        len(set(hashes)) == len(hashes), "identical manifest bytes are supplied twice"
    )
    marks = {row["team"]: mark for row, mark, _ in loaded if mark is not None}
    eligible = ms.choose(marks) if len(marks) == 2 else None
    return {
        "schema": SCHEMA,
        "status": "complete" if eligible else "incomplete",
        "mark": MARK,
        "mark_s": int(MARK_S),
        "agent_timeout_s": int(TIMEOUT_S),
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "task_attestation": ATTESTATION,
        "teams": [row for row, _, _ in loaded],
        "eligible": eligible,
        "tie_size": len(eligible) if eligible else None,
        "pick": secrets.choice(eligible) if eligible else None,
        "limitations": list(LIMITATIONS),
    }


def _encode(result: dict[str, Any]) -> bytes:
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return (text + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Pick between 2 teams from their owner-supplied h03 mark checkpoint manifests.",
        allow_abbrev=False,
    )
    parser.add_argument("--task", required=True, metavar="TASK", help=ATTESTATION)
    for name in ("team_a", "manifest_a", "team_b", "manifest_b"):
        parser.add_argument(name, metavar=name.upper())
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    entries = [(args.team_a, args.manifest_a), (args.team_b, args.manifest_b)]
    try:
        output = _encode(evaluate(args.task, entries))
    except OSError:
        parser.error("a manifest path cannot be resolved")
    except (ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
