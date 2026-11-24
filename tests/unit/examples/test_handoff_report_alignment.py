import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.trial import read_trial

RUN = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
NATIVE = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
V1 = "harbor.revised-dispatch.decision/v1"
V2 = "harbor.revised-dispatch.decision/v2"
ACCURACY = "revision_correct"
ALIGNMENT = "commit_matches_acknowledged_revision"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
Json = dict[str, Any]
Steps = tuple[tuple[str, ...], ...]
INITIAL: Steps = (
    ("sent", "initial"),
    ("returned", "initial"),
    ("acknowledged", "initial"),
)
SENT, RETURNED, ACKED, COMMIT = (
    ("sent", "revision"),
    ("returned", "revision"),
    ("acknowledged", "revision"),
    ("commit",),
)
PATHS: dict[str, Steps] = {
    "timely_ack": INITIAL + (SENT, RETURNED, ACKED, COMMIT),
    "absent_revision": INITIAL + (COMMIT,),
    "late_ack": INITIAL + (SENT, RETURNED, COMMIT, ACKED),
    "returned_only": INITIAL + (SENT, RETURNED, COMMIT),
    "unreturned": INITIAL + (SENT, COMMIT),
    "no_commit": INITIAL + (SENT, RETURNED, ACKED),
}


def frame(record: Json) -> str:
    return json.dumps(record, separators=(",", ":")) + "\n"


def sealed_journal(steps: Steps, reward: int) -> str:
    start = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": RUN,
        "edges": EDGES,
        "acknowledgements": True,
    }
    lines = [frame(start)]
    for seq, (kind, *message) in enumerate(steps, start=1):
        record: Json = {"seq": seq, "type": kind}
        if kind == "sent":
            record |= {"message": message[0], "sender": "reporter"}
        elif kind == "commit":
            record |= {"receiver": "booker"}
        else:
            record |= {"message": message[0], "receiver": "booker"}
        lines.append(frame(record))
    lines.append(frame({"seq": len(lines), "type": "closed", "reward": reward}))
    return "".join(lines)


def verdict(steps: Steps, accuracy: object, correct: object, alignment: object) -> Json:
    counts = Counter(kind for kind, *_ in steps)
    return {
        "schema": V2,
        "run": RUN,
        "draft_changed": False if counts["commit"] else None,
        "decision_correct": correct,
        ACCURACY: accuracy,
        ALIGNMENT: alignment,
        "event_counts": {
            kind: counts[kind]
            for kind in ("sent", "returned", "acknowledged", "commit")
        },
    }


def write_trial(root: Path, steps: Steps, record: Json, graded: bool) -> Path:
    reward = int(record["decision_correct"] is True)
    (root / "verifier").mkdir(parents=True)
    (root / "verifier" / "handoffs.jsonl").write_text(sealed_journal(steps, reward))
    (root / "verifier" / "decision.json").write_text(json.dumps(record) + "\n")
    if graded:
        native = {
            "id": NATIVE,
            "reward_valid": True,
            "verifier_result": {"rewards": {"reward": reward}},
        }
        (root / "result.json").write_text(json.dumps(native))
    return root


ACCEPTED = {
    "matching_inaccurate_report_is_incorrect": ("timely_ack", False, False, True),
    "mismatching_inaccurate_report_can_be_correct": ("timely_ack", False, True, False),
    "both_incorrect_mismatch": ("timely_ack", False, False, False),
    "matching_accurate_report_is_correct": ("timely_ack", True, True, True),
    "absent_revision": ("absent_revision", None, True, None),
    "late_ack_both_correct": ("late_ack", True, True, None),
    "returned_only": ("returned_only", True, False, None),
    "unreturned": ("unreturned", False, False, None),
    "no_commit": ("no_commit", True, None, None),
}


@pytest.mark.parametrize("graded", [True, False], ids=["graded", "ungraded"])
@pytest.mark.parametrize("boundary", ["returned", "acknowledged"])
@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_v2_alignment_known_only_for_revision_acknowledged_before_commit(
    tmp_path: Path, case: str, boundary: str, graded: bool
) -> None:
    path, accuracy, correct, alignment = ACCEPTED[case]
    record = verdict(PATHS[path], accuracy, correct, alignment)
    findings, link = read_trial(
        write_trial(tmp_path / "t", PATHS[path], record, graded), boundary=boundary
    )
    assert link["decision"] == {
        "draft_changed": record["draft_changed"],
        "decision_correct": correct,
        ACCURACY: accuracy,
        ALIGNMENT: alignment,
    }
    assert (accuracy is None) == (SENT not in PATHS[path])
    assert link["native_grade_status"] == ("valid" if graded else "unavailable")
    assert {row.reward for row in findings} == {
        float(correct is True) if graded else None
    }


@pytest.mark.parametrize("graded", [True, False], ids=["graded", "ungraded"])
def test_genuine_v1_decision_keeps_only_old_keys(tmp_path: Path, graded: bool) -> None:
    record = verdict(PATHS["timely_ack"], True, True, True)
    del record[ACCURACY], record[ALIGNMENT]
    record["schema"] = V1
    _, link = read_trial(
        write_trial(tmp_path / "t", PATHS["timely_ack"], record, graded),
        boundary="returned",
    )
    assert link["decision"] == {"draft_changed": False, "decision_correct": True}


TIMELY = verdict(PATHS["timely_ack"], False, False, True)
REJECTED: dict[str, tuple[str, Json]] = {
    name: (path, verdict(PATHS[path], *fields))
    for name, (path, *fields) in {
        "alignment_known_after_late_ack": ("late_ack", False, True, False),
        "alignment_known_without_ack": ("returned_only", False, False, True),
        "alignment_known_without_commit": ("no_commit", False, None, False),
        "alignment_null_with_timely_ack": ("timely_ack", False, False, None),
        "accuracy_known_without_revision": ("absent_revision", False, True, None),
        "accuracy_null_with_revision": ("returned_only", None, True, None),
        "numeric_accuracy": ("timely_ack", 0, False, True),
        "numeric_alignment": ("timely_ack", False, False, 1),
        "false_alignment_both_correct": ("timely_ack", True, True, False),
        "true_alignment_correct_report_wrong_choice": ("timely_ack", True, False, True),
        "true_alignment_wrong_report_right_choice": ("timely_ack", False, True, True),
    }.items()
} | {
    "v1_carrying_v2_keys": ("timely_ack", TIMELY | {"schema": V1}),
    "v2_missing_alignment": (
        "timely_ack",
        {key: value for key, value in TIMELY.items() if key != ALIGNMENT},
    ),
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_inconsistent_v2_decision_is_rejected(tmp_path: Path, name: str) -> None:
    path, record = REJECTED[name]
    trial = write_trial(tmp_path / "t", PATHS[path], record, graded=False)
    with pytest.raises(ValueError):
        read_trial(trial, boundary="returned")
