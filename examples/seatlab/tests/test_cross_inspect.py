import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_cross import inputs, inspect_port
from examples.seatlab.turn_fork import protocol
from examples.seatlab.visibility.clarify_run import _encode

_RESULTS = Path(__file__).resolve().parents[1] / "results"
type _Document = dict[str, Any]
type _Checkpoint = tuple[_Document, _Document, str]


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def checkpoint() -> _Checkpoint:
    return inputs.build(
        _RESULTS / "turn-fork-packet.json",
        (_RESULTS / "turn-fork.sha256").read_text(encoding="ascii").strip(),
        _RESULTS / "turn-fork-direct-live.json",
        _RESULTS / "turn-fork-subscription-live.json",
    )


def test_samples_share_exact_prompts_and_expose_only_dispatch_input(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, digest = checkpoint
    samples = list(inspect_port.samples(packet, board, digest))
    assert len(samples) < sum(row["obs_id"] is not None for row in board["selections"])
    assert len({sample["id"] for sample in samples}) == len(samples)
    for sample, obs in zip(samples, board["observations"], strict=True):
        text = inputs.prompt(packet, obs)
        assert sample == {
            "id": obs["obs_id"],
            "input": text,
            "metadata": {"board_sha256": digest, "prompt_sha256": _hash(text)},
        }
        payload = json.loads(text.split("\n", 1)[1])
        assert payload.keys() == {"cases"}
        assert len(payload["cases"]) == 6
        for row in payload["cases"]:
            expected = {"alias", "utilities", "revision_1"}
            if row["alias"] in obs["opened"]:
                expected.add("revision_2")
            assert row.keys() == expected


def test_external_score_binds_the_exact_observation_and_reply(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, digest = checkpoint
    obs = board["observations"][0]
    source = inputs.source_index(packet)[obs["source_id"]]
    reply = json.dumps({"choices": {f"Q{i}": "D" for i in range(1, 7)}}) + "\n"
    first, second = obs["opened"]
    assert inspect_port.grade_sample(packet, board, digest, obs["obs_id"], reply) == {
        "schema": "seatlab.decision-cross-external-score/v1",
        "board_sha256": digest,
        "obs_id": obs["obs_id"],
        "prompt_sha256": obs["prompt_sha256"],
        "response_sha256": _hash(reply),
        "state": "complete",
        "outcome": protocol.grade(source, (first, second), reply),
    }


def test_external_score_rejects_mistaken_sample_bindings(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, digest = checkpoint
    with pytest.raises(ValueError, match="unknown decision-cross sample"):
        inspect_port.grade_sample(packet, board, digest, "s6501-r0-ffffffffffff", "{}")

    swapped = deepcopy(board)
    first, second = swapped["observations"][:2]
    first["obs_id"], second["obs_id"] = second["obs_id"], first["obs_id"]
    with pytest.raises(ValueError, match="board commitment"):
        inspect_port.grade_sample(packet, swapped, digest, first["obs_id"], "{}")
    swapped_digest = sha256(_encode(swapped)).hexdigest()
    with pytest.raises(ValueError, match="observation commitment"):
        inspect_port.grade_sample(
            packet, swapped, swapped_digest, first["obs_id"], "{}"
        )


@pytest.mark.parametrize(
    ("reply", "response_sha256"),
    [
        ("not a dispatch reply", _hash("not a dispatch reply")),
        ("x" * 8193, None),
        ("é" * 4097, None),
        ("\ud800", None),
    ],
)
def test_invalid_external_first_reply_remains_incomplete(
    checkpoint: _Checkpoint, reply: str, response_sha256: str | None
) -> None:
    packet, board, digest = checkpoint
    obs = board["observations"][0]
    result = inspect_port.grade_sample(packet, board, digest, obs["obs_id"], reply)
    assert result == {
        "schema": "seatlab.decision-cross-external-score/v1",
        "board_sha256": digest,
        "obs_id": obs["obs_id"],
        "prompt_sha256": obs["prompt_sha256"],
        "response_sha256": response_sha256,
        "state": "incomplete",
        "outcome": None,
    }
