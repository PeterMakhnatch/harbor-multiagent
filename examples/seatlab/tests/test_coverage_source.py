import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.coverage.source import load
from examples.seatlab.handoff import journal
from examples.seatlab.handoff.packet import Block, grid, parse
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer

_NOTE = '{"note":"Private revision note."}'
_READ_P = '{"read":"P"}'
_READ_Q = '{"read":"Q"}'
_PARTIAL = '{"choices":{"P":"B"}}'
_COMPLETE = '{"choices":{"P":"B","Q":"A"}}'


def _blocks() -> tuple[Block, ...]:
    packet = {
        "schema": "seatlab.handoff-input/v1",
        "brief": "Read the stock records.",
        "choices": ["A", "B"],
        "cases": [
            {
                "id": "stock",
                "cluster": "north",
                "records": [
                    {
                        "id": key,
                        "old_version": "1",
                        "new_version": "2" if key == "P" else "1",
                        "before": f"old stock {key}",
                        "after": "new stock P" if key == "P" else "old stock Q",
                        "question": f"Which bay for {key}?",
                        "old_best": "A",
                        "best": "B" if key == "P" else "A",
                    }
                    for key in ("P", "Q")
                ],
            }
        ],
    }
    return grid(parse(json.dumps(packet).encode()))


def _study(tmp_path: Path, name: str = "study") -> Path:
    study = tmp_path.resolve() / name
    study.mkdir(mode=0o700)
    return study


def _record(
    study: Path, block: Block, replies: dict[str, str | None]
) -> dict[str, str]:
    prompts: dict[str, str] = {}

    def respond(prompt: str, run: str, _role: str) -> Answer:
        stage = run.removeprefix(f"{block.key}-")
        prompts[stage] = prompt
        text = replies[stage]
        if text is None:
            raise RuntimeError("Private callback diagnostic.")
        return Answer(text)

    journal.play(study, block, respond)
    assert prompts.keys() == replies.keys()
    return prompts


def _shared(final: str | None) -> dict[str, str | None]:
    return {
        "note": _NOTE,
        "bound-select": _READ_P,
        "anonymous-select": _READ_P,
        "both-decide": final,
    }


def test_freeze_exact_bound_prompt_for_shared_and_distinct_lookups(
    tmp_path: Path,
) -> None:
    study = _study(tmp_path)
    blocks = _blocks()
    shared, distinct = blocks
    expected_shared = _record(study, shared, _shared(_PARTIAL))["both-decide"]
    distinct_prompts = _record(
        study,
        distinct,
        {
            "note": _NOTE,
            "bound-select": _READ_P,
            "anonymous-select": _READ_Q,
            "anonymous-decide": _COMPLETE,
            "bound-decide": None,
        },
    )
    expected_distinct = distinct_prompts["bound-decide"]
    frozen = load(study, blocks)
    assert len(frozen.entries) == len(blocks)
    for entry, block, prompt in zip(
        frozen.entries, blocks, (expected_shared, expected_distinct), strict=True
    ):
        assert entry.block == block
        assert entry.status == "ready"
        assert entry.prompt == prompt
        assert entry.prompt_sha256 == sha256(prompt.encode()).hexdigest()
        assert "new stock P" in prompt
        assert "old stock Q" not in prompt
        assert _PARTIAL not in prompt
        assert _COMPLETE not in prompt
        assert "Private revision note" not in repr(entry)
        assert "new stock P" not in repr(entry)
    assert expected_distinct != distinct_prompts["anonymous-decide"]


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        ({"note": '{"note":""}'}, "no_note"),
        (
            {
                "note": _NOTE,
                "bound-select": '{"read":"unknown"}',
                "anonymous-select": _READ_Q,
                "anonymous-decide": _COMPLETE,
            },
            "no_lookup",
        ),
        (
            {"note": _NOTE, "bound-select": _READ_P, "anonymous-select": None},
            "no_request",
        ),
    ],
)
def test_unenrolled_slots_preserve_distinct_reasons(
    tmp_path: Path, replies: dict[str, str | None], expected: str
) -> None:
    study = _study(tmp_path)
    blocks = _blocks()
    _record(study, blocks[0], replies)
    first, other = load(study, blocks).entries
    assert (first.status, first.prompt, first.prompt_sha256) == (expected, None, None)
    assert (other.status, other.prompt, other.prompt_sha256) == ("no_note", None, None)


def test_missing_final_receipt_is_not_a_request(tmp_path: Path) -> None:
    study = _study(tmp_path)
    blocks = _blocks()
    block = blocks[0]
    _record(study, block, _shared(None))
    recorded = load(study, blocks)
    assert recorded.entries[0].status == "ready"
    (study / block.key / "both-decide.json").unlink()
    missing = load(study, blocks)
    assert missing.entries[0].status == "no_request"
    assert missing.entries[0].prompt_sha256 is None
    assert missing.sha256 != recorded.sha256


def test_seal_changes_with_input_not_original_final_reply(tmp_path: Path) -> None:
    blocks = _blocks()
    first = _study(tmp_path, "first")
    _record(first, blocks[0], _shared(_PARTIAL))
    baseline = load(first, blocks)
    second = _study(tmp_path, "second")
    _record(second, blocks[0], _shared(_COMPLETE))
    assert load(second, blocks).sha256 == baseline.sha256
    third = _study(tmp_path, "third")
    _record(third, blocks[0], _shared(None))
    assert load(third, blocks).sha256 == baseline.sha256
    fourth = _study(tmp_path, "fourth")
    changed = _shared(_PARTIAL) | {"note": '{"note":"A different revision note."}'}
    _record(fourth, blocks[0], changed)
    assert load(fourth, blocks).sha256 != baseline.sha256
    fifth = _study(tmp_path, "fifth")
    reopened = _shared(_PARTIAL) | {
        "bound-select": _READ_Q,
        "anonymous-select": _READ_Q,
    }
    _record(fifth, blocks[0], reopened)
    assert load(fifth, blocks).sha256 != baseline.sha256


@pytest.mark.parametrize("stage", ["note", "both-decide"])
def test_rejects_changed_source_or_bad_prompt_digest(
    tmp_path: Path, stage: str
) -> None:
    study = _study(tmp_path)
    blocks = _blocks()
    block = blocks[0]
    _record(study, block, _shared(_COMPLETE))
    receipt = study / block.key / f"{stage}.json"
    decoded = json.loads(receipt.read_bytes())
    if stage == "note":
        text = '{"note":"A different private note."}'
        decoded.update(text=text, response_sha256=sha256(text.encode()).hexdigest())
    else:
        decoded["prompt_sha256"] = "0" * 64
    receipt.write_text(json.dumps(decoded), encoding="utf-8")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        load(study, blocks)


def test_rejects_evidence_changed_between_collection_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study = _study(tmp_path)
    blocks = _blocks()
    block = blocks[0]
    _record(study, block, _shared(_PARTIAL))
    receipt = study / block.key / "both-decide.json"
    collect = journal.collect

    def collect_then_change(
        path: Path, register: tuple[Block, ...]
    ) -> list[dict[str, Any]]:
        rows = collect(path, register)
        decoded = json.loads(receipt.read_bytes())
        decoded.update(
            text=_COMPLETE, response_sha256=sha256(_COMPLETE.encode()).hexdigest()
        )
        receipt.write_text(json.dumps(decoded), encoding="utf-8")
        return rows

    monkeypatch.setattr(journal, "collect", collect_then_change)
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        load(study, blocks)
