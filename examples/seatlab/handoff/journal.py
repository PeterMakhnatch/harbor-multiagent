import os
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import Any, cast

from ..visibility.clarify_run import (
    _CHECKOUT,
    _DIRECTORY,
    StudyError,
    _directory,
    _private,
    _publish,
    _study_path,
)
from ..visibility.route_audit import _block, _read
from ..visibility.route_run import Answer, _validate_answer
from .episode import Turn, episode
from .packet import Block, grid

_BLOCK_NAME = re.compile(r"ho-[0-9a-f]{12}-[a-z0-9-]{1,20}-r[01]")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_PUBLIC_FIELDS = ("run", "role", "status", "prompt_sha256", "response_sha256", "usage")
_FAILED = dict(status="callback_error", text=None, response_sha256=None, usage=None)


def _check_block(block: Block) -> None:
    if (
        type(block) is not Block
        or type(block.index) is not int
        or not 0 <= block.index < len(block.packet.cases)
        or type(block.rep) is not int
        or block.rep not in (0, 1)
        or type(block.fingerprint) is not str
        or _FINGERPRINT.fullmatch(block.fingerprint) is None
        or _BLOCK_NAME.fullmatch(block.key) is None
    ):
        raise StudyError("invalid_block")


@contextmanager
def _study(study: Path) -> Generator[int]:
    path = _study_path(study)
    with _directory(path) as root:
        _private(root, mode=0o700)
        if os.path.samestat(os.fstat(root), _CHECKOUT.stat()) or any(
            parent.samefile(_CHECKOUT) for parent in path.parents
        ):
            raise StudyError("private_study_outside_checkout_required")
        yield root


def _claim(block: Block) -> dict[str, str]:
    return {
        "schema": "seatlab.handoff-claim/v1",
        "block": block.key,
        "packet_sha256": block.fingerprint,
    }


def _header(block: Block, turn: Turn) -> dict[str, str]:
    return {
        "schema": "seatlab.handoff-turn/v1",
        "block": block.key,
        "packet_sha256": block.fingerprint,
        "stage": turn.stage,
        "run": f"{block.key}-{turn.stage}",
        "role": turn.role,
        "prompt_sha256": sha256(turn.prompt.encode("utf-8")).hexdigest(),
    }


def _received(answer: Answer) -> dict[str, Any]:
    raw, usage = _validate_answer(answer)
    return {
        "status": "received",
        "text": answer.text,
        "response_sha256": sha256(raw).hexdigest(),
        "usage": usage,
    }


def _play(
    fd: int, block: Block, respond: Callable[[str, str, str], Answer]
) -> dict[str, Any]:
    halted = False

    def ask(turn: Turn) -> str | None:
        nonlocal halted
        if halted:
            return None
        header = _header(block, turn)
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

    return episode(block, ask)


def play(
    study: Path, block: Block, respond: Callable[[str, str, str], Answer]
) -> dict[str, Any]:
    _check_block(block)
    try:
        with _study(study) as root:
            try:
                os.mkdir(block.key, 0o700, dir_fd=root)
            except FileExistsError:
                raise StudyError("block_already_claimed") from None
            fd = os.open(block.key, _DIRECTORY, dir_fd=root)
            try:
                _private(fd, mode=0o700)
                _publish(fd, "claim.json", _claim(block), mode=0o600)
                return _play(fd, block, respond)
            finally:
                os.close(fd)
    except OSError:
        raise StudyError("study_io_failed") from None


def _evidence(fd: int, name: str) -> dict[str, Any]:
    try:
        return _read(fd, name)
    except (OSError, ValueError, TypeError, RecursionError):
        raise StudyError("invalid_evidence") from None


def _verify(block: Block, turn: Turn, receipt: dict[str, Any]) -> None:
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
    if receipt != _header(block, turn) | fields:
        raise StudyError("invalid_evidence")


def _replay(
    fd: int, block: Block, remaining: set[str]
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
        _verify(block, turn, receipt)
        remaining.remove(name)
        turns.append({key: receipt[key] for key in _PUBLIC_FIELDS})
        halted = receipt["status"] == "callback_error"
        return cast(str | None, receipt["text"])

    outcome = episode(block, ask)
    if remaining:
        raise StudyError("unexpected_evidence")
    return outcome, turns


def _collect_block(root: int, block: Block) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    with _block(root, block.key) as (fd, disk_state):
        if fd is None and disk_state == "missing":
            state = "unclaimed"
            outcome = episode(block, lambda _: None)
        elif fd is None:
            raise StudyError("invalid_evidence")
        else:
            with os.scandir(fd) as entries:
                remaining = {entry.name for entry in islice(entries, 7)}
            if not remaining:
                state = "unbound"
                outcome = episode(block, lambda _: None)
            else:
                if "claim.json" not in remaining or _evidence(
                    fd, "claim.json"
                ) != _claim(block):
                    raise StudyError("invalid_evidence")
                remaining.remove("claim.json")
                state = "claimed"
                outcome, turns = _replay(fd, block, remaining)
    return {
        "block": block.key,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "fingerprint": block.fingerprint,
        "state": state,
        "outcome": outcome,
        "turns": turns,
    }


def collect(study: Path, blocks: tuple[Block, ...]) -> list[dict[str, Any]]:
    if not 0 < len(blocks) <= 512:
        raise StudyError("invalid_register")
    for block in blocks:
        _check_block(block)
    allowed = {block.key for block in blocks}
    if len(allowed) != len(blocks) or blocks != grid(blocks[0].packet):
        raise StudyError("invalid_register")
    try:
        with _study(study) as root:
            with os.scandir(root) as entries:
                for entry in islice(entries, len(allowed) + 1):
                    if entry.name not in allowed:
                        raise StudyError("unexpected_evidence")
            return [_collect_block(root, block) for block in blocks]
    except OSError:
        raise StudyError("study_io_failed") from None
