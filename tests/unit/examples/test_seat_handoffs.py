import dataclasses
import json
import random
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs import core

type Raw = dict[str, Any]
type Edge = tuple[str, str]
type Edges = tuple[Edge, ...]
type Step = tuple[str, ...]
type Label = tuple[str, str, str]
type Table = dict[Edge, Label]
type Case = tuple[Edges, list[Step], object, Table]
type AckCase = tuple[bool, list[Step], object, Table]

RUN = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
SENDER = "reporter"
TASK_EDGES: Edges = (("initial", "booker"), ("revision", "booker"))
FANOUT_EDGES: Edges = (
    ("initial", "booker"),
    ("initial", "auditor"),
    ("revision", "booker"),
)
OPEN = object()


def frame(payload: Raw) -> str:
    return json.dumps(payload, separators=(",", ":"))


def header(edges: Edges, run: str = RUN, acknowledgements: bool = False) -> Raw:
    record: Raw = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": run,
    }
    if acknowledgements:
        record["acknowledgements"] = True
    record["edges"] = [
        {"message": message, "sender": SENDER, "receiver": receiver}
        for message, receiver in edges
    ]
    return record


def event(seq: int, step: Step) -> Raw:
    kind = step[0]
    if kind == "sent":
        return {"seq": seq, "type": "sent", "message": step[1], "sender": SENDER}
    if kind == "returned":
        return {"seq": seq, "type": "returned", "message": step[1], "receiver": step[2]}
    if kind == "commit":
        return {"seq": seq, "type": "commit", "receiver": step[1]}
    if kind == "acknowledged":
        return {
            "seq": seq,
            "type": "acknowledged",
            "message": step[1],
            "receiver": step[2],
        }
    raise ValueError(f"unknown step {kind}")


def close_frame(seq: int, reward: object) -> Raw:
    return {"seq": seq, "type": "closed", "reward": reward}


def write_log(
    directory: Path,
    edges: Edges,
    steps: list[Step],
    reward: object = OPEN,
    tail: str = "",
    run: str = RUN,
    acknowledgements: bool = False,
) -> Path:
    if not isinstance(directory, Path):
        raise TypeError("directory must be a Path")
    frames = [frame(header(edges, run, acknowledgements))]
    frames += [frame(event(seq, step)) for seq, step in enumerate(steps, start=1)]
    if reward is not OPEN:
        frames.append(frame(close_frame(len(frames), reward)))
    path = directory / "handoffs.jsonl"
    path.write_text("".join(line + "\n" for line in frames) + tail)
    return path


def classify(edges: Edges, steps: list[Step], closed: bool) -> Table:
    expected: Table = {}
    for message, receiver in edges:
        returned = (
            steps.index(("returned", message, receiver))
            if ("returned", message, receiver) in steps
            else None
        )
        commit = (
            steps.index(("commit", receiver)) if ("commit", receiver) in steps else None
        )
        if returned is not None and commit is not None:
            expected[message, receiver] = (
                "confirmed",
                "before" if returned < commit else "after",
                "recorded",
            )
        elif returned is not None:
            expected[message, receiver] = (
                ("confirmed", "no_commit", "not_recorded")
                if closed
                else ("confirmed", "unknown", "unknown")
            )
        elif commit is not None:
            expected[message, receiver] = (
                "absent" if closed else "unknown",
                "unknown",
                "recorded",
            )
        else:
            expected[message, receiver] = (
                ("absent", "unknown", "not_recorded")
                if closed
                else ("unknown", "unknown", "unknown")
            )
    return expected


def observed(rows: list[core.Finding]) -> Table:
    if not all(isinstance(row, core.Finding) for row in rows):
        raise TypeError("inspect must return Finding rows")
    table = {
        (row.message, row.receiver): (row.receipt, row.timing, row.commit)
        for row in rows
    }
    if len(table) != len(rows):
        raise AssertionError("inspect emitted a repeated message/receiver edge")
    return table


SENT: list[Step] = [
    ("sent", "initial"),
    ("returned", "initial", "booker"),
    ("sent", "revision"),
]

CASES: dict[str, Case] = {
    "revision_presented_before_commit": (
        TASK_EDGES,
        SENT + [("returned", "revision", "booker"), ("commit", "booker")],
        1,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("confirmed", "before", "recorded"),
        },
    ),
    "revision_presented_after_commit": (
        TASK_EDGES,
        SENT + [("commit", "booker"), ("returned", "revision", "booker")],
        0,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("confirmed", "after", "recorded"),
        },
    ),
    "complete_log_revision_never_presented": (
        TASK_EDGES,
        SENT + [("commit", "booker")],
        0,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("absent", "unknown", "recorded"),
        },
    ),
    "complete_log_missing_commit": (
        TASK_EDGES,
        SENT + [("returned", "revision", "booker")],
        None,
        {
            ("initial", "booker"): ("confirmed", "no_commit", "not_recorded"),
            ("revision", "booker"): ("confirmed", "no_commit", "not_recorded"),
        },
    ),
    "active_prefix_missing_commit_is_unknown": (
        TASK_EDGES,
        SENT + [("returned", "revision", "booker")],
        OPEN,
        {
            ("initial", "booker"): ("confirmed", "unknown", "unknown"),
            ("revision", "booker"): ("confirmed", "unknown", "unknown"),
        },
    ),
    "incoming_partial_before_revision": (
        TASK_EDGES,
        [("sent", "initial")],
        OPEN,
        {
            ("initial", "booker"): ("unknown", "unknown", "unknown"),
            ("revision", "booker"): ("unknown", "unknown", "unknown"),
        },
    ),
    "incoming_partial_commit_before_revision": (
        TASK_EDGES,
        SENT + [("commit", "booker")],
        OPEN,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("unknown", "unknown", "recorded"),
        },
    ),
    "header_only_prefix": (
        TASK_EDGES,
        [],
        OPEN,
        {
            ("initial", "booker"): ("unknown", "unknown", "unknown"),
            ("revision", "booker"): ("unknown", "unknown", "unknown"),
        },
    ),
    "grade_without_delivery": (
        TASK_EDGES,
        [("sent", "initial"), ("sent", "revision"), ("commit", "booker")],
        1,
        {
            ("initial", "booker"): ("absent", "unknown", "recorded"),
            ("revision", "booker"): ("absent", "unknown", "recorded"),
        },
    ),
    "fanout_presented_to_other_receiver_only": (
        FANOUT_EDGES,
        [
            ("sent", "initial"),
            ("returned", "initial", "auditor"),
            ("sent", "revision"),
            ("returned", "revision", "booker"),
            ("commit", "booker"),
        ],
        0,
        {
            ("initial", "booker"): ("absent", "unknown", "recorded"),
            ("initial", "auditor"): ("confirmed", "no_commit", "not_recorded"),
            ("revision", "booker"): ("confirmed", "before", "recorded"),
        },
    ),
    "everything_presented_after_commit": (
        TASK_EDGES,
        [
            ("sent", "initial"),
            ("sent", "revision"),
            ("commit", "booker"),
            ("returned", "initial", "booker"),
            ("returned", "revision", "booker"),
        ],
        1,
        {
            ("initial", "booker"): ("confirmed", "after", "recorded"),
            ("revision", "booker"): ("confirmed", "after", "recorded"),
        },
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_inspect_classifies_handoff_edges(tmp_path: Path, name: str) -> None:
    edges, steps, reward, expected = CASES[name]
    rows = core.inspect(write_log(tmp_path, edges, steps, reward))

    assert observed(rows) == expected
    assert expected == classify(edges, steps, reward is not OPEN)
    assert {row.sender for row in rows} == {SENDER}
    assert {row.reward for row in rows} == {None if reward is OPEN else reward}
    assert {row.trial for row in rows} == {RUN}
    assert {row.basis for row in rows} == {
        "world_return" if reward is OPEN else "sealed_world"
    }


def test_grade_is_not_evidence_of_receipt(tmp_path: Path) -> None:
    rows = core.inspect(write_log(tmp_path, TASK_EDGES, [("sent", "initial")], 1))

    assert {row.receipt for row in rows} == {"absent"}
    assert {row.reward for row in rows} == {1}
    assert {row.basis for row in rows} == {"sealed_world"}


@pytest.mark.parametrize(
    "tail",
    [
        '{"seq":4,"type":"comm',
        frame({"seq": 4, "type": "closed", "reward": 1}),
        frame(
            {"seq": 4, "type": "returned", "message": "revision", "receiver": "booker"}
        ),
    ],
)
def test_final_frame_without_newline_is_not_evidence(tmp_path: Path, tail: str) -> None:
    rows = core.inspect(write_log(tmp_path, TASK_EDGES, SENT, tail=tail))

    assert observed(rows) == {
        ("initial", "booker"): ("confirmed", "unknown", "unknown"),
        ("revision", "booker"): ("unknown", "unknown", "unknown"),
    }
    assert {row.reward for row in rows} == {None}
    assert {row.basis for row in rows} == {"world_return"}


@pytest.mark.parametrize(
    "tail",
    [
        '{"seq":5,"type":"comm',
        frame({"seq": 5, "type": "commit", "receiver": "booker"}),
    ],
)
def test_bytes_after_closed_frame_are_rejected(tmp_path: Path, tail: str) -> None:
    with pytest.raises(ValueError):
        core.inspect(write_log(tmp_path, TASK_EDGES, SENT, 0, tail=tail))


def test_missing_or_headerless_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        core.inspect(tmp_path / "handoffs.jsonl")
    (tmp_path / "handoffs.jsonl").write_text("")
    with pytest.raises(ValueError):
        core.inspect(tmp_path / "handoffs.jsonl")
    (tmp_path / "handoffs.jsonl").write_text(frame(header(TASK_EDGES)))
    with pytest.raises(ValueError):
        core.inspect(tmp_path / "handoffs.jsonl")


ACKED_INITIAL: list[Step] = SENT[:2] + [
    ("acknowledged", "initial", "booker"),
    SENT[2],
    ("returned", "revision", "booker"),
]
RETURNED_BEFORE_COMMIT: Table = {
    ("initial", "booker"): ("confirmed", "before", "recorded"),
    ("revision", "booker"): ("confirmed", "before", "recorded"),
}

ACK_CASES: dict[str, AckCase] = {
    "acknowledged_after_commit": (
        True,
        ACKED_INITIAL + [("commit", "booker"), ("acknowledged", "revision", "booker")],
        1,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("confirmed", "after", "recorded"),
        },
    ),
    "closed_returned_never_acknowledged": (
        True,
        ACKED_INITIAL + [("commit", "booker")],
        0,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("absent", "unknown", "recorded"),
        },
    ),
    "open_returned_not_yet_acknowledged": (
        True,
        ACKED_INITIAL + [("commit", "booker")],
        OPEN,
        {
            ("initial", "booker"): ("confirmed", "before", "recorded"),
            ("revision", "booker"): ("unknown", "unknown", "recorded"),
        },
    ),
    "legacy_log_without_acknowledgements": (
        False,
        SENT + [("returned", "revision", "booker"), ("commit", "booker")],
        0,
        {
            ("initial", "booker"): ("unknown", "unknown", "recorded"),
            ("revision", "booker"): ("unknown", "unknown", "recorded"),
        },
    ),
}


@pytest.mark.parametrize("name", sorted(ACK_CASES))
def test_acknowledged_boundary_is_separate_from_return(
    tmp_path: Path, name: str
) -> None:
    acknowledgements, steps, reward, expected = ACK_CASES[name]
    path = write_log(
        tmp_path, TASK_EDGES, steps, reward, acknowledgements=acknowledgements
    )

    returned = core.inspect(path)
    acknowledged = core.inspect(path, boundary="acknowledged")

    assert observed(returned) == RETURNED_BEFORE_COMMIT
    assert observed(acknowledged) == expected
    assert all(row.basis != "receiver_ack" for row in returned)
    for row in acknowledged:
        if not acknowledgements:
            assert (row.receipt, row.basis) == ("unknown", "none")
        elif row.receipt != "unknown":
            assert row.basis == "receiver_ack"
    missing_ack = {
        (row.message, row.receiver)
        for row in acknowledged
        if row.receipt != "confirmed"
    }
    assert all(
        row.receipt == "confirmed"
        for row in returned
        if (row.message, row.receiver) in missing_ack
    )
    assert [row.commit for row in acknowledged] == [row.commit for row in returned]
    assert {row.reward for row in acknowledged} == {row.reward for row in returned}
    assert {row.reward for row in acknowledged} == {None if reward is OPEN else reward}


@pytest.mark.parametrize(
    ("acknowledgements", "steps"),
    [
        (True, [SENT[0], ("acknowledged", "initial", "booker"), SENT[1]]),
        (
            True,
            SENT
            + [
                ("acknowledged", "initial", "booker"),
                ("acknowledged", "initial", "booker"),
            ],
        ),
        (True, SENT + [("acknowledged", "initial", "auditor")]),
        (False, SENT + [("acknowledged", "initial", "booker")]),
    ],
    ids=["before_return", "duplicate", "undeclared_receiver", "undeclared_recording"],
)
def test_invalid_acknowledgements_are_rejected(
    tmp_path: Path, acknowledgements: bool, steps: list[Step]
) -> None:
    path = write_log(tmp_path, TASK_EDGES, steps, 0, acknowledgements=acknowledgements)

    with pytest.raises(ValueError):
        core.inspect(path)
    with pytest.raises(ValueError):
        core.inspect(path, boundary="acknowledged")


def test_unknown_boundary_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        core.inspect(write_log(tmp_path, TASK_EDGES, SENT, 0), boundary="understood")


def test_explicit_false_acknowledgement_header_is_rejected(tmp_path: Path) -> None:
    path = write_log(tmp_path, TASK_EDGES, SENT, 0, acknowledgements=True)
    path.write_text(
        path.read_text().replace('"acknowledgements":true', '"acknowledgements":false')
    )

    with pytest.raises(ValueError):
        core.inspect(path)


def corrupt(lines: list[str]) -> list[str]:
    return lines[:2] + ['{"seq":2,"type":"retur'] + lines[3:]


def gap(lines: list[str]) -> list[str]:
    return lines[:2] + lines[3:]


def reversed_send(lines: list[str]) -> list[str]:
    return [lines[0], frame(event(1, SENT[1])), frame(event(2, SENT[0]))] + lines[3:]


def reversed_seq(lines: list[str]) -> list[str]:
    return [lines[0], lines[2], lines[1]] + lines[3:]


def before_close(lines: list[str], steps: list[Step]) -> list[str]:
    last = json.loads(lines[-1])
    if last["type"] != "closed" or last["seq"] != len(lines) - 1:
        raise ValueError("mutation expects a closed log")
    body = lines[:-1] + [
        frame(event(seq, step)) for seq, step in enumerate(steps, start=len(lines) - 1)
    ]
    return body + [frame(close_frame(len(body), last["reward"]))]


def duplicate_return(lines: list[str]) -> list[str]:
    return before_close(lines, [SENT[1]])


def duplicate_send(lines: list[str]) -> list[str]:
    return before_close(lines, [SENT[0]])


def duplicate_commit(lines: list[str]) -> list[str]:
    return before_close(lines, [("commit", "booker"), ("commit", "booker")])


def trailing_after_close(lines: list[str]) -> list[str]:
    return lines + [frame(event(len(lines), ("commit", "booker")))]


def wrong_receiver(lines: list[str]) -> list[str]:
    return lines[:2] + [frame(event(2, ("returned", "initial", "auditor")))] + lines[3:]


def wrong_sender(lines: list[str]) -> list[str]:
    return [
        lines[0],
        frame({"seq": 1, "type": "sent", "message": "initial", "sender": "booker"}),
    ] + lines[2:]


def undeclared_message(lines: list[str]) -> list[str]:
    return [
        lines[0],
        frame({"seq": 1, "type": "sent", "message": "bonus", "sender": SENDER}),
    ] + lines[2:]


def extra_field(lines: list[str]) -> list[str]:
    payload = json.loads(lines[2])
    payload["slot"] = "A"
    return lines[:2] + [frame(payload)] + lines[3:]


def wrong_schema(lines: list[str]) -> list[str]:
    return [lines[0].replace("harbor.handoffs/v1", "harbor.handoffs/v0")] + lines[1:]


def missing_header(lines: list[str]) -> list[str]:
    return lines[1:]


def noncanonical_run(lines: list[str]) -> list[str]:
    return [lines[0].replace(RUN, RUN.upper())] + lines[1:]


def not_object(lines: list[str]) -> list[str]:
    return lines[:2] + ["[2]"] + lines[3:]


MUTATIONS = [
    corrupt,
    gap,
    reversed_send,
    reversed_seq,
    duplicate_return,
    duplicate_send,
    duplicate_commit,
    trailing_after_close,
    wrong_receiver,
    wrong_sender,
    undeclared_message,
    extra_field,
    wrong_schema,
    missing_header,
    not_object,
    noncanonical_run,
]


@pytest.mark.parametrize(
    "mutate", MUTATIONS, ids=[mutate.__name__ for mutate in MUTATIONS]
)
def test_inspect_rejects_invalid_complete_lines(
    tmp_path: Path, mutate: Callable[[list[str]], list[str]]
) -> None:
    valid = write_log(tmp_path, TASK_EDGES, SENT, 0)
    lines = valid.read_text().splitlines()
    assert len(lines) == 5
    core.inspect(valid)
    valid.write_text("".join(line + "\n" for line in mutate(lines)))

    with pytest.raises(ValueError):
        core.inspect(valid)


def random_steps(rng: random.Random, edges: Edges) -> list[Step]:
    messages = sorted({message for message, _ in edges})
    receivers = sorted({receiver for _, receiver in edges})
    pending: list[Step] = [("sent", message) for message in messages]
    pending += [
        ("returned", message, receiver)
        for message, receiver in edges
        if rng.random() < 0.75
    ]
    pending += [("commit", receiver) for receiver in receivers if rng.random() < 0.7]
    steps: list[Step] = []
    for _ in range(len(pending)):
        ready = [
            step
            for step in pending
            if step[0] != "returned" or ("sent", step[1]) in steps
        ]
        choice = rng.choice(ready)
        pending.remove(choice)
        steps.append(choice)
    assert not pending
    return steps


@pytest.mark.parametrize("seed", range(64))
def test_seeded_logs_match_reference_classification(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    edges = rng.choice([TASK_EDGES, FANOUT_EDGES])
    steps = random_steps(rng, edges)
    run = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if rng.random() < 0.5:
        reward: object = rng.choice([0, 1, 0.5, None])
        tail = ""
    else:
        reward = OPEN
        steps = steps[: rng.randint(0, len(steps))]
        tail = rng.choice(
            [
                "",
                '{"seq":',
                frame({"seq": len(steps) + 1, "type": "closed", "reward": 1}),
            ]
        )

    rows = core.inspect(write_log(tmp_path, edges, steps, reward, tail, run))

    assert observed(rows) == classify(edges, steps, reward is not OPEN), (
        seed,
        steps,
        reward,
    )
    assert {row.trial for row in rows} == {run}
    assert {row.reward for row in rows} == {None if reward is OPEN else reward}
    for row in rows:
        assert row.timing not in {"before", "after"} or (row.receipt, row.commit) == (
            "confirmed",
            "recorded",
        )
        assert row.timing != "no_commit" or (row.receipt, row.commit) == (
            "confirmed",
            "not_recorded",
        )
        assert row.basis == ("world_return" if reward is OPEN else "sealed_world")


FIELDS = (
    "trial",
    "message",
    "sender",
    "receiver",
    "receipt",
    "timing",
    "commit",
    "reward",
    "basis",
)


def finding(**overrides: Any) -> core.Finding:
    values: Raw = {
        "trial": "run-1",
        "message": "revision",
        "sender": SENDER,
        "receiver": "booker",
        "receipt": "confirmed",
        "timing": "before",
        "commit": "recorded",
        "reward": 1,
        "basis": "world_return",
    }
    values.update(overrides)
    return core.Finding(**values)


def test_finding_is_immutable_record_with_contract_fields() -> None:
    row = finding()

    assert tuple(field.name for field in dataclasses.fields(core.Finding)) == FIELDS
    assert json.loads(json.dumps(dataclasses.asdict(row)))["timing"] == "before"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(row, "timing", "after")


@pytest.mark.parametrize(
    "overrides",
    [
        {"receipt": "unknown", "timing": "before"},
        {"receipt": "absent", "timing": "after"},
        {"commit": "unknown", "timing": "after"},
        {"commit": "recorded", "timing": "no_commit"},
        {"receipt": "absent", "commit": "not_recorded", "timing": "no_commit"},
        {"receipt": "seen"},
        {"timing": "late"},
        {"commit": "maybe"},
        {"basis": "chat"},
        {"reward": 1.5},
        {"reward": -0.1},
        {"reward": "1"},
        {"trial": ""},
        {"trial": "../trial"},
        {"receiver": "booker\nreporter"},
        {"message": "x" * 4096},
    ],
    ids=lambda overrides: ",".join(
        f"{key}={str(value)[:12]!r}" for key, value in overrides.items()
    ),
)
def test_finding_rejects_contract_violations(overrides: Raw) -> None:
    with pytest.raises((TypeError, ValueError)):
        finding(**overrides)


def test_finding_accepts_unknown_and_absent_without_timing() -> None:
    finding(
        receipt="unknown", timing="unknown", commit="unknown", reward=None, basis="none"
    )
    finding(
        receipt="absent",
        timing="unknown",
        commit="not_recorded",
        reward=0,
        basis="sealed_world",
    )
    finding(
        receipt="confirmed",
        timing="no_commit",
        commit="not_recorded",
        reward=None,
        basis="world_return",
    )
    finding(trial="run_1.a-b")
    finding(
        receipt="absent",
        timing="unknown",
        commit="recorded",
        reward=0,
        basis="receiver_ack",
    )


def summary_trial(directory: Path, name: str, run: str) -> list[core.Finding]:
    edges, steps, reward, _ = CASES[name]
    directory.mkdir()
    return core.inspect(write_log(directory, edges, steps, reward, run=run))


def test_summarize_counts_receipts_rewards_and_unproven_success(tmp_path: Path) -> None:
    trials = [
        "revision_presented_before_commit",
        "grade_without_delivery",
        "everything_presented_after_commit",
        "complete_log_revision_never_presented",
        "incoming_partial_commit_before_revision",
    ]
    runs = [str(uuid.UUID(int=index + 1, version=4)) for index in range(len(trials))]
    rows = [
        row
        for index, name in enumerate(trials)
        for row in summary_trial(tmp_path / name, name, runs[index])
    ]
    rows.append(
        finding(
            trial="external-1",
            receipt="unknown",
            timing="unknown",
            commit="unknown",
            basis="none",
        )
    )
    labels: list[Label] = [
        label for name in trials for label in CASES[name][3].values()
    ] + [("unknown", "unknown", "unknown")]

    summary = core.summarize(rows)

    assert json.loads(json.dumps(summary)) == summary
    assert (summary["rows"], summary["trials"]) == (11, 6)
    assert summary["receipt"] == {"confirmed": 6, "absent": 3, "unknown": 2}
    assert summary["timing"] == {"before": 4, "after": 2, "no_commit": 0, "unknown": 5}
    assert summary["commit"] == {"recorded": 10, "not_recorded": 0, "unknown": 1}
    assert summary["basis"] == {
        "model_request": 0,
        "world_return": 2,
        "sealed_world": 8,
        "receiver_ack": 0,
        "none": 1,
    }
    assert summary["receipt_timing"] == {
        receipt: {
            timing: sum(1 for label in labels if label[:2] == (receipt, timing))
            for timing in ("before", "after", "no_commit", "unknown")
        }
        for receipt in ("confirmed", "absent", "unknown")
    }
    assert summary["reward"] == {
        "graded_trials": 5,
        "ungraded_trials": 1,
        "mean": 0.8,
        "full_reward_trials": 4,
    }
    assert summary["success_without_on_time"] == {
        "successful_trials": 4,
        "confirmed_before": 1,
        "without_confirmed_before": 3,
        "proven_not_before": 2,
        "unresolved": 1,
    }
    assert summary["success_without_on_time_by_message"] == {
        "initial": {
            "successful_trials": 3,
            "confirmed_before": 1,
            "without_confirmed_before": 2,
            "proven_not_before": 2,
            "unresolved": 0,
        },
        "revision": {
            "successful_trials": 4,
            "confirmed_before": 1,
            "without_confirmed_before": 3,
            "proven_not_before": 2,
            "unresolved": 1,
        },
    }


@pytest.mark.parametrize(
    "second",
    [
        {"timing": "after"},
        {"sender": "booker", "receiver": "auditor"},
        {"receiver": "auditor", "reward": 0},
    ],
    ids=["repeated_edge", "conflicting_sender", "conflicting_reward"],
)
def test_summarize_rejects_inconsistent_rows(second: Raw) -> None:
    with pytest.raises(ValueError):
        core.summarize([finding(), finding(**second)])
