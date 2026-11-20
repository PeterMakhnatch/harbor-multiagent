"""Run one registered block of direct second ballots without retrying a cell."""

import argparse
import json
import os
import sqlite3
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, NoReturn

from ..visibility import deepseek, spend
from . import ballot_packet
from .analysis import _Excluded, _parse, _sha
from .ballot_schedule import Cell, grid
from .schedule import TASK_TREE

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA = "seatlab.committee-peer-turn/v1"
_MODELS = (deepseek.MODEL, deepseek.REVISION, deepseek.REVISION.lower())
_CONTINUE = ("complete", "invalid_ballot")
_STOP = (
    "started",
    "transport_failed",
    "identity_failed",
    "budget_unresolved",
    "evidence_invalid",
)
_EVIDENCE_ERRORS = (
    OSError,
    ValueError,
    RuntimeError,
    KeyError,
    TypeError,
    AttributeError,
    _Excluded,
)
_MAX_RECEIPT_BYTES = 512 * 1024
_TURN_FAILURES = {
    "served_model_unverified": "identity_failed",
    "balance_below_minimum": "budget_unresolved",
    "balance_unverified": "budget_unresolved",
    "budget_guard": "budget_unresolved",
    "usage_unverified": "budget_unresolved",
    "payload_invalid": "evidence_invalid",
    "payload_too_large": "evidence_invalid",
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "peer ballots: invalid_arguments\n")


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    else:
        _sync_directory(path.parent)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("private study directory required")


def _read_private(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_nlink != 1
            or info.st_size > _MAX_RECEIPT_BYTES
        ):
            raise ValueError("unsafe private receipt")
        return stream.read()


def _publish(path: Path, data: bytes, *, replace: bool = False) -> None:
    # A reader sees a whole receipt. The initial link never replaces a claim.
    with NamedTemporaryFile(dir=path.parent, prefix=".peer-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if replace:
                temporary.replace(path)
            else:
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _study_cells(path: Path, packet_sha256: str) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError("private study directory required")
    path = path.resolve()
    cells = path / "cells"
    if path.is_relative_to(_REPO) or any(
        os.path.lexists(parent / ".git") for parent in (cells, path, *path.parents)
    ):
        raise ValueError("study must be outside repositories")
    _private_directory(path)
    _private_directory(cells)
    binding = path / "packet.sha256"
    expected = (packet_sha256 + "\n").encode("ascii")
    try:
        actual = _read_private(binding)
    except FileNotFoundError:
        if any(cells.iterdir()):
            raise ValueError("study packet binding missing") from None
        try:
            _publish(binding, expected)
        except FileExistsError:
            actual = _read_private(binding)
        else:
            actual = expected
    if actual != expected:
        raise ValueError("study packet changed")
    return cells


def _claim(cell: Cell, packet_sha256: str, prompt: str) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "cell_id": cell.cell_id,
        "packet_sha256": packet_sha256,
        "status": "started",
        "prompt_sha256": _sha(prompt),
        "served_model": None,
        "response_sha256": None,
        "response_text": None,
        "ballot": None,
    }


def _received(
    packet: dict, cell: Cell, claim: dict, model: object, text: object
) -> dict:
    if not isinstance(model, str) or model not in _MODELS:
        return claim | {"status": "identity_failed"}
    if not isinstance(text, str):
        return claim | {"status": "transport_failed"}
    try:
        digest = _sha(text)
    except UnicodeEncodeError:
        return claim | {"status": "transport_failed"}
    try:
        ballot = ballot_packet.decode(packet, cell.map_index, cell.seat_index, text)
    except ballot_packet.InvalidBallot:
        ballot = None
    return claim | {
        "status": "complete" if ballot is not None else "invalid_ballot",
        "served_model": model,
        "response_sha256": digest,
        "response_text": text,
        "ballot": ballot,
    }


def _existing(path: Path, packet: dict, cell: Cell, claim: dict) -> str | None:
    try:
        data = _read_private(path)
    except FileNotFoundError:
        return None
    except _EVIDENCE_ERRORS:
        return "evidence_invalid"
    try:
        receipt = _parse(data.decode("utf-8"))
        status = receipt["status"] if isinstance(receipt, dict) else None
        if status in _CONTINUE:
            expected = _received(
                packet, cell, claim, receipt["served_model"], receipt["response_text"]
            )
        elif status in _STOP:
            expected = claim | {"status": status}
        else:
            return "evidence_invalid"
    except _EVIDENCE_ERRORS:
        return "evidence_invalid"
    return expected["status"] if receipt == expected else "evidence_invalid"


def _turn(packet: dict, cell: Cell, prompt: str, claim: dict) -> dict:
    try:
        response = deepseek.turn(prompt, run_id=cell.cell_id, role=cell.role)
    except deepseek.SeatUnavailable as exc:
        status = _TURN_FAILURES.get(exc.category, "transport_failed")
        return claim | {"status": status}
    except (OSError, spend.SpendRefused, sqlite3.Error):
        return claim | {"status": "budget_unresolved"}
    except Exception:
        # Keep unforeseen provider diagnostics out of public output and receipts.
        return claim | {"status": "transport_failed"}
    try:
        return _received(packet, cell, claim, response.served_model, response.text)
    except _EVIDENCE_ERRORS:
        return claim | {"status": "evidence_invalid"}


def _run(
    packet_path: Path,
    study: Path,
    digest: str,
    cells: tuple[Cell, ...],
    states: dict[str, str],
) -> str | None:
    # read_packet checks both the announced digest and the stock task evidence.
    packet = ballot_packet.read_packet(packet_path, digest)
    if packet.get("task_git_tree") != TASK_TREE:
        return "evidence_invalid"
    directory = _study_cells(study, digest)
    pending = []
    stop = None
    for cell in cells:
        prompt = ballot_packet.prompt(packet, cell.map_index, cell.seat_index, cell.arm)
        claim = _claim(cell, digest, prompt)
        path = directory / f"{cell.cell_id}.json"
        status = _existing(path, packet, cell, claim)
        if status is None:
            pending.append((cell, path, prompt, claim))
        else:
            states[cell.cell_id] = status
            if pending:
                stop = (
                    "evidence_invalid"  # A hole before a later claim is not resumable.
                )
            elif status in _STOP and stop is None:
                stop = status
    if stop is not None:
        return stop
    for cell, path, prompt, claim in pending:
        try:
            _publish(path, json.dumps(claim).encode("utf-8"))
        except FileExistsError:
            # Another process owns this claim. Do not advance past its open turn.
            status = _existing(path, packet, cell, claim) or "evidence_invalid"
            states[cell.cell_id] = status
            if status in _STOP:
                return status
            continue
        except (OSError, KeyboardInterrupt):
            status = _existing(path, packet, cell, claim)
            if status is not None:
                states[cell.cell_id] = status
            raise
        states[cell.cell_id] = "started"
        receipt = _turn(packet, cell, prompt, claim)
        _publish(path, json.dumps(receipt).encode("utf-8"), replace=True)
        status = states[cell.cell_id] = receipt["status"]
        if status in _STOP:
            return status
    return None


def main(argv: list[str] | None = None) -> int:
    cli = _Parser(
        prog="python -m examples.seatlab.committee.ballot_run", description=__doc__
    )
    cli.add_argument("packet", type=Path)
    cli.add_argument("study", type=Path)
    cli.add_argument("--expect-sha256", required=True)
    cli.add_argument("--block", type=int, choices=(0, 1), required=True)
    args = cli.parse_args(argv)
    cells = tuple(cell for cell in grid() if cell.block == args.block)
    states: dict[str, str] = {}
    try:
        stop = _run(args.packet, args.study, args.expect_sha256, cells, states)
    except _EVIDENCE_ERRORS:
        stop = "evidence_invalid"
    except KeyboardInterrupt:
        stop = "started" if "started" in states.values() else "interrupted"
    complete = sum(status == "complete" for status in states.values())
    print(
        json.dumps(
            {
                "block": args.block,
                "planned": len(cells),
                "complete": complete,
                "attempted": len(states),
                "incomplete": len(states) - complete,
                "unattempted": len(cells) - len(states),
                "invalid_ballot": sum(
                    status == "invalid_ballot" for status in states.values()
                ),
                "stop": stop,
            }
        )
    )
    return int(stop is not None)


if __name__ == "__main__":
    raise SystemExit(main())
