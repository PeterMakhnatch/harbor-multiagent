import os
from collections.abc import Callable
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from . import route_memory as m
from . import route_packet, route_protocol
from .clarify_run import (
    _DIRECTORY,
    StudyError,
    _directory,
    _private,
    _publish,
    _study_path,
)
from .route_run import Answer, _validate_answer

_BLOCKS = frozenset(m.blocks())


def _record_turn(
    directory: int,
    digest: str,
    block: route_protocol.Block,
    request: m.Request,
    respond: Callable[[str, str, str], Answer],
) -> str:
    run = m.run_id(block, request.stage)
    prompt_digest = sha256(request.prompt.encode("utf-8")).hexdigest()
    interrupted = False
    try:
        answer = respond(request.prompt, run, request.role)
        raw, usage = _validate_answer(answer)
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        text = response_digest = usage = None
    else:
        text = answer.text
        response_digest = sha256(raw).hexdigest()
    _publish(
        directory,
        f"{request.stage}.json",
        {
            "schema": "seatlab.memory-turn/v1",
            "block": m.key(block),
            "stage": request.stage,
            "run": run,
            "role": request.role,
            "packet_sha256": digest,
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


def _run_turns(
    directory: int,
    digest: str,
    block: route_protocol.Block,
    menu: m.Menu,
    respond: Callable[[str, str, str], Answer],
) -> None:
    remaining = set(m.STAGES)
    text: str | None = None
    with closing(m.steps(menu, block)) as steps:
        for _ in range(len(m.STAGES) + 1):
            try:
                request = steps.send(text)
            except StopIteration:
                return
            role = (
                "route_review" if request.stage.endswith("-review") else "route_decide"
            )
            if request.stage not in remaining or request.role != role:
                raise StudyError("invalid_schedule")
            remaining.remove(request.stage)
            text = _record_turn(directory, digest, block, request, respond)


def _play(
    packet: Path,
    digest: str,
    study: Path,
    block: route_protocol.Block,
    respond: Callable[[str, str, str], Answer],
) -> str:
    if (
        type(block) is not route_protocol.Block
        or any(
            type(value) is not int
            for value in (
                block.policy,
                block.round,
                block.rotation,
                block.swap,
                block.rep,
            )
        )
        or block not in _BLOCKS
    ):
        raise StudyError("invalid_block")
    try:
        menu = m.menu(route_packet.load(packet, digest), block)
    except (OSError, ValueError):
        raise StudyError("invalid_packet") from None
    try:
        with _directory(_study_path(study)) as study_fd:
            _private(study_fd, mode=0o700)
            if menu is None:
                return "ineligible"
            block_id = m.key(block)
            try:
                os.mkdir(block_id, 0o700, dir_fd=study_fd)
            except FileExistsError:
                raise StudyError("block_already_claimed") from None
            directory = os.open(block_id, _DIRECTORY, dir_fd=study_fd)
            try:
                _private(directory, mode=0o700)
                _publish(
                    directory,
                    "claim.json",
                    {
                        "schema": "seatlab.memory-claim/v1",
                        "block": block_id,
                        "packet_sha256": digest,
                    },
                    mode=0o600,
                )
                _run_turns(directory, digest, block, menu, respond)
            finally:
                os.close(directory)
    except OSError:
        raise StudyError("study_io_failed") from None
    return "complete"


def play_block(
    packet: Path,
    digest: str,
    study: Path,
    block: route_protocol.Block,
    *,
    reviewer: Callable[[str], str],
    decider: Callable[[str], str],
) -> str:
    def respond(prompt: str, _run: str, role: str) -> Answer:
        callback = reviewer if role == "route_review" else decider
        return Answer(callback(prompt))

    return _play(packet, digest, study, block, respond)
