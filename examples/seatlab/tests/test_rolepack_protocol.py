from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.rolepack import protocol


def _record(
    record_id: str = "a", task: str = "resource choice", *, scalar: bool = False
) -> dict[str, Any]:
    original = "Choose once.\r\nReturn only a JSON action — no explanation.\n"
    items = [
        {"key": "second", "valid": [0, 1, 2], "optimal": [1, 2]},
        {"key": "first", "valid": ["rest", "café"], "optimal": ["rest"]},
    ]
    answer: Any = {"second": 0, "first": "café"}
    if scalar:
        items = [{"key": "choice", "valid": [0, 1, 2], "optimal": []}]
        answer = 0
    return {
        "id": record_id,
        "task": task,
        "world": "world-0",
        "prompt": original,
        "prompt_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "peer": {
            "answer": {"choice": answer},
            "run": "prior-public-r0",
            "source_sha256": sha256(b"public report bytes\n").hexdigest(),
            "response_sha256": None,
        },
        "rubric": {"field": "choice", "scalar": scalar, "items": items},
    }


def _pack(*records: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "seatlab.rolepack/v1", "records": list(records)}


def test_duplicate_record_id_is_rejected_across_tasks() -> None:
    with pytest.raises(ValueError, match="duplicate record"):
        protocol.validate(_pack(_record(), _record(task="another task")))


def test_rounds_are_stable_and_missing_proposals_remove_both_arms() -> None:
    a = _record("a")
    b = _record("b", task="other work")
    c = _record("c", task="other work")
    c["peer"] = None
    rows = protocol.plan(_pack(a, c, b))
    assert rows == protocol.plan(_pack(b, a, c))
    assert [row["index"] for row in rows] == list(range(12))
    assert [row["sample_id"] for row in rows] == [
        "b:d0:original",
        "c:d0:peer",
        "a:d0:original",
        "b:d0:peer",
        "c:d0:original",
        "a:d0:peer",
        "b:d1:original",
        "c:d1:peer",
        "a:d1:original",
        "b:d1:peer",
        "c:d1:original",
        "a:d1:peer",
    ]
    for row in rows:
        record = {"a": a, "b": b, "c": c}[row["record_id"]]
        assert (row["task"], row["world"]) == (record["task"], record["world"])
        if row["record_id"] == "c":
            assert row["prompt_sha256"] is None
        else:
            rendered = protocol.prompt(record, row["arm"])
            assert row["prompt_sha256"] == sha256(rendered.encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="unavailable"):
        protocol.prompt(c, "peer")


def test_original_only_stage_preserves_order_without_inventing_a_peer() -> None:
    late, early = _record("z"), _record("a")
    stage = {"schema": protocol.SINGLE_SCHEMA, "records": [late, early]}
    with pytest.raises(ValueError, match="cannot register a peer"):
        protocol.plan(stage)
    late["peer"] = early["peer"] = None
    assert [
        (row["sample_id"], row["prompt_sha256"]) for row in protocol.plan(stage)
    ] == [
        (f"{record['id']}:d{draw}:original", record["prompt_sha256"])
        for draw in (0, 1)
        for record in (late, early)
    ]
    assert all(
        row["prompt_sha256"] is None for row in protocol.plan(_pack(late, early))
    )


def test_prompt_keeps_original_bytes_and_does_not_expose_changes_to_peer_score() -> (
    None
):
    before = _record()
    after = deepcopy(before)
    after["rubric"]["items"][0]["optimal"] = [0]
    after["rubric"]["items"][1]["optimal"] = ["café"]
    after["peer"].update(
        run="different-public-run", source_sha256="a" * 64, response_sha256="b" * 64
    )
    draft = protocol.prompt(before, "peer")
    answer = '{"choice":{"first":"café","second":0}}\n'
    assert protocol.prompt(before, "original") == before["prompt"]
    assert draft.startswith(before["prompt"])
    assert draft.endswith(answer)
    assert draft == protocol.prompt(after, "peer")
    assert protocol.plan(_pack(before)) == protocol.plan(_pack(after))
    assert protocol.grade(before, answer)["correct"] == [False, False]
    assert protocol.grade(after, answer)["correct"] == [True, True]
    assert protocol.digest(before) != protocol.digest(after)


def test_grade_uses_rubric_order_and_preserves_distinct_optimal_actions() -> None:
    record = _record()
    for action in (1, 2):
        reply = f'{{"choice":{{"first":"rest","second":{action}}}}}'
        assert protocol.grade(record, reply) == {
            "values": [action, "rest"],
            "correct": [True, True],
        }


def test_zero_optima_does_not_imply_an_invalid_action_or_default_success() -> None:
    record = _record(scalar=True)
    protocol.validate(_pack(record))
    for action in range(3):
        assert protocol.grade(record, f'{{"choice":{action}}}') == {
            "values": [action],
            "correct": [False],
        }


@pytest.mark.parametrize(
    "reply",
    [
        '{"choice":0,"choice":1}',
        '{"choice":true}',
        '{"choice":1.0}',
        '{"choice":null}',
        '{"choice":NaN}',
        '{"choice":3}',
        '{"choice":"1"}',
        '{"choice":1,"rationale":"not an action"}',
        '```json\n{"choice":0}\n```',
        '[{"choice":0}]',
    ],
)
def test_rejects_invalid_scalar_model_actions(reply: str) -> None:
    with pytest.raises(ValueError):
        protocol.grade(_record(scalar=True), reply)


@pytest.mark.parametrize(
    "reply",
    [
        r'{"choice":{"first":"rest","firs\u0074":"café","second":0}}',
        '{"choice":{"first":"rest"}}',
        '{"choice":{"first":"rest","second":0,"third":1}}',
    ],
)
def test_rejects_duplicate_missing_and_extra_vector_items(reply: str) -> None:
    with pytest.raises(ValueError):
        protocol.grade(_record(), reply)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid", [False, 1]),
        ("valid", [0, 0, 1]),
        ("valid", []),
        ("optimal", [1, 1]),
        ("optimal", [7]),
        ("optimal", [True]),
        ("optimal", ["1"]),
        ("key", "different-key"),
    ],
)
def test_rejects_inconsistent_rubrics(field: str, value: Any) -> None:
    record = _record(scalar=True)
    record["rubric"]["items"][0][field] = value
    with pytest.raises(ValueError):
        protocol.validate(_pack(record))


def test_rejects_extra_fields_in_a_prior_answer_even_if_the_action_is_legal() -> None:
    record = _record(scalar=True)
    record["peer"]["answer"]["prior_score"] = 0
    with pytest.raises(ValueError, match="answer fields"):
        protocol.prompt(record, "peer")


def test_source_and_response_commitments_are_distinct_fields() -> None:
    record = _record()
    record["peer"]["source_sha256"] = None
    with pytest.raises(ValueError, match="public source hash"):
        protocol.validate(_pack(record))
    record["peer"]["source_sha256"] = "a" * 64
    record["peer"]["response_sha256"] = "not a hash"
    with pytest.raises(ValueError, match="prior response hash"):
        protocol.validate(_pack(record))


def test_original_prompt_commits_to_the_exact_bytes() -> None:
    record = _record()
    record["prompt"] = record["prompt"].replace("\r\n", "\n")
    with pytest.raises(ValueError, match="original prompt hash mismatch"):
        protocol.prompt(record, "original")


def test_canonical_pack_encoding_and_strict_source_parse() -> None:
    value = {"é": 0, "z": {"b": 1, "a": "café"}}
    raw = '{"z":{"a":"café","b":1},"é":0}\n'.encode("utf-8")
    assert protocol.canonical_bytes(value) == raw
    assert protocol.digest(value) == sha256(raw).hexdigest()
    assert protocol.loads(raw) == value
    with pytest.raises(ValueError, match="duplicate JSON key"):
        protocol.loads(b'{"records":[{"id":"a","id":"b"}]}')
    with pytest.raises(ValueError, match="UTF-8"):
        protocol.loads(b'{"text":"\xff"}')


def test_rubric_item_count_is_bounded() -> None:
    record = _record()
    record["rubric"]["items"] = [
        {"key": f"item-{index}", "valid": [0], "optimal": []} for index in range(129)
    ]
    with pytest.raises(ValueError, match="rubric items count"):
        protocol.validate(_pack(record))
