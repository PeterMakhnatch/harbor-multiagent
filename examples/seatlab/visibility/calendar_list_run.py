"""Collect a first-attempt receiver reply. No scorer or other seat enters the callback."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import (
    calendar_list_packet,
    calendar_list_protocol,
    calendar_packet,
    calendar_protocol,
)
from .replica_io import MAX_TEXT_BYTES

SCHEMA = "seatlab.calendar-list-response/v1"
ROLE = "cal_decide"
_ID = re.compile(r"cl-w(?:00|04|08)-d[01]-r[01]-(?:blind|peer)-k[01]-(?:rr|er|re|ee)")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_LIMIT = 6 * MAX_TEXT_BYTES + 1024


class CaptureError(ValueError):
    """A short diagnostic without raw model or host information."""


def _owner(fd: int, mode: int) -> None:
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
        raise CaptureError("private_storage_required")
    if mode == 0o600 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise CaptureError("private_regular_file_required")


@contextmanager
def _root(study: Path) -> Generator[int, None, None]:
    if study.anchor != "/" or len(study.parts) < 2 or ".." in study.parts:
        raise CaptureError("physical_absolute_study_required")
    fd = os.open("/", _DIRECTORY)
    try:
        for part in (*study.parts[1:], None):
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CaptureError("private_study_outside_checkout_required")
            if part is not None:
                child = os.open(part, _DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
        _owner(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _cell_dir(
    root: int, run: str, *, claim: bool = False
) -> Generator[int, None, None]:
    if not _ID.fullmatch(run):
        raise CaptureError("unregistered_run")
    if claim:
        try:
            os.mkdir(run, 0o700, dir_fd=root)
        except FileExistsError:
            raise CaptureError("attempt_already_exists") from None
    fd = os.open(run, _DIRECTORY, dir_fd=root)
    try:
        _owner(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


def _read(directory: int, name: str) -> dict[str, Any]:
    if not name or name != Path(name).name:
        raise CaptureError("invalid_receipt_name")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as source:
        _owner(fd, 0o600)
        data = source.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise CaptureError("receipt_too_large")
    value = calendar_packet._json(data)
    if not isinstance(value, dict):
        raise CaptureError("invalid_receipt")
    return value


def _capture(
    study: Path, cell: calendar_list_protocol.Cell, respond: Callable[[int, str], str]
) -> str:
    prompt = calendar_list_protocol.prompt(cell.case, cell.condition)
    with _root(study) as root, _cell_dir(root, cell.id, claim=True) as directory:
        # Even a process death now consumes this first attempt. A different
        # worker cannot accidentally repurchase the same cell.
        try:
            text = respond(directory, prompt)
            if type(text) is not str or len(text) > MAX_TEXT_BYTES:
                raise CaptureError("invalid_response_type_or_size")
            raw = text.encode("utf-8")
            if len(raw) > MAX_TEXT_BYTES:
                raise CaptureError("invalid_response_type_or_size")
        except Exception:  # noqa: BLE001 - Do not export callback error text.
            raise CaptureError("receiver_unavailable") from None
        receipt = {
            "schema": SCHEMA,
            "run": cell.id,
            "prompt_sha256": sha256(prompt.encode()).hexdigest(),
            "response_sha256": sha256(raw).hexdigest(),
            "text": text,
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(
            os.open("response.json", flags, 0o600, dir_fd=directory), "wb"
        ) as out:
            out.write(
                (
                    json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                ).encode()
            )
            out.flush()
            os.fsync(out.fileno())
        try:
            calendar_protocol.decode_slot(text)
        except ValueError:
            return "model_invalid"
    return "complete"


def _plan() -> tuple[calendar_list_protocol.Cell, ...]:
    return calendar_list_protocol.cells(calendar_list_packet.load())


def collect(study: Path, run: str, respond: Callable[[str], str]) -> str:
    """Give an external harness only the registered prompt. Keep its first reply verbatim."""
    for cell in _plan():
        if cell.id == run:
            return _capture(study, cell, lambda _directory, prompt: respond(prompt))
    raise CaptureError("unregistered_run")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("demo", "direct"))
    parser.add_argument(
        "study",
        type=Path,
        help="existing private, physical directory outside a checkout",
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--run", help="one registered first attempt")
    which.add_argument(
        "--draw",
        type=int,
        choices=(0, 1),
        help="all 96 first attempts in a registered draw",
    )
    args = parser.parse_args(argv)
    try:
        batch = [
            cell
            for cell in _plan()
            if (cell.id == args.run if args.run is not None else cell.draw == args.draw)
        ]
        if not batch:
            raise CaptureError("unregistered_run")
        if args.mode == "direct":
            from . import calendar_client, deepseek

            calendar_client._bind_direct()
            deepseek.preflight()
        for cell in batch:
            if args.mode == "demo":
                status = _capture(
                    args.study, cell, lambda _directory, _prompt: '{"slot":0}'
                )
            else:
                status = _capture(
                    args.study,
                    cell,
                    lambda directory, prompt, run=cell.id: calendar_client._direct_turn(
                        directory, f"{run}:{ROLE}", prompt
                    ),
                )
            print(
                json.dumps({"run": cell.id, "mode": args.mode, "status": status}),
                flush=True,
            )
    except CaptureError as exc:
        print(f"calendar-list: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - Keep private path and provider detail on the host.
        print("calendar-list: source_or_transport_unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
