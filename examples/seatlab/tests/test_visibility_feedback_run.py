import json
import os
import stat
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.visibility import feedback_protocol as protocol
from examples.seatlab.visibility import feedback_run as runner
from examples.seatlab.visibility.route_run import Answer

_BLOCK = protocol.Block(5201, "switch", 0)
_REVIEW = ' \n{"available":["C","A"]}\n'
_DECIDE = '{"bay":"D"}'
_USAGE = {
    "served_model": "fixture-review",
    "input_tokens": 11,
    "cache_tokens": 3,
    "output_tokens": 5,
}


def _study(root: Path) -> Path:
    study = root.resolve() / "study"
    study.mkdir(mode=0o700)
    return study


def _receipt(
    run: str, role: str, prompt: str, answer: Answer | None
) -> dict[str, object]:
    return {
        "schema": "seatlab.feedback-turn/v1",
        "run": run,
        "role": role,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "status": "received" if answer is not None else "callback_error",
        "text": answer.text if answer is not None else None,
        "response_sha256": (
            sha256(answer.text.encode()).hexdigest() if answer is not None else None
        ),
        "usage": _USAGE
        if answer is not None and answer.served_model is not None
        else None,
    }


def _review(_prompt: str) -> Answer:
    return Answer(_REVIEW, "fixture-review", 11, 3, 5)


def _decide(_prompt: str) -> Answer:
    return Answer(_DECIDE)


def _unexpected(_prompt: str, _run: str, _role: str) -> Answer:
    pytest.fail("a claimed block cannot generate another turn")


def test_feedback_closed_loop_records_each_physical_call(tmp_path: Path) -> None:
    study = _study(tmp_path)
    directory = study / "hz-s5201-switch-r0"
    suffixes = ["warm-0-review", "warm-0-decide", "warm-1-review", "warm-1-decide"]
    for step in range(2, 10):
        arms = ("recent", "lifetime") if step % 2 == 0 else ("lifetime", "recent")
        suffixes.extend(
            f"{arm}-{step}-{stage}"
            for arm in arms
            for stage in ("select", "review", "decide")
        )
    names = {"claim.json"}
    calls: list[tuple[str, dict[str, object]]] = []

    def respond(prompt: str, run: str, role: str) -> Answer:
        assert {path.name for path in directory.iterdir()} == names
        assert len(calls) < len(suffixes)
        suffix = suffixes[len(calls)]
        assert run == f"{directory.name}-{suffix}"
        stage = suffix.rsplit("-", 1)[-1]
        assert role == ("route_review" if stage == "review" else "route_decide")
        if role == "route_review":
            answer = _review(prompt)
        elif stage == "select":
            answer = Answer('{"ask":"X"}')
        else:
            answer = _decide(prompt)
        filename = f"{suffix}.json"
        calls.append((filename, _receipt(run, role, prompt, answer)))
        names.add(filename)
        return answer

    rows = runner.play(study, _BLOCK, respond)
    assert len(rows) == 18
    assert all(row["status"] == "complete" and row["bay"] == "D" for row in rows)
    assert len(calls) == 52
    assert {path.name for path in directory.iterdir()} == names
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert json.loads((directory / "claim.json").read_bytes()) == {
        "schema": "seatlab.feedback-claim/v1",
        "block": directory.name,
        "fingerprint": protocol.fingerprint(_BLOCK),
    }
    for filename, receipt in calls:
        assert json.loads((directory / filename).read_bytes()) == receipt
    for path in directory.iterdir():
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid() and info.st_nlink == 1


def test_feedback_callback_failure_halts_and_keeps_first_attempt(
    tmp_path: Path,
) -> None:
    study = _study(tmp_path)
    directory = study / _BLOCK.key
    expected = [
        ("warm-0-review", "route_review", _review),
        ("warm-0-decide", "route_decide", _decide),
        ("warm-1-review", "route_review", _review),
        ("warm-1-decide", "route_decide", None),
    ]
    receipts: dict[str, dict[str, object]] = {}

    def respond(prompt: str, run: str, role: str) -> Answer:
        assert len(receipts) < len(expected)
        suffix, expected_role, callback = expected[len(receipts)]
        assert (run, role) == (f"{_BLOCK.key}-{suffix}", expected_role)
        answer = callback(prompt) if callback is not None else None
        receipts[f"{suffix}.json"] = _receipt(run, role, prompt, answer)
        if answer is None:
            raise OSError(f"private transport diagnostic {study}")
        return answer

    with pytest.raises(runner.StudyError, match="^callback_error$") as caught:
        runner.play(study, _BLOCK, respond)
    assert caught.value.__context__ is None
    assert len(receipts) == len(expected)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert set(before) == {"claim.json", *receipts}
    for filename, receipt in receipts.items():
        assert json.loads(before[filename]) == receipt
    with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
        runner.play(study, _BLOCK, _unexpected)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize("text", ["", "not JSON ☃"], ids=["empty", "malformed"])
def test_feedback_model_text_is_not_a_callback_failure(
    tmp_path: Path, text: str
) -> None:
    study = _study(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def respond(prompt: str, run: str, role: str) -> Answer:
        calls.append((prompt, run, role))
        if run == f"{_BLOCK.key}-warm-1-decide":
            return Answer(text)
        if role == "route_review":
            return _review(prompt)
        return _decide(prompt)

    rows = runner.play(study, _BLOCK, respond)
    assert len(calls) == 4
    assert len(rows) == 18 and rows[0]["status"] == "complete"
    if text:
        assert rows[1]["status"] == "invalid_decide"
    else:
        assert rows[1]["status"] in {"invalid_decide", "missing_decide"}
    assert rows[1]["bay"] is None and rows[1]["exact"] is not None
    assert all(row["status"] == "blocked_history" for row in rows[2:])
    prompt, run, role = calls[-1]
    path = study / _BLOCK.key / "warm-1-decide.json"
    assert json.loads(path.read_bytes()) == _receipt(run, role, prompt, Answer(text))
