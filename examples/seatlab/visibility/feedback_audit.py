import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import clarify_run as files
from . import feedback_protocol as protocol
from . import feedback_summary
from .route_audit import _block, _read
from .route_run import Answer, _validate_answer

_CLAIM = "claim.json"
_MAX_ARTIFACTS = 1 + 4 + 2 * 8 * 3
_USAGE_KEYS = {"served_model", "input_tokens", "cache_tokens", "output_tokens"}


def _register(blocks: tuple[protocol.Block, ...]) -> set[str]:
    if type(blocks) is not tuple or len(blocks) > 64 * 4:
        raise files.StudyError("invalid_feedback_register")
    if any(
        type(block) is not protocol.Block
        or type(block.seed) is not int
        or type(block.regime) is not str
        or type(block.rep) is not int
        for block in blocks
    ):
        raise files.StudyError("invalid_feedback_register")
    seeds = tuple(dict.fromkeys(block.seed for block in blocks))
    try:
        allowed = set(protocol.grid(seeds))
    except ValueError:
        raise files.StudyError("invalid_feedback_register") from None
    if not allowed.issuperset(blocks) or len(set(blocks)) != len(blocks):
        raise files.StudyError("invalid_feedback_register")
    return {block.key for block in blocks}


def _turn(
    fd: int, name: str, block: protocol.Block, turn: protocol.Turn
) -> tuple[dict[str, Any], str | None]:
    record = _read(fd, name)
    expected = {
        "schema": "seatlab.feedback-turn/v1",
        "run": protocol.run_id(block, turn),
        "role": turn.role,
        "prompt_sha256": sha256(turn.prompt.encode("utf-8")).hexdigest(),
    }
    if record.keys() != expected.keys() | {
        "status",
        "text",
        "response_sha256",
        "usage",
    } or any(record[key] != value for key, value in expected.items()):
        raise files.StudyError("invalid_feedback_turn")
    status, text = record["status"], record["text"]
    digest, usage = record["response_sha256"], record["usage"]
    if status == "received" and type(text) is str:
        if usage is not None and (
            type(usage) is not dict or usage.keys() != _USAGE_KEYS
        ):
            raise files.StudyError("invalid_feedback_turn")
        raw, canonical_usage = _validate_answer(Answer(text, **(usage or {})))
        if usage != canonical_usage or digest != sha256(raw).hexdigest():
            raise files.StudyError("invalid_feedback_turn")
    elif status != "callback_error" or any(
        x is not None for x in (text, digest, usage)
    ):
        raise files.StudyError("invalid_feedback_turn")
    public = {
        "run": expected["run"],
        "role": expected["role"],
        "prompt_sha256": expected["prompt_sha256"],
        "response_sha256": digest,
        "status": status,
        "usage": usage,
    }
    return public, text


def _replay(
    fd: int, block: protocol.Block, fingerprint: str
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    unseen: set[str] = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            unseen.add(entry.name)
            if len(unseen) > _MAX_ARTIFACTS:
                raise files.StudyError("unexpected_feedback_artifact")
    if not unseen:
        return "unbound", protocol.episode(block, lambda _: None), []
    if _CLAIM not in unseen or _read(fd, _CLAIM) != {
        "schema": "seatlab.feedback-claim/v1",
        "block": block.key,
        "fingerprint": fingerprint,
    }:
        raise files.StudyError("invalid_feedback_claim")
    unseen.remove(_CLAIM)
    turns: list[dict[str, Any]] = []
    stopped = False

    def ask(turn: protocol.Turn) -> str | None:
        nonlocal stopped
        if stopped:
            return None
        name = f"{turn.suffix}.json"
        if name not in unseen:
            stopped = True
            return None
        public, text = _turn(fd, name, block, turn)
        unseen.remove(name)
        turns.append(public)
        stopped = public["status"] == "callback_error"
        return text

    rows = protocol.episode(block, ask)
    if unseen:
        raise files.StudyError("unexpected_feedback_artifact")
    return "claimed", rows, turns


def _collect_block(root: int, block: protocol.Block) -> dict[str, Any]:
    fingerprint = protocol.fingerprint(block)
    with _block(root, block.key) as (fd, opened):
        if fd is not None:
            state, rows, turns = _replay(fd, block, fingerprint)
        elif opened == "missing":
            state, rows, turns = (
                "unclaimed",
                protocol.episode(block, lambda _: None),
                [],
            )
        else:
            raise files.StudyError("invalid_feedback_block")
    return {
        "seed": block.seed,
        "regime": block.regime,
        "rep": block.rep,
        "block": block.key,
        "fingerprint": fingerprint,
        "state": state,
        "rows": rows,
        "turns": turns,
    }


def collect(study: Path, blocks: tuple[protocol.Block, ...]) -> dict[str, Any]:
    allowed = _register(blocks)
    try:
        with files._directory(files._study_path(study)) as root:
            files._private(root, mode=0o700)
            with os.scandir(root) as entries:
                if any(entry.name not in allowed for entry in entries):
                    raise files.StudyError("unregistered_feedback_artifact")
            public = [_collect_block(root, block) for block in blocks]
    except files.StudyError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise files.StudyError("invalid_feedback_evidence") from None
    return {
        "schema": "seatlab.feedback-report/v1",
        "blocks": public,
        "outcomes": feedback_summary.summarize(public),
    }
