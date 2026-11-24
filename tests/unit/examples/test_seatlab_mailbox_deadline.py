import copy
import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.fanin.mailbox_deadline import (
    MILESTONES,
    STATUSES,
    Rejected,
    assess,
    cohort,
)

RESULTS = Path(__file__).resolve().parents[3] / "examples/seatlab/results/gather-time"
STAGES = ("request", "request_inbox", "reply", "reply_inbox")
SEATS = ("p2", "p3", "p4")
EVENTS = "ab" * 32
CLOCK = (
    ((3, 0), (7, 20), (10, 40), (11, 45)),
    ((4, 0), (8, 20), (12, 50), (13, 60)),
    ((5, 0), (6, 10), (9, 30), (11, 45)),
)
SUBMIT = (14, 70)
HORIZON = (16, 90)


def cut(
    seq: int,
    *,
    submit: tuple[int, int] | None = SUBMIT,
    through: list[int] | None = None,
) -> dict[str, Any]:
    edges = [
        {"seat": seat}
        | {
            stage: list(point) if point[0] <= seq else None
            for stage, point in zip(STAGES, path, strict=True)
        }
        for seat, path in zip(SEATS, CLOCK, strict=True)
    ]
    marks = [point for path in CLOCK for point in path if point[0] <= seq]
    booked = submit if submit is not None and submit[0] <= seq else None
    if booked is not None:
        marks.append(booked)
    if through is None and marks:
        through = [seq, max(ms for _, ms in marks)]
    return {
        "schema": "seatlab.fanin-mailbox-cut/v1",
        "events_sha256": EVENTS,
        "booker": "p1",
        "reporters": list(SEATS),
        "edges": edges,
        "submit": None if booked is None else list(booked),
        "through": through,
    }


def status(raw: dict[str, Any], milestone: str, by_ms: int) -> str:
    return assess(raw, milestone=milestone, by_ms=by_ms)["status"]


def load() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    release = json.loads((RESULTS / "rows.json").read_text())
    cuts = json.loads((RESULTS / "mailbox-cuts.json").read_text())["cuts"]
    return release, cuts


def released(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [*release["rows"], *release["controls"]]


def by_events(cuts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {raw["events_sha256"]: raw for raw in cuts}


def test_target_is_latest_of_three_by_journal_seq_not_role_or_first_ms() -> None:
    full = cut(HORIZON[0], through=list(HORIZON))
    requests = assess(full, milestone="requests-returned", by_ms=0)
    assert requests["target"] == [8, 20]
    assert requests["target"] != list(CLOCK[0][1])
    assert requests["target"] != list(CLOCK[-1][1])
    assert assess(full, milestone="replies-returned", by_ms=0)["target"] == [13, 60]
    assert assess(full, milestone="booked", by_ms=0)["target"] == list(SUBMIT)


def test_deadline_is_inclusive_and_earlier_deadline_is_known_non_completion() -> None:
    full = cut(HORIZON[0], through=list(HORIZON))
    for milestone, target_ms in zip(MILESTONES, (20, 60, 70), strict=True):
        for by_ms in range(HORIZON[1] + 2):
            expected = "reached" if by_ms >= target_ms else "not_reached"
            assert status(full, milestone, by_ms) == expected, (milestone, by_ms)


def test_two_of_three_returned_is_not_a_target_and_ends_with_coverage() -> None:
    partial = cut(7)
    assessed = assess(partial, milestone="requests-returned", by_ms=20)
    assert assessed["target"] is None
    assert assessed["observed_through"] == [7, 20]
    stages = [s["stage"] for s in assessed["stages"]]
    assert stages == ["request_inbox", "request", "request_inbox"]
    assert assessed["status"] == "coverage_ended"
    assert status(partial, "requests-returned", 19) == "not_reached"


@pytest.mark.parametrize(
    ("through", "unknown_from"),
    [([9, 30], 30), ([10, 30], 30), ([10, 31], 31)],
)
def test_capture_ending_at_or_before_deadline_never_claims_non_completion(
    through: list[int], unknown_from: int
) -> None:
    ended = cut(9, through=through)
    assert status(ended, "requests-returned", 20) == "reached"
    for milestone in ("replies-returned", "booked"):
        assert status(ended, milestone, unknown_from - 1) == "not_reached"
        assert status(ended, milestone, unknown_from) == "coverage_ended"
        assert status(ended, milestone, HORIZON[1]) == "coverage_ended"


def test_no_request_means_no_origin_for_every_milestone_and_deadline() -> None:
    empty = cut(2)
    assert empty["through"] is None
    for milestone in MILESTONES:
        for by_ms in (0, 70, HORIZON[1]):
            assessed = assess(empty, milestone=milestone, by_ms=by_ms)
            assert assessed["status"] == "no_origin"
            assert assessed["target"] is None
            assert {s["stage"] for s in assessed["stages"]} == {None}


def test_every_shorter_capture_agrees_with_the_later_known_target() -> None:
    full = cut(HORIZON[0], through=list(HORIZON))
    for seq in range(2, HORIZON[0] + 1):
        prefix = cut(seq)
        for milestone in MILESTONES:
            for by_ms in range(HORIZON[1] + 2):
                early = assess(prefix, milestone=milestone, by_ms=by_ms)
                later = status(full, milestone, by_ms)
                assert later != "coverage_ended"
                if early["status"] in ("reached", "not_reached"):
                    assert early["status"] == later, (seq, milestone, by_ms)
                if early["status"] == "not_reached" and early["target"] is None:
                    assert early["observed_through"][1] > by_ms
                if early["status"] == "coverage_ended":
                    assert early["observed_through"][1] <= by_ms
                assert (early["status"] == "no_origin") == (seq < 3)


@pytest.mark.parametrize("by_ms", [-1, True, 1 << 40 | 1, 1.0])
def test_deadline_must_be_a_plain_bounded_millisecond_count(by_ms: Any) -> None:
    with pytest.raises(Rejected, match="deadline_by_ms"):
        assess(cut(HORIZON[0]), milestone="booked", by_ms=by_ms)


def test_released_submit_ms_is_the_exact_booked_deadline_threshold() -> None:
    release, cuts = load()
    bound = by_events(cuts)
    checked = 0
    for row in released(release):
        submit_ms = row["timing"]["submit_ms"]
        raw = bound[row["inputs"]["events"]]
        if submit_ms is None:
            continue
        assert status(raw, "booked", submit_ms) == "reached"
        assert status(raw, "booked", submit_ms - 1) == "not_reached"
        checked += 1
    assert checked == 18


def test_unbooked_released_team_is_known_late_only_inside_its_capture() -> None:
    release, cuts = load()
    [row] = [r for r in release["rows"] if r["timing"]["submit_ms"] is None]
    raw = by_events(cuts)[row["inputs"]["events"]]
    end = raw["through"][1]
    assert row["complete"] is False
    assert status(raw, "booked", end - 1) == "not_reached"
    assert status(raw, "booked", end) == "coverage_ended"
    assert status(raw, "replies-returned", end) == "reached"


@pytest.mark.parametrize("by_ms", [0, 4557, 19648, 19649, 30976, 30977, 64043])
def test_cohort_counts_follow_release_timing_and_keep_scripted_controls_apart(
    by_ms: int,
) -> None:
    release, cuts = load()
    end = {raw["events_sha256"]: raw["through"][1] for raw in cuts}
    result = cohort(release, cuts, milestone="booked", by_ms=by_ms)

    def expect(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts = dict.fromkeys(STATUSES, 0)
        for row in rows:
            submit_ms = row["timing"]["submit_ms"]
            if submit_ms is not None and submit_ms <= by_ms:
                counts["reached"] += 1
            elif submit_ms is not None or end[row["inputs"]["events"]] > by_ms:
                counts["not_reached"] += 1
            else:
                counts["coverage_ended"] += 1
        return counts

    for arm in ("serial", "parallel"):
        assert result["arms"][arm] == expect(
            [r for r in release["rows"] if r["arm"] == arm]
        )
    assert result["controls"] == expect(release["controls"])
    assert (
        sum(sum(c.values()) for c in result["arms"].values())
        == len(release["rows"])
        == 16
    )
    assert sum(result["controls"].values()) == len(release["controls"]) == 3
    if by_ms == 4557:
        assert result["controls"]["reached"] == 3
        assert all(c["reached"] == 0 for c in result["arms"].values())


def test_missing_cut_and_absent_release_row_are_no_cut_not_failures() -> None:
    release, cuts = load()
    [row] = [r for r in release["rows"] if r["timing"]["submit_ms"] is None]
    events = row["inputs"]["events"]
    kept = [raw for raw in cuts if raw["events_sha256"] != events]
    trimmed = copy.deepcopy(release)
    trimmed["rows"] = [r for r in trimmed["rows"] if r["inputs"]["events"] != events]
    with pytest.raises(Rejected, match="cut_foreign"):
        cohort(trimmed, cuts, milestone="booked", by_ms=1 << 40)
    for source, coverage in ((release, "missing"), (trimmed, "no_release_row")):
        result = cohort(source, kept, milestone="booked", by_ms=1 << 40)
        assert len(result["teams"]) == 19
        [team] = [t for t in result["teams"] if t["assessment"] is None]
        assert (team["world"], team["arm"]) == (row["world"], row["arm"])
        assert team["coverage"] == coverage
        assert result["arms"]["serial"] == {
            "reached": 7,
            "not_reached": 0,
            "coverage_ended": 0,
            "no_origin": 0,
            "no_cut": 1,
        }
        assert result["arms"]["parallel"]["reached"] == 8
        assert result["controls"]["reached"] == 3


def test_empty_release_still_registers_all_nineteen_teams_as_no_cut() -> None:
    release, _ = load()
    empty = {"schema": release["schema"], "rows": [], "controls": []}
    for milestone in MILESTONES:
        result = cohort(empty, [], milestone=milestone, by_ms=0)
        assert len(result["teams"]) == 19
        assert {t["coverage"] for t in result["teams"]} == {"no_release_row"}
        assert all(t["assessment"] is None for t in result["teams"])
        assert [c["no_cut"] for c in result["arms"].values()] == [8, 8]
        assert result["controls"]["no_cut"] == 3
        assert (
            sum(sum(c.values()) for c in (*result["arms"].values(), result["controls"]))
            == 19
        )
