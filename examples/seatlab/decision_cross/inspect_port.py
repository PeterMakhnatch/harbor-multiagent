from collections.abc import Iterator
from hashlib import sha256
from typing import Any

from ..turn_fork import packet as packet_format
from ..turn_fork import protocol
from ..visibility.clarify_run import _encode
from ..visibility.route_run import Answer, _validate_answer
from . import inputs

type _Document = dict[str, Any]


def _observations(board: _Document, digest: str) -> dict[str, _Document]:
    board = packet_format._object(
        board,
        "schema",
        "packet_sha256",
        "direct_report_sha256",
        "commands_report_sha256",
        "observations",
        "selections",
    )
    rows = board["observations"]
    if (
        board["schema"] != "seatlab.decision-cross-board/v1"
        or not isinstance(rows, list)
        or len(rows) > 48
        or sha256(_encode(board)).hexdigest() != digest
    ):
        raise ValueError("invalid decision-cross board commitment")
    index: dict[str, _Document] = {}
    for obs in rows:
        if not isinstance(obs, dict) or not isinstance(obs.get("obs_id"), str):
            raise ValueError("invalid decision-cross observation")
        obs_id = obs["obs_id"]
        if obs_id in index:
            raise ValueError("duplicate decision-cross observation")
        index[obs_id] = obs
    return index


def samples(packet: _Document, board: _Document, digest: str) -> Iterator[_Document]:
    for obs_id, obs in _observations(board, digest).items():
        yield {
            "id": obs_id,
            "input": inputs.prompt(packet, obs),
            "metadata": {
                "board_sha256": digest,
                "prompt_sha256": obs["prompt_sha256"],
            },
        }


def grade_sample(
    packet: _Document, board: _Document, digest: str, obs_id: str, text: str
) -> _Document:
    observations = _observations(board, digest)
    obs = observations.get(obs_id) if isinstance(obs_id, str) else None
    if obs is None:
        raise ValueError("unknown decision-cross sample")
    inputs.prompt(packet, obs)
    source = inputs.source_index(packet)[obs["source_id"]]
    first, second = obs["opened"]
    response_sha256 = outcome = None
    try:
        raw, _ = _validate_answer(Answer(text))
        response_sha256 = sha256(raw).hexdigest()
        outcome = protocol.grade(source, (first, second), text)
    except ValueError:
        pass
    return {
        "schema": "seatlab.decision-cross-external-score/v1",
        "board_sha256": digest,
        "obs_id": obs_id,
        "prompt_sha256": obs["prompt_sha256"],
        "response_sha256": response_sha256,
        "state": "incomplete" if outcome is None else "complete",
        "outcome": outcome,
    }
