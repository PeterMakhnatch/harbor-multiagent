import json
from copy import deepcopy
from hashlib import sha256

import pytest

from examples.seatlab.calendar_questions import analysis, source, stages
from examples.seatlab.rolepack import evidence, protocol

type _Document = protocol.Document


def _view(free_monday: range) -> str:
    hours = range(9, 17)
    monday = ", ".join(f"{hour:02d}:00" for hour in hours if hour not in free_monday)
    closed = ", ".join(f"{hour:02d}:00" for hour in hours)
    group = "\n".join(
        [
            f"Mon: {monday}",
            *(f"{day}: {closed}" for day in ("Tue", "Wed", "Thu", "Fri")),
        ]
    )
    empty = "\n".join(f"{day}: -" for day in ("Mon", "Tue", "Wed", "Thu", "Fri"))
    return (
        "Schedule one 2-hour meeting for all attendees at both seats.\n"
        "Unlisted cells are free; '-' means no busy cells.\n"
        f"{group}\n\n{empty}\n\n{empty}\n\nTask:\n"
    )


@pytest.fixture
def src() -> _Document:
    views = (_view(range(9, 12)), _view(range(10, 13)))
    starts = ([0, 1], [1, 2])
    rows = []
    for world in ("w00", "w04", "w08"):
        for direction in (0, 1):
            sender = views[direction]
            receiver = (
                views[1 - direction]
                + 'Your initial candidate list: {"starts":[1]}\n'
                + 'Sender\'s revised candidate list: {"starts":[1]}\n'
                + 'Return only a JSON object with a single "slot" key.\n'
            )
            for repetition in (0, 1):
                rows.append(
                    {
                        "id": f"cal-rx-{world}-d{direction}-r{repetition}-blind",
                        "world": world,
                        "receiver_prompt": receiver,
                        "receiver_sha256": sha256(receiver.encode()).hexdigest(),
                        "sender_view": sender,
                        "sender_sha256": sha256(sender.encode()).hexdigest(),
                        "receiver_starts": starts[1 - direction].copy(),
                        "sender_starts": starts[direction].copy(),
                        "optimum": 1,
                    }
                )
    return source.validate({"schema": source.SCHEMA, "records": rows})


def _sample(
    pack: _Document, record_id: str, draw: int, payload: _Document | str
) -> _Document:
    record = next(record for record in pack["records"] if record["id"] == record_id)
    return {
        "id": f"{record_id}:d{draw}:original",
        "input": protocol.prompt(record, "original"),
        "output": json.dumps(payload) if isinstance(payload, dict) else payload,
        "error": None,
    }


def _ask(first: int, second: int) -> _Document:
    return {"ask": {"first": first, "second": second}}


def _availability(first: str, second: str) -> _Document:
    return {"availability": {"first": first, "second": second}}


@pytest.fixture
def reports(src: _Document) -> tuple[_Document, _Document, _Document]:
    a, b, c, d, e, f, g, _, h, *_ = (row["id"] for row in src["records"])
    question_pack = stages.question(src)
    question = evidence.from_samples(
        question_pack,
        [
            _sample(question_pack, a, 0, _ask(1, 1)),
            _sample(question_pack, b, 0, _ask(2, 3)),
            _sample(question_pack, c, 0, _ask(3, 4)),
            _sample(question_pack, d, 0, "unparsed"),
            _sample(question_pack, d, 1, _ask(1, 1)),
            _sample(question_pack, e, 0, _ask(1, 2)),
            _sample(question_pack, f, 0, _ask(1, 1)),
            _sample(question_pack, g, 1, _ask(1, 1)),
            _sample(question_pack, h, 0, _ask(3, 3)),
        ],
    )
    answer_pack = stages.answer(src, question)
    assert answer_pack is not None
    answer = evidence.from_samples(
        answer_pack,
        [
            _sample(answer_pack, a, 0, _availability("yes", "no")),
            _sample(answer_pack, b, 0, _availability("yes", "no")),
            _sample(answer_pack, c, 1, _availability("no", "no")),
            _sample(answer_pack, e, 0, _availability("yes", "no")),
            _sample(answer_pack, f, 0, _availability("yes", "yes")),
            _sample(answer_pack, h, 0, _availability("no", "no")),
        ],
    )
    final_pack = stages.final(src, question, answer)
    assert final_pack is not None
    final = evidence.from_samples(
        final_pack,
        [
            _sample(final_pack, f"{a}--visible", 0, {"slot": 1}),
            _sample(final_pack, f"{a}--withheld", 0, {"slot": 0}),
            _sample(final_pack, f"{a}--visible", 1, {"slot": 0}),
            _sample(final_pack, f"{a}--withheld", 1, {"slot": 1}),
            _sample(final_pack, f"{b}--visible", 0, {"slot": 1}),
            _sample(final_pack, f"{b}--withheld", 0, {"slot": 0}),
            _sample(final_pack, f"{b}--visible", 1, {"slot": 35}),
            _sample(final_pack, f"{e}--visible", 0, {"slot": 0}),
            _sample(final_pack, f"{e}--withheld", 0, {"slot": 1}),
            _sample(final_pack, f"{e}--visible", 1, {"slot": 1}),
        ],
    )
    return question, answer, final


def test_summary_counts_occurrences_and_bounds_all_planned_draws(
    src: _Document, reports: tuple[_Document, _Document, _Document]
) -> None:
    result = analysis.summarize(src, *reports)
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    overall = result["overall"]
    assert overall["registered_sources"] == 12
    assert overall["question"] == {
        "completed_sources": 6,
        "sources_with_locally_valid_query": 4,
        "sources_with_joint_slot_queried": 3,
        "unique_queries": 9,
    }
    assert overall["answer"] == {
        "answered_sources": 5,
        "eligible_query_occurrences": 12,
        "eligible_yes_occurrences": 5,
        "eligible_no_occurrences": 7,
        "answered_query_occurrences": 10,
        "matched_query_occurrences": 8,
        "matched_yes": 4,
        "matched_no": 4,
        "false_positive": 1,
        "false_negative": 1,
    }
    visible, withheld = (overall["final"][arm] for arm in ("visible", "withheld"))
    for counts in (visible, withheld):
        assert counts["registered_source_draws"] == 24
        assert counts["eligible_source_draws"] == 10
    assert visible["attempted_source_draws"] == 6
    assert withheld["attempted_source_draws"] == 4
    assert (visible["valid_source_draws"], withheld["valid_source_draws"]) == (5, 4)
    assert (visible["optimal_source_draws"], withheld["optimal_source_draws"]) == (3, 2)
    assert visible["matched_optimal_source_draws"] == 2
    assert withheld["matched_optimal_source_draws"] == 2
    assert visible["errors_after_inaccurate_sender_status"] == 1
    assert withheld["errors_after_inaccurate_sender_status"] == 2
    assert withheld["errors_after_joint_slot_not_queried"] == 1
    assert withheld["errors_after_either_prefix_issue"] == 2
    assert overall["paired"]["valid_matched_source_draws"] == 4
    assert overall["paired"]["correct_to_incorrect"] == 2
    assert overall["paired"]["incorrect_to_correct"] == 2
    assert overall["paired"]["decision_changes"] == 4
    assert overall["worlds_with_valid_matched_draws"] == 2
    assert overall["observed_equal_world_visible_minus_withheld"] == -0.25
    assert overall["equal_world_visible_minus_withheld_bounds"] == pytest.approx(
        [-19 / 24, 5 / 6]
    )
    worlds = result["by_world"]
    assert [world["registered_sources"] for world in worlds.values()] == [4, 4, 4]
    assert worlds["w00"]["observed_visible_minus_withheld"] == 0.5
    assert worlds["w04"]["visible_minus_withheld_bounds"] == [-0.875, 0.75]
    assert worlds["w08"]["observed_visible_minus_withheld"] is None
    assert worlds["w08"]["visible_minus_withheld_bounds"] == [-1, 1]


def test_summary_keeps_the_board_when_whole_stages_are_missing(
    src: _Document, reports: tuple[_Document, _Document, _Document]
) -> None:
    question, answer, _ = reports
    for prefix, expected in (
        ((None, None), (0, 0, 0)),
        ((question, None), (6, 0, 0)),
        ((question, answer), (6, 5, 10)),
    ):
        overall = analysis.summarize(src, *prefix, None)["overall"]
        assert overall["registered_sources"] == 12
        assert overall["question"]["completed_sources"] == expected[0]
        assert overall["answer"]["answered_sources"] == expected[1]
        for counts in overall["final"].values():
            assert counts["registered_source_draws"] == 24
            assert counts["eligible_source_draws"] == expected[2]
            assert counts["attempted_source_draws"] == 0
        assert overall["observed_equal_world_visible_minus_withheld"] is None
        assert overall["equal_world_visible_minus_withheld_bounds"] == [-1, 1]


def test_summary_rejects_forged_grades_and_out_of_lineage_reports(
    src: _Document, reports: tuple[_Document, _Document, _Document]
) -> None:
    for stage in range(3):
        tampered = list(deepcopy(reports))
        outcome = next(
            row["outcome"]
            for row in tampered[stage]["rows"]
            if row["state"] == "complete"
        )
        outcome["correct"][0] = not outcome["correct"][0]
        with pytest.raises(ValueError, match="grade mismatch"):
            analysis.summarize(src, tampered[0], tampered[1], tampered[2])

    question, answer, final = reports
    with pytest.raises(ValueError, match="does not match the pack"):
        analysis.summarize(src, question, final, None)
    with pytest.raises(ValueError, match="no eligible stage pack"):
        analysis.summarize(src, None, answer, None)
    with pytest.raises(ValueError, match="no eligible stage pack"):
        analysis.summarize(src, question, None, final)
