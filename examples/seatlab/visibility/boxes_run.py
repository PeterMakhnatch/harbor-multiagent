"""Run the registered box receiver study once on the host's direct Flash ledger."""

import argparse
import fcntl
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeGuard

from . import boxes_choice, boxes_packet, deepseek, spend

RECEIPT_SCHEMA = "seatlab.box-receiver/v1"
ROLE = "box_receiver"
MAX_RESPONSE_BYTES = 64 * 1024
FAILURE_REASONS = frozenset(
    {
        "transport_failed",
        "budget_unresolved",
        "usage_unverified",
        "served_model_unverified",
        "provider_response_invalid",
        "settlement_unverified",
    }
)
_DIRECT_FAILURES = {
    "balance_below_minimum": "budget_unresolved",
    "balance_unverified": "budget_unresolved",
    "budget_guard": "budget_unresolved",
    "usage_unverified": "usage_unverified",
    "served_model_unverified": "served_model_unverified",
    "provider_response_invalid": "provider_response_invalid",
    "payload_invalid": "provider_response_invalid",
    "payload_too_large": "provider_response_invalid",
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CALL_FIELDS = (
    "role,reserved_at,served_model,hit,miss,output,started_at,finished_at,"
    "cost_low,cost_high"
)


class StudyError(ValueError):
    """A fixed diagnostic without provider text, account data, or host paths."""


class _Usage(TypedDict):
    input: int
    cached: int
    output: int


class _Receipt(TypedDict):
    schema: str
    run: str
    role: str
    packet_sha256: str
    prompt_sha256: str
    status: Literal["accepted", "invalid", "failed"]
    reason: str | None
    requested_model: str
    served_model: str | None
    usage: _Usage | None
    response_text: str | None
    response_sha256: str | None
    original_box: int | None


def _require_absolute(path: Path) -> None:
    if path.anchor != "/" or ".." in path.parts or path == Path("/"):
        raise StudyError("physical_absolute_path_required")


@contextmanager
def _directory(path: Path) -> Generator[int, None, None]:
    """Open physical path components and reject Git checkouts and worktrees."""
    fd = os.open("/", _DIRECTORY)
    try:
        for part in (*path.parts[1:], None):
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise StudyError("private_paths_outside_repos_required")
            if part is not None:
                child = os.open(part, _DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
        yield fd
    finally:
        os.close(fd)


def _require_private(info: os.stat_result, *, directory: bool = False) -> None:
    mode = info.st_mode
    if info.st_uid != os.getuid() or not (
        stat.S_ISDIR(mode) and stat.S_IMODE(mode) == 0o700
        if directory
        else stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600 and info.st_nlink == 1
    ):
        raise StudyError("private_storage_required")


@contextmanager
def _ledger_lock(
    root: Path, *, filename: str = ".box-receivers.lock"
) -> Generator[None, None, None]:
    # Only runners for the same study namespace contend for this lock.
    with _directory(root) as directory:
        _require_private(os.fstat(directory), directory=True)
        _require_private(
            os.stat("direct-flash.sqlite3", dir_fd=directory, follow_symlinks=False)
        )
        fd = os.open(
            filename,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        try:
            _require_private(os.fstat(fd))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StudyError("box_study_already_running") from None
            yield
        finally:
            os.close(fd)


def _preflight_ledger(
    root: Path, *, prefix: str = "bx-", role: str | None = None
) -> None:
    try:
        with spend._connect(root) as db:
            budget = db.execute(
                "SELECT opening,last_balance,lane_cap FROM budget LIMIT 2"
            ).fetchall()
            if len(budget) != 1 or any(
                type(value) is not int or value < 0 for value in budget[0]
            ):
                raise StudyError("ledger_not_initialized")
            opening, last, cap = budget[0]
            if not (
                spend._FLOOR <= last <= opening
                and spend._HOLD <= cap <= spend._SHARED_CAP
            ):
                raise StudyError("ledger_not_initialized")
            if (
                db.execute(
                    f"SELECT {_CALL_FIELDS} FROM calls WHERE run GLOB ? OR role = ? LIMIT 1",
                    (prefix + "*", role),
                ).fetchone()
                is not None
            ):
                raise StudyError("prior_box_reservation")
    except (OSError, sqlite3.Error, spend.SpendRefused):
        raise StudyError("ledger_not_initialized") from None


@contextmanager
def _claim(parent: int, name: str) -> Generator[int, None, None]:
    os.mkdir(name, 0o700, dir_fd=parent)
    os.fsync(parent)
    fd = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        _require_private(os.fstat(fd), directory=True)
        yield fd
    finally:
        os.close(fd)


def _non_negative_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _valid_usage(usage: tuple[int, int, int]) -> bool:
    return all(_non_negative_int(value) for value in usage) and usage[1] <= usage[0]


def _valid_model(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.casefold() in {
        deepseek.MODEL,
        deepseek.REVISION.casefold(),
    }


def _settlement(
    root: Path, run_id: str, role: str
) -> tuple[str, tuple[int, int, int]] | None:
    try:
        with spend._connect(root) as db:
            rows = db.execute(
                f"SELECT {_CALL_FIELDS} FROM calls WHERE run=? LIMIT 2",
                (run_id,),
            ).fetchall()
    except (OSError, sqlite3.Error, spend.SpendRefused):
        raise StudyError("settlement_unverified") from None
    if not rows:
        return None
    if len(rows) != 1 or rows[0]["role"] != role:
        raise StudyError("settlement_unverified")
    _, reserved, model, hit, miss, output, started, finished, low, high = rows[0]
    if not isinstance(reserved, str) or not reserved:
        raise StudyError("settlement_unverified")
    if all(
        value is None
        for value in (model, hit, miss, output, started, finished, low, high)
    ):
        return None  # Full hold, not a settlement or evidence of no provider call.
    if (
        not _valid_model(model)
        or not _non_negative_int(hit)
        or not _non_negative_int(miss)
        or not _non_negative_int(output)
        or not _non_negative_int(low)
        or not _non_negative_int(high)
        or high < low
        or not all(isinstance(value, str) and value for value in (started, finished))
    ):
        raise StudyError("settlement_unverified")
    return model, (hit + miss, hit, output)


def _decision(
    prompt: str,
    *,
    run_id: str,
    role: str,
    packet_sha256: str,
    numbering: int,
    spend_dir: Path,
) -> _Receipt:
    receipt: _Receipt = {
        "schema": RECEIPT_SCHEMA,
        "run": run_id,
        "role": role,
        "packet_sha256": packet_sha256,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "status": "failed",
        "reason": None,
        "requested_model": deepseek.MODEL,
        "served_model": None,
        "usage": None,
        "response_text": None,
        "response_sha256": None,
        "original_box": None,
    }
    reason = None
    model = None
    usage = None
    text = None
    try:
        turn = deepseek.turn(prompt, run_id=run_id, role=role)
    except deepseek.SeatUnavailable as error:
        reason = _DIRECT_FAILURES.get(error.category, "transport_failed")
        if error.category == "provider_response_invalid":
            usage = error.settled_usage
    except (spend.SpendRefused, sqlite3.Error, ArithmeticError):
        reason = "budget_unresolved"
    except (OSError, RuntimeError):
        reason = "transport_failed"
    else:
        model = turn.served_model
        usage = (turn.input_tokens, turn.cache_tokens, turn.output_tokens)
        text = turn.text

    try:
        settled = _settlement(spend_dir, run_id, role)
        if usage is None:
            if settled is not None or reason is None:
                raise StudyError("settlement_unverified")
        elif (
            not _valid_usage(usage)
            or settled is None
            or settled[1] != usage
            or (reason is None and settled[0] != model)
        ):
            raise StudyError("settlement_unverified")
        else:
            receipt["served_model"] = settled[0]
            receipt["usage"] = {
                "input": usage[0],
                "cached": usage[1],
                "output": usage[2],
            }
    except StudyError:
        reason = "settlement_unverified"

    if reason is not None:
        receipt["reason"] = reason
        return receipt
    if not isinstance(text, str):
        receipt["reason"] = "provider_response_invalid"
        return receipt
    try:
        raw = text.encode()
    except UnicodeEncodeError:
        raw = None
    if raw is None or len(raw) > MAX_RESPONSE_BYTES:
        receipt["reason"] = "provider_response_invalid"
        return receipt
    receipt["response_text"] = text
    receipt["response_sha256"] = sha256(raw).hexdigest()
    try:
        original_box = boxes_choice.decode(text, numbering)
    except (ValueError, RecursionError):
        receipt["status"] = "invalid"
        receipt["reason"] = "invalid_choice"
    else:
        receipt["status"] = "accepted"
        receipt["original_box"] = original_box
    return receipt


def _write_receipt(directory: int, receipt: _Receipt) -> None:
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd = os.open(
        "receipt.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(fd, "wb") as stream:
        _require_private(os.fstat(fd))
        stream.write(raw)
        stream.flush()
        os.fsync(fd)
    os.fsync(directory)


def _record(
    cells_fd: int,
    prompt: str,
    *,
    run_id: str,
    role: str,
    packet_sha256: str,
    numbering: int,
    spend_dir: Path,
) -> _Receipt:
    with _claim(cells_fd, run_id) as directory:
        receipt = _decision(
            prompt,
            run_id=run_id,
            role=role,
            packet_sha256=packet_sha256,
            numbering=numbering,
            spend_dir=spend_dir,
        )
        _write_receipt(directory, receipt)
    return receipt


def _preflight_host(study: Path, spend_dir: Path) -> None:
    _require_absolute(study)
    _require_absolute(spend_dir)
    env_spend = os.environ.get("SEATLAB_SPEND_DIR")
    if (
        env_spend is None
        or env_spend == ""
        or Path(env_spend) != spend_dir
        or (spend._HOST_LEDGER is not None and spend._HOST_LEDGER != spend_dir)
    ):
        raise StudyError("spend_environment_mismatch")
    try:
        deepseek._key()  # Local availability check; turn checks the live floor.
    except deepseek.SeatUnavailable:
        raise StudyError("host_key_unavailable") from None


def run(study: Path, *, spend_dir: Path) -> dict[str, Any]:
    """Create a fresh private study and keep the first attempt for each eligible cell."""
    _preflight_host(study, spend_dir)

    with _directory(study.parent) as parent, _ledger_lock(spend_dir):
        _preflight_ledger(spend_dir)
        try:
            cases = boxes_packet.load(boxes_choice.PACKET, boxes_choice.PACKET_SHA256)
        except (OSError, ValueError):
            raise StudyError("packet_unverified") from None
        eligible = [
            (cell, case)
            for cell in boxes_choice.grid()
            if (case := cases[cell.instance]) is not None
        ]
        counts = {"accepted": 0, "invalid": 0, "failed": 0}
        stopped = None
        with (
            _claim(parent, study.name) as study_fd,
            _claim(study_fd, "cells") as cells_fd,
        ):
            for cell, case in eligible:
                prompt = boxes_choice.prompt(case, cell)
                receipt = _record(
                    cells_fd,
                    prompt,
                    run_id=cell.id,
                    role=ROLE,
                    packet_sha256=boxes_choice.PACKET_SHA256,
                    numbering=cell.numbering,
                    spend_dir=spend_dir,
                )
                status = receipt["status"]
                counts[status] += 1
                if status == "failed":
                    stopped = {"run": cell.id, "reason": receipt["reason"]}
                    break
    return {
        "eligible": len(eligible),
        **counts,
        "unattempted": len(eligible) - sum(counts.values()),
        "stopped": stopped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = run(args.study, spend_dir=args.spend_dir)
    except StudyError as error:
        result = str(error)
    except FileExistsError:
        result = "study_or_claim_exists"
    except OSError:
        result = "study_io_failed"
    except KeyboardInterrupt:
        result = "interrupted"
    else:
        print(json.dumps(summary, sort_keys=True))
        return int(summary["stopped"] is not None)
    print(json.dumps({"error": result}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
