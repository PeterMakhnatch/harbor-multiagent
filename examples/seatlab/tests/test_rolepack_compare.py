import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.rolepack import compare, evidence, protocol

type _Document = dict[str, Any]


def _record(
    source: str, world: str, width: int = 1, *, task: str = "routing", peer: bool = True
) -> _Document:
    prompt = f"Route the requests for {source}.\n"
    return {
        "id": source,
        "task": task,
        "world": world,
        "prompt": prompt,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "peer": {
            "answer": {"choices": {str(i): 0 for i in range(width)}},
            "run": f"prior-{source}",
            "source_sha256": "1" * 64,
            "response_sha256": None,
        }
        if peer
        else None,
        "rubric": {
            "field": "choices",
            "scalar": False,
            "items": [
                {"key": str(i), "valid": [0, 1, 2], "optimal": [1, 2]}
                for i in range(width)
            ],
        },
    }


def _sample(
    record: _Document, draw: int, arm: str, *actions: int, suffix: str = ""
) -> _Document:
    answer = {"choices": {str(i): action for i, action in enumerate(actions)}}
    return {
        "id": f"{record['id']}:d{draw}:{arm}",
        "input": protocol.prompt(record, arm),
        "output": json.dumps(answer) + suffix,
        "error": None,
    }


def test_compare_weights_draws_sources_and_unequal_worlds_separately() -> None:
    wide = _record("wide", "w1", 4)
    narrow = _record("narrow", "w1")
    solo = _record("solo", "w2")
    unavailable = _record("absent", "w3", peer=False)
    other_task = _record("other", "w1", task="scheduling")
    pack = {
        "schema": protocol.SCHEMA,
        "records": [wide, narrow, solo, unavailable, other_task],
    }
    baseline = evidence.from_samples(
        pack,
        [
            _sample(wide, 0, "original", 1, 0, 0, 0),
            _sample(wide, 1, "original", 1, 1, 0, 0),
            _sample(narrow, 0, "original", 0),
            _sample(narrow, 1, "original", 0),
            _sample(solo, 0, "original", 1),
            _sample(narrow, 0, "peer", 1),
            _sample(narrow, 1, "peer", 1),
        ],
    )
    candidate = evidence.from_samples(
        pack,
        [
            _sample(wide, 0, "original", 2, 1, 0, 0),
            _sample(narrow, 0, "original", 1),
            _sample(narrow, 1, "original", 1),
            _sample(solo, 0, "original", 0),
            _sample(solo, 1, "original", 0),
            _sample(narrow, 0, "peer", 1, suffix="\n"),
            _sample(narrow, 1, "peer", 1),
        ],
    )
    result = compare.compare(pack, baseline, candidate)
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    original = result["tasks"]["routing"]["by_arm"]["original"]
    assert original["observed_equal_world_candidate_minus_baseline"] == -0.1875
    assert original["equal_world_candidate_minus_baseline_bounds"] == [
        -0.28125,
        0.09375,
    ]
    assert original["matched_worlds"] == original["eligible_worlds"] == 2
    assert original["registered_worlds"] == 3
    assert original["registered_source_draws"] == 8
    assert original["eligible_source_draws"] == 6
    assert original["unavailable_source_draws"] == 2
    assert original["matched_source_draws"] == 4
    assert original["baseline_only_source_draws"] == 1
    assert original["candidate_only_source_draws"] == 1
    assert original["matched_actions_per_side"] == 7
    assert original["baseline"]["states"]["complete"] == 5
    assert original["candidate"]["states"]["complete"] == 5
    assert original["baseline"]["scored_actions"] == 11
    assert original["candidate"]["scored_actions"] == 8
    assert original["baseline"]["matched_optimal_actions"] == 2
    assert original["candidate"]["matched_optimal_actions"] == 4
    assert original["disagreements"] == 5
    assert original["right_to_wrong"] == 1
    assert original["wrong_to_right"] == 3
    assert original["both_right"] == 1
    assert original["both_wrong"] == 2
    w1, w2, w3 = (original["by_world"][world] for world in ("w1", "w2", "w3"))
    assert w1["candidate_minus_baseline_bounds"] == [0.4375, 0.6875]
    assert w2["candidate_minus_baseline_bounds"] == [-1.0, -0.5]
    assert w3["candidate_minus_baseline_bounds"] is None
    peer = result["tasks"]["routing"]["by_arm"]["peer"]
    assert peer["whole_response_agreement"] == {
        "compared_responses": 2,
        "exact_matches": 1,
        "action_vector_matches": 2,
    }
    assert peer["by_world"]["w2"]["candidate_minus_baseline_bounds"] == [-1.0, 1.0]
    assert peer["matched_worlds"] == 1
    assert peer["eligible_worlds"] == 2
    assert peer["equal_world_candidate_minus_baseline_bounds"] == [-0.75, 0.75]
    other = result["tasks"]["scheduling"]["by_arm"]["original"]
    assert other["matched_source_draws"] == 0
    assert other["observed_equal_world_candidate_minus_baseline"] is None
    assert other["equal_world_candidate_minus_baseline_bounds"] == [-1.0, 1.0]


def test_distinct_optimal_responses_disagree_without_a_score_change() -> None:
    case = _record("one", "w1")
    pack = {"schema": protocol.SCHEMA, "records": [case]}
    baseline = evidence.from_samples(pack, [_sample(case, 0, "original", 1)])
    candidate = evidence.from_samples(pack, [_sample(case, 0, "original", 2)])
    result = compare.compare(pack, baseline, candidate)
    arm = result["tasks"]["routing"]["by_arm"]["original"]
    assert arm["disagreements"] == arm["both_right"] == 1
    assert arm["whole_response_agreement"]["exact_matches"] == 0
    assert arm["whole_response_agreement"]["action_vector_matches"] == 0
    assert arm["right_to_wrong"] == arm["wrong_to_right"] == 0
    assert arm["observed_equal_world_candidate_minus_baseline"] == 0.0


def test_compare_verifies_both_reports_against_the_pack() -> None:
    case = _record("one", "w1")
    pack = {"schema": protocol.SCHEMA, "records": [case]}
    report = evidence.from_samples(pack, [_sample(case, 0, "original", 1)])
    forged = deepcopy(report)
    completed = next(row for row in forged["rows"] if row["state"] == "complete")
    completed["outcome"]["correct"] = [False]
    for baseline, candidate in ((forged, report), (report, forged)):
        with pytest.raises(ValueError, match="grade mismatch"):
            compare.compare(pack, baseline, candidate)
