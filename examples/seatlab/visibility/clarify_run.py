"""Seal and run the registered two-turn clarification study.

Only run-window calls the paid direct seat. A reserved cell is never retried.
"""

import argparse
import fcntl
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from ..capsule import _reject_constant, _unique_object
from . import clarify_schedule as schedule
from . import clarify_views, deepseek, dispatch_packet, spend

_PLAN_SCHEMA = "seatlab-clarification-plan-v1"
_RECEIPT_SCHEMA = "seatlab-clarification-turns-v1"
_CHECKOUT = Path(__file__).resolve().parents[3]
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_FAILURES = frozenset(
    {
        "transport_failed",
        "budget_unresolved",
        "served_model_invalid",
        "evidence_invalid",
        "packet_changed",
        "outside_window",
    }
)


class StudyError(ValueError):
    """A bounded diagnostic that contains no provider or packet text."""


def _encode(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _object(raw: bytes) -> dict[str, Any]:
    value = json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _absolute(path: Path) -> Path:
    path = path.expanduser()
    if ".." in path.parts:
        raise StudyError("noncanonical_path")
    return path.absolute()


def _study_path(path: Path) -> Path:
    path = _absolute(path)
    if path == _CHECKOUT or path.is_relative_to(_CHECKOUT):
        raise StudyError("private_study_outside_checkout_required")
    return path


def _private(fd: int, *, mode: int, regular: bool = False) -> None:
    details = os.fstat(fd)
    if (
        details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != mode
        or (regular and details.st_nlink != 1)
    ):
        raise StudyError("unsafe_study_path")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Open every component without following symlinks."""
    root, *parts = _absolute(path).parts
    fd = os.open(root, _DIRECTORY)
    try:
        for part in parts:
            next_fd = os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _regular(directory: int, name: str, mode: int | None = None) -> Iterator[BinaryIO]:
    fd = os.open(name, _READ, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StudyError("not_a_regular_file")
        if mode is not None:
            _private(fd, mode=mode, regular=True)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def _publish(
    directory: int,
    name: str,
    document: object,
    *,
    mode: int,
    previous_inode: int | None = None,
) -> int:
    """Publish a complete file without replacing an older first attempt."""
    temporary = f".{uuid4().hex}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(fd, mode)
            stream.write(_encode(document))
            stream.flush()
            os.fsync(fd)
            inode = os.fstat(fd).st_ino
        if previous_inode is None:
            os.link(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        else:
            with _regular(directory, name, mode) as previous:
                if os.fstat(previous.fileno()).st_ino != previous_inode:
                    raise StudyError("receipt_changed")
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        return inode
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _packet_name(seed: int) -> str:
    return schedule.packet_path(Path(), seed).name


def _packet_bytes(packets: Path) -> dict[int, bytes]:
    raw = {}
    with _directory(packets) as directory:
        for seed in schedule.SEEDS:
            with _regular(directory, _packet_name(seed)) as stream:
                data = stream.read(16_385)
            if len(data) > 16_384:
                raise StudyError("evidence_invalid")
            raw[seed] = data
    return raw


def _hash_packets(raw: dict[int, bytes]) -> dict[str, str]:
    return {_packet_name(seed): sha256(data).hexdigest() for seed, data in raw.items()}


def _verified_packets(raw: dict[int, bytes]) -> dict[int, dict[str, Any]]:
    checked = {}
    for seed, data in raw.items():
        try:
            packet = dispatch_packet.verify_packet(_object(data))
            if packet["seed"] != seed or packet["repetition"] != 1:
                raise ValueError("unregistered packet identity")
        except (ValueError, TypeError, KeyError, RuntimeError, RecursionError):
            raise StudyError("evidence_invalid") from None
        checked[seed] = packet
    return checked


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise StudyError("start_utc_required")
    try:
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() != timedelta(0):
            raise ValueError("not UTC")
    except ValueError:
        raise StudyError("start_utc_required") from None
    return instant


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _cell_fields(cell: schedule.Cell) -> dict[str, Any]:
    return {
        "cell_id": cell.cell_id,
        "window": cell.window,
        "seed": cell.seed,
        "mask": cell.mask,
        "arm": cell.arm,
    }


def _bounds(start: datetime, window: int) -> tuple[datetime, datetime]:
    begin = start + timedelta(minutes=schedule.OFFSETS_MINUTES[window])
    return begin, begin + timedelta(minutes=schedule.WINDOW_MINUTES)


def _plan(hashes: dict[str, str], start: datetime) -> dict[str, Any]:
    windows = []
    for window in range(len(schedule.OFFSETS_MINUTES)):
        begin, end = _bounds(start, window)
        windows.append(
            {"window": window, "start_utc": _iso(begin), "end_utc": _iso(end)}
        )
    return {
        "schema": _PLAN_SCHEMA,
        "requested_model": deepseek.MODEL,
        "model_revision": deepseek.REVISION,
        "packet_sha256": hashes,
        "start_utc": _iso(start),
        "windows": windows,
        "cells": [
            {
                **_cell_fields(cell),
                "hidden": list(cell.hidden),
                "queued_case": cell.queued_case,
            }
            for cell in schedule.grid()
        ],
    }


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _decode_plan(raw: bytes) -> dict[str, Any]:
    try:
        document = _object(raw)
        hashes = document["packet_sha256"]
        if (
            not isinstance(hashes, dict)
            or hashes.keys() != {_packet_name(seed) for seed in schedule.SEEDS}
            or not all(_is_digest(digest) for digest in hashes.values())
        ):
            raise ValueError("invalid packet hashes")
        expected = _plan(hashes, _utc(document["start_utc"]))
        if raw != _encode(expected):
            raise ValueError("inconsistent plan")
    except (ValueError, KeyError, OverflowError, RecursionError):
        raise StudyError("plan_invalid") from None
    return expected


def seal(packets: Path, study: Path, *, start_utc: str) -> dict[str, Any]:
    """Verify the four named packets and publish a new private plan offline."""
    study = _study_path(study)
    raw = _packet_bytes(packets)
    _verified_packets(raw)
    try:
        document = _plan(_hash_packets(raw), _utc(start_utc))
    except OverflowError:
        raise StudyError("start_utc_required") from None
    with _directory(study.parent) as parent:
        os.mkdir(study.name, 0o700, dir_fd=parent)
        fd = os.open(study.name, _DIRECTORY, dir_fd=parent)
        try:
            _private(fd, mode=0o700)
            os.mkdir("cells", 0o700, dir_fd=fd)
            _publish(fd, "plan.json", document, mode=0o400)
        finally:
            os.close(fd)
    return document


def load_plan(study: Path) -> dict[str, Any]:
    """Read a sealed plan offline; cell IDs and UTC bounds are in the document."""
    with _directory(_study_path(study)) as directory:
        _private(directory, mode=0o700)
        with _regular(directory, "plan.json", 0o400) as stream:
            return _decode_plan(stream.read(131_073))


def _now() -> datetime:
    return datetime.now(UTC)


def _require_window(bounds: tuple[datetime, datetime]) -> None:
    if not bounds[0] <= _now() <= bounds[1]:
        raise StudyError("outside_window")


def _unchanged_packets(packets: Path, expected: dict[str, str]) -> dict[int, bytes]:
    try:
        raw = _packet_bytes(packets)
    except (OSError, StudyError):
        raise StudyError("packet_changed") from None
    if _hash_packets(raw) != expected:
        raise StudyError("packet_changed")
    return raw


def _served_identity(value: object) -> bool:
    return isinstance(value, str) and value in (
        deepseek.MODEL,
        deepseek.REVISION,
        deepseek.REVISION.lower(),
    )


def _turn(prompt: str, cell: schedule.Cell, stage: str) -> deepseek.SeatTurn:
    try:
        response = deepseek.turn(
            prompt, run_id=f"{cell.cell_id}-{stage}", role="dispatch"
        )
    except deepseek.SeatUnavailable as error:
        if error.category in {"budget_guard", "usage_unverified"}:
            raise StudyError("budget_unresolved") from None
        if error.category == "served_model_unverified":
            raise StudyError("served_model_invalid") from None
        if error.category in {
            "payload_invalid",
            "payload_too_large",
            "provider_response_invalid",
        }:
            raise StudyError("evidence_invalid") from None
        raise StudyError("transport_failed") from None
    except (spend.SpendRefused, sqlite3.Error):
        raise StudyError("budget_unresolved") from None
    except (OSError, RuntimeError):
        raise StudyError("transport_failed") from None
    if not isinstance(response, deepseek.SeatTurn):
        raise StudyError("evidence_invalid")
    if not _served_identity(response.served_model):
        raise StudyError("served_model_invalid")
    return response


def _receipt(cell: schedule.Cell, digest: str) -> dict[str, Any]:
    return {
        "schema": _RECEIPT_SCHEMA,
        **_cell_fields(cell),
        "packet_sha256": digest,
        "status": "started",
        "selected_case": None,
        "delivered_case": None,
        "actions": None,
        "grade": None,
        "turns": {},
    }


def _receipt_status(directory: int, cell: schedule.Cell, digest: str) -> str | None:
    name = f"{cell.cell_id}.json"
    try:
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(entry.st_mode):
        return "receipt_invalid"
    try:
        with _regular(directory, name, 0o600) as stream:
            document = _object(stream.read(16_385))
    except (OSError, ValueError, RecursionError):
        return "receipt_invalid"
    expected = _receipt(cell, digest)
    if document.keys() != expected.keys() or any(
        document[key] != expected[key]
        for key in (
            "schema",
            "cell_id",
            "window",
            "seed",
            "mask",
            "arm",
            "packet_sha256",
        )
    ):
        return "receipt_invalid"
    status = document["status"]
    if not isinstance(status, str) or status not in _FAILURES | {"started", "complete"}:
        return "receipt_invalid"
    if status == "complete":
        selected, delivered = document["selected_case"], document["delivered_case"]
        turns = document["turns"]
        if (
            type(selected) is not int
            or selected not in range(4)
            or type(delivered) is not int
            or delivered != cell.delivery(selected)
            or not isinstance(document["actions"], dict)
            or not isinstance(document["grade"], dict)
            or not isinstance(turns, dict)
            or turns.keys() != {"select", "act"}
            or any(
                not isinstance(turn, dict)
                or turn.keys() != {"prompt_sha256", "served_model"}
                or not _is_digest(turn["prompt_sha256"])
                or not _served_identity(turn["served_model"])
                for turn in turns.values()
            )
        ):
            return "receipt_invalid"
    return status


def _run_cell(
    directory: int,
    packets: Path,
    hashes: dict[str, str],
    bounds: tuple[datetime, datetime],
    cell: schedule.Cell,
    packet: dict[str, Any],
) -> str:
    name = f"{cell.cell_id}.json"
    digest = hashes[_packet_name(cell.seed)]
    receipt = _receipt(cell, digest)
    try:
        inode = _publish(directory, name, receipt, mode=0o600)
    except FileExistsError:
        # An existing path permanently consumes the cell, even if unreadable.
        return _receipt_status(directory, cell, digest) or "receipt_invalid"

    def ask(stage: str, prompt: str) -> str:
        prompt_hash = sha256(prompt.encode()).hexdigest()
        _unchanged_packets(packets, hashes)
        _require_window(bounds)
        turn = _turn(prompt, cell, stage)
        receipt["turns"][stage] = {
            "prompt_sha256": prompt_hash,
            "served_model": turn.served_model,
        }
        if not isinstance(turn.text, str):
            raise StudyError("evidence_invalid")
        return turn.text

    try:
        prompt = clarify_views.build_initial(packet, cell)
        selected = clarify_views.validate_selector(ask("select", prompt))
        receipt.update(selected_case=selected, delivered_case=cell.delivery(selected))
        inode = _publish(directory, name, receipt, mode=0o600, previous_inode=inode)
        prompt = clarify_views.build_final(packet, cell, selected)
        actions = clarify_views.validate_actions(packet, cell, ask("act", prompt))
        grade = clarify_views.grade_actions(packet, cell, actions)
        receipt.update(actions=actions, grade=grade, status="complete")
    except StudyError as error:
        if str(error) not in _FAILURES:
            raise
        receipt["status"] = str(error)
    except (ValueError, TypeError, KeyError, RuntimeError):
        receipt["status"] = "evidence_invalid"
    _publish(directory, name, receipt, mode=0o600, previous_inode=inode)
    return receipt["status"]


def run_window(packets: Path, study: Path, window: int) -> dict[str, Any]:
    """Run unattempted cells once, stopping on the first new failure."""
    if type(window) is not int or not 0 <= window < len(schedule.OFFSETS_MINUTES):
        raise StudyError("window_invalid")
    with _directory(_study_path(study)) as directory:
        _private(directory, mode=0o700)
        with _regular(directory, "plan.json", 0o400) as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StudyError("study_already_running") from None
            plan = _decode_plan(stream.read(131_073))
            bounds = _bounds(_utc(plan["start_utc"]), window)
            _require_window(bounds)
            hashes = plan["packet_sha256"]
            verified = _verified_packets(_unchanged_packets(packets, hashes))
            cells_fd = os.open("cells", _DIRECTORY, dir_fd=directory)
            try:
                _private(cells_fd, mode=0o700)
                return _run_pending(cells_fd, packets, hashes, bounds, window, verified)
            finally:
                os.close(cells_fd)


def _run_pending(
    directory: int,
    packets: Path,
    hashes: dict[str, str],
    bounds: tuple[datetime, datetime],
    window: int,
    verified: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    cells = [cell for cell in schedule.grid() if cell.window == window]
    statuses = {
        cell.cell_id: _receipt_status(directory, cell, hashes[_packet_name(cell.seed)])
        for cell in cells
    }
    stop = None
    for cell in cells:
        if statuses[cell.cell_id] is not None:
            continue
        try:
            _require_window(bounds)
        except StudyError as error:
            stop = {"cell_id": cell.cell_id, "status": str(error)}
            break
        status = _run_cell(
            directory, packets, hashes, bounds, cell, verified[cell.seed]
        )
        statuses[cell.cell_id] = status
        if status in _FAILURES:
            stop = {"cell_id": cell.cell_id, "status": status}
            break
    return {
        "window": window,
        "scheduled": len(cells),
        "complete": sum(status == "complete" for status in statuses.values()),
        "incomplete": {
            cell_id: status
            for cell_id, status in statuses.items()
            if status is not None and status != "complete"
        },
        "unattempted": [
            cell_id for cell_id, status in statuses.items() if status is None
        ],
        "stopped": stop,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("seal")
    register.add_argument("packets", type=Path)
    register.add_argument("study", type=Path)
    register.add_argument("--start-utc", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("study", type=Path)
    run = commands.add_parser("run-window")
    run.add_argument("packets", type=Path)
    run.add_argument("study", type=Path)
    run.add_argument("window", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            result = seal(args.packets, args.study, start_utc=args.start_utc)
        elif args.command == "plan":
            result = load_plan(args.study)
        else:
            result = run_window(args.packets, args.study, args.window)
    except StudyError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"error": "study_io_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(args.command == "run-window" and result["stopped"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
