import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any, TypedDict

import pytest

from examples.seatlab.turn_fork import journal, protocol
from examples.seatlab.visibility.route_run import Answer

_DIGEST = "e" * 64
_TRIAL = "private-upstream-trial-id"
_OPEN = '\n {"open":["Q1","Q2"]} \n'
_OTHER_OPEN = '{"open":["Q3","Q4"]}'
_TURN_FIELDS = {
    "stage",
    "role",
    "run",
    "prompt_sha256",
    "response_sha256",
    "usage",
    "status",
}


class _NativeUsage(TypedDict):
    served_model: str
    input_tokens: int
    cache_tokens: int
    output_tokens: int


_USAGE: _NativeUsage = {
    "served_model": "synthetic",
    "input_tokens": 23,
    "cache_tokens": 3,
    "output_tokens": 5,
}


def _source() -> dict[str, Any]:
    return {
        "source_id": "s6501-r0",
        "seed": 6501,
        "repetition": 0,
        "trial_id": _TRIAL,
        "world_sha256": "a" * 64,
        "upstream": {
            role: {"prompt_sha256": "b" * 64, "response_sha256": "c" * 64}
            for role in ("inventory", "brief")
        },
        "card": "AABCDA",
        "cases": [
            {
                "id": index,
                "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
                "reports": [
                    {"revision": 1, "available": ["B", "C"]},
                    {"revision": 2, "available": ["A", "B"]},
                ],
                "after": ["A", "B"],
            }
            for index in range(6)
        ],
    }


@pytest.fixture
def study(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "private-study"
    path.mkdir(mode=0o700)
    return path


def _decisions(bay: str) -> str:
    return json.dumps({"choices": {f"Q{index}": bay for index in range(1, 7)}})


def _answer(_prompt: str, _run: str, role: str) -> Answer:
    return Answer(_OPEN if role == "inventory" else _decisions("A"))


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _cell(study: Path) -> Path:
    return study / "s6501-r0-aligned"


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_replays_each_actual_pair_and_keeps_repeat_independent(study: Path) -> None:
    source = _source()
    before = journal.collect(study, _DIGEST, source, "aligned")
    assert before == {
        "source_id": "s6501-r0",
        "seed": 6501,
        "repetition": 0,
        "arm": "aligned",
        "state": "unclaimed",
        "outcome": None,
        "turns": [],
    }
    pushes = []
    for arm, raw, pair, bay, optimum in (
        ("aligned", _OPEN, ("Q1", "Q2"), "A", 6),
        ("repeat", _OTHER_OPEN, ("Q3", "Q4"), "B", 0),
    ):
        calls: list[tuple[str, str, str]] = []
        decisions = _decisions(bay)

        def respond(prompt: str, run: str, role: str) -> Answer:
            calls.append((prompt, run, role))
            return Answer(raw, **_USAGE) if role == "inventory" else Answer(decisions)

        row = journal.play(study, _DIGEST, source, arm, respond)
        assert len(calls) == 2
        pushes.append(calls[0][0])
        assert calls[1][0] == protocol.decide_prompt(source, pair)
        assert row == journal.collect(study, _DIGEST, source, arm)
        assert set(row) == set(before)
        assert row["state"] == "complete"
        assert row["outcome"]["opened"] == list(pair)
        assert row["outcome"]["optimal"] == optimum
        assert row["outcome"]["feasible"] == 6
        assert row["outcome"]["opened_sensitive"] == 2
        for (prompt, run, role), (stage, expected_role, text, usage), turn in zip(
            calls,
            (
                ("push", "inventory", raw, _USAGE),
                ("decide", "dispatch", decisions, None),
            ),
            row["turns"],
            strict=True,
        ):
            assert role == expected_role
            assert run == f"tf-{_DIGEST[:12]}-s6501-r0-{arm}-{stage}"
            assert turn == {
                "stage": stage,
                "role": role,
                "run": run,
                "prompt_sha256": _sha(prompt),
                "response_sha256": _sha(text),
                "usage": usage,
                "status": "received",
            }
        public = json.dumps(row)
        assert _TRIAL not in public
        assert str(study) not in public
        assert not any(_TRIAL in prompt for prompt, _, _ in calls)
    assert pushes[0] == pushes[1]


@pytest.mark.parametrize(
    "failure", ("callback", "answer_contract", "push_parse", "decide_parse")
)
def test_failed_attempts_are_sealed_and_never_retried(
    study: Path, failure: str
) -> None:
    source = _source()
    calls: list[str] = []
    secret = f"private failure from {study}"

    def respond(_prompt: str, _run: str, role: str) -> Answer:
        calls.append(role)
        if failure == "callback":
            raise RuntimeError(secret)
        if failure == "answer_contract":
            return Answer(secret, input_tokens=10)
        if role == "inventory" and failure == "decide_parse":
            return Answer(_OPEN)
        return Answer(secret, **_USAGE)

    row = journal.play(study, _DIGEST, source, "aligned", respond)
    expected_calls = (
        ["inventory", "dispatch"] if failure == "decide_parse" else ["inventory"]
    )
    assert calls == expected_calls
    assert row == journal.collect(study, _DIGEST, source, "aligned")
    assert row["state"] == "incomplete"
    assert row["outcome"] is None
    assert len(row["turns"]) == len(expected_calls)
    last = row["turns"][-1]
    assert last.keys() == _TURN_FIELDS
    if failure in ("callback", "answer_contract"):
        assert last["status"] == "callback_error"
        assert last["response_sha256"] is None
        assert last["usage"] is None
    else:
        assert last["status"] == "received"
        assert last["response_sha256"] == _sha(secret)
        assert last["usage"] == _USAGE
    assert "private failure" not in json.dumps(row)
    assert str(study) not in json.dumps(row)
    with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
        journal.play(study, _DIGEST, source, "aligned", respond)
    assert calls == expected_calls


@pytest.mark.parametrize(
    "fault",
    (
        "truncated",
        "duplicate",
        "missing_push",
        "alternate_push",
        "run",
        "role",
        "reply",
    ),
)
def test_collect_rejects_ambiguous_or_unbound_turns(study: Path, fault: str) -> None:
    source = _source()
    journal.play(study, _DIGEST, source, "aligned", _answer)
    push, decide = _cell(study) / "push.json", _cell(study) / "decide.json"
    receipt = json.loads(decide.read_bytes())
    if fault == "truncated":
        decide.write_bytes(decide.read_bytes()[:-2])
    elif fault == "duplicate":
        decide.write_bytes(b'{"status":"callback_error",' + decide.read_bytes()[1:])
    elif fault == "missing_push":
        push.unlink()
    elif fault == "alternate_push":
        revised = json.loads(push.read_bytes())
        revised.update(text=_OTHER_OPEN, response_sha256=_sha(_OTHER_OPEN))
        _write(push, revised)
    else:
        key = "response_sha256" if fault == "reply" else fault
        receipt[key] = "forged"
        _write(decide, receipt)
    with pytest.raises(journal.StudyError, match="^invalid_evidence$"):
        journal.collect(study, _DIGEST, source, "aligned")
    with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
        journal.play(study, _DIGEST, source, "aligned", _answer)


def test_claim_binds_full_packet_and_source_truth(study: Path) -> None:
    source = _source()
    journal.play(study, _DIGEST, source, "aligned", _answer)
    misbound = deepcopy(source)
    misbound["cases"][0]["after"] = ["B", "C"]
    for digest, candidate in (
        (_DIGEST[:12] + "a" * 52, source),
        (_DIGEST, source | {"trial_id": "different-upstream-trial"}),
        (_DIGEST, misbound),
    ):
        with pytest.raises(journal.StudyError, match="^invalid_evidence$"):
            journal.collect(study, digest, candidate, "aligned")
        with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
            journal.play(study, digest, candidate, "aligned", _answer)


def test_study_rejects_symlinks_and_public_permissions(study: Path) -> None:
    source = _source()
    alias = study.parent / "study-alias"
    alias.symlink_to(study, target_is_directory=True)
    with pytest.raises(journal.StudyError, match="^study_io_failed$"):
        journal.play(alias, _DIGEST, source, "aligned", _answer)
    study.chmod(0o755)
    with pytest.raises(journal.StudyError, match="^unsafe_study_path$"):
        journal.play(study, _DIGEST, source, "aligned", _answer)
    assert not list(study.iterdir())


def test_claim_precedes_callback_and_only_contends_with_same_cell(study: Path) -> None:
    source = _source()
    entered, release = Event(), Event()
    calls: list[str] = []

    def respond(_prompt: str, _run: str, role: str) -> Answer:
        calls.append(role)
        if role == "inventory":
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test callback wait")
            return Answer(_OPEN)
        return Answer(_decisions("A"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(journal.play, study, _DIGEST, source, "aligned", respond)
        try:
            assert entered.wait(timeout=10)
            assert os.stat(_cell(study) / "claim.json").st_mode & 0o777 == 0o600
            pending = pool.submit(journal.collect, study, _DIGEST, source, "aligned")
            assert pending.result(timeout=10)["state"] == "incomplete"
            loser = pool.submit(
                journal.play, study, _DIGEST, source, "aligned", respond
            )
            with pytest.raises(journal.StudyError, match="^cell_already_claimed$"):
                loser.result(timeout=10)
            parallel = pool.submit(
                journal.play, study, _DIGEST, source, "repeat", _answer
            )
            assert parallel.result(timeout=10)["state"] == "complete"
        finally:
            release.set()
        assert winner.result(timeout=10)["state"] == "complete"
    assert calls == ["inventory", "dispatch"]
