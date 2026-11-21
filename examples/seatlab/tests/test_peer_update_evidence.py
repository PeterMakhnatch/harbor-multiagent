import json
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import inputs
from examples.seatlab.peer_update import evidence, protocol
from examples.seatlab.turn_fork import protocol as stock

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_MODEL = "openai-codex/gpt-6.2-sol"
_REPLY = json.dumps({"choices": {f"Q{i}": "D" for i in range(1, 7)}}) + "\n"


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def study():
    packet, board, digest = inputs.build(
        _RESULTS / "turn-fork-packet.json",
        (_RESULTS / "turn-fork.sha256").read_text(encoding="ascii").strip(),
        _RESULTS / "turn-fork-direct-live.json",
        _RESULTS / "turn-fork-subscription-live.json",
    )
    source_id = "s6501-r0"
    source = inputs.source_index(packet)[source_id]
    obs = min(
        (obs for obs in board["observations"] if obs["source_id"] == source_id),
        key=lambda obs: obs["prompt_sha256"],
    )
    direct = {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": "direct",
        "rows": [],
    }
    for draw in inputs.draws(board):
        row = {
            **draw,
            "run": inputs.run_id(digest, draw["obs_id"], draw["draw"]),
            "state": "unclaimed",
            "turn": None,
            "outcome": None,
        }
        if draw["obs_id"] == obs["obs_id"] and draw["draw"] == 0:
            row.update(
                state="complete",
                turn={
                    "status": "received",
                    "prompt_sha256": obs["prompt_sha256"],
                    "response_sha256": _digest(_REPLY),
                    "usage": {
                        "served_model": "deepseek-v4.1-flash-1017",
                        "input_tokens": 13,
                        "cache_tokens": 4,
                        "output_tokens": 2,
                    },
                },
                outcome=stock.grade(source, tuple(obs["opened"]), _REPLY),
            )
        direct["rows"].append(row)
    return packet, board, protocol.build(packet, board, digest, direct), direct


def _available(study):
    return [row for row in study[2]["rows"] if row["prompt_sha256"] is not None]


def _sample(study, row, *, reply=_REPLY, error=None):
    packet, board, _, direct = study
    return {
        "id": row["sample_id"],
        "input": protocol.prompt(packet, board, direct, row),
        "output": reply,
        "error": error,
    }


def test_missing_and_invalid_replies_are_not_scored(study):
    packet, board, panel, _ = study
    winner, invalid, failed, unclaimed = _available(study)
    sample = _sample(study, winner)
    bad_text = "private invalid reviewer reply"
    report = evidence.from_samples(
        packet,
        board,
        panel,
        [
            _sample(study, failed, reply=None, error="private command error"),
            _sample(study, invalid, reply=bad_text),
            sample,
        ],
    )
    assert report["panel_sha256"] == protocol.panel_digest(panel)
    rows = report["rows"]
    source = inputs.source_index(packet)[winner["source_id"]]
    obs = next(
        obs for obs in board["observations"] if obs["obs_id"] == winner["obs_id"]
    )
    actions = stock.grade(source, tuple(obs["opened"]), _REPLY)["actions"]
    target = aliases(source["cases"], source["seed"], source["repetition"])[
        winner["target"]
    ]
    assert rows[winner["index"]] == {
        **winner,
        "state": "complete",
        "turn": {
            "prompt_sha256": winner["prompt_sha256"],
            "response_sha256": _digest(_REPLY),
        },
        "outcome": {
            "actions": actions,
            "target": next(action for action in actions if action["id"] == target),
        },
    }
    assert rows[invalid["index"]] == {
        **invalid,
        "state": "incomplete",
        "turn": {
            "prompt_sha256": invalid["prompt_sha256"],
            "response_sha256": _digest(bad_text),
        },
        "outcome": None,
    }
    assert rows[failed["index"]] == {
        **failed,
        "state": "incomplete",
        "turn": None,
        "outcome": None,
    }
    assert rows[unclaimed["index"]]["state"] == "unclaimed"
    assert sum(row["state"] == "unavailable" for row in rows) == 44
    public = json.dumps(report)
    assert "private" not in public
    assert json.dumps(_REPLY) not in public


def test_rejects_duplicate_unregistered_and_misbound_samples(study):
    packet, board, panel, _ = study
    first, second, *_ = [_sample(study, row) for row in _available(study)]
    unavailable = next(row for row in panel["rows"] if row["prompt_sha256"] is None)
    for samples, message in (
        ([first, first], "duplicate"),
        ([{**first, "id": "not-registered"}], "unknown"),
        ([{**first, "id": unavailable["sample_id"]}], "unavailable"),
        ([{**first, "input": second["input"]}], "prompt mismatch"),
        ([{**first, "input": first["input"] + "\n"}], "prompt mismatch"),
    ):
        with pytest.raises(ValueError, match=message):
            evidence.from_samples(packet, board, panel, samples)


@pytest.mark.parametrize("reply", ["x" * 8193, "é" * 4097, "\ud800"])
def test_over_limit_or_unencodable_output_does_not_get_receipt_hash(study, reply):
    packet, board, panel, _ = study
    first = _available(study)[0]
    result = evidence.from_samples(
        packet, board, panel, [_sample(study, first, reply=reply)]
    )["rows"][first["index"]]
    assert result["state"] == "incomplete"
    assert result["turn"] is None
    assert result["outcome"] is None


def test_service_matches_receipts_for_exact_dispatch_turns(study, tmp_path):
    packet, board, panel, _ = study
    complete, invalid, failed, _ = _available(study)
    report = evidence.from_samples(
        packet,
        board,
        panel,
        [
            _sample(study, complete),
            _sample(study, invalid, reply="not json"),
            _sample(study, failed, reply=None, error="command error"),
        ],
    )
    receipts = tmp_path.resolve() / "dispatch"
    receipts.mkdir(mode=0o700)

    def receipt(name, row):
        turn = report["rows"][row["index"]]["turn"]
        path = receipts / name
        path.write_text(
            json.dumps(
                {
                    "schema": "seatlab.omp-seat-receipt/v1",
                    "prompt_sha256": turn["prompt_sha256"],
                    "reply_sha256": turn["response_sha256"],
                    "served_model": _MODEL,
                    "usage": {
                        "prompt_tokens": 11,
                        "cached_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 15,
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    receipt("first.json", complete)
    with pytest.raises(ValueError, match="service_evidence"):
        evidence.service(report, receipts, _MODEL)
    receipt("second.json", complete)
    with pytest.raises(ValueError, match="service_evidence"):
        evidence.service(report, receipts, _MODEL)
    second = receipt("second.json", invalid)
    observed = evidence.service(report, receipts, _MODEL)
    assert observed["verified_served_model"] == _MODEL
    assert observed["roles"] == {
        "dispatch": {
            "matched": 2,
            "incomplete_without_reply": 1,
            "input_tokens": 22,
            "cache_tokens": 6,
            "output_tokens": 8,
        }
    }
    second.chmod(0o644)
    with pytest.raises(ValueError, match="service_evidence"):
        evidence.service(report, receipts, _MODEL)


def test_collect_reads_native_inspect_log_without_accepting_scores(study, tmp_path):
    log_api = pytest.importorskip("inspect_ai.log")
    model_api = pytest.importorskip("inspect_ai.model")
    packet, board, panel, _ = study
    row = _available(study)[0]
    sample = _sample(study, row, reply="invalid returned payload")
    log = log_api.EvalLog(
        status="success",
        eval=log_api.EvalSpec(
            created="2026-11-20T00:00:00Z",
            task="peer_update",
            dataset=log_api.EvalDataset(samples=1),
            model="seatlab/dispatch",
            config=log_api.EvalConfig(),
        ),
        samples=[
            log_api.EvalSample(
                id=sample["id"],
                epoch=1,
                input=sample["input"],
                target="",
                output=model_api.ModelOutput(completion=sample["output"]),
                scores={"private": log_api.EvalSampleScore(value=1)},
            )
        ],
    )
    path = tmp_path / "review.eval"
    log_api.write_eval_log(log, path)
    result = evidence.collect(packet, board, panel, [path])["rows"][row["index"]]
    assert result["state"] == "incomplete"
    assert result["outcome"] is None
    assert result["turn"]["response_sha256"] == _digest(sample["output"])
    with pytest.raises(ValueError, match="duplicate"):
        evidence.collect(packet, board, panel, [path, path])
