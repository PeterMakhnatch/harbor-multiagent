import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from examples.seatlab.calendar_questions import source, stages
from examples.seatlab.rolepack import calendar_source, evidence, protocol

type _Doc = protocol.Document


@pytest.fixture(scope="module")
def sources() -> _Doc:
    if not (path := os.environ.get("SEATLAB_CALENDAR_ROOT")):
        pytest.skip("set SEATLAB_CALENDAR_ROOT to the trusted calendar-list checkout")
    root = Path(path).absolute()
    original = {"schema": protocol.SCHEMA, "records": calendar_source.export(root)}
    return source.build(root, original)


def _sample(
    record: _Doc, reply: object, *, draw: int = 0, error: str | None = None
) -> _Doc:
    return {
        "id": f"{record['id']}:d{draw}:original",
        "input": protocol.prompt(record, "original"),
        "output": json.dumps(reply),
        "error": error,
    }


def _ask(first: int, second: int) -> _Doc:
    return {"ask": {"first": first, "second": second}}


def _availability(first: str, second: str) -> _Doc:
    return {"availability": {"first": first, "second": second}}


def _request(first: int, second: int) -> str:
    return f"Requested slot IDs: first={first}; second={second}.\n"


def test_stages_do_not_replace_a_missing_or_failed_first_draw(sources: _Doc) -> None:
    questions = stages.question(sources)
    first, second = questions["records"][:2]
    later = evidence.from_samples(
        questions,
        [
            _sample(first, _ask(0, 0), draw=1),
            _sample(second, _ask(1, 1), error="dispatch failed"),
            _sample(second, _ask(1, 1), draw=1),
        ],
    )
    assert stages.answer(sources, later) is None
    assert stages.final(sources, later, None) is None

    first_attempt = evidence.from_samples(questions, [_sample(first, _ask(0, 0))])
    answers = stages.answer(sources, first_attempt)
    assert answers is not None
    answer_record = answers["records"][0]
    valid_reply = _availability("yes", "yes")
    failed = evidence.from_samples(
        answers,
        [
            _sample(answer_record, _availability("unknown", "yes")),
            _sample(answer_record, valid_reply, draw=1),
        ],
    )
    assert stages.final(sources, first_attempt, failed) is None
    assert stages.final(sources, first_attempt, evidence.from_samples(answers, [])) is None
    with pytest.raises(ValueError, match="no calendar answer pack"):
        stages.final(sources, later, failed)


def _assert_final_pair(
    row: _Doc, slots: tuple[int, int], visible: _Doc, withheld: _Doc, answers: str
) -> None:
    prefix = row["receiver_prompt"] + "\n" + _request(*slots)
    with_answer = prefix + f"Peer availability: {answers}\n"
    without_answer = prefix + "Peer availability: no answer is available.\n"
    assert visible["prompt"].startswith(with_answer)
    assert withheld["prompt"].startswith(without_answer)
    assert visible["prompt"].removeprefix(with_answer) == withheld[
        "prompt"
    ].removeprefix(without_answer)
    for record in (visible, withheld):
        assert record["peer"] is None
        assert row["id"] not in record["prompt"]
        assert row["sender_view"] not in record["prompt"]
        unasked = next(slot for slot in range(35) if slot not in slots)
        assert protocol.grade(record, json.dumps({"slot": unasked}))["values"] == [
            unasked
        ]


def _assert_final_plan(finals: _Doc) -> None:
    eligible = [row for row in protocol.plan(finals) if row["prompt_sha256"] is not None]
    assert {(row["record_id"], row["draw"], row["arm"]) for row in eligible} == {
        (record["id"], draw, "original")
        for draw in (0, 1)
        for record in finals["records"]
    }
    for draw in (0, 1):
        draw_ids = [row["record_id"] for row in eligible if row["draw"] == draw]
        assert {record_id.rsplit("--", 1)[1] for record_id in draw_ids[:2]} == {
            "visible",
            "withheld",
        }


def test_stages_keep_real_ordered_values_and_private_views(sources: _Doc) -> None:
    questions = stages.question(sources)
    rows = sources["records"][:3]
    first, second, third = questions["records"][:3]
    duplicate = next(slot for slot in range(35) if slot not in rows[0]["sender_starts"])
    ordered = (30, 2)
    question_report = evidence.from_samples(
        questions,
        [
            _sample(first, _ask(duplicate, duplicate)),
            _sample(second, _ask(*ordered)),
            _sample(third, _ask(0, 1)),
        ],
    )
    assert rows[0]["receiver_prompt"] in first["prompt"]
    assert first["prompt"].startswith("Previous task (for context only):\n")
    assert rows[0]["id"] not in first["prompt"]
    assert rows[0]["sender_view"] not in first["prompt"]
    assert protocol.grade(first, json.dumps(_ask(duplicate, duplicate))) == {
        "values": [duplicate, duplicate],
        "correct": [False, False],
    }

    answers = stages.answer(sources, question_report)
    assert answers is not None
    assert [record["id"] for record in answers["records"]] == [row["id"] for row in rows]
    for row, answer_record, slots in zip(
        rows, answers["records"], [(duplicate, duplicate), ordered, (0, 1)], strict=True
    ):
        prompt = answer_record["prompt"]
        assert prompt.startswith(row["sender_view"] + _request(*slots))
        continuation = prompt.removeprefix(row["sender_view"])
        assert "Your initial candidate list:" not in continuation
        assert "Sender's revised candidate list:" not in continuation
        assert row["receiver_prompt"] not in prompt
        assert row["id"] not in prompt

    reply = _availability("yes", "yes")
    a0, a1, _ = answers["records"]
    assert protocol.grade(a0, json.dumps(reply)) == {
        "values": ["yes", "yes"],
        "correct": [False, False],
    }
    answer_report = evidence.from_samples(
        answers,
        [
            _sample(a0, reply),
            _sample(a1, _availability("no", "yes")),
        ],
    )
    finals = stages.final(sources, question_report, answer_report)
    assert finals is not None
    assert [record["id"] for record in finals["records"]] == [
        first["id"] + "--visible",
        first["id"] + "--withheld",
        second["id"] + "--withheld",
        second["id"] + "--visible",
    ]
    f0, f1, f2, f3 = finals["records"]
    _assert_final_pair(rows[0], (duplicate, duplicate), f0, f1, "first=yes; second=yes.")
    _assert_final_pair(rows[1], ordered, f3, f2, "first=no; second=yes.")
    _assert_final_plan(finals)


def test_stages_reject_misbound_and_inconsistent_reports(sources: _Doc) -> None:
    questions = stages.question(sources)
    first = questions["records"][0]
    question_report = evidence.from_samples(questions, [_sample(first, _ask(0, 1))])
    answers = stages.answer(sources, question_report)
    assert answers is not None
    answer_report = evidence.from_samples(
        answers, [_sample(answers["records"][0], _availability("no", "no"))]
    )
    with pytest.raises(ValueError, match="does not match the pack"):
        stages.answer(sources, answer_report)
    with pytest.raises(ValueError, match="does not match the pack"):
        stages.final(sources, question_report, question_report)

    tampered = deepcopy(answer_report)
    complete = next(row for row in tampered["rows"] if row["state"] == "complete")
    verdicts = complete["outcome"]["correct"]
    verdicts[0] = not verdicts[0]
    with pytest.raises(ValueError, match="grade mismatch"):
        stages.final(sources, question_report, tampered)
