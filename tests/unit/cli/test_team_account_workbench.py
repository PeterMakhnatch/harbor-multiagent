import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.account_keeper import encode
from examples.seatlab.workbench.generation_receipts import owner
from examples.seatlab.workbench.native_account import project_result
from examples.seatlab.workbench.team_account import main

JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
OTHER_JOB = "8c7b6a5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d"
FINAL_A = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
FINAL_B = "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
EARLIER = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
SENTINEL = "sentinel-config-value-4c1d"
SLOT_A = f"pair-review-{SENTINEL}__a1b2c3d"
SLOT_B = "pair-review__e4f5a6b"
Json = dict[str, Any]
FINISHED: Json = {"finished_at": "2026-11-20T15:04:00+00:00"}


def seat(name: str, under_test: bool, usage: Json) -> Json:
    return {
        "seat": name,
        "under_test": under_test,
        "execution_disposition": "returned",
        "agent_info": {
            "name": "terminus-2",
            "model_info": {"name": "openai/gpt-5-mini", "provider": "openrouter"},
        },
        "agent_result": {"metadata": {"transcript": SENTINEL}, **usage},
    }


def trial(
    job_id: str,
    trial_id: str,
    slot: str,
    reward: float,
    author_cost: float | None = None,
) -> Json:
    author: Json = {
        "n_input_tokens": 1200,
        "n_cache_tokens": 800,
        "n_output_tokens": 90,
    }
    if author_cost is not None:
        author["cost_usd"] = author_cost
    return {
        "id": trial_id,
        "trial_name": slot,
        "config": {
            "job_id": job_id,
            "trial_name": slot,
            "participants": [{"seat": "author"}, {"seat": "critic"}],
            "agent": {"kwargs": {"api_note": SENTINEL}},
        },
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": {"exception_message": SENTINEL},
        "seat_results": [
            seat("author", True, author),
            seat(
                "critic",
                False,
                {"n_input_tokens": 300, "n_output_tokens": 20, "cost_usd": 0},
            ),
        ],
    }


def finals(job_id: str) -> list[Json]:
    return [
        trial(job_id, FINAL_A, SLOT_A, 0.0),
        trial(job_id, FINAL_B, SLOT_B, 1.0, author_cost=0.25),
    ]


def write_job(
    root: Path, job_id: str, documents: list[Json], closure: Json = FINISHED
) -> Path:
    root.mkdir()
    stats = {"n_retries": 1, "n_completed_trials": len(documents)}
    summary = {
        "id": job_id,
        "n_total_trials": len(documents),
        "stats": stats,
        "config": {"job_name": SENTINEL},
        **closure,
    }
    (root / "result.json").write_text(json.dumps(summary))
    (root / "job.log").write_text(SENTINEL)
    for document in documents:
        (root / document["trial_name"]).mkdir()
        (root / document["trial_name"] / "result.json").write_text(json.dumps(document))
        (root / document["trial_name"] / "trial.log").write_text(SENTINEL)
    return root


def write_lines(path: Path, rows: list[Json]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def export(capsys: pytest.CaptureFixture[str], job: Path) -> Json:
    code, out, _ = run(capsys, "native", job, "--packet")
    assert code == 0
    return json.loads(out)


def write_archive(root: Path) -> Path:
    root.mkdir()
    for document in [*finals(JOB), trial(JOB, EARLIER, SLOT_A, 1.0, author_cost=0.5)]:
        name, data = encode(
            JOB, project_result(document, job_id=JOB, slot_name=document["trial_name"])
        )
        (root / name).write_bytes(data)
    return root


@pytest.fixture
def job(tmp_path: Path) -> Path:
    return write_job(tmp_path / "job", JOB, finals(JOB))


def test_native_keeps_unknown_versus_zero_money_zero_reward_and_cache_once(job, capsys):
    code, out, _ = run(capsys, "native", job, "--trial", FINAL_A, "--json")
    report = json.loads(out)
    assert code == 0
    assert report["grade_counts"] == {"valid": 2, "invalid": 0, "unknown": 0}
    assert (
        report["selected_result"]["grade_state"] == "valid"
        and report["selected_result"]["reward"] == 0
    )
    selected = {s["name"]: s["harness_usd"] for s in report["selected_result"]["seats"]}
    assert selected == {"author": None, "critic": "0"}
    assert report["by_seat_label"]["author"]["harness_usd"] == {
        "known_seats": 1,
        "unknown_seats": 1,
        "known_sum": "0.25",
    }
    assert report["by_seat_label"]["critic"]["harness_usd"] == {
        "known_seats": 2,
        "unknown_seats": 0,
        "known_sum": "0",
    }
    assert report["seats"]["input_tokens_including_cache"]["known_sum"] == 3000
    assert report["seats"]["cache_tokens"] == {
        "known_seats": 2,
        "unknown_seats": 2,
        "known_sum": 1600,
    }


def test_outputs_omit_config_logs_and_directory_names(job, capsys):
    for mode in (("--packet",), ("--json",), ()):
        code, out, err = run(capsys, "native", job, *mode)
        assert code == 0
        for private in (SENTINEL, SLOT_B, str(job.parent)):
            assert private not in out + err


def test_identical_worker_packet_counts_once(job, tmp_path, capsys):
    packet = export(capsys, job)
    _, single, _ = run(capsys, "native", job, "--json")
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps(packet))
    second.write_text(json.dumps(packet, indent=2, sort_keys=True))
    code, out, _ = run(capsys, "merge", first, second, "--json")
    merged = json.loads(out)
    assert code == 0 and merged.pop("duplicate_packets_ignored") == 1
    assert merged == {
        k: v for k, v in json.loads(single).items() if k != "duplicate_packets_ignored"
    }


def test_merge_rejects_shared_execution_or_conflicting_same_scope_money(
    job, tmp_path, capsys
):
    packet = export(capsys, job)
    other = export(
        capsys,
        write_job(
            tmp_path / "other", OTHER_JOB, [trial(OTHER_JOB, FINAL_A, SLOT_B, 1.0)]
        ),
    )
    raised = json.loads(json.dumps(packet))
    next(a for a in raised["attempts"] if a["trial"] == FINAL_B)["seats"][0][
        "harness_usd"
    ] = "0.3"
    unknown = json.loads(json.dumps(packet))
    next(a for a in unknown["attempts"] if a["trial"] == FINAL_A)["seats"][1][
        "harness_usd"
    ] = None
    cases = (
        (other, "more than one scope"),
        (raised, "disagree"),
        (unknown, "disagree"),
    )
    for index, (candidate, reason) in enumerate(cases):
        paths = (
            write_lines(tmp_path / f"base-{index}.json", [packet]),
            write_lines(tmp_path / f"alt-{index}.json", [candidate]),
        )
        code, out, err = run(capsys, "merge", *paths, "--json")
        assert (code, out) == (2, "") and reason in err and str(tmp_path) not in err


def test_earlier_charged_execution_never_adds_a_final_grade(job, tmp_path, capsys):
    archive = write_archive(tmp_path / "archive")
    code, out, _ = run(capsys, "native", job, "--archive", archive, "--json")
    report = json.loads(out)
    history = report["attempt_archive"]
    assert code == 0
    assert (
        report["grade_counts"] == {"valid": 2, "invalid": 0, "unknown": 0}
        and report["surviving_results"] == 2
    )
    assert report["scopes"][0]["job_closed"] is True
    assert report["seats"]["harness_usd"]["known_sum"] == "0.25"
    assert (
        history["finals"]["confirmed"] == 2
        and history["nonfinal"]["superseded_confirmed_slot"] == 1
    )
    assert history["complete"] and history["harness_usd"]["total"] == "0.5"
    assert list(history["attempts"]) == [EARLIER]
    assert not {"grade_state", "reward"} & history["attempts"][EARLIER].keys()


@pytest.mark.parametrize(
    ("closure", "closed"), [({"finished_at": None}, False), ({}, None)]
)
def test_unfinished_job_claims_no_confirmed_earlier_cost(
    tmp_path, capsys, closure, closed
):
    job = write_job(tmp_path / "job", JOB, finals(JOB), closure)
    archive = write_archive(tmp_path / "archive")
    code, out, _ = run(capsys, "native", job, "--archive", archive, "--json")
    report = json.loads(out)
    history = report["attempt_archive"]
    assert code == 0
    assert report["grade_counts"] == {"valid": 2, "invalid": 0, "unknown": 0}
    assert report["scopes"][0]["job_closed"] is closed
    assert history["complete"] is False and history["expected_nonfinal"] is None
    assert history["harness_usd"]["total"] is None and history["attempts"] == {}


def test_generation_owner_conflict_never_assigns_money_twice(job, tmp_path, capsys):
    def call(call_id: str, generation: str, total: float, nano: int) -> list[Json]:
        started = {
            "event": "started",
            "at": "2026-11-20T15:00:00Z",
            "call_id": call_id,
            "identity": "author",
            "model": "openai/gpt-5-mini",
            "request_sha256": "0" * 64,
            "reserved_nano": 2_000_000,
            "input_token_bound": 4096,
            "output_token_bound": 512,
        }
        settled = {
            "event": "settled",
            "at": "2026-11-20T15:00:02Z",
            "call_id": call_id,
            "cost_nano": nano,
            "cost_basis": "openrouter_generation.total_cost",
            "receipt": {"id": generation, "total_cost": total},
        }
        return [started, settled, settled]

    ledger = write_lines(
        tmp_path / "events.jsonl",
        call("c1", "gen-a", 0.0015, 1_500_000) + call("c2", "gen-b", 0.002, 2_000_000),
    )
    claim = owner(FINAL_B, "author", "gen-a")
    owners = write_lines(tmp_path / "owners.jsonl", [claim, claim])
    code, out, _ = run(
        capsys, "native", job, "--ledger", ledger, "--owners", owners, "--json"
    )
    report = json.loads(out)
    receipts = report["provider_receipts"]
    assert code == 0 and report["seats"]["harness_usd"]["known_sum"] == "0.25"
    assert receipts["known_usd"] == {
        "settled": "0.0035",
        "assigned_to_team": "0.0015",
        "not_assigned": "0.002",
    }
    assert receipts["team"] == [
        {"trial": FINAL_B, "seat": "author", "generations": 1, "usd": "0.0015"}
    ]
    write_lines(owners, [claim, owner(FINAL_A, "critic", "gen-a")])
    code, out, err = run(
        capsys, "native", job, "--ledger", ledger, "--owners", owners, "--json"
    )
    assert (code, out) == (2, "") and "second seat" in err

    code, out, _ = run(capsys, "ledger", ledger, "--json")
    independent = json.loads(out)
    assert code == 0
    assert independent["all_calls"]["known_usd"] == "0.0035"
    assert independent["requested_routes"] is independent["reported_routes"] is None
    assert "openai" not in out and "author" not in out and FINAL_B not in out
    code, out, _ = run(capsys, "ledger", ledger, "--routes", "--json")
    independent = json.loads(out)
    assert code == 0
    assert [row["model"] for row in independent["requested_routes"]] == [
        "openai/gpt-5-mini"
    ]
    assert independent["reported_routes"] == [
        {"model": None, "provider": None, "settled": 2, "known_usd": "0.0035"}
    ]
    assert "gen-a" not in out and "author" not in out
