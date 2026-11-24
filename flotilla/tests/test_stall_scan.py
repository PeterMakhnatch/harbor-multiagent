"""Stall scan (docs/results/scripts/stall_scan.py) on synthetic trial folders: first gap per seat above the threshold,
grouping by start time into events, in-flight counts at the median start, several globs, and the JSON output."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import FLOTILLA, load

SS = load(FLOTILLA / "docs" / "results" / "scripts" / "stall_scan.py", "flotilla_test_stall_scan")
T0 = datetime(2026, 11, 24, 0, 8, 0, tzinfo=UTC)
A, B, C = "openrouter/a/model", "openrouter/b/model", "openrouter/c/model"


def seat(jobs, job, seat_name, model, offsets, trial="t-1__x"):
    folder = jobs / job / trial / "seats" / seat_name / "agent"
    folder.mkdir(parents=True)
    steps = [{"step_id": index, "source": "agent", "timestamp": (T0 + timedelta(seconds=offset)).isoformat()}
             for index, offset in enumerate(offsets, 1)]
    steps.append({"step_id": len(steps) + 1, "source": "system"})
    (folder / "trajectory.json").write_text(json.dumps({"agent": {"model_name": model}, "steps": steps}))


def fixture(jobs):
    seat(jobs, "wa-000001", "p1", A, [-100, 10, 610, 1300, 2000])
    seat(jobs, "wa-000001", "p2", B, [-100, 0, 30, 60, 90])
    seat(jobs, "wa-000002", "p1", A, [-50, 60, 760, 800])
    seat(jobs, "wa-000002", "p2", B, [-50, 100, 800])
    seat(jobs, "wa-000003", "p1", C, [-20, 300, 700, 1000])
    seat(jobs, "wa-000004", "p1", C, [100, 600, 1100])
    seat(jobs, "wb-000001", "p1", B, [3900, 4000, 4600], trial="t-2__y")
    seat(jobs, "wb-000001", "p2", A, [3900, 4000, 4400], trial="t-2__y")
    seat(jobs, "wc-000001", "p1", A, [0, 3000])


def test_stalls_group_into_events_with_in_flight_counts(tmp_path, capsys):
    jobs = tmp_path / "jobs"
    fixture(jobs)
    seats = SS.seats_of(jobs, ["wa-??????", "wb-??????", "wa-000001"], 500)
    assert len(seats) == 8
    stalled = sorted((row["job"], row["seat"], row["gap_s"]) for row in seats if row["gap_start"] is not None)
    assert stalled == [("wa-000001", "p1", 600), ("wa-000002", "p1", 700), ("wa-000002", "p2", 700),
                       ("wb-000001", "p1", 600)]
    events = SS.events_of(seats, 120)
    assert [(event["seats"], event["trials"]) for event in events] == [(3, 2), (1, 1)]
    first = events[0]
    assert first["median"] == (T0 + timedelta(seconds=60)).timestamp()
    assert first["models"][A] == {"stalled": 2, "in_flight": 2}
    assert first["models"][B] == {"stalled": 1, "in_flight": 2}
    assert first["models"][C] == {"stalled": 0, "in_flight": 1}
    assert events[1]["models"][B] == {"stalled": 1, "in_flight": 1}
    assert len(SS.events_of(seats, 45)) == 3
    out = tmp_path / "out.json"
    assert SS.main([str(jobs), "wa-??????", "wb-??????", "--threshold", "500", "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "4 seats in 3 trials stalled, gaps 600-700 s" in text
    assert "event 1: 3 seats in 2 trials, gap starts 2026-11-24 00:08:10-00:09:40 UTC, median 00:09:00" in text
    assert f"{A:<44} stalled 2 of 2 in flight" in text
    payload = json.loads(out.read_text())
    assert len(payload["stalls"]) == 4 and payload["stalls"][0]["gap_start_utc"] == "2026-11-24 00:08:10"
    assert payload["events"][1]["start"] == "2026-11-24 01:14:40"
    assert SS.main([str(jobs), "wa-000003", "wa-000004", "--threshold", "500"]) == 0
    assert "no stalls" in capsys.readouterr().out
    assert sum(row["gap_start"] is not None for row in SS.seats_of(jobs, ["wa-000001"], 700)) == 0
