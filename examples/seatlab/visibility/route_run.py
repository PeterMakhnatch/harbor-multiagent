"""Run one registered routed review block with private first-attempt receipts."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from . import route_packet, route_protocol
from .clarify_run import (
    _DIRECTORY,
    StudyError,
    _directory,
    _private,
    _publish,
    _study_path,
)

_MAX_REPLY_BYTES = 8192
_MODEL_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_MAX_TOKENS = (1 << 63) - 1
_BLOCKS = frozenset(route_protocol.blocks())


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    served_model: str | None = None
    input_tokens: int | None = None
    cache_tokens: int | None = None
    output_tokens: int | None = None


def _validate_answer(answer: Answer) -> tuple[bytes, dict[str, str | int] | None]:
    if type(answer) is not Answer or type(answer.text) is not str:
        raise ValueError("invalid_answer")
    if len(answer.text) > _MAX_REPLY_BYTES:
        raise ValueError("invalid_answer")
    raw = answer.text.encode("utf-8")
    if len(raw) > _MAX_REPLY_BYTES:
        raise ValueError("invalid_answer")

    model, total, cached, output = (
        answer.served_model,
        answer.input_tokens,
        answer.cache_tokens,
        answer.output_tokens,
    )
    if model is None and total is None and cached is None and output is None:
        return raw, None
    if (
        type(model) is not str
        or _MODEL_TAG.fullmatch(model) is None
        or type(total) is not int
        or type(cached) is not int
        or type(output) is not int
        or not 0 <= cached <= total <= _MAX_TOKENS
        or not 0 <= output <= _MAX_TOKENS
    ):
        raise ValueError("invalid_answer")
    # This records callback metadata, not an independent model attestation.
    return raw, {
        "served_model": model,
        "input_tokens": total,
        "cache_tokens": cached,
        "output_tokens": output,
    }


def _run_turns(
    directory: int,
    case: route_packet.Case,
    digest: str,
    block: route_protocol.Block,
    respond: Callable[[str, str, str], Answer],
) -> None:
    block_id = block.id

    def ask(lens: str, stage: str, prompt: str, run: str) -> str:
        role = "route_review" if stage == "review" else "route_decide"
        prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
        try:
            answer = respond(prompt, run, role)
            raw, usage = _validate_answer(answer)
        except KeyboardInterrupt:
            # Cancellation leaves an absent turn, not a fabricated receipt.
            raise KeyboardInterrupt from None
        except (Exception, SystemExit):
            text = response_digest = usage = None
        else:
            text = answer.text
            response_digest = sha256(raw).hexdigest()
        _publish(
            directory,
            f"{lens}-{stage}.json",
            {
                "schema": "seatlab.routed-turn/v1",
                "block": block_id,
                "lens": lens,
                "stage": stage,
                "run": run,
                "role": role,
                "packet_sha256": digest,
                "prompt_sha256": prompt_digest,
                "status": "callback_error" if text is None else "received",
                "text": text,
                "response_sha256": response_digest,
                "usage": usage,
            },
            mode=0o600,
        )
        if text is None:
            raise StudyError("callback_error")
        return text

    own = route_protocol.view(case, block, "duplicate")
    for lens in route_protocol.lenses(block):
        peer = own if lens == "duplicate" else route_protocol.view(case, block, lens)
        text = ask(
            lens,
            "review",
            route_protocol.review_prompt(peer),
            route_protocol.run_id(block, lens, "review"),
        )
        try:
            review = route_protocol.parse_review(text)
        except ValueError:
            continue
        for channel in route_protocol.channels(block, lens):
            ask(
                lens,
                channel,
                route_protocol.decision_prompt(own, peer.fields, review, channel),
                route_protocol.run_id(block, lens, channel),
            )


def _play(
    packet: Path,
    digest: str,
    study: Path,
    block: route_protocol.Block,
    respond: Callable[[str, str, str], Answer],
) -> str:
    """Run one block. Trusted callbacks receive (prompt, run ID, role)."""
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
        cases = route_packet.load(packet, digest)
    except (OSError, ValueError):
        raise StudyError("invalid_packet") from None
    case = next(
        (
            item
            for item in cases
            if (item.policy, item.round) == (block.policy, block.round)
        ),
        None,
    )
    if case is None:
        raise StudyError("invalid_packet")

    try:
        with _directory(_study_path(study)) as study_fd:
            _private(study_fd, mode=0o700)
            if case.reports is None:
                return "ineligible"
            block_id = block.id
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
                        "schema": "seatlab.routed-claim/v1",
                        "block": block_id,
                        "packet_sha256": digest,
                    },
                    mode=0o600,
                )
                _run_turns(directory, case, digest, block, respond)
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
    """Run prompt-only callbacks once per stage of an eligible block."""

    def respond(prompt: str, _run: str, role: str) -> Answer:
        callback = reviewer if role == "route_review" else decider
        return Answer(callback(prompt))

    return _play(packet, digest, study, block, respond)
