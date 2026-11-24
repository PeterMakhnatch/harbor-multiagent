import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.schedule_changes import inspect_changes

TASK = "ab" * 32
OTHER_TASK = "cd" * 32
SECRET = "SENTINEL-PRIVATE-7Q"
THU = {"day": "Thu", "start": "14:00"}
MON = {"day": "Mon", "start": "09:00"}
FRI = {"day": "Fri", "start": "10:00"}
TIMEOUT = "AgentTimeoutError"
THU_SPELLED = {"day": " thursday ", "start": 14, "note": SECRET}
SEATS = ["p1", "p2"]
LIMITS = {"max_text": 4000, "max_messages": 200}
Json = dict[str, Any]
Step = tuple[str, str, Any]


def native_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def failure(kind: str | None) -> Json | None:
    if kind is None:
        return None
    return {
        "exception_type": kind,
        "exception_message": "Agent execution timed out",
        "exception_traceback": "Traceback (most recent call last): ...",
        "occurred_at": "2026-11-19T08:48:30.000000Z",
    }


def dump(path: Path, record: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def tape(subs: list[Json], msgs: list[Json], script: list[Step]) -> list[Json]:
    start = {"condition": "talk", "seats": SEATS, "world_version": 2, "limits": LIMITS}
    events = [{"seq": 1, "t": 0.0, "seat": None, "kind": "start", "data": start}]
    for kind, seat, value in script:
        if kind == "send":
            message = msgs[int(value[1:]) - 1]
            data = {"id": value, "to": message["to"], "chars": len(message["text"])}
        elif kind == "deliver":
            data = {"ids": value}
        else:
            data = {"n": value, "answer": subs[value - 1]["answer"]}
        events.append(
            {
                "seq": len(events) + 1,
                "t": len(events) * 0.1,
                "seat": seat,
                "kind": kind,
                "data": data,
            }
        )
    return events


def trial(
    root: Path,
    number: int,
    *,
    reward: int,
    writes: list[tuple[str, Json]],
    sends: list[tuple[str, str]] | None = None,
    script: list[Step] | None = None,
    cut: int | None = None,
    task: str = TASK,
    native: int | None = None,
    errors: tuple[str | None, str | None] = (None, None),
) -> Path:
    directory = root / f"schedule-000-talk__{number}"
    subs = [
        {"n": n, "seat": seat, "answer": answer, "t": 1.0 + n}
        for n, (seat, answer) in enumerate(writes, start=1)
    ]
    msgs = [
        {
            "id": f"m{n}",
            "from": sender,
            "to": to,
            "text": f"{SECRET} body {n}",
            "t": 0.5 + n,
        }
        for n, (sender, to) in enumerate(sends or [], start=1)
    ]
    final = subs[-1] if subs else None
    dump(
        directory / "result.json",
        {
            "schema_version": 1,
            "id": native_id(native or number),
            "task_name": "flotilla/schedule-000-talk",
            "trial_name": directory.name,
            "task_checksum": task,
            "started_at": "2026-11-19T08:48:12.537840Z",
            "finished_at": "2026-11-19T08:48:35.745212Z",
            "exception_info": failure(errors[0]),
            "verifier_result": {"rewards": {"reward": float(reward)}},
            "reward_valid": True,
            "seat_results": [
                {
                    "seat": seat,
                    "under_test": True,
                    "exception_info": failure(errors[1] if seat == "p1" else None),
                    "agent_info": {
                        "name": "oracle",
                        "version": "1",
                        "model_info": None,
                    },
                }
                for seat in SEATS
            ],
        },
    )
    dump(
        directory / "verifier/score.json",
        {
            "family": "schedule",
            "condition": "talk",
            "seats": SEATS,
            "instance": 0,
            "world_version": 2,
            "fields_total": 1,
            "fields_correct": reward,
            "error": None,
            "deceived": None,
            "score": float(reward),
            "n_messages": len(msgs),
            "n_submissions": len(subs),
            "submitted_by": final["seat"] if final else None,
            "answer": final["answer"] if final else None,
        },
    )
    dump(
        directory / "verifier/world-state.json",
        {
            "condition": "talk",
            "seats": SEATS,
            "world_version": 2,
            "limits": LIMITS,
            "frozen": True,
            "sent": {seat: sum(m["from"] == seat for m in msgs) for seat in SEATS},
            "messages": msgs,
            "submissions": subs,
        },
    )
    if script is not None:
        lines = [json.dumps(e, sort_keys=True) for e in tape(subs, msgs, script)][:cut]
        events = directory / "artifacts/logs/world/events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return directory


def switch(root: Path, number: int = 1, **options: Any) -> Path:
    return trial(root, number, reward=0, writes=[("p1", THU), ("p2", MON)], **options)


def only_move(report: Json, index: int = 0) -> Json:
    row = report["trials"][index]
    assert row["status"] == "usable"
    assert len(row["transitions"]) == 1
    return row["transitions"][0]


def verdict(move: Json) -> tuple[Any, Any, str]:
    return move["before_correct"], move["after_correct"], move["change"]


def assert_private(report: Json) -> None:
    text = json.dumps(report)
    for private in (SECRET, "Thu", "Mon", "14:00", "09:00", "body"):
        assert private not in text
    assert set(re.findall(r"[0-9a-f]{64}", text)) <= {TASK, OTHER_TASK}


def test_zero_scored_switch_needs_an_independent_positive_witness(
    tmp_path: Path,
) -> None:
    team = switch(tmp_path)
    alone = inspect_changes([team])
    move = only_move(alone)
    assert (move["from_seat"], move["to_seat"], move["final_write"]) == (
        "p1",
        "p2",
        True,
    )
    assert verdict(move) == (None, False, "unknown")

    unrelated = trial(tmp_path, 2, reward=1, writes=[("p1", THU)], task=OTHER_TASK)
    assert verdict(only_move(inspect_changes([team, unrelated]))) == (
        None,
        False,
        "unknown",
    )

    witness = trial(tmp_path, 3, reward=1, writes=[("p2", THU_SPELLED)])
    report = inspect_changes([team, unrelated, witness])
    assert (report["inputs"], report["usable_trials"]) == (3, 3)
    assert verdict(only_move(report)) == (True, False, "worsened")
    assert report["summary"]["cross_seat_worsened"] == 1
    assert report["summary"]["final_cross_seat_worsened"] == 1
    assert_private(report)


def test_duplicate_native_id_cannot_witness(tmp_path: Path) -> None:
    team = switch(tmp_path)
    first = trial(tmp_path, 2, reward=1, writes=[("p1", THU)])
    copy = trial(tmp_path, 3, reward=1, writes=[("p1", THU)], native=2)
    report = inspect_changes([team, first, copy])
    assert [row["status"] for row in report["trials"]] == [
        "usable",
        "duplicate_trial",
        "duplicate_trial",
    ]
    assert report["trials"][1]["transitions"] == []
    assert verdict(only_move(report)) == (None, False, "unknown")


def test_same_miss_on_another_failed_run_proves_wrong(tmp_path: Path) -> None:
    team = switch(tmp_path)
    miss = trial(tmp_path, 2, reward=0, writes=[("p2", THU_SPELLED)])
    move = only_move(inspect_changes([team, miss]))
    assert (move["before_correct"], move["after_correct"]) == (False, False)


def test_equivalent_spelling_case_and_hour_is_unchanged(tmp_path: Path) -> None:
    team = trial(tmp_path, 1, reward=1, writes=[("p1", THU_SPELLED), ("p2", THU)])
    report = inspect_changes([team])
    move = only_move(report)
    assert move["choice_changed"] is False
    assert verdict(move) == (True, True, "unchanged")
    assert_private(report)


def test_inconsistent_positives_leave_the_group_unscored(tmp_path: Path) -> None:
    team = switch(tmp_path)
    thursday = trial(tmp_path, 2, reward=1, writes=[("p1", THU)])
    monday = trial(tmp_path, 3, reward=1, writes=[("p1", MON)])
    elsewhere = trial(tmp_path, 4, reward=1, writes=[("p1", THU)], task=OTHER_TASK)
    report = inspect_changes([team, thursday, monday, elsewhere])
    statuses = [row["status"] for row in report["trials"]]
    assert statuses == ["conflicting_reference"] * 3 + ["usable"]
    assert all(row["transitions"] == [] for row in report["trials"])
    assert report["usable_trials"] == 1


def test_no_final_write_is_a_valid_zero_without_transitions(tmp_path: Path) -> None:
    row = inspect_changes([trial(tmp_path, 1, reward=0, writes=[])])["trials"][0]
    assert row["status"] == "usable"
    assert (row["native_reward"], row["submissions"], row["transitions"]) == (0, 0, [])


def test_missing_and_unreadable_evidence_and_input_cap(tmp_path: Path) -> None:
    missing = switch(tmp_path, 1)
    (missing / "verifier/world-state.json").unlink()
    torn = switch(tmp_path, 2)
    (torn / "verifier/score.json").write_text('{"family": "sched', encoding="utf-8")
    report = inspect_changes([missing, torn, tmp_path / "absent"])
    rows = report["trials"]
    assert [row["status"] for row in rows] == [
        "missing_evidence",
        "invalid_evidence",
        "missing_evidence",
    ]
    assert all(row["native_trial"] is None and row["transitions"] == [] for row in rows)
    assert report["usable_trials"] == 0
    with pytest.raises(ValueError):
        inspect_changes([])
    with pytest.raises(ValueError):
        inspect_changes([tmp_path] * 4097)


def test_two_candidate_priors_on_one_digest_cannot_both_be_true(tmp_path: Path) -> None:
    first = switch(tmp_path, 1)
    second = trial(tmp_path, 2, reward=0, writes=[("p1", FRI), ("p2", MON)])
    joint = inspect_changes([first, second])["summary"]["bounds"]
    assert joint["cross_seat_worsened"] == [0, 1]
    other = trial(
        tmp_path, 3, reward=0, writes=[("p1", FRI), ("p2", MON)], task=OTHER_TASK
    )
    apart = inspect_changes([first, other])["summary"]["bounds"]
    assert apart["cross_seat_worsened"] == [0, 2]


def test_graded_timeout_is_held_out_unless_included(tmp_path: Path) -> None:
    team = switch(tmp_path, 1)
    timed = trial(
        tmp_path, 2, reward=1, writes=[("p1", THU)], errors=(TIMEOUT, TIMEOUT)
    )
    strict = inspect_changes([team, timed])
    held = strict["trials"][1]
    assert (held["status"], held["native_reward"], held["transitions"]) == (
        "seat_timeout",
        1,
        [],
    )
    assert verdict(only_move(strict)) == (None, False, "unknown")
    assert strict["summary"]["graded_timeouts"] == 1

    included = inspect_changes([team, timed], include_timeouts=True)
    assert [row["status"] for row in included["trials"]] == ["usable", "usable"]
    assert verdict(only_move(included)) == (True, False, "worsened")
    assert included["summary"]["graded_timeouts"] == 1
    assert_private(included)


@pytest.mark.parametrize(
    "errors",
    [
        ("RuntimeError", "RuntimeError"),
        (TIMEOUT, None),
        (None, TIMEOUT),
        (TIMEOUT, "RuntimeError"),
    ],
    ids=["other_error", "top_only", "seat_only", "seat_other_error"],
)
def test_other_or_inconsistent_errors_are_invalid(
    tmp_path: Path, errors: tuple[str | None, str | None]
) -> None:
    timed = trial(tmp_path, 1, reward=1, writes=[("p1", THU)], errors=errors)
    report = inspect_changes([timed], include_timeouts=True)
    assert report["trials"][0]["status"] == "invalid_evidence"


def test_partial_result_claiming_a_used_id_marks_both_claimants(tmp_path: Path) -> None:
    whole = switch(tmp_path, 1)
    claimant = switch(tmp_path, 2, native=1)
    (claimant / "verifier/world-state.json").unlink()
    unrelated = switch(tmp_path, 3)
    shutil.rmtree(unrelated / "verifier")
    report = inspect_changes([whole, claimant, unrelated])
    statuses = [row["status"] for row in report["trials"]]
    assert statuses == ["duplicate_trial", "duplicate_trial", "missing_evidence"]
    assert report["usable_trials"] == 0


def test_rubric_disagreement_holds_out_the_whole_row(tmp_path: Path) -> None:
    team = switch(tmp_path, 1)
    half_hour = {"day": "Thu", "start": "14:30", "note": SECRET}
    split = trial(tmp_path, 2, reward=1, writes=[("p1", half_hour), ("p2", THU)])
    report = inspect_changes([team, split])
    held = report["trials"][1]
    assert (held["status"], held["transitions"]) == ("rubric_ambiguous", [])
    assert report["summary"]["statuses"]["rubric_ambiguous"] == 1
    assert verdict(only_move(report)) == (None, False, "unknown")
    assert "14:30" not in json.dumps(report)
    assert_private(report)


@pytest.mark.parametrize("start", [23, None], ids=["hour_23", "null"])
def test_value_neither_rubric_accepts_is_an_ordinary_miss(
    tmp_path: Path, start: Any
) -> None:
    malformed = {"day": "Thu", "start": start, "note": SECRET}
    team = trial(tmp_path, 1, reward=1, writes=[("p1", malformed), ("p2", THU)])
    report = inspect_changes([team])
    assert verdict(only_move(report)) == (False, True, "improved")
    assert_private(report)


REPEATED = [
    ("send", "p1", "m1"),
    ("deliver", "p2", ["m1"]),
    ("submit", "p1", 1),
    ("deliver", "p2", ["m1"]),
    ("submit", "p2", 2),
]
FRESH = [
    ("send", "p1", "m1"),
    ("deliver", "p2", ["m1"]),
    ("submit", "p1", 1),
    ("send", "p1", "m2"),
    ("deliver", "p2", ["m1", "m2"]),
    ("submit", "p2", 2),
]
UNREAD = [("send", "p1", "m1"), ("submit", "p1", 1), ("submit", "p2", 2)]
SILENT = [("submit", "p1", 1), ("submit", "p2", 2)]


@pytest.mark.parametrize(
    ("sends", "script", "cut", "label"),
    [
        ([("p1", "all")], None, None, "unknown"),
        ([("p1", "all")], REPEATED, 5, "unknown"),
        ([("p1", "all")], REPEATED, None, "previous_return"),
        ([("p1", "all"), ("p1", "p2")], FRESH, None, "new_return"),
        ([("p1", "all")], UNREAD, None, "sent_not_returned"),
        ([], SILENT, None, "not_sent"),
    ],
    ids=[
        "no_log",
        "truncated_before_final",
        "repeated_fetch",
        "fresh_message",
        "unread",
        "silent",
    ],
)
def test_peer_return_comes_from_the_closed_log_prefix(
    tmp_path: Path,
    sends: list[tuple[str, str]],
    script: list[Step] | None,
    cut: int | None,
    label: str,
) -> None:
    team = switch(tmp_path, sends=sends, script=script, cut=cut)
    report = inspect_changes([team])
    assert only_move(report)["peer_return"] == label
    assert report["summary"]["cross_seat_by_peer_return"][label]["unknown"] == 1
    assert_private(report)
