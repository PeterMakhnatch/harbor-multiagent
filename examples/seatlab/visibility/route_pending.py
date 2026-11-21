from __future__ import annotations

import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import clarify_run as files
from . import route_audit, route_memory_audit, route_packet, spend
from . import route_memory as memory
from . import route_protocol as protocol

_STAGING = re.compile(r"\.[0-9a-f]{32}\.tmp")


def _packet(path: Path, digest: str) -> tuple[route_packet.Case, ...]:
    try:
        expected = route_packet._digest(digest)
        with files._directory(path.parent) as parent:
            with files._regular(parent, path.name) as stream:
                raw = stream.read(route_packet._MAX_PACKET_BYTES + 1)
        if sha256(raw).hexdigest() != expected:
            raise ValueError("packet digest mismatch")
        return route_packet._decode(raw)
    except route_audit._READ_ERRORS:
        raise files.StudyError("invalid_packet") from None


def _stages(
    block: protocol.Block, *, delegated: bool
) -> Iterator[tuple[str, str, str]]:
    if delegated:
        for stage in memory.STAGES:
            role = "route_review" if stage.endswith("-review") else "route_decide"
            yield f"{stage}.json", memory.run_id(block, stage), role
        return
    for lens in protocol.lenses(block):
        for stage in ("review", *protocol.channels(block, lens)):
            role = "route_review" if stage == "review" else "route_decide"
            yield f"{lens}-{stage}.json", protocol.run_id(block, lens, stage), role


def _ledger_blocks(
    root: Path, prefix: str, registered: dict[tuple[str, str], int]
) -> set[int]:
    blocks: set[int] = set()
    seen: set[tuple[str, str]] = set()
    try:
        path = spend._location(files._absolute(root))
        with files._directory(path.parent) as parent:
            with files._regular(parent, path.name, 0o600) as stream:
                header = stream.read(20)
                if (
                    not header.startswith(b"SQLite format 3\x00")
                    or header[18:] != b"\x01\x01"
                ):
                    raise files.StudyError("invalid_spend_evidence")
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
            ) as db:
                rows = db.execute(
                    "SELECT run,role FROM calls WHERE substr(run,1,?) IN (?,?)",
                    (len(prefix), prefix, prefix.encode("ascii")),
                )
                for key in rows:
                    if key not in registered or key in seen:
                        raise files.StudyError("invalid_spend_evidence")
                    seen.add(key)
                    blocks.add(registered[key])
    except (OSError, ValueError, sqlite3.Error):
        raise files.StudyError("invalid_spend_evidence") from None
    return blocks


def _check_artifacts(directory: int, expected: set[str]) -> None:
    with os.scandir(directory) as entries:
        for entry in entries:
            staging = _STAGING.fullmatch(entry.name) is not None
            if entry.name not in expected and not staging:
                raise files.StudyError("invalid_study_evidence")
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                if staging:
                    continue
                raise
            mode = stat.S_IMODE(info.st_mode)
            private_mode = mode & 0o077 == 0 if staging else mode == 0o600
            if (
                not stat.S_ISREG(info.st_mode)
                or not private_mode
                or info.st_uid != os.geteuid()
            ):
                raise files.StudyError("invalid_study_evidence")


def _slot(
    directory: int,
    block: protocol.Block,
    digest: str,
    *,
    delegated: bool,
    eligible: bool,
    ledger_only: bool,
) -> str:
    name = memory.key(block) if delegated else block.id
    with route_audit._block(directory, name) as (fd, state):
        if state == "missing":
            if ledger_only:
                return "ledger_only"
            return "ready" if eligible else "ineligible"
        if fd is None:
            raise files.StudyError("invalid_study_evidence")
        expected = {file for file, _, _ in _stages(block, delegated=delegated)}
        _check_artifacts(fd, expected | {"claim.json"})
        claim = (
            route_memory_audit._claim(fd, state, name, digest, True)
            if delegated
            else route_audit._claim(fd, state, block, digest)
        )
        if claim == "unbound_claim":
            return "occupied_unbound"
        if claim == "claimed" and eligible:
            return "claimed"
        raise files.StudyError("invalid_study_evidence")


def _ranges(ready: list[int]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for index in ready:
        if ranges and ranges[-1]["stop"] == index:
            ranges[-1]["stop"] += 1
        else:
            ranges.append({"start": index, "stop": index + 1})
    return ranges


def snapshot(
    packet: Path, digest: str, study: Path, *, workflow: str, spend_dir: Path | None = None
) -> dict[str, Any]:
    if workflow not in ("review", "delegate"):
        raise files.StudyError("invalid_workflow")
    cases = _packet(packet, digest)
    delegated = workflow == "delegate"
    blocks = memory.blocks() if delegated else protocol.blocks()
    prefix = "ma-" if delegated else "rv-"
    keys = {memory.key(block) if delegated else block.id for block in blocks}
    reserved: set[int] = set()
    if spend_dir is not None:
        registered = {
            (run, role): index
            for index, block in enumerate(blocks)
            for _, run, role in _stages(block, delegated=delegated)
        }
        reserved = _ledger_blocks(spend_dir, prefix, registered)

    ready: list[int] = []
    counts = dict.fromkeys(
        ("ready", "ineligible", "claimed", "occupied_unbound", "ledger_only"), 0
    )
    try:
        with files._directory(files._study_path(study)) as directory:
            files._private(directory, mode=0o700)
            with os.scandir(directory) as entries:
                if any(
                    e.name.startswith(prefix) and e.name not in keys for e in entries
                ):
                    raise files.StudyError("invalid_study_evidence")
            for index, block in enumerate(blocks):
                eligible = (
                    memory.menu(cases, block) is not None
                    if delegated
                    else cases[block.policy * 6 + block.round - 1].reports is not None
                )
                state = _slot(
                    directory,
                    block,
                    digest,
                    delegated=delegated,
                    eligible=eligible,
                    ledger_only=index in reserved,
                )
                counts[state] += 1
                if state == "ready":
                    ready.append(index)
    except OSError:
        raise files.StudyError("invalid_study_evidence") from None
    return {
        "schema": "seatlab.route-availability/v1",
        "workflow": workflow,
        "packet_sha256": digest,
        "registered": len(blocks),
        "ready": ready,
        "ranges": _ranges(ready),
        "by_state": counts,
        "ledger_checked": spend_dir is not None,
    }
