"""Bind one policy's tool records to its independently replayed native turns."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Literal, TypedDict

from . import (
    calendar_client,
    calendar_packet,
    calendar_protocol,
    calendar_replica_report,
    calendar_replica_series,
    replica_io,
)
from . import calendar_tools_protocol as tools
from .calendar_report import _object


class _Action(TypedDict):
    run: str
    seat: str
    role: str
    status: Literal["bound", "missing", "invalid"]
    tool_status: str | None
    exact_local_list: bool | None
    selected_slot: int | None
    select_prompt_sha256: str | None
    select_response_sha256: str | None
    final_prompt_sha256: str | None
    final_response_sha256: str | None


class _Policy(TypedDict):
    native: dict[str, Any] | None
    actions: dict[str, _Action]
    protocol_status: Literal["complete", "missing", "invalid"]


_Failure = Literal["missing", "invalid"]
_CHILDREN = {"block", "actions", "meter"}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_ACTION_BYTES = 6 * replica_io.MAX_TEXT_BYTES + 2048
_DIGEST = re.compile(r"[a-f0-9]{64}")
_BAD_EVIDENCE = (OSError, ValueError, RuntimeError)


def _private(info: os.stat_result, mode: int) -> bool:
    return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == mode


@contextmanager
def _directory(path: str | Path, parent: int | None = None) -> Generator[int]:
    fd = os.open(path, _DIR_FLAGS, dir_fd=parent)
    try:
        if not _private(os.fstat(fd), 0o700):
            raise ValueError("expected a private directory")
        yield fd
    finally:
        os.close(fd)


def _child(
    stack: ExitStack, parent: int, name: str
) -> tuple[int | None, _Failure | None]:
    try:
        return stack.enter_context(_directory(name, parent)), None
    except FileNotFoundError:
        return None, "missing"
    except _BAD_EVIDENCE:
        return None, "invalid"


def _entries(fd: int, allowed: set[str]) -> tuple[set[str], bool]:
    names: set[str] = set()
    unexpected = False
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name in allowed:
                names.add(entry.name)
            else:
                unexpected = True
    return names, unexpected


def _read(fd: int, name: str, limit: int) -> dict[str, Any]:
    receipt = os.open(name, _FILE_FLAGS, dir_fd=fd)
    try:
        info = os.fstat(receipt)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not _private(info, 0o600)
            or info.st_size > limit
        ):
            raise ValueError("unsafe receipt")
        with os.fdopen(receipt, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
    finally:
        os.close(receipt)
    if len(raw) > limit:
        raise ValueError("oversized receipt")
    return _object(raw)


def _native(
    root: Path, block: calendar_protocol.Block
) -> tuple[dict[str, Any] | None, _Failure | None]:
    report = calendar_replica_report.analyze(root)
    coords = (report["world"], report["direction"], report["repetition"])
    if coords == (None, None, None) and report["status"] in {"not_started", "partial"}:
        return None, "missing"
    if any(type(value) is not int for value in coords) or coords != (
        block.world,
        block.direction,
        block.repetition,
    ):
        return None, "invalid"
    # The stock reader checks the exact task and both source fingerprints.
    for key in ("native_sha256", "semantics_sha256"):
        value = report[key]
        if value is None and report["status"] == "partial":
            return None, "missing"
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            return None, "invalid"
    return calendar_replica_series._public_report(report), (
        "invalid" if report["status"] == "invalid_evidence" else None
    )


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > replica_io.MAX_TEXT_BYTES
        or len(value.encode("utf-8")) > replica_io.MAX_TEXT_BYTES
    ):
        raise ValueError("invalid receipt text")
    return value


def _native_reply(
    fd: int, identifier: str, view: tools.View, hashes: dict[str, str]
) -> str:
    run, role = identifier.split(":")
    raw = _read(fd, f"{run}_{role}.json", replica_io.MAX_FRAME_BYTES)
    text = _text(raw.get("response_text"))
    prompt_hash, reply_hash = tools.digest(view.base), tools.digest(text)
    if hashes != {
        "prompt_sha256": prompt_hash,
        "response_sha256": reply_hash,
    } or raw != {
        "schema": replica_io.TURN_SCHEMA,
        "run": run,
        "role": role,
        "seat": view.seat,
        "prompt_sha256": prompt_hash,
        "response_sha256": reply_hash,
        "response_text": text,
    }:
        raise ValueError("native turn binding mismatch")
    return text


def _action(run: str, identifier: str, block: calendar_protocol.Block) -> _Action:
    role = identifier.split(":")[1]
    seat = (
        "p1"
        if role == "cal_initial_p1"
        else "p2"
        if role == "cal_initial_p2"
        else block.sender
        if role == "cal_recheck"
        else block.receiver
    )
    return {
        "run": run,
        "seat": seat,
        "role": role,
        "status": "missing",
        "tool_status": None,
        "exact_local_list": None,
        "selected_slot": None,
        "select_prompt_sha256": None,
        "select_response_sha256": None,
        "final_prompt_sha256": None,
        "final_response_sha256": None,
    }


def _bind(
    fd: int, row: _Action, identifier: str, policy: str, view: tools.View, text: str
) -> None:
    receipt = _read(fd, f"{row['run']}.json", _ACTION_BYTES)
    selection = _text(receipt.get("select_text"))
    result = tools.tool_result(view, policy, selection)
    select_prompt_hash = tools.digest(tools.select_prompt(view))
    select_response_hash = tools.digest(selection)
    final_prompt_hash = tools.digest(tools.final_prompt(view, result))
    final_response_hash = tools.digest(text)
    if receipt != {
        "schema": "seatlab.calendar-tool-turn/v1",
        "run": row["run"],
        "native_id": identifier,
        "seat": row["seat"],
        "base_sha256": tools.digest(view.base),
        "select_prompt_sha256": select_prompt_hash,
        "select_text": selection,
        "select_response_sha256": select_response_hash,
        "tool_status": result["status"],
        "tool_result_sha256": tools.result_digest(result),
        "final_prompt_sha256": final_prompt_hash,
        "final_response_sha256": final_response_hash,
    }:
        raise ValueError("tool turn binding mismatch")
    exact: bool | None = None
    slot: int | None = None
    if view.role == "cal_decide":
        try:
            slot = calendar_protocol.decode_slot(text)
        except ValueError:
            pass
    else:
        try:
            exact = calendar_protocol.decode_list(text) == calendar_packet.starts(
                view.table
            )
        except ValueError:
            exact = False
    row.update(
        status="bound",
        tool_status=result["status"],
        exact_local_list=exact,
        selected_slot=slot,
        select_prompt_sha256=select_prompt_hash,
        select_response_sha256=select_response_hash,
        final_prompt_sha256=final_prompt_hash,
        final_response_sha256=final_response_hash,
    )


def _replay(
    directory: Path,
    block: calendar_protocol.Block,
    policy: str,
    planned: dict[str, str],
    output: _Policy,
) -> None:
    issues: set[_Failure] = set()
    with ExitStack() as stack:
        root = stack.enter_context(_directory(directory))
        _, stray = _entries(root, _CHILDREN)
        if stray or (directory.parent.name, directory.name) != (
            tools.native_id(block),
            policy,
        ):
            issues.add("invalid")

        action_fd, action_issue = _child(stack, root, "actions")
        block_fd, block_issue = _child(stack, root, "block")
        _, meter_issue = _child(stack, root, "meter")
        issues.update(
            issue for issue in (action_issue, block_issue, meter_issue) if issue
        )

        present: set[str] = set()
        if action_fd is not None:
            present, stray = _entries(
                action_fd, {f"{run}.json" for run in planned.values()}
            )
            if stray:
                issues.add("invalid")

        if block_fd is not None:
            output["native"], native_issue = _native(directory / "block", block)
            if native_issue is not None:
                issues.add(native_issue)
        native = output["native"]
        hashes = native["turns"] if native is not None else {}
        if hashes.keys() - planned.keys():
            issues.add("invalid")
        if not hashes:
            issues.add("missing")

        turn_fd, turn_issue = (
            _child(stack, block_fd, "turns")
            if block_fd is not None and hashes
            else (None, None)
        )
        session = tools.Session(block) if turn_fd is not None else None
        for identifier, run in planned.items():
            has_receipt = f"{run}.json" in present
            if not has_receipt and identifier not in hashes:
                continue
            row = output["actions"][identifier] = _action(run, identifier, block)
            if identifier not in hashes:
                # Without a stock-bound reply this record is stray, not a played turn.
                row["status"] = "invalid"
            elif turn_fd is None or session is None:
                row["status"] = turn_issue or "invalid"
            else:
                try:
                    view = session.view(identifier)
                    if (view.seat, view.role) != (row["seat"], row["role"]):
                        raise ValueError("incorrect native seat")
                    text = _native_reply(turn_fd, identifier, view, hashes[identifier])
                    # Only stock-bound text can advance the native sequence. A missing
                    # tool receipt cannot affect the next stock prompt.
                    session.accept(identifier, text)
                    if has_receipt and action_fd is not None:
                        _bind(action_fd, row, identifier, policy, view, text)
                    else:
                        row["status"] = action_issue or "missing"
                except FileNotFoundError:
                    row["status"] = "missing"
                except _BAD_EVIDENCE:
                    row["status"] = "invalid"
            if row["status"] != "bound":
                issues.add(row["status"])

    if "invalid" in issues:
        output["protocol_status"] = "invalid"
    elif "missing" not in issues:
        output["protocol_status"] = "complete"


def read_policy(
    directory: Path, block: calendar_protocol.Block, policy: str
) -> _Policy:
    """Read a private policy bundle without exposing its text or inferring rewards."""
    output: _Policy = {"native": None, "actions": {}, "protocol_status": "missing"}
    try:
        if policy not in {"off", "on"}:
            raise ValueError("invalid policy")
        directory = directory.absolute()
        if directory.resolve() != directory:
            raise ValueError("expected a physical policy path")
        planned = {
            identifier: tools.action_id(block, policy, identifier)
            for identifier in calendar_client._request_plan(block)
        }
        _replay(directory, block, policy, planned, output)
    except FileNotFoundError:
        pass
    except _BAD_EVIDENCE:
        output["protocol_status"] = "invalid"
    return output
