from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "masbench_scheduling_cases", Path(__file__).resolve().parents[1] / "cases.py"
)
assert _spec is not None and _spec.loader is not None
cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cases)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _source_case(tmp_path: Path, case_id: str = "4/1") -> Path:
    return tmp_path / "Scheduling" / "meeting" / "minutes_10" / "5days" / case_id


def _calendar(member_id: int = 1, activities: list | None = None) -> dict:
    return {
        "start_time": 0,
        "end_time": 720,
        "total_days": 5,
        "time_unit": "minutes_10",
        "member_id": member_id,
        "activities": [] if activities is None else activities,
        "member_name": "do not export this text",
    }


def _reference(solutions: list, duration: int = 2) -> dict:
    return {
        "meeting": {
            "task_type": "meeting",
            "duration": duration,
            "solutions": solutions,
        }
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    source = _source_case(tmp_path)
    source.mkdir(parents=True)
    for number in range(1, 6):
        _write_json(source / f"Member{number}_final_schedule.json", _calendar(number))
    _write_json(source / "task_plans", _reference([]))
    return source


def test_import_expands_half_open_intervals_and_preserves_exact_gold(
    tmp_path: Path, source: Path
) -> None:
    activities = [
        {"date": 5, "duration": "23:50-00:00"},
        {"date": 2, "duration": "00:20-00:40", "description": "Free: still occupied"},
        {"date": 1, "duration": "23:50-00:00"},
        {"date": 2, "duration": "00:00-00:30"},
        {"date": 2, "duration": "00:40-00:50"},
        {"date": 3, "duration": "23:50-00:10"},
        {"date": 4, "duration": "23:50-24:00"},
    ]
    _write_json(
        source / "Member1_final_schedule.json", _calendar(activities=activities)
    )
    _write_json(source / "Member5_final_schedule.json", _calendar(5, activities[:1]))
    _write_json(
        source / "task_plans",
        _reference(
            [
                {"solution_id": index, "start_time": start, "end_time": start + 2}
                for index, start in enumerate((718, 143, 410, 410, 0))
            ]
        ),
    )

    assert cases.load_case(tmp_path, "4/1") == {
        "schema": "seatlab.calendar-case/v1",
        "case_id": "4/1",
        "dataset_commit": "c0301a650db95958924bb080192c09fef39389a5",
        "duration": 2,
        "horizon": 720,
        "slots_per_day": 144,
        "members": {
            "m1": [143, 144, 145, 146, 147, 148, 431, 432, 575, 719],
            "m2": [],
            "m3": [],
            "m4": [],
            "m5": [719],
        },
        "gold": [0, 143, 410, 718],
        "source_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source.iterdir()
        },
    }


def test_member_starts_allow_an_end_at_midnight_but_not_a_cross_day_start() -> None:
    starts = cases.member_starts([0, 141, 146, 717])
    assert starts[:2] == [1, 2]
    assert {142, 144, 718} <= set(starts)
    assert {0, 140, 141, 143, 145, 287, 431, 575, 717, 719}.isdisjoint(starts)


def test_overlapping_starts_survive_while_a_busy_tick_breaks_a_run() -> None:
    assert cases.member_starts([1, 1], duration=3, horizon=12, slots_per_day=6) == [
        2,
        3,
        6,
        7,
        8,
        9,
    ]


def test_reference_audit_keeps_cross_day_gold_and_calendar_only_starts() -> None:
    case = {
        "duration": 2,
        "horizon": 8,
        "slots_per_day": 4,
        "members": {"m1": [0], "m2": [6], "m3": [7], "m4": [], "m5": [5]},
        "gold": [1, 3],
    }
    assert cases.evaluate_reference(case) == {
        "agrees": False,
        "only_in_reference": [3],
        "only_in_calendars": [2],
        "intersection": [1],
    }
    assert case["gold"] == [1, 3]
    case["gold"] = [1, 2]
    assert cases.evaluate_reference(case) == {
        "agrees": True,
        "only_in_reference": [],
        "only_in_calendars": [],
        "intersection": [1, 2],
    }


@pytest.mark.parametrize("duration", [2, 20])
def test_explicit_empty_reference_is_not_a_missing_reference(
    tmp_path: Path, source: Path, duration: int
) -> None:
    path = source / "task_plans"
    _write_json(path, _reference([], duration))
    assert cases.load_case(tmp_path, "4/1")["gold"] == []
    path.rename(source / "task_plans.json")
    with pytest.raises(FileNotFoundError, match="task_plans"):
        cases.load_case(tmp_path, "4/1")


def test_list_cases_accepts_only_published_tree_and_sorts_numerically(
    tmp_path: Path, source: Path
) -> None:
    for case_id in ("2/10", "2/2", "3/2", "4/01", "4/0", "6/1"):
        _source_case(tmp_path, case_id).mkdir(parents=True)
    (source.parent / "README").touch()
    assert cases.list_cases(tmp_path) == ["2/2", "2/10", "3/2", "4/1"]


@pytest.mark.parametrize(
    "case_id", ["./4/1", "4/../1", "4/1/", "4/01", "4/0", "4/١", "4/1\n", "6/1"]
)
def test_rejects_invalid_case_ids(tmp_path: Path, case_id: str) -> None:
    with pytest.raises(ValueError, match="stratum/number"):
        cases.load_case(tmp_path, case_id)


def test_rejects_symlinks_in_case_tree_and_source_files(
    tmp_path: Path, source: Path
) -> None:
    linked_case = source.with_name("2")
    linked_case.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink directory"):
        cases.list_cases(tmp_path)
    with pytest.raises(ValueError, match="non-symlink directory"):
        cases.load_case(tmp_path, "4/2")

    member = source / "Member1_final_schedule.json"
    moved = member.rename(tmp_path / member.name)
    member.symlink_to(moved)
    with pytest.raises(ValueError, match="non-symlink regular file"):
        cases.load_case(tmp_path, "4/1")


@pytest.mark.parametrize(
    "activity",
    [
        None,
        {"duration": "00:00-00:10"},
        {"date": True, "duration": "00:00-00:10"},
        {"date": 0, "duration": "00:00-00:10"},
        {"date": 6, "duration": "00:00-00:10"},
        {"date": 1, "duration": "00:01-00:20"},
        {"date": 1, "duration": "23:50-24:10"},
        {"date": 1, "duration": "24:00-01:00"},
        {"date": 1, "duration": "00:00-00:60"},
        {"date": 5, "duration": "23:50-00:10"},
    ],
)
def test_rejects_invalid_activity_boundaries(
    tmp_path: Path, source: Path, activity: object
) -> None:
    _write_json(
        source / "Member1_final_schedule.json", _calendar(activities=[activity])
    )
    with pytest.raises(ValueError, match=r"activities\[0\]"):
        cases.load_case(tmp_path, "4/1")


@pytest.mark.parametrize(
    "reference",
    [
        {},
        {"meeting": {"task_type": "meeting", "duration": 2}},
        _reference([None]),
        _reference([{"start_time": True, "end_time": 3}]),
        _reference([{"start_time": -1, "end_time": 1}]),
        _reference([{"start_time": 1, "end_time": 2}]),
        _reference([{"start_time": 719, "end_time": 721}]),
        _reference([{"start_time": 0, "end_time": 2}], duration=20),
    ],
)
def test_rejects_invalid_published_reference(
    tmp_path: Path, source: Path, reference: object
) -> None:
    _write_json(source / "task_plans", reference)
    with pytest.raises(ValueError, match="task_plans"):
        cases.load_case(tmp_path, "4/1")


@pytest.mark.parametrize(
    "raw", [b"{", b'{"meeting": {}, "meeting": {}}', b"NaN", b"\xff"]
)
def test_rejects_malformed_source_json(
    tmp_path: Path, source: Path, raw: bytes
) -> None:
    (source / "task_plans").write_bytes(raw)
    with pytest.raises(ValueError, match="invalid source JSON"):
        cases.load_case(tmp_path, "4/1")
