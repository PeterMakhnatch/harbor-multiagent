import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import feedback_audit, feedback_run
from examples.seatlab.visibility import feedback_protocol as protocol
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer

_BLOCK = protocol.Block(6013, "switch", 0)
_FINAL = "recent-9-decide"
_PRIVATE_REPLY = "unparseable private reply"


def _study(tmp_path: Path) -> Path:
    study = tmp_path.resolve() / "study"
    study.mkdir(mode=0o700)
    final_pair = sorted(protocol.shipments(_BLOCK.seed)[9].after, reverse=True)
    final_report = json.dumps({"available": final_pair})

    def respond(_prompt: str, run: str, role: str) -> Answer:
        if role == "route_review":
            text = (
                final_report
                if run == f"{_BLOCK.key}-recent-9-review"
                else '{"available":["C","A"]}'
            )
        elif run.endswith("-select"):
            text = '{"ask":"X"}'
        else:
            text = '{"bay":"A"}'
        return Answer(text, "fixture-model", 9, 1, 2)

    feedback_run.play(study, _BLOCK, respond)
    return study


def _record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace(path: Path, **updates: Any) -> None:
    path.write_text(json.dumps(_record(path) | updates), encoding="utf-8")


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("end", ["absent", "callback_error", "malformed"])
def test_feedback_replay_retains_review_before_failed_decision(
    tmp_path: Path, end: str
) -> None:
    study = _study(tmp_path)
    blocks = protocol.grid((_BLOCK.seed,))
    unbound = next(block for block in blocks if block != _BLOCK)
    (study / unbound.key).mkdir(mode=0o700)
    directory = study / _BLOCK.key
    final = directory / f"{_FINAL}.json"
    if end == "absent":
        final.unlink()
    elif end == "callback_error":
        _replace(final, status=end, text=None, response_sha256=None, usage=None)
    else:
        _replace(final, text=_PRIVATE_REPLY, response_sha256=_digest(_PRIVATE_REPLY))

    report = feedback_audit.collect(study, blocks)
    public = {item["block"]: item for item in report["blocks"]}
    assert report["schema"] == "seatlab.feedback-report/v1"
    assert public[unbound.key]["state"] == "unbound"
    assert public[unbound.key]["turns"] == []
    for block in (block for block in blocks if block not in (_BLOCK, unbound)):
        assert public[block.key]["state"] == "unclaimed"
        assert public[block.key]["turns"] == []
        assert all(row["optimal"] is None for row in public[block.key]["rows"])
    claimed = public[_BLOCK.key]
    assert claimed["state"] == "claimed"
    row = claimed["rows"][-1]
    assert (row["arm"], row["step"]) == ("recent", 9)
    expected_status = "invalid_decide" if end == "malformed" else "missing_decide"
    assert row["status"] == expected_status
    assert row["desk"] == row["current_desk"] == "X"
    assert row["exact"] is True and row["copied"] is True
    assert row["bay"] is row["optimal"] is row["feasible"] is None
    assert all(row["status"] == "complete" for row in claimed["rows"][:-1])
    turns = claimed["turns"]
    assert len(turns) == (51 if end == "absent" else 52)
    review = turns[50]
    assert review["run"] == f"{_BLOCK.key}-recent-9-review"
    assert (
        review["response_sha256"]
        == _record(directory / "recent-9-review.json")["response_sha256"]
    )
    if end == "callback_error":
        assert turns[-1]["status"] == "callback_error"
        assert turns[-1]["response_sha256"] is turns[-1]["usage"] is None
    elif end == "malformed":
        assert turns[-1]["status"] == "received"
        assert turns[-1]["response_sha256"] == _digest(_PRIVATE_REPLY)
    assert all("text" not in turn for turn in turns)
    serialized = json.dumps(report)
    assert str(study) not in serialized
    assert _PRIVATE_REPLY not in serialized


@pytest.mark.parametrize(
    "tamper",
    ["other_round", "unvisited_branch", "missing_prefix", "terminal_prefix", "usage"],
)
def test_feedback_replay_rejects_contradictory_evidence(
    tmp_path: Path, tamper: str
) -> None:
    study = _study(tmp_path)
    directory = study / _BLOCK.key
    first = directory / "warm-0-decide.json"
    if tamper == "other_round":
        first_prompt = _record(first)["prompt_sha256"]
        wrong_prompt = next(
            digest
            for receipt in sorted(directory.glob("*-decide.json"))
            if (digest := _record(receipt)["prompt_sha256"]) != first_prompt
        )
        _replace(first, prompt_sha256=wrong_prompt)
    elif tamper == "unvisited_branch":
        text = '{"ask":"X","ask":"X"}'
        _replace(
            directory / "recent-2-select.json", text=text, response_sha256=_digest(text)
        )
    elif tamper == "missing_prefix":
        first.unlink()
    elif tamper == "terminal_prefix":
        _replace(
            first, status="callback_error", text=None, response_sha256=None, usage=None
        )
    else:
        _replace(first, usage=_record(first)["usage"] | {"cache_tokens": True})

    with pytest.raises(StudyError):
        feedback_audit.collect(study, protocol.grid((_BLOCK.seed,)))
