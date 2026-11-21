import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.rolepack import evidence, protocol

type _Doc = dict[str, Any]

_MODEL = "openai-codex/gpt-6.2-sol"
_REPLY = '{"choice":0}\n'


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def pack() -> _Doc:
    prompt = "Private example: choose a slot.\n"
    scalar = {
        "id": "slot-w1",
        "task": "schedules",
        "world": "w1",
        "prompt": prompt,
        "prompt_sha256": _hash(prompt),
        "peer": {
            "answer": {"choice": 1},
            "run": "source-w1",
            "source_sha256": _hash("a public source report\n"),
            "response_sha256": None,
        },
        "rubric": {
            "field": "choice",
            "scalar": True,
            "items": [{"key": "choice", "valid": [0, 1], "optimal": [0]}],
        },
    }
    pickup = "Private example: select 2 pickup zones.\n"
    vector = {
        **scalar,
        "id": "zones-w1",
        "task": "pickups",
        "prompt": pickup,
        "prompt_sha256": _hash(pickup),
        "peer": {**scalar["peer"], "answer": {"zones": {"b": "N", "a": "N"}}},
        "rubric": {
            "field": "zones",
            "scalar": False,
            "items": [
                {"key": "b", "valid": ["N", "S"], "optimal": ["S"]},
                {"key": "a", "valid": ["N", "S"], "optimal": ["N"]},
            ],
        },
    }
    missing = {**scalar, "id": "slot-w2", "world": "w2", "peer": None}
    return {"schema": "seatlab.rolepack/v1", "records": [scalar, missing, vector]}


def _slot(pack: _Doc, record_id: str, draw: int, arm: str) -> _Doc:
    return next(
        slot
        for slot in protocol.plan(pack)
        if (slot["record_id"], slot["draw"], slot["arm"]) == (record_id, draw, arm)
    )


def _sample(pack: _Doc, slot: _Doc, reply: str | None, error: object = None) -> _Doc:
    record = next(
        record for record in pack["records"] if record["id"] == slot["record_id"]
    )
    return {
        "id": slot["sample_id"],
        "input": protocol.prompt(record, slot["arm"]),
        "output": reply,
        "error": error,
    }


def test_collect_keeps_invalid_and_absent_first_attempts_distinct(pack):
    good = _slot(pack, "slot-w1", 0, "original")
    bad = _slot(pack, "slot-w1", 0, "peer")
    failed = _slot(pack, "slot-w1", 1, "original")
    interrupted = _slot(pack, "slot-w1", 1, "peer")
    vector = _slot(pack, "zones-w1", 0, "original")
    bad_reply = '{"choice":0,"choice":0}\n'
    samples = [
        _sample(pack, interrupted, None, "private interruption"),
        _sample(pack, bad, bad_reply),
        _sample(pack, failed, _REPLY, "private transport failure"),
        _sample(pack, vector, '{"zones":{"a":"N","b":"N"}}'),
        _sample(pack, good, _REPLY),
    ]
    report = evidence.from_samples(pack, samples)
    assert evidence.verify(pack, report) == report
    assert report["pack_sha256"] == protocol.digest(pack)
    rows = report["rows"]
    assert rows[good["index"]] == {
        **good,
        "state": "complete",
        "turn": {
            "prompt_sha256": good["prompt_sha256"],
            "response_sha256": _hash(_REPLY),
        },
        "outcome": {"values": [0], "correct": [True]},
    }
    assert rows[vector["index"]]["outcome"] == {
        "values": ["N", "N"],
        "correct": [False, True],
    }
    assert rows[bad["index"]]["turn"]["response_sha256"] == _hash(bad_reply)
    assert rows[failed["index"]]["turn"]["response_sha256"] == _hash(_REPLY)
    assert rows[interrupted["index"]]["turn"] is None
    assert all(
        rows[slot["index"]]["state"] == "incomplete"
        for slot in (bad, failed, interrupted)
    )
    assert all(
        rows[slot["index"]]["outcome"] is None for slot in (bad, failed, interrupted)
    )
    assert sum(row["state"] == "unavailable" for row in rows) == 4
    assert sum(row["state"] == "unclaimed" for row in rows) == 3
    public = json.dumps(report)
    for secret in (
        "Private example",
        "private",
        json.dumps(_REPLY),
        "verified_served_model",
    ):
        assert secret not in public


def test_rejects_misbound_prompt_and_does_not_replace_first_failure(pack):
    slot = _slot(pack, "slot-w1", 0, "peer")
    first = _sample(pack, slot, "not JSON")
    later = _sample(pack, slot, _REPLY)
    with pytest.raises(ValueError, match="duplicate"):
        evidence.from_samples(pack, [first, later])
    with pytest.raises(ValueError, match="prompt mismatch"):
        evidence.from_samples(pack, [{**later, "input": later["input"] + "\n"}])
    for sample_id in (
        "unregistered:d0:original",
        "slot-w2:d0:original",
        "slot-w2:d0:peer",
    ):
        with pytest.raises(ValueError, match="unknown or unavailable"):
            evidence.from_samples(pack, [{**later, "id": sample_id}])


def test_verify_rejects_tampered_outcomes_hashes_and_slots(pack):
    slot = _slot(pack, "slot-w1", 0, "original")
    report = evidence.from_samples(pack, [_sample(pack, slot, _REPLY)])
    index = slot["index"]
    for changes in (
        {"outcome": {"values": [1], "correct": [True]}},
        {"outcome": {"values": [False], "correct": [True]}},
        {"outcome": {"values": [0], "correct": [1]}},
        {"outcome": {"values": [0], "correct": [True], "raw": _REPLY}},
        {"turn": {"prompt_sha256": _hash("moved"), "response_sha256": _hash(_REPLY)}},
        {"turn": {"prompt_sha256": slot["prompt_sha256"], "response_sha256": "A" * 64}},
        {"state": "unclaimed"},
        {"draw": False},
    ):
        bad = deepcopy(report)
        bad["rows"][index].update(changes)
        with pytest.raises(ValueError):
            evidence.verify(pack, bad)
    with pytest.raises(ValueError):
        evidence.verify(pack, {**report, "rows": report["rows"][:-1]})


def test_service_matches_a_dispatch_multiset_including_invalid_reply(pack, tmp_path):
    first = _slot(pack, "slot-w1", 0, "original")
    second = _slot(pack, "slot-w1", 1, "original")
    broken = _slot(pack, "slot-w1", 0, "peer")
    missing = _slot(pack, "slot-w1", 1, "peer")
    report = evidence.from_samples(
        pack,
        [
            _sample(pack, first, _REPLY),
            _sample(pack, second, _REPLY),
            _sample(pack, broken, "not JSON"),
            _sample(pack, missing, None, "private error"),
        ],
    )
    receipts = tmp_path.resolve() / "dispatch"
    receipts.mkdir(mode=0o700)
    assert (
        evidence.service(evidence.collect(pack, []), receipts, _MODEL)[
            "verified_served_model"
        ]
        is None
    )

    def write(index, slot):
        turn = report["rows"][slot["index"]]["turn"]
        path = receipts / f"{index}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "seatlab.omp-seat-receipt/v1",
                    "prompt_sha256": turn["prompt_sha256"],
                    "reply_sha256": turn["response_sha256"],
                    "served_model": _MODEL,
                    "usage": {
                        "prompt_tokens": 9,
                        "cached_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 12,
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

    write(0, first)
    write(1, broken)
    with pytest.raises(ValueError, match="service_evidence"):
        evidence.service(report, receipts, _MODEL)
    write(2, second)
    joined = evidence.service(report, receipts, _MODEL)
    assert joined["pack_sha256"] == report["pack_sha256"]
    assert joined["verified_served_model"] == _MODEL
    assert joined["roles"] == {
        "dispatch": {
            "matched": 3,
            "incomplete_without_reply": 1,
            "input_tokens": 27,
            "cache_tokens": 6,
            "output_tokens": 9,
        }
    }
    write(3, broken)
    with pytest.raises(ValueError, match="service_evidence"):
        evidence.service(report, receipts, _MODEL)


def test_collect_reads_original_inspect_logs_without_adopting_scores(pack, tmp_path):
    logs = pytest.importorskip("inspect_ai.log")
    model = pytest.importorskip("inspect_ai.model")
    event = pytest.importorskip("inspect_ai.event")
    slot = _slot(pack, "slot-w1", 0, "original")
    sample = _sample(pack, slot, "not JSON")

    def save(name, epoch, reply, *, sent=None, observed=True):
        path = tmp_path / name
        log = logs.EvalLog(
            status="success",
            eval=logs.EvalSpec(
                created="2026-11-20T00:00:00Z",
                task="rolepack",
                dataset=logs.EvalDataset(samples=1),
                model="seatlab-command/dispatch",
                config=logs.EvalConfig(),
            ),
            samples=[
                logs.EvalSample(
                    id=sample["id"],
                    epoch=epoch,
                    input=sample["input"],
                    target="",
                    output=model.ModelOutput.from_content(
                        "seatlab-command/dispatch", reply
                    ),
                    scores={"private": logs.EvalSampleScore(value=1)},
                    events=[
                        event.ModelEvent(
                            model="seatlab-command/dispatch",
                            input=[
                                model.ChatMessageUser(
                                    content=sample["input"] if sent is None else sent
                                )
                            ],
                            tools=[],
                            tool_choice="none",
                            config=model.GenerateConfig(),
                            output=model.ModelOutput.from_content(
                                "seatlab-command/dispatch", reply
                            ),
                        )
                    ]
                    if observed
                    else [],
                )
            ],
        )
        logs.write_eval_log(log, path)
        return path

    original = save("original.eval", 1, sample["output"])
    later = save("later.eval", 1, _REPLY)
    wrong_epoch = save("retry.eval", 2, _REPLY)
    assert evidence.collect(pack, [original]) == evidence.from_samples(pack, [sample])
    with pytest.raises(ValueError, match="duplicate"):
        evidence.collect(pack, [original, later])
    with pytest.raises(ValueError, match="first-attempt"):
        evidence.collect(pack, [wrong_epoch])
    for unsuitable in (
        save("changed-input.eval", 1, _REPLY, sent="a different model input"),
        save("not-called.eval", 1, _REPLY, observed=False),
    ):
        with pytest.raises(ValueError, match="first-attempt"):
            evidence.collect(pack, [unsuitable])
