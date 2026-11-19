"""Tests for offline hybrid_cost.py protocol accounting and boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

import hybrid_cost  # noqa: E402
from cases import DATASET_COMMIT, member_starts  # noqa: E402


def _make_case(
    *,
    case_id: str = "2/1",
    horizon: int = 120,
    slots_per_day: int = 24,
    duration: int = 2,
    free_starts_by_role: dict[str, list[int]] | None = None,
    busy_by_role: dict[str, list[int]] | None = None,
    gold: list[int] | None = None,
) -> dict[str, Any]:
    if busy_by_role is None:
        assert free_starts_by_role is not None
        busy_by_role = {}
        for role in hybrid_cost.ROLES:
            free_ticks: set[int] = set()
            for start in free_starts_by_role[role]:
                assert start // slots_per_day == (start + duration - 1) // slots_per_day
                for offset in range(duration):
                    free_ticks.add(start + offset)
            busy_by_role[role] = sorted(set(range(horizon)) - free_ticks)
            assert (
                member_starts(
                    busy_by_role[role],
                    duration=duration,
                    horizon=horizon,
                    slots_per_day=slots_per_day,
                )
                == sorted(set(free_starts_by_role[role]))
            )
    return {
        "schema": "seatlab.calendar-case/v1",
        "case_id": case_id,
        "dataset_commit": DATASET_COMMIT,
        "duration": duration,
        "horizon": horizon,
        "slots_per_day": slots_per_day,
        "members": busy_by_role,
        "gold": [] if gold is None else list(gold),
        "source_sha256": {
            "Member1_final_schedule.json": "1" * 64,
            "Member2_final_schedule.json": "2" * 64,
            "Member3_final_schedule.json": "3" * 64,
            "Member4_final_schedule.json": "4" * 64,
            "Member5_final_schedule.json": "5" * 64,
            "task_plans": "6" * 64,
        },
    }


def _chain_starts_reaching_bound(
    *,
    rounds_before_suffix: int,
    step: int = 4,
    common_tail: list[int] | None = None,
    tail_by_role: dict[str, list[int]] | None = None,
) -> dict[str, list[int]]:
    """Build 5 monotone start lists that advance bound by exact ``step`` per round.

    Even-parity seats (m1, m2) receive even multiples ``2*j * step <= K * step``;
    odd-parity seats (m3, m4, m5) receive odd multiples ``(2*j + 1) * step <= K * step``,
    where ``K = rounds_before_suffix``.

    Exact scalar reply vectors for rounds r = 1..K (queried at bound = (r - 1) * step):
      - Odd r = 2j + 1 (bound = 2j * step):
          (2j*s, 2j*s, (2j+1)*s, (2j+1)*s, (2j+1)*s) -> max = r * step
      - Even r = 2j + 2 (bound = (2j + 1) * step):
          ((2j+2)*s, (2j+2)*s, (2j+1)*s, (2j+1)*s, (2j+1)*s) -> max = r * step

    Because the two parity sets are disjoint and every optional tail start satisfies
    ``tick >= K * step``, rounds 1..K never agree or exhaust early, and round K exits
    with ``next_bound == K * step``. Within each seat, consecutive chain starts differ
    by ``2 * step >= 6`` ticks, so duration=2 windows stay isolated.
    """
    assert rounds_before_suffix >= 1
    assert step >= 3
    target_bound = rounds_before_suffix * step
    even_chain = [i * step for i in range(0, rounds_before_suffix + 1, 2)]
    odd_chain = [i * step for i in range(1, rounds_before_suffix + 1, 2)]
    starts: dict[str, list[int]] = {
        "m1": list(even_chain),
        "m2": list(even_chain),
        "m3": list(odd_chain),
        "m4": list(odd_chain),
        "m5": list(odd_chain),
    }
    if common_tail:
        assert all(tick >= target_bound for tick in common_tail)
        for role in hybrid_cost.ROLES:
            starts[role].extend(common_tail)
    if tail_by_role:
        for role, extra in tail_by_role.items():
            assert all(tick >= target_bound for tick in extra)
            starts[role].extend(extra)
    return {role: sorted(set(values)) for role, values in starts.items()}


def test_zero_leading_hex_and_fixed_width_encoding_and_decoding() -> None:
    # Horizon 20, bound 3 -> 17 bits -> ceil(17 / 4) = 5 hex digits.
    assert hybrid_cost.suffix_hex_width(3, 20) == 5
    # Empty suffix produces all leading zeros.
    assert hybrid_cost.encode_suffix_hex([], 3, 20) == "00000"
    assert hybrid_cost.encode_suffix_hex([0, 1, 2], 3, 20) == "00000"
    # Single start at bound (bit 0) keeps 4 leading zeros.
    assert hybrid_cost.encode_suffix_hex([3], 3, 20) == "00001"
    # Single start at bound+1 (bit 1) keeps 4 leading zeros.
    assert hybrid_cost.encode_suffix_hex([4], 3, 20) == "00002"
    # Highest legal bit is horizon-1 = 19 (bit 16 -> 0x10000).
    assert hybrid_cost.encode_suffix_hex([19], 3, 20) == "10000"
    assert hybrid_cost.decode_suffix_hex("10000", 3, 20) == 1 << 16

    # Nonzero high padding bit (bit 17 -> 0x20000) is rejected.
    with pytest.raises(ValueError, match="nonzero high padding bits"):
        hybrid_cost.decode_suffix_hex("20000", 3, 20)
    # Uppercase or wrong width rejected.
    with pytest.raises(ValueError, match="lowercase hex digits"):
        hybrid_cost.decode_suffix_hex("0000A", 3, 20)
    with pytest.raises(ValueError, match="lowercase hex digits"):
        hybrid_cost.decode_suffix_hex("0001", 3, 20)


def test_no_intersection_after_more_than_8_rounds() -> None:
    # 8 scalar rounds advance bound from 0 -> 4 -> 8 -> ... -> 32.
    # After bound 32, seats still have disjoint starts >= 32, so scalar cursor
    # continues beyond 8 rounds and eventually exhausts with None.
    horizon = 120
    starts = _chain_starts_reaching_bound(
        rounds_before_suffix=8,
        step=4,
        tail_by_role={
            "m1": [36, 48],
            "m2": [40, 52],
            "m3": [44],
            "m4": [48],
            "m5": [40, 56],
        },
    )
    # Note: verify none of the starts intersect.
    common = set(starts["m1"]).intersection(*(starts[r] for r in hybrid_cost.ROLES[1:]))
    assert common == set()

    cursor_arm = hybrid_cost.simulate_cursor(starts, horizon=horizon)
    cursor8_arm = hybrid_cost.simulate_cursor8(starts, horizon=horizon)
    hybrid8_arm = hybrid_cost.simulate_hybrid8(starts, horizon=horizon)
    bitset_arm = hybrid_cost.simulate_bitset(starts, horizon=horizon)

    assert cursor_arm["choice"] is None
    # Round 9 (bound 32): (32, 32, 44, 48, 40) -> 48; round 10 (bound 48): m3 is None.
    assert cursor_arm["round_trips"] == 10
    assert cursor_arm["suffix_exchanges"] == 0
    assert cursor_arm["suffix_masks"] == 0
    assert cursor_arm["suffix_bit_positions"] == 0

    assert cursor8_arm["choice"] is None
    assert cursor8_arm["round_trips"] == 8
    assert cursor8_arm["suffix_exchanges"] == 0

    assert hybrid8_arm["choice"] is None
    assert hybrid8_arm["round_trips"] == 9
    assert hybrid8_arm["seat_exchanges"] == 45
    assert hybrid8_arm["suffix_exchanges"] == 1
    assert hybrid8_arm["suffix_masks"] == 5
    # Bound entering round 9 is 32, so transmitted positions = 5 * (120 - 32) = 440.
    assert hybrid8_arm["suffix_bit_positions"] == 5 * (horizon - 32)
    expected_suffix_req = 5 * len(json.dumps(["suffix-hex", 32], separators=(",", ":")))
    assert hybrid8_arm["request_bytes"] == cursor8_arm["request_bytes"] + expected_suffix_req
    expected_mask_len = hybrid_cost.suffix_hex_width(32, horizon)
    assert hybrid8_arm["response_bytes"] == cursor8_arm["response_bytes"] + 5 * (
        expected_mask_len + 2
    )

    assert bitset_arm["choice"] is None
    assert bitset_arm["round_trips"] == 1
    assert bitset_arm["seat_exchanges"] == 5
    assert bitset_arm["scalar_disclosures"] == 0
    assert bitset_arm["suffix_exchanges"] == 1
    assert bitset_arm["suffix_masks"] == 5
    assert bitset_arm["suffix_bit_positions"] == 5 * horizon
    assert bitset_arm["request_bytes"] == 5 * len(
        json.dumps(["suffix-hex", 0], separators=(",", ":"))
    )
    assert bitset_arm["response_bytes"] == 5 * (
        hybrid_cost.suffix_hex_width(0, horizon) + 2
    )


def test_earliest_match_at_current_bound_vs_bound_plus_one() -> None:
    # After 8 scalar rounds with step=4, round 8 produces max reply = 32.
    # Case A: earliest common start is exactly at bound 32 (bit 0 of suffix mask).
    # Case B: earliest common start is at bound + 1 = 33 (bit 1 of suffix mask).
    horizon = 120
    starts_at_bound = _chain_starts_reaching_bound(
        rounds_before_suffix=8, step=4, common_tail=[32, 40]
    )
    starts_at_bound_plus_1 = _chain_starts_reaching_bound(
        rounds_before_suffix=8, step=4, common_tail=[33, 40]
    )

    h_bound = hybrid_cost.simulate_hybrid8(starts_at_bound, horizon=horizon)
    c_bound = hybrid_cost.simulate_cursor(starts_at_bound, horizon=horizon)
    c8_bound = hybrid_cost.simulate_cursor8(starts_at_bound, horizon=horizon)
    b_bound = hybrid_cost.simulate_bitset(starts_at_bound, horizon=horizon)

    assert c_bound["choice"] == 32
    assert c_bound["round_trips"] == 9
    assert c8_bound["choice"] is None
    assert c8_bound["round_trips"] == 8
    assert h_bound["choice"] == 32
    assert h_bound["round_trips"] == 9
    assert h_bound["suffix_exchanges"] == 1
    assert h_bound["suffix_bit_positions"] == 5 * (horizon - 32)
    assert b_bound["choice"] == 32

    h_plus1 = hybrid_cost.simulate_hybrid8(starts_at_bound_plus_1, horizon=horizon)
    c_plus1 = hybrid_cost.simulate_cursor(starts_at_bound_plus_1, horizon=horizon)
    c8_plus1 = hybrid_cost.simulate_cursor8(starts_at_bound_plus_1, horizon=horizon)
    b_plus1 = hybrid_cost.simulate_bitset(starts_at_bound_plus_1, horizon=horizon)

    assert c_plus1["choice"] == 33
    # In round 9 at bound 32, m1/m2 reply 32 while m3..m5 reply 33, so cursor needs round 10.
    assert c_plus1["round_trips"] == 10
    assert c8_plus1["choice"] is None
    assert c8_plus1["round_trips"] == 8
    assert h_plus1["choice"] == 33
    assert h_plus1["round_trips"] == 9
    assert h_plus1["suffix_exchanges"] == 1
    assert h_plus1["suffix_bit_positions"] == 5 * (horizon - 32)
    assert b_plus1["choice"] == 33

    # Verify the suffix masks themselves distinguish bit 0 (32 -> 0x...101) vs bit 1 (33 -> 0x...102).
    suffix_a = hybrid_cost._run_suffix_round(starts_at_bound, bound=32, horizon=horizon)
    suffix_b = hybrid_cost._run_suffix_round(
        starts_at_bound_plus_1, bound=32, horizon=horizon
    )
    assert suffix_a["masks"]["m1"] == "0" * 19 + "101"
    assert suffix_a["masks"]["m3"] == "0" * 19 + "101"
    assert suffix_b["masks"]["m1"] == "0" * 19 + "103"
    assert suffix_b["masks"]["m3"] == "0" * 19 + "102"


def test_day_boundaries_block_cross_day_starts_and_gold_never_steers_choice() -> None:
    # slots_per_day=10, horizon=20, duration=2:
    # Tick 9 and tick 10 are both free for everyone, so [9, 10] crosses day 0 -> day 1
    # and is NOT a feasible start. Tick 10 (start of day 1, ticks 10..11) IS feasible.
    # Deliberately put cross-day tick 9 in gold to verify gold is ignored during solving
    # and only used when computing official_exact_match.
    busy = {
        role: [t for t in range(20) if t not in (9, 10, 11)]
        for role in hybrid_cost.ROLES
    }
    case = _make_case(
        case_id="2/1",
        horizon=20,
        slots_per_day=10,
        duration=2,
        busy_by_role=busy,
        gold=[9],
    )
    assert member_starts(busy["m1"], duration=2, horizon=20, slots_per_day=10) == [10]

    row = hybrid_cost.analyze_case(case)
    assert row["exact_arms_agree"] is True
    for arm_name in hybrid_cost.ARMS:
        arm = row["arms"][arm_name]
        assert arm["choice"] == 10
        assert arm["physically_feasible"] is True
        # Gold contains only cross-day tick 9, so exact match is False even though
        # the protocol chose the physically feasible day-1 start 10.
        assert arm["official_exact_match"] is False

    # Now put 10 in gold and verify only official_exact_match changes, not choice or bytes.
    case_with_gold_10 = {**case, "gold": [10]}
    row2 = hybrid_cost.analyze_case(case_with_gold_10)
    for arm_name in hybrid_cost.ARMS:
        assert row2["arms"][arm_name]["choice"] == row["arms"][arm_name]["choice"]
        assert (
            row2["arms"][arm_name]["payload_bytes"]
            == row["arms"][arm_name]["payload_bytes"]
        )
        assert row2["arms"][arm_name]["official_exact_match"] is True


def test_case_counter_denominators_separate_null_from_feasible_and_exact() -> None:
    # Case 1: terminates in 1 round at tick 0 -> all 4 arms pick 0, feasible & gold match.
    c1 = _make_case(
        case_id="2/1",
        horizon=48,
        slots_per_day=24,
        duration=2,
        free_starts_by_role={r: [0] for r in hybrid_cost.ROLES},
        gold=[0],
    )
    # Case 2: needs 9 scalar rounds (bound reaches 32 after round 8), common start at 32 ->
    # cursor/hybrid8/bitset find 32, budget8 stops at round 8 with choice=None.
    starts_9 = _chain_starts_reaching_bound(
        rounds_before_suffix=8, step=4, common_tail=[32]
    )
    # Build busy ticks using slots_per_day=100, horizon=100 so no day boundary hits.
    c2 = _make_case(
        case_id="3/1",
        horizon=100,
        slots_per_day=100,
        duration=2,
        free_starts_by_role=starts_9,
        gold=[32],
    )
    # Case 3: no common start at all -> all 4 arms choose None.
    c3 = _make_case(
        case_id="4/1",
        horizon=48,
        slots_per_day=24,
        duration=2,
        free_starts_by_role={
            "m1": [0],
            "m2": [4],
            "m3": [8],
            "m4": [12],
            "m5": [16],
        },
        gold=[],
    )
    rows = [hybrid_cost.analyze_case(c) for c in (c1, c2, c3)]
    summary = hybrid_cost.summarize_cases(rows)
    assert rows[1]["arms"]["cursor"]["round_trips"] == 9
    assert rows[1]["arms"]["budget8"]["choice"] is None
    assert rows[1]["arms"]["hybrid8"]["suffix_exchanges"] == 1
    assert summary["case_count"] == 3
    assert summary["exact_arms_agree"] == {"passes": 3, "denominator": 3}

    # cursor, hybrid8, bitset: 2 non-null choices (c1, c2), 1 null (c3).
    for arm_name in ("cursor", "hybrid8", "bitset"):
        arm = summary["arms"][arm_name]
        assert arm["cases"] == 3
        assert arm["non_null_choices"] == 2
        assert arm["null_choices"] == 1
        assert arm["official_exact_match"] == {"passes": 2, "denominator": 3}
        assert arm["physically_feasible"] == {
            "passes": 2,
            "infeasible": 0,
            "denominator": 2,
        }

    # cursor8: only 1 non-null choice (c1); c2 timed out at 8 rounds, c3 exhausted.
    c8 = summary["arms"]["budget8"]
    assert c8["cases"] == 3
    assert c8["non_null_choices"] == 1
    assert c8["null_choices"] == 2
    assert c8["official_exact_match"] == {"passes": 1, "denominator": 3}
    assert c8["physically_feasible"] == {
        "passes": 1,
        "infeasible": 0,
        "denominator": 1,
    }


def _write_synthetic_dataset(root: Path, case_ids: list[str]) -> None:
    base = root / "Scheduling" / "meeting" / "minutes_10" / "5days"
    for case_id in case_ids:
        case_dir = base / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        for member_id in range(1, 6):
            schedule = {
                "start_time": 0,
                "end_time": 720,
                "total_days": 5,
                "time_unit": "minutes_10",
                "member_id": member_id,
                "activities": [],
            }
            (case_dir / f"Member{member_id}_final_schedule.json").write_text(
                json.dumps(schedule), encoding="utf-8"
            )
        plans = {
            "meeting": {
                "task_type": "meeting",
                "duration": 2,
                "solutions": [{"solution_id": 0, "start_time": 0, "end_time": 2}],
            }
        }
        (case_dir / "task_plans").write_text(json.dumps(plans), encoding="utf-8")


def test_cli_outputs_200_numeric_sorted_cases_and_disjoint_24_native_split(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "masbench"
    expected_ids = [
        f"{s}/{n}" for s, size in ((2, 40), (3, 60), (4, 60), (5, 40))
        for n in range(1, size + 1)
    ]
    _write_synthetic_dataset(dataset, expected_ids)

    out = tmp_path / "hybrid_cost.json"
    exit_code = hybrid_cost.main([str(dataset), "--out", str(out)])
    assert exit_code == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema"] == hybrid_cost.SCHEMA
    assert report["case_count"] == 200
    assert [row["case_id"] for row in report["cases"]] == expected_ids

    # Verify registered hashes exist on each case row.
    first_case = report["cases"][0]
    assert len(first_case["case_sha256"]) == 64
    assert len(first_case["source_sha256"]) == 6

    # Verify disjoint native split has cases 7..12 per stratum (24 cases total).
    expected_native = [f"{s}/{n}" for s in ("2", "3", "4", "5") for n in range(7, 13)]
    native_summary = report["summary"]["disjoint_native_7_to_12"]
    assert native_summary["case_ids"] == expected_native
    assert native_summary["case_count"] == 24
    assert list(native_summary["by_stratum"]) == ["2", "3", "4", "5"]
    for stratum in ("2", "3", "4", "5"):
        assert native_summary["by_stratum"][stratum]["case_count"] == 6

    assert report["summary"]["remaining_cases"]["case_count"] == 176
    assert (
        report["summary"]["all_cases"]["arms"]["bitset"]["sums"]["request_bytes"]
        == 200 * 5 * len(json.dumps(["suffix-hex", 0], separators=(",", ":")))
    )
    assert (
        report["summary"]["all_cases"]["arms"]["bitset"]["sums"]["suffix_bit_positions"]
        == 200 * 5 * 720
    )

    # Writing to an existing output path must fail rather than overwrite.
    with pytest.raises(SystemExit) as exc:
        hybrid_cost.main([str(dataset), "--out", str(out)])
    assert exc.value.code == 2


def test_analyze_dataset_rejects_non_200_case_count(tmp_path: Path) -> None:
    dataset = tmp_path / "small"
    _write_synthetic_dataset(dataset, ["2/1", "3/1"])
    with pytest.raises(ValueError, match="expected exactly 200 official cases"):
        hybrid_cost.analyze_dataset(dataset)


def test_hybrid8_stops_on_round_8_without_suffix_exchange() -> None:
    # Unanimous at round 8 (bound 28): hybrid8 and cursor8 match cursor with 0 suffix exchanges.
    horizon = 120
    starts_unanimous_8 = _chain_starts_reaching_bound(
        rounds_before_suffix=7, step=4, common_tail=[28]
    )
    c = hybrid_cost.simulate_cursor(starts_unanimous_8, horizon=horizon)
    c8 = hybrid_cost.simulate_cursor8(starts_unanimous_8, horizon=horizon)
    h8 = hybrid_cost.simulate_hybrid8(starts_unanimous_8, horizon=horizon)

    assert c["choice"] == 28
    assert c["round_trips"] == 8
    assert c8 == c
    assert h8 == c
    assert h8["suffix_exchanges"] == 0
    assert h8["suffix_masks"] == 0
    assert h8["suffix_bit_positions"] == 0

    # Exhaustion at round 8 (bound 28, m2 has no start >= 28):
    # Rounds 1..7 advance bound 0 -> 4 -> 8 -> 12 -> 16 -> 20 -> 24 -> 28,
    # with round 7 vector (24, 24, 28, 28, 28), then round 8 vector (28, None, 28, 28, 28)
    # exhausts m2 so hybrid8 stops with None and 0 suffix exchanges.
    starts_exhaust_8 = _chain_starts_reaching_bound(
        rounds_before_suffix=7, step=4, tail_by_role={"m1": [28]}
    )
    c_ex = hybrid_cost.simulate_cursor(starts_exhaust_8, horizon=horizon)
    c8_ex = hybrid_cost.simulate_cursor8(starts_exhaust_8, horizon=horizon)
    h8_ex = hybrid_cost.simulate_hybrid8(starts_exhaust_8, horizon=horizon)
    b_ex = hybrid_cost.simulate_bitset(starts_exhaust_8, horizon=horizon)

    assert c_ex["choice"] is None
    assert c_ex["round_trips"] == 8
    assert c8_ex == c_ex
    assert h8_ex == c_ex
    assert h8_ex["suffix_exchanges"] == 0
    assert h8_ex["suffix_masks"] == 0
    assert h8_ex["suffix_bit_positions"] == 0
    assert b_ex["choice"] is None
