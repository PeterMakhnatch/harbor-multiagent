import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import feedback_protocol as protocol
from .clarify_run import (
    _DIRECTORY,
    StudyError,
    _directory,
    _private,
    _publish,
    _study_path,
)
from .route_run import Answer, _validate_answer


def _record_turn(
    directory: int,
    block: protocol.Block,
    turn: protocol.Turn,
    respond: Callable[[str, str, str], Answer],
) -> str:
    run = protocol.run_id(block, turn)
    prompt_digest = sha256(turn.prompt.encode("utf-8")).hexdigest()
    interrupted = False
    try:
        answer = respond(turn.prompt, run, turn.role)
        raw, usage = _validate_answer(answer)
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        text = response_digest = usage = None
    else:
        text = answer.text
        response_digest = sha256(raw).hexdigest()
    _publish(
        directory,
        f"{turn.suffix}.json",
        {
            "schema": "seatlab.feedback-turn/v1",
            "run": run,
            "role": turn.role,
            "prompt_sha256": prompt_digest,
            "status": "callback_error" if text is None else "received",
            "text": text,
            "response_sha256": response_digest,
            "usage": usage,
        },
        mode=0o600,
    )
    if interrupted:
        raise KeyboardInterrupt
    if text is None:
        raise StudyError("callback_error")
    return text


def _block_key(block: protocol.Block) -> str:
    if (
        type(block) is not protocol.Block
        or type(block.seed) is not int
        or type(block.regime) is not str
        or type(block.rep) is not int
    ):
        raise StudyError("invalid_block")
    try:
        valid = block in protocol.grid((block.seed,))
    except ValueError:
        valid = False
    if not valid:
        raise StudyError("invalid_block")
    return block.key


def play(
    study: Path,
    block: protocol.Block,
    respond: Callable[[str, str, str], Answer],
) -> list[dict[str, Any]]:
    block_key = _block_key(block)
    try:
        with _directory(_study_path(study)) as study_fd:
            _private(study_fd, mode=0o700)
            try:
                os.mkdir(block_key, 0o700, dir_fd=study_fd)
            except FileExistsError:
                raise StudyError("block_already_claimed") from None
            directory = os.open(block_key, _DIRECTORY, dir_fd=study_fd)
            try:
                _private(directory, mode=0o700)
                _publish(
                    directory,
                    "claim.json",
                    {
                        "schema": "seatlab.feedback-claim/v1",
                        "block": block_key,
                        "fingerprint": protocol.fingerprint(block),
                    },
                    mode=0o600,
                )
                return protocol.episode(
                    block, lambda turn: _record_turn(directory, block, turn, respond)
                )
            finally:
                os.close(directory)
    except OSError:
        raise StudyError("study_io_failed") from None
