import os
from collections.abc import Callable
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import Any, cast

from ..handoff.episode import Turn
from ..handoff.journal import (
    _FAILED,
    _FINGERPRINT,
    _PUBLIC_FIELDS,
    _check_block,
    _evidence,
    _received,
    _study,
)
from ..handoff.packet import grid
from ..visibility.clarify_run import _DIRECTORY, StudyError, _private, _publish
from ..visibility.route_audit import _block
from ..visibility.route_run import Answer
from .episode import episode
from .source import Entry, Frozen

_CLAIM = "claim.json"
_MAX_TURNS = 3
_ROLES = {
    "rewrite": "route_review",
    "both-decide": "route_decide",
    "prefix-decide": "route_decide",
    "rewrite-decide": "route_decide",
}


def key(entry: Entry, frozen_sha256: str) -> str:
    block = entry.block
    return f"np-{frozen_sha256[:12]}-{block.case.id}-r{block.rep}"


def _register(source: Frozen) -> dict[str, Entry]:
    if (
        type(source) is not Frozen
        or type(source.sha256) is not str
        or _FINGERPRINT.fullmatch(source.sha256) is None
        or type(source.entries) is not tuple
        or not 0 < len(source.entries) <= 512
    ):
        raise StudyError("invalid_register")
    for entry in source.entries:
        if type(entry) is not Entry:
            raise StudyError("invalid_register")
        _check_block(entry.block)
    blocks = tuple(entry.block for entry in source.entries)
    if source.packet_sha256 != blocks[0].fingerprint or blocks != grid(
        blocks[0].packet
    ):
        raise StudyError("invalid_register")
    entries = {key(entry, source.sha256): entry for entry in source.entries}
    if len(entries) != len(blocks):
        raise StudyError("invalid_register")
    return entries


def _binding(source: Frozen, block: str) -> dict[str, str]:
    return {
        "block": block,
        "packet_sha256": source.packet_sha256,
        "source_sha256": source.sha256,
    }


def _claim(binding: dict[str, str]) -> dict[str, str]:
    return binding | {"schema": "seatlab.packing-claim/v1"}


def _header(binding: dict[str, str], turn: Turn, sequence: int) -> dict[str, Any]:
    if _ROLES.get(turn.stage) != turn.role or not 0 <= sequence < _MAX_TURNS:
        raise StudyError("invalid_turn")
    return binding | {
        "schema": "seatlab.packing-turn/v1",
        "stage": turn.stage,
        "sequence": sequence,
        "run": f"{binding['block']}-{turn.stage}",
        "role": turn.role,
        "prompt_sha256": sha256(turn.prompt.encode("utf-8")).hexdigest(),
    }


def _play(
    fd: int,
    binding: dict[str, str],
    entry: Entry,
    respond: Callable[[str, str, str], Answer],
) -> dict[str, Any]:
    halted = False
    started: set[str] = set()

    def ask(turn: Turn) -> str | None:
        nonlocal halted
        if halted:
            return None
        header = _header(binding, turn, len(started))
        if turn.stage in started:
            raise StudyError("invalid_turn")
        started.add(turn.stage)
        interrupted = False
        try:
            fields = _received(respond(turn.prompt, header["run"], turn.role))
        except (Exception, SystemExit, KeyboardInterrupt) as error:
            fields = _FAILED
            halted = True
            interrupted = isinstance(error, KeyboardInterrupt)
        _publish(fd, f"{turn.stage}.json", header | fields, mode=0o600)
        if interrupted:
            raise KeyboardInterrupt from None
        return cast(str | None, fields["text"])

    return episode(entry, ask)


def play(
    study: Path,
    source: Frozen,
    entry: Entry,
    respond: Callable[[str, str, str], Answer],
) -> dict[str, Any]:
    entries = _register(source)
    if type(entry) is not Entry:
        raise StudyError("entry_not_registered")
    _check_block(entry.block)
    name = key(entry, source.sha256)
    registered = entries.get(name)
    if registered is None or registered != entry:
        raise StudyError("entry_not_registered")
    binding = _binding(source, name)
    try:
        with _study(study) as root:
            try:
                os.mkdir(name, 0o700, dir_fd=root)
            except FileExistsError:
                raise StudyError("block_already_claimed") from None
            fd = os.open(name, _DIRECTORY, dir_fd=root)
            try:
                _private(fd, mode=0o700)
                _publish(fd, _CLAIM, _claim(binding), mode=0o600)
                return _play(fd, binding, registered, respond)
            finally:
                os.close(fd)
    except OSError:
        raise StudyError("study_io_failed") from None


def _verify(header: dict[str, Any], receipt: dict[str, Any]) -> None:
    if receipt.get("status") == "callback_error":
        fields = _FAILED
    else:
        text = receipt.get("text")
        usage = receipt.get("usage")
        if type(text) is not str or (usage is not None and not isinstance(usage, dict)):
            raise StudyError("invalid_evidence")
        try:
            fields = _received(Answer(text, **(usage or {})))
        except (TypeError, ValueError):
            raise StudyError("invalid_evidence") from None
    if type(receipt.get("sequence")) is not int or receipt != header | fields:
        raise StudyError("invalid_evidence")


def _replay(
    fd: int,
    binding: dict[str, str],
    entry: Entry,
    remaining: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    halted = False
    turns: list[dict[str, Any]] = []

    def ask(turn: Turn) -> str | None:
        nonlocal halted
        name = f"{turn.stage}.json"
        if halted or name not in remaining:
            halted = True
            return None
        receipt = _evidence(fd, name)
        _verify(_header(binding, turn, len(turns)), receipt)
        remaining.remove(name)
        turns.append({field: receipt[field] for field in _PUBLIC_FIELDS})
        halted = receipt["status"] == "callback_error"
        return cast(str | None, receipt["text"])

    outcome = episode(entry, ask)
    if remaining:
        raise StudyError("unexpected_evidence")
    return outcome, turns


def _collect_block(
    root: int, source: Frozen, name: str, entry: Entry
) -> dict[str, Any]:
    block = entry.block
    turns: list[dict[str, Any]] = []
    with _block(root, name) as (fd, disk_state):
        if fd is None:
            if disk_state != "missing":
                raise StudyError("invalid_evidence")
            state = "unclaimed"
            outcome = episode(entry, lambda _: None)
        else:
            with os.scandir(fd) as files:
                remaining = {file.name for file in islice(files, _MAX_TURNS + 2)}
            if not remaining:
                state = "unbound"
                outcome = episode(entry, lambda _: None)
            else:
                binding = _binding(source, name)
                if _CLAIM not in remaining or _evidence(fd, _CLAIM) != _claim(binding):
                    raise StudyError("invalid_evidence")
                remaining.remove(_CLAIM)
                state = "claimed"
                outcome, turns = _replay(fd, binding, entry, remaining)
    return {
        "block": name,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "fingerprint": source.sha256,
        "state": state,
        "outcome": outcome,
        "turns": turns,
    }


def collect(study: Path, source: Frozen) -> list[dict[str, Any]]:
    entries = _register(source)
    try:
        with _study(study) as root:
            with os.scandir(root) as files:
                for file in islice(files, len(entries) + 1):
                    if file.name not in entries:
                        raise StudyError("unexpected_evidence")
            return [
                _collect_block(root, source, name, entry)
                for name, entry in entries.items()
            ]
    except OSError:
        raise StudyError("study_io_failed") from None
