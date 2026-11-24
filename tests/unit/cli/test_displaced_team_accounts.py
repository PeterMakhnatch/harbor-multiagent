import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.team_account import main

JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
OTHER_JOB = "8c7b6a5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d"
FINAL = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
OTHER = "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
LONE = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
RIVAL_A = "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
RIVAL_B = "6f5e4d3c-2b1a-4f9e-8d7c-6b5a4f3e2d1c"
FINAL_SLOT = "relay-sentinel__a1b2c3d"
LONE_SLOT = "relay-sentinel__e4f5a6b"
RIVAL_SLOT = "relay-sentinel__c7d8e9f"
EMPTY_SLOT = "relay-sentinel__f0a1b2c"
Json = dict[str, Any]


def trial(
    trial_id: str, slot: str, reward: float, cost: float | None, job_id: str = JOB
) -> Json:
    lead: Json = {"n_input_tokens": 100, "n_output_tokens": 10}
    if cost is not None:
        lead["cost_usd"] = cost
    return {
        "id": trial_id,
        "trial_name": slot,
        "config": {
            "job_id": job_id,
            "trial_name": slot,
            "participants": [{"seat": "p1"}, {"seat": "p2"}],
        },
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": reward}},
        "seat_results": [
            {
                "seat": "p1",
                "under_test": True,
                "execution_disposition": "returned",
                "agent_result": lead,
            },
            {
                "seat": "p2",
                "under_test": False,
                "execution_disposition": "returned",
                "agent_result": {"n_input_tokens": 50, "n_output_tokens": 5},
            },
        ],
    }


def write_dir(root: Path, documents: list[Json], empty: tuple[str, ...] = ()) -> Path:
    root.mkdir()
    (root / "job.log").write_text("sentinel")
    (root / ".sources").mkdir()
    for name in empty:
        (root / name).mkdir()
        (root / name / "trial.log").write_text("sentinel")
    for document in documents:
        (root / document["trial_name"]).mkdir()
        (root / document["trial_name"] / "result.json").write_text(json.dumps(document))
    return root


def write_job(root: Path, summary: Json) -> Path:
    root = write_dir(root, [trial(FINAL, FINAL_SLOT, 1.0, 0.25)])
    (root / "result.json").write_text(
        json.dumps({"id": JOB, "n_total_trials": 4, **summary})
    )
    return root


@pytest.fixture
def job(tmp_path: Path) -> Path:
    return write_job(tmp_path / "job", {"stats": {"n_retries": 0}})


def run(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_copies_reconcile_once_and_leave_canonical_surviving_results_unchanged(
    job, tmp_path, capsys
):
    first = write_dir(
        tmp_path / "first",
        [
            trial(FINAL, FINAL_SLOT, 1.0, 0.25),
            trial(LONE, LONE_SLOT, 0.0, 0.125),
            trial(RIVAL_A, RIVAL_SLOT, 1.0, 0.0625),
        ],
        empty=(EMPTY_SLOT,),
    )
    second = write_dir(
        tmp_path / "second",
        [
            trial(LONE, LONE_SLOT, 0.0, 0.125),
            trial(OTHER, FINAL_SLOT, 0.0, 0.5),
            trial(RIVAL_B, RIVAL_SLOT, 1.0, None),
        ],
    )
    _, baseline, _ = run(capsys, "native", job, "--json")
    code, out, _ = run(
        capsys, "native", job, "--fragment", first, "--fragment", second, "--json"
    )
    report = json.loads(out)
    displaced = report.pop("displaced")
    assert code == 0 and report == json.loads(baseline)
    assert report["grade_counts"] == {"valid": 1, "invalid": 0, "unknown": 0}
    assert report["seats"]["harness_usd"]["known_sum"] == "0.25"
    assert (
        displaced["snapshot"]["job_closed"] is None
        and displaced["snapshot"]["result_role"] == "surviving"
    )
    assert (displaced["trial_dirs"], displaced["trial_dirs_without_result"]) == (7, 1)
    assert (displaced["result_copies"], displaced["executions"]) == (6, 5)
    assert displaced["execution_kinds"] == {
        "result_copy": 1,
        "other_in_result_slot": 1,
        "sole_candidate": 1,
        "competing_candidates": 2,
    }
    assert displaced["slots"] == {
        "registered": 4,
        "with_result": 1,
        "with_result_and_other_execution": 1,
        "without_result": 3,
        "without_result_one_candidate": 1,
        "without_result_competing_candidates": 1,
        "without_result_or_displaced_execution": 1,
    }
    detached = displaced["detached_seats"]["all"]
    assert detached["seat_records"] == 8
    assert detached["harness_usd"] == {
        "known_seats": 3,
        "unknown_seats": 5,
        "known_sum": "0.6875",
    }
    lone = next(row for row in displaced["drill"] if row["trial"] == LONE)
    assert (lone["kind"], lone["sources"], lone["copies"]) == (
        "sole_candidate",
        [0, 1],
        2,
    )
    assert all(not {"grade_state", "reward"} & row.keys() for row in displaced["drill"])
    code, out, err = run(
        capsys, "native", job, "--fragment", first, "--fragment", second
    )
    assert code == 0 and "Displaced executions: 5 distinct from 6 result copies" in out
    assert (
        "Slots with a surviving result: 1/4" in out
        and "Slots with a final result" not in out
    )
    assert "relay-sentinel" not in out + err and str(tmp_path) not in out + err


def test_closed_job_labels_packet_results_final(tmp_path, capsys):
    closed = write_job(
        tmp_path / "closed",
        {
            "finished_at": "2026-11-23T16:10:00",
            "stats": {"n_completed_trials": 4, "n_retries": 0},
        },
    )
    fragment = write_dir(tmp_path / "fragment", [trial(OTHER, FINAL_SLOT, 0.0, 0.5)])
    code, out, _ = run(capsys, "native", closed, "--fragment", fragment, "--json")
    snapshot = json.loads(out)["displaced"]["snapshot"]
    assert code == 0 and (snapshot["job_closed"], snapshot["result_role"]) == (
        True,
        "final",
    )
    code, out, _ = run(capsys, "native", closed, "--fragment", fragment)
    assert (
        code == 0
        and "Slots with a final result: 1/4; with another displaced execution 1 (1 executions)"
        in out
    )


@pytest.mark.parametrize(
    ("documents", "reason"),
    [
        (
            [trial(LONE, LONE_SLOT, 1.0, None, job_id=OTHER_JOB)],
            "belongs to another job",
        ),
        ([trial(FINAL, FINAL_SLOT, 0.0, 0.25)], "project differently"),
        ([trial(FINAL, LONE_SLOT, 1.0, 0.25)], "two trial slots"),
        (
            [
                trial(t, f"relay-sentinel__{i}", 1.0, None)
                for i, t in enumerate((LONE, RIVAL_A, RIVAL_B, OTHER))
            ],
            "more trial slots",
        ),
    ],
)
def test_foreign_conflicting_or_impossible_fragments_fail_closed(
    job, tmp_path, capsys, documents, reason
):
    fragment = write_dir(tmp_path / "fragment", documents)
    code, out, err = run(capsys, "native", job, "--fragment", fragment, "--json")
    assert (code, out) == (2, "") and reason in err and str(tmp_path) not in err


def test_alias_missing_or_job_shaped_fragments_fail_closed(job, tmp_path, capsys):
    fragment = write_dir(tmp_path / "fragment", [trial(LONE, LONE_SLOT, 1.0, None)])
    replacement = write_dir(tmp_path / "replacement", [])
    (replacement / "result.json").write_text(
        json.dumps({"id": JOB, "n_total_trials": 4})
    )
    cases = (
        ((fragment, f"{fragment}/."), "supplied twice"),
        ((tmp_path / "absent",), "missing or unreadable"),
        ((replacement,), "holds a job result"),
    )
    for paths, reason in cases:
        code, out, err = run(
            capsys,
            "native",
            job,
            *[arg for path in paths for arg in ("--fragment", path)],
            "--json",
        )
        assert (code, out) == (2, "") and reason in err and str(tmp_path) not in err
