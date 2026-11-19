import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from encoding import (  # noqa: E402
    ROLES,
    _new_tally,
    _tally,
    analyze_case,
    decode_bitmap,
    decode_full,
    decode_runs,
    encode_bitmap,
    encode_runs,
)


def test_bitmap_orders_bits_across_bytes_and_pads_partial_byte() -> None:
    assert encode_bitmap([0, 7, 8], 9) == "gYA="  # bytes 0x81 and 0x80
    assert decode_bitmap("gYA=", 9) == [0, 7, 8]
    assert encode_bitmap([], 9) == "AAA="
    assert decode_bitmap("AAA=", 9) == []
    assert encode_bitmap(list(range(16)), 16) == "//8="
    assert decode_bitmap("//8=", 16) == list(range(16))


@pytest.mark.parametrize(
    "body",
    [
        "gYA",  # missing base64 padding
        "gYA==",  # excessive base64 padding
        "gYB=",  # nonzero base64 residual bits, not a canonical spelling
        "gf8=",  # canonical base64, nonzero horizon padding bits
        "gQ==",  # one decoded byte when two are required
        "gYA\n",  # whitespace is not a valid part of the body
        "__8=",  # URL-safe alphabet instead of standard alphabet
        "/tmp/gYA=",
        "",
        ["gYA="],
    ],
)
def test_bitmap_rejects_malformed_payloads(body: object) -> None:
    with pytest.raises(ValueError, match="bitmap"):
        decode_bitmap(body, 9)


def test_runs_use_maximal_inclusive_ranges_and_encode_empty() -> None:
    assert encode_runs([0, 1, 2, 4, 7, 8], 9) == [[0, 2], [4, 4], [7, 8]]
    assert decode_runs([[0, 2], [4, 4], [7, 8]], 9) == [0, 1, 2, 4, 7, 8]
    assert encode_runs([], 9) == []
    assert decode_runs([], 9) == []


@pytest.mark.parametrize(
    "body",
    [
        [[0, 1], [2, 3]],  # adjacent, not maximal
        [[0, 3], [2, 4]],  # overlapping
        [[3, 3], [3, 3]],  # duplicate
        [[4, 4], [1, 2]],  # out of order
        [[2, 1]],
        [[-1, 1]],
        [[0, 9]],
        [[0, True]],
        [[1, "2"]],
        [[1, 2, 3]],
        [(1, 2)],
        "/tmp/runs",
    ],
)
def test_runs_reject_malformed_ranges(body: object) -> None:
    with pytest.raises(ValueError, match="run"):
        decode_runs(body, 9)


@pytest.mark.parametrize("starts", [[1, 1], [2, 1], [-1], [9], [True], [1.5], ["1"]])
@pytest.mark.parametrize("encode", [encode_bitmap, encode_runs, decode_full])
def test_encoders_and_plain_decoder_reject_malformed_ticks(
    starts: list[int], encode: Callable[[Sequence[int], int], object]
) -> None:
    with pytest.raises(ValueError, match="strictly increasing integer ticks"):
        encode(starts, 9)


def test_exact_round_trips_for_every_small_set() -> None:
    for horizon in (1, 7, 8, 9):
        for mask in range(1 << horizon):
            ticks = [tick for tick in range(horizon) if mask & (1 << tick)]
            assert decode_bitmap(encode_bitmap(ticks, horizon), horizon) == ticks
            assert decode_runs(encode_runs(ticks, horizon), horizon) == ticks


def _case(
    last_busy: list[int], *, gold: list[int], all_busy: bool = False
) -> dict[str, Any]:
    first_four_busy = list(range(9)) if all_busy else [1, 2, 3, 4, 5, 6]
    return {
        "case_id": "2/1",
        "dataset_commit": "synthetic",
        "duration": 1,
        "horizon": 9,
        "slots_per_day": 9,
        "members": {**dict.fromkeys(ROLES[:-1], first_four_busy), "m5": last_busy},
        "gold": gold,
    }


@pytest.mark.parametrize("gold, expected_score", [([7], 1), ([8], 0)])
def test_case_compares_the_exact_messages_from_all_five_seats(
    gold: list[int], expected_score: int
) -> None:
    row = analyze_case(_case(list(range(7)), gold=gold))
    assert row["seat_count"] == 5
    assert row["all_static_decodes_exact"] is True
    assert row["earliest_agreement"] is True
    assert row["reference_score_agreement"] is True
    assert row["smallest_payload_modes"] == ["cursor"]
    assert {
        mode: result["start"] for mode, result in row["modes"].items()
    } == dict.fromkeys(("full", "runs", "bitmap", "cursor"), 7)
    for mode, expected_bytes in {"full": 33, "runs": 59, "bitmap": 30}.items():
        assert row["modes"][mode] == {
            "start": 7,
            "reference_score": expected_score,
            "exact_members": 5,
            "round_trips": 1,
            "request_payload_bytes": 0,
            "response_payload_bytes": expected_bytes,
            "payload_bytes": expected_bytes,
        }
    assert row["modes"]["cursor"] == {
        "start": 7,
        "reference_score": expected_score,
        "exact_members": None,
        "round_trips": 2,
        "request_payload_bytes": 10,
        "response_payload_bytes": 10,
        "payload_bytes": 20,
    }


def test_empty_case_counts_each_tied_arm_without_a_sole_win() -> None:
    empty = analyze_case(_case(list(range(9)), gold=[], all_busy=True))
    assert empty["all_static_decodes_exact"] is True
    assert empty["earliest_agreement"] is True
    assert empty["smallest_payload_modes"] == ["full", "runs"]
    assert all(mode["start"] is None for mode in empty["modes"].values())
    assert empty["modes"]["cursor"]["payload_bytes"] == 25

    sole = analyze_case(_case(list(range(7)), gold=[7]))
    group = _new_tally()
    for record in (empty, sole):
        _tally(group, record)
    assert group["case_count"] == 2
    assert group["tie_cases"] == 1
    assert group["modes"]["full"]["sole_wins"] == 0
    assert group["modes"]["full"]["tied_wins"] == 1
    assert group["modes"]["runs"]["sole_wins"] == 0
    assert group["modes"]["runs"]["tied_wins"] == 1
    assert group["modes"]["cursor"]["sole_wins"] == 1
    assert group["modes"]["cursor"]["tied_wins"] == 0
    assert group["modes"]["cursor"]["total_payload_bytes"] == 45
    assert group["modes"]["full"]["exact_member_decodes"] == 10
