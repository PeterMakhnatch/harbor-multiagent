import json
from hashlib import sha256
from pathlib import Path
from typing import Any, TypedDict

import pytest

from examples.seatlab.decision_cross import journal
from examples.seatlab.turn_fork import packet, protocol
from examples.seatlab.visibility.clarify_run import _encode
from examples.seatlab.visibility.route_run import Answer

type Record = dict[str, Any]

_DIGEST = "e" * 64
_OPENED = ("Q1", "Q3")
_TURN = {"status", "prompt_sha256", "response_sha256", "usage"}


class _Usage(TypedDict):
    served_model: str
    input_tokens: int
    cache_tokens: int
    output_tokens: int


@pytest.fixture
def study(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / "sealed"
    root.mkdir(mode=0o700)
    return root


@pytest.fixture
def cell() -> tuple[Record, Record, Record]:
    results = Path(__file__).resolve().parents[1] / "results"
    digest = (results / "turn-fork.sha256").read_text(encoding="ascii").strip()
    source = packet.load(results / "turn-fork-packet.json", digest)["sources"][0]
    prompt = protocol.decide_prompt(source, _OPENED)
    prompt_digest = sha256(prompt.encode()).hexdigest()
    source_id = source["source_id"]
    identity = {
        "source_id": source_id,
        "obs_id": f"{source_id}-{prompt_digest[:12]}",
        "prompt_sha256": prompt_digest,
    }
    draw = {"index": 0, **identity, "draw": 0}
    return draw, source, identity | {"opened": list(_OPENED)}


def _decision(bay: str) -> str:
    return json.dumps({"choices": {f"Q{index}": bay for index in range(1, 7)}})


def _answer(_prompt: str, _run: str, _role: str) -> Answer:
    return Answer(_decision("A"))


def test_identical_inputs_have_independent_draws(
    study: Path, cell: tuple[Record, Record, Record]
) -> None:
    draw, source, obs = cell
    repeat = draw | {"index": 1, "draw": 1}
    stages: list[tuple[str, str, str]] = []
    bodies = [_decision("A"), _decision("B")]
    usage: _Usage = {
        "served_model": "local-test-model",
        "input_tokens": 25,
        "cache_tokens": 3,
        "output_tokens": 9,
    }

    def respond(prompt: str, run: str, role: str) -> Answer:
        stages.append((prompt, run, role))
        return Answer(bodies[len(stages) - 1], **usage)

    first = journal.play(study, _DIGEST, draw, source, obs, respond)
    assert journal.collect(study, _DIGEST, repeat, source, obs) == {
        **repeat,
        "run": f"dx-{_DIGEST[:12]}-{obs['obs_id']}-r1",
        "state": "unclaimed",
        "turn": None,
        "outcome": None,
    }
    second = journal.play(study, _DIGEST, repeat, source, obs, respond)
    assert len(stages) == 2
    assert stages[0][0] == stages[1][0] == protocol.decide_prompt(source, _OPENED)
    for slot, row, body, (_, run, role) in zip(
        (draw, repeat), (first, second), bodies, stages, strict=True
    ):
        assert run == f"dx-{_DIGEST[:12]}-{obs['obs_id']}-r{slot['draw']}"
        assert role == "dispatch"
        assert row == journal.collect(study, _DIGEST, slot, source, obs)
        assert row == {
            **slot,
            "run": run,
            "state": "complete",
            "turn": {
                "status": "received",
                "prompt_sha256": obs["prompt_sha256"],
                "response_sha256": sha256(body.encode()).hexdigest(),
                "usage": usage,
            },
            "outcome": protocol.grade(source, _OPENED, body),
        }
    assert first["outcome"]["actions"] != second["outcome"]["actions"]
    assert len(list(study.iterdir())) == 4
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in study.iterdir())


@pytest.mark.parametrize("fault", ("prompt", "text", "usage", "orphan", "mode"))
def test_collect_rejects_corrupt_evidence(
    study: Path, cell: tuple[Record, Record, Record], fault: str
) -> None:
    journal.play(study, _DIGEST, *cell, _answer)
    (turn,) = study.glob("*.turn.json")
    (claim,) = study.glob("*.claim.json")
    receipt = json.loads(turn.read_bytes())
    if fault == "orphan":
        claim.unlink()
    elif fault == "mode":
        turn.chmod(0o644)
    else:
        match fault:
            case "prompt":
                receipt["prompt_sha256"] = "f" * 64
            case "text":
                receipt["text"] = _decision("B")
            case "usage":
                receipt["usage"] = {"input_tokens": -1}
        turn.write_bytes(_encode(receipt))
    with pytest.raises(journal.StudyError, match="^invalid_evidence$"):
        journal.collect(study, _DIGEST, *cell)


def test_claim_precedes_callback_and_occupied_cell_never_calls_again(
    study: Path, cell: tuple[Record, Record, Record]
) -> None:
    calls = 0

    def duplicate(_prompt: str, _run: str, _role: str) -> Answer:
        nonlocal calls
        calls += 1
        return Answer(_decision("B"))

    def respond(prompt: str, run: str, role: str) -> Answer:
        in_flight = journal.collect(study, _DIGEST, *cell)
        assert in_flight["state"] == "incomplete"
        assert in_flight["turn"] is None
        with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
            journal.play(study, _DIGEST, *cell, duplicate)
        return _answer(prompt, run, role)

    assert journal.play(study, _DIGEST, *cell, respond)["state"] == "complete"
    with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
        journal.play(study, _DIGEST, *cell, duplicate)
    assert calls == 0


@pytest.mark.parametrize("failure", ("interrupt", "exception", "malformed"))
def test_incomplete_attempts_stay_claimed_and_keep_replies_private(
    study: Path, cell: tuple[Record, Record, Record], failure: str
) -> None:
    secret = f"not a decision: {study}"
    calls = 0

    def respond(_prompt: str, _run: str, _role: str) -> Answer:
        nonlocal calls
        calls += 1
        if failure == "interrupt":
            raise KeyboardInterrupt(secret)
        if failure == "exception":
            raise RuntimeError(secret)
        return Answer(secret)

    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            journal.play(study, _DIGEST, *cell, respond)
    else:
        row = journal.play(study, _DIGEST, *cell, respond)
        assert row == journal.collect(study, _DIGEST, *cell)
    row = journal.collect(study, _DIGEST, *cell)
    assert row["state"] == "incomplete"
    assert row["outcome"] is None
    turn = row["turn"]
    assert turn.keys() == _TURN
    assert turn["usage"] is None
    assert str(study) not in json.dumps(row)
    (persisted,) = study.glob("*.turn.json")
    if failure == "malformed":
        assert turn["status"] == "received"
        assert turn["response_sha256"] == sha256(secret.encode()).hexdigest()
        assert json.loads(persisted.read_bytes())["text"] == secret
    else:
        assert turn["status"] == "callback_error"
        assert turn["response_sha256"] is None
        assert secret.encode() not in persisted.read_bytes()
    with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
        journal.play(study, _DIGEST, *cell, respond)
    assert calls == 1
