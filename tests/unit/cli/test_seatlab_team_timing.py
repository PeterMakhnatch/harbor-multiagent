import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.team_timing import main

JOB = "5d1e7c3a-2b4f-4e6a-8c9d-0f1a2b3c4d5e"
OTHER_JOB = "6e2f8d4b-3c5a-4f7b-9d0e-1a2b3c4d5e6f"
TRIALS = (
    "a7c1e2d3-4b5f-4a6e-8d7c-9b0a1f2e3d4c",
    "b8d2f3e4-5c6a-4b7f-9e8d-0c1b2a3f4e5d",
)
SECRET = "sentinel-private-7f3e"
SLOTS = (f"pair-{SECRET}__a1b2c3d", "pair__e4f5a6b")
BASE = datetime(2026, 11, 20, 15, tzinfo=UTC)
ZONES = (UTC, timezone(timedelta(hours=-5)), timezone(timedelta(hours=5, minutes=30)))
MS = 10**6
UNDER_TEST = {"p1": True, "p2": True, "helper": False}
ENDINGS = {"p1": "returned", "p2": "returned", "helper": "stop_requested"}
WALL = (
    {"p1": (0, 4000), "p2": (1000, 3000), "helper": (0, 6000)},
    {"p1": (0, 2000), "p2": (2000, 5000), "helper": (1000, 3000)},
)
MONOTONIC = (
    {"p1": (10, 50), "p2": (20, 40), "helper": (10, 70)},
    {"p1": (0, 30), "p2": (30, 60), "helper": (10, 40)},
)
FIELDS = ("span_ns", "occupied_ns", "shared_ns", "all_ns", "seat_ns", "last_alone_ns")
KEYS = {"schema", "caveat", "job", "registered", "retained", "missing", "retries"}
KEYS |= {"job_closed", "clock", "selection", "measured", "coverage", "totals"}
SCHEMA = "seatlab.workbench.team-timing/v1"
Json = dict[str, Any]


def sums(unit: int, *values: int) -> Json:
    return dict(zip(FIELDS, (value * unit for value in values), strict=True))


def stamp(ms: int, zone: timezone) -> str:
    text = (BASE + timedelta(milliseconds=ms)).astimezone(zone).isoformat()
    return text.replace("+00:00", "Z") if zone is UTC else text


def trial(index: int, wall: dict[str, Any], job: str = JOB) -> Json:
    points = [point for window in wall.values() if window for point in window]
    seats = [
        {
            "seat": name,
            "under_test": UNDER_TEST[name],
            "execution_disposition": ENDINGS[name],
            "agent_info": {
                "name": "terminus-2",
                "model_info": {"name": f"model-{SECRET}", "provider": f"via-{SECRET}"},
            },
            "agent_execution": window
            and {
                "started_at": stamp(window[0], zone),
                "finished_at": stamp(window[1], zone),
            },
            "agent_result": {"metadata": {"transcript": SECRET}},
        }
        for (name, window), zone in zip(wall.items(), ZONES)
    ]
    return {
        "id": TRIALS[index],
        "trial_name": SLOTS[index],
        "config": {
            "job_id": job,
            "trial_name": SLOTS[index],
            "participants": [{"seat": name} for name in wall],
            "agent": {"kwargs": {"note": SECRET}},
        },
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 0.73125}},
        "exception_info": {"exception_message": SECRET},
        "agent_execution": {
            "started_at": stamp(min(points), UTC),
            "finished_at": stamp(max(points), UTC),
        },
        "seat_results": seats,
    }


def journal(index: int, closed: bool = True, endings: Json = ENDINGS) -> list[Json]:
    seats = [
        {"name": name, "under_test": UNDER_TEST[name]} for name in MONOTONIC[index]
    ]
    rows: list[Json] = [
        {
            "schema": "harbor.seat-events/v1",
            "kind": "open",
            "seq": 0,
            "trial": TRIALS[index],
            "seats": seats,
        }
    ]
    edges = [(start, 0, name) for name, (start, _) in MONOTONIC[index].items()]
    edges += [(end, 1, name) for name, (_, end) in MONOTONIC[index].items()]
    for elapsed, ending, name in sorted(edges):
        row = {
            "kind": "play_end" if ending else "run_enter",
            "seq": len(rows),
            "elapsed_ns": elapsed,
            "seat": name,
        }
        rows.append({**row, "disposition": endings[name]} if ending else row)
    tail = {"kind": "closed", "seq": len(rows), "elapsed_ns": rows[-1]["elapsed_ns"]}
    return rows + [tail] if closed else rows


def write_job(
    root: Path,
    documents: Sequence[Json],
    journals: Sequence[list[Json]] = (),
    registered: int | None = None,
    finished: str | None = "2026-11-20T10:00:09.000000",
) -> Path:
    root.mkdir()
    total = len(documents) if registered is None else registered
    stats = {"n_retries": 1, "n_completed_trials": total if finished else 0}
    summary = {
        "id": JOB,
        "n_total_trials": total,
        "stats": stats,
        "finished_at": finished,
        "config": {"job_name": SECRET},
    }
    (root / "result.json").write_text(json.dumps(summary))
    for position, document in enumerate(documents):
        slot = root / document["trial_name"]
        (slot / "agent").mkdir(parents=True)
        (slot / "result.json").write_text(json.dumps(document))
        (slot / "agent" / "trajectory.json").write_text(json.dumps({"prompt": SECRET}))
        if position < len(journals):
            (slot / "seat-events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in journals[position])
            )
    return root


def private(text: str, job: Path) -> None:
    for value in (
        SECRET,
        *SLOTS,
        *TRIALS,
        str(job.parent),
        "2026-11-20",
        "terminus-2",
        "0.73125",
        str(int(BASE.timestamp())),
    ):
        assert value not in text


def report(
    capsys: pytest.CaptureFixture[str], job: Path, *flags: str, code: int
) -> Json:
    assert main([str(job), *flags]) == code
    text = capsys.readouterr()
    private(text.out + text.err, job)
    assert main([str(job), *flags, "--json"]) == code
    captured = capsys.readouterr()
    private(captured.out + captured.err, job)
    document = json.loads(captured.out)
    assert set(document) == KEYS | ({"roles"} if "--roles" in flags else set())
    assert (document["schema"], document["job"]) == (SCHEMA, JOB)
    assert type(document["caveat"]) is str and document["caveat"]
    assert all(
        type(count) is int and count > 0 for count in document["coverage"].values()
    )
    assert sum(document["coverage"].values()) == document["retained"]
    assert document["measured"] == document["coverage"].get("measured", 0)
    return document


@pytest.fixture
def job(tmp_path: Path) -> Path:
    return write_job(
        tmp_path / "job",
        [trial(0, WALL[0]), trial(1, WALL[1])],
        [journal(0), journal(1)],
    )


def test_clock_and_selection_each_measure_their_own_closed_cohort(job, capsys):
    wall = report(capsys, job, "--roles", code=0)
    assert {
        key: wall[key]
        for key in ("registered", "retained", "missing", "retries", "job_closed")
    } == {
        "registered": 2,
        "retained": 2,
        "missing": 0,
        "retries": 1,
        "job_closed": True,
    }
    assert (wall["clock"], wall["selection"], wall["coverage"]) == (
        "result",
        "all",
        {"measured": 2},
    )
    assert wall["totals"] == sums(MS, 11000, 11000, 6000, 2000, 19000, 4000)
    assert sorted(wall["roles"], key=lambda role: role["seat"]) == [
        {"seat": "helper", "windows": 2, "last_unique": 1, "last_alone_ns": 2000 * MS},
        {"seat": "p1", "windows": 2, "last_unique": 0, "last_alone_ns": 0},
        {"seat": "p2", "windows": 2, "last_unique": 1, "last_alone_ns": 2000 * MS},
    ]
    tested = report(capsys, job, "--under-test", "--roles", code=0)
    assert (tested["selection"], tested["totals"]) == (
        "under_test",
        sums(MS, 9000, 9000, 2000, 2000, 11000, 4000),
    )
    assert sorted(
        (role["seat"], role["last_unique"], role["last_alone_ns"])
        for role in tested["roles"]
    ) == [("p1", 1, 1000 * MS), ("p2", 1, 3000 * MS)]
    monotonic = report(capsys, job, "--clock", "journal", code=0)
    assert (monotonic["clock"], monotonic["totals"]) == (
        "journal",
        sums(1, 120, 120, 70, 20, 210, 40),
    )
    still = {"p1": (500, 500), "p2": (500, 500), "helper": (500, 500)}
    zero = report(
        capsys, write_job(job.parent / "zero", [trial(0, still)]), "--roles", code=0
    )
    assert zero["totals"] == sums(0, 0, 0, 0, 0, 0, 0) and {
        role["last_unique"] for role in zero["roles"]
    } == {0}


def test_invalid_clock_and_conflict_stay_inside_their_source_and_selection(
    tmp_path, capsys
):
    reversed_helper = {**WALL[0], "helper": (6000, 0)}
    raised = journal(0, endings={**ENDINGS, "p1": "raised"})
    job = write_job(
        tmp_path / "job",
        [trial(0, reversed_helper), trial(1, WALL[1])],
        [raised, journal(1)],
    )
    wall = report(capsys, job, code=1)
    assert (wall["coverage"], wall["totals"]) == (
        {"measured": 1, "invalid_clock": 1},
        sums(MS, 5000, 5000, 2000, 0, 7000, 2000),
    )
    tested = report(capsys, job, "--under-test", code=0)
    assert tested["totals"] == sums(MS, 9000, 9000, 2000, 2000, 11000, 4000)
    monotonic = report(capsys, job, "--clock", "journal", code=1)
    assert (monotonic["coverage"], monotonic["totals"]) == (
        {"measured": 1, "journal_conflict": 1},
        sums(1, 60, 60, 30, 0, 90, 20),
    )


def test_unobserved_or_unfinished_cohorts_exit_three_with_observed_subtotals(
    tmp_path, capsys
):
    bare = write_job(tmp_path / "bare", [trial(0, WALL[0]), trial(1, WALL[1])])
    assert report(capsys, bare, code=0)["totals"] == sums(
        MS, 11000, 11000, 6000, 2000, 19000, 4000
    )
    blind = report(capsys, bare, "--clock", "journal", code=3)
    assert (blind["measured"], blind["coverage"], blind["totals"]) == (
        0,
        {"no_journal": 2},
        None,
    )
    lost = report(
        capsys,
        write_job(tmp_path / "lost", [trial(0, WALL[0])], [journal(0)], registered=2),
        code=3,
    )
    assert (
        lost["registered"],
        lost["retained"],
        lost["missing"],
        lost["measured"],
    ) == (2, 1, 1, 1)
    assert lost["totals"] == sums(MS, 6000, 6000, 4000, 2000, 12000, 2000)
    open_journal = write_job(
        tmp_path / "open",
        [trial(0, WALL[0]), trial(1, WALL[1])],
        [journal(0), journal(1, closed=False)],
    )
    partial = report(capsys, open_journal, "--clock", "journal", code=3)
    assert (partial["coverage"], partial["totals"]) == (
        {"measured": 1, "incomplete_journal": 1},
        sums(1, 60, 60, 40, 20, 120, 20),
    )
    gap = write_job(
        tmp_path / "gap", [trial(0, WALL[0]), trial(1, {**WALL[1], "p2": None})]
    )
    for flags in ((), ("--under-test",)):
        assert report(capsys, gap, *flags, code=3)["coverage"] == {
            "measured": 1,
            "missing_intervals": 1,
        }
    running = report(
        capsys,
        write_job(
            tmp_path / "running", [trial(0, WALL[0]), trial(1, WALL[1])], finished=None
        ),
        code=3,
    )
    assert (running["job_closed"], running["measured"]) == (False, 2)
    empty = report(capsys, write_job(tmp_path / "empty", []), code=3)
    assert (empty["registered"], empty["coverage"], empty["totals"]) == (0, {}, None)
    solo = write_job(
        tmp_path / "solo", [trial(0, {"p1": (0, 4000), "helper": (0, 6000)})]
    )
    assert report(capsys, solo, code=0)["totals"] == sums(
        MS, 6000, 6000, 4000, 4000, 10000, 2000
    )
    alone = report(capsys, solo, "--under-test", code=3)
    assert (alone["coverage"], alone["totals"]) == ({"fewer_than_two": 1}, None)


def test_malformed_job_or_identity_exits_two_without_a_report(tmp_path, capsys):
    foreign = write_job(
        tmp_path / "foreign", [trial(0, WALL[0]), trial(1, WALL[1], job=OTHER_JOB)]
    )
    twin = trial(1, WALL[1])
    twin["seat_results"][1]["seat"] = "p1"
    twins = write_job(tmp_path / "twins", [trial(0, WALL[0]), twin])
    (tmp_path / "plain").write_text(SECRET)
    for target in (tmp_path / "absent", tmp_path / "plain", foreign, twins):
        for flags in ((), ("--json",), ("--clock", "journal", "--json")):
            assert main([str(target), *flags]) == 2
            captured = capsys.readouterr()
            assert captured.out == ""
            private(captured.err, target)
