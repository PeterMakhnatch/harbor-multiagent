import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import native_account
from examples.seatlab.workbench.seat_stream import Malformed

JOB = "5a4f0c2e-1b7d-4c39-9e8a-2f6d0b1c3a47"
OTHER_JOB = "7c1e9a30-4d2b-4f86-a1c5-8e3b2d9f0a61"
TRIALS = (
    "0b6a2f1c-9d3e-4a57-8c41-6e2f0d1b9a35",
    "3e8d1c5a-2b7f-4960-a3d4-9c1e6b2f0a78",
    "9f2c7e14-6a3b-4d58-b0e1-5d7a2c9f3b46",
)
ROSTER = ["p1", "p2"]
Json = dict[str, Any]


def result(
    trial: str,
    name: str,
    *,
    roster: list[str] | None = ROSTER,
    seats: bool = True,
    job_id: str = JOB,
    configured_name: str | None = None,
) -> Json:
    config: Json = {"job_id": job_id, "trial_name": configured_name or name}
    if roster is not None:
        config["participants"] = [{"seat": seat} for seat in roster]
    document: Json = {"id": trial, "trial_name": name, "config": config}
    if seats:
        document["seat_results"] = [
            {"seat": seat, "under_test": seat == "p1"} for seat in ROSTER
        ]
    return document


def write_job(root: Path, registered: int, finals: Mapping[str, Json | None]) -> str:
    job = root / "job"
    job.mkdir()
    (job / "result.json").write_text(
        json.dumps({"id": JOB, "n_total_trials": registered})
    )
    for name, document in finals.items():
        (job / name).mkdir()
        if document is not None:
            (job / name / "result.json").write_text(json.dumps(document))
    return str(job)


def inventory(job: str) -> list[tuple[str, Json, str, list[str] | None]]:
    return list(native_account.retained(job, native_account._root(job)))


def test_retained_yields_local_identity_without_judging_rosters(tmp_path):
    finals = {
        "relay__a": result(TRIALS[0], "relay__a"),
        "relay__b": result(TRIALS[1], "relay__b", seats=False),
        "relay__c": result(TRIALS[2], "relay__c", roster=None, seats=False),
        "relay__d": None,
    }
    job = write_job(tmp_path, 4, finals)
    assert inventory(job) == [
        ("relay__a", finals["relay__a"], TRIALS[0], ROSTER),
        ("relay__b", finals["relay__b"], TRIALS[1], ROSTER),
        ("relay__c", finals["relay__c"], TRIALS[2], None),
    ]
    with pytest.raises(Malformed, match="not a multi-seat result"):
        native_account.build(job)
    os.remove(os.path.join(job, "relay__c", "result.json"))
    attempts = native_account.build(job)["attempts"]
    assert [(row["trial"], row["slot"], len(row["seats"])) for row in attempts] == [
        (TRIALS[0], hashlib.sha256(b"relay__a").hexdigest(), 2),
        (TRIALS[1], hashlib.sha256(b"relay__b").hexdigest(), 2),
    ]


@pytest.mark.parametrize(
    ("registered", "finals", "reason"),
    [
        (2, {"a": result(TRIALS[0], "a", job_id=OTHER_JOB)}, "belongs to another job"),
        (2, {"a": result(TRIALS[0], "a", configured_name="b")}, "another directory"),
        (2, {"a": result(TRIALS[0].upper(), "a")}, "not a canonical lowercase UUID"),
        (
            2,
            {"a": result(TRIALS[0], "a"), "b": result(TRIALS[0], "b")},
            "two directories hold the same trial",
        ),
        (
            1,
            {"a": result(TRIALS[0], "a"), "b": result(TRIALS[1], "b")},
            "more trial results than n_total_trials",
        ),
    ],
)
def test_retained_rejects_foreign_duplicate_or_surplus_finals(
    tmp_path, registered, finals, reason
):
    job = write_job(tmp_path, registered, finals)
    with pytest.raises(Malformed, match=reason):
        inventory(job)


def test_retained_bounds_rows_and_bytes_before_yielding(tmp_path, monkeypatch):
    finals = {"a": result(TRIALS[0], "a"), "b": result(TRIALS[1], "b", seats=False)}
    job = write_job(tmp_path, 2, finals)
    monkeypatch.setattr(native_account, "MAX_SEAT_ROWS", 3)
    rows = native_account.retained(job, native_account._root(job))
    assert next(rows)[2] == TRIALS[0]
    with pytest.raises(Malformed, match="too many seat rows"):
        next(rows)
    monkeypatch.undo()
    job_id, registered, retries, closed, _ = native_account._root(job)
    first = os.path.getsize(os.path.join(job, "a", "result.json"))
    root = (job_id, registered, retries, closed, first)
    with pytest.raises(Malformed, match="256 MiB in total"):
        list(native_account.retained(job, root))
