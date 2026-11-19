import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interactive_verify as verifier  # noqa: E402

ROLES = ("m1", "m2", "m3", "m4", "m5")


def _digest(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _case(free, *, horizon=20, slots_per_day=None, duration=1, gold=()):
    return {
        "schema": "seatlab.calendar-case/v1",
        "case_id": "2/9",
        "horizon": horizon,
        "slots_per_day": horizon if slots_per_day is None else slots_per_day,
        "duration": duration,
        "members": {
            role: sorted(set(range(horizon)).difference(free[role])) for role in ROLES
        },
        "gold": list(gold),
    }


def _scalar_row(number, bound, replies):
    query = {"round": number, "bound": bound}
    bundle = {**query, "replies": dict(zip(ROLES, replies, strict=True))}
    return {**bundle, "query_sha256": _digest(query), "bundle_sha256": _digest(bundle)}


def _suffix_row(number, bound, masks, *, encoding="suffix-hex"):
    query = {"round": number, "bound": bound, "encoding": encoding}
    bundle = {**query, "replies": dict(zip(ROLES, masks, strict=True))}
    return {**bundle, "query_sha256": _digest(query), "bundle_sha256": _digest(bundle)}


def _world(case, rounds, choice, *, mode="hybrid8", view_mode=None):
    vmode = mode if view_mode is None else view_mode
    return {
        "schema": "seatlab.calendar-interactive-world/v1",
        "case_id": case["case_id"],
        "mode": mode,
        "initial_view_sha256": {
            role: _digest(
                {
                    "role": role,
                    "mode": vmode,
                    "horizon": case["horizon"],
                    "slots_per_day": case["slots_per_day"],
                    "duration": case["duration"],
                    "busy": case["members"][role],
                }
            )
            for role in ROLES
        },
        "rounds": rounds,
        "choice": choice,
        "choice_view_sha256": rounds[-1]["bundle_sha256"] if rounds else None,
        "acknowledged": True,
    }


def _nine_round_case_and_world(*, common_tick=13, horizon=20, gold=None):
    """Build a case that disagrees for 8 scalar rounds (bounds 0..7 -> bound 8)
    and resolves in the round-9 suffix exchange."""
    # Alternating availability on ticks 0..7 forces 1-tick bound increments per round:
    # r1 bound=0: (0,1,0,0,0) -> next bound 1
    # r2 bound=1: (2,1,1,1,1) -> next bound 2
    # ...
    # r8 bound=7: (8,7,7,7,7) -> next bound 8
    # At bound 8, suffix has horizon-8 = 12 bits (3 hex digits).
    m1_free = [0, 2, 4, 6, 8, 10]
    m2_free = [1, 3, 5, 7, 9, 11]
    m3_free = list(range(8)) + [10]
    m4_free = list(range(8)) + [11]
    m5_free = list(range(8)) + [12]
    if common_tick is not None:
        for seat in (m1_free, m2_free, m3_free, m4_free, m5_free):
            seat.append(common_tick)
        # Also include a later common tick to verify earliest-common selection.
        if common_tick + 3 < horizon:
            for seat in (m1_free, m2_free, m3_free, m4_free, m5_free):
                seat.append(common_tick + 3)
    free = {
        "m1": sorted(set(m1_free)),
        "m2": sorted(set(m2_free)),
        "m3": sorted(set(m3_free)),
        "m4": sorted(set(m4_free)),
        "m5": sorted(set(m5_free)),
    }
    case = _case(
        free,
        horizon=horizon,
        gold=[] if gold is None else list(gold),
    )
    rounds = [
        _scalar_row(
            tick + 1,
            tick,
            (tick + tick % 2, tick + (tick + 1) % 2, tick, tick, tick),
        )
        for tick in range(8)
    ]
    bound = 8
    width = (horizon - bound + 3) // 4
    masks = []
    for role in ROLES:
        bits = 0
        for start in free[role]:
            if start >= bound:
                bits |= 1 << (start - bound)
        masks.append(f"{bits:0{width}x}")
    rounds.append(_suffix_row(9, bound, masks))
    return case, _world(case, rounds, common_tick)


def test_hybrid8_resolves_in_round_9_suffix_with_exact_resources():
    # horizon=20, bound=8 => 12 bit positions, width=3 hex digits.
    # common ticks: 13 (bit 5 -> 0x020) and 16 (bit 8 -> 0x100).
    # m1 >=8: {8,10,13,16} -> bits {0,2,5,8} -> 0x125
    # m2 >=8: {9,11,13,16} -> bits {1,3,5,8} -> 0x12a
    # m3 >=8: {10,13,16}   -> bits {2,5,8}   -> 0x124
    # m4 >=8: {11,13,16}   -> bits {3,5,8}   -> 0x128
    # m5 >=8: {12,13,16}   -> bits {4,5,8}   -> 0x130
    case, world = _nine_round_case_and_world(common_tick=13, horizon=20, gold=[13])
    assert world["rounds"][-1]["replies"] == {
        "m1": "125",
        "m2": "12a",
        "m3": "124",
        "m4": "128",
        "m5": "130",
    }

    score = verifier.verify(case, world)
    # Scalar rounds 1..8: bounds 0..7 (each 1 byte) => 8 * 5 * 1 = 40 request bytes.
    # Round 9 request: compact ["suffix-hex",8] = 16 bytes * 5 = 80 request bytes. Total = 120.
    # Scalar responses: 40 single-digit ints (0..8) => 40 bytes.
    # Round 9 responses: 5 quoted 3-char hex strings (each 5 bytes) => 25 bytes. Total = 65.
    # Distinct scalar disclosures:
    #   m1: 0,2,4,6,8 (5); m2: 1,3,5,7 (4); m3,m4,m5: 0..7 (8 each) => 33.
    assert score == {
        "case_id": "2/9",
        "mode": "hybrid8",
        "choice": 13,
        "official_exact_match": True,
        "physically_feasible": True,
        "reward": 1,
        "resources": {
            "round_trips": 9,
            "seat_exchanges": 45,
            "scalar_disclosures": 40,
            "distinct_disclosed_starts": 33,
            "request_payload_bytes": 120,
            "response_payload_bytes": 65,
            "payload_bytes": 185,
            "suffix_exchanges": 1,
            "suffix_masks": 5,
            "suffix_bit_positions": 60,
        },
    }

    # Gold mismatch still verifies and returns reward 0 with feasible=True.
    case["gold"] = [16]
    miss = verifier.verify(case, world)
    assert miss["official_exact_match"] is False
    assert miss["physically_feasible"] is True
    assert miss["reward"] == 0


def test_hybrid8_leading_zeros_and_null_suffix_intersection():
    # No common start at or above bound 8; horizon=21 => 13 bit positions => 4 hex digits
    # with top 3 padding bits required to be 0 and leading zeros required.
    case, world = _nine_round_case_and_world(common_tick=None, horizon=21, gold=[13])
    # m1 >=8: {8,10} -> bits {0,2} -> "0005" (leading zeros mandatory).
    assert world["rounds"][-1]["replies"]["m1"] == "0005"
    score = verifier.verify(case, world)
    assert score["choice"] is None
    assert score["official_exact_match"] is False
    assert score["physically_feasible"] is None
    assert score["reward"] == 0
    assert score["resources"]["suffix_exchanges"] == 1
    assert score["resources"]["suffix_masks"] == 5
    # 5 * (21 - 8) = 65 transmitted bit positions (not 5 * 16 padded hex bits).
    assert score["resources"]["suffix_bit_positions"] == 65
    # Each quoted 4-char hex mask is 6 bytes -> 30 response bytes in round 9 (+ 40 scalar = 70).
    assert score["resources"]["response_payload_bytes"] == 70


def test_hybrid8_early_termination_within_eight_scalar_rounds():
    case = _case(
        {
            "m1": [0, 3, 10],
            "m2": [1, 7, 10],
            "m3": [2, 6, 10],
            "m4": [3, 10],
            "m5": [4, 8, 10],
        },
        horizon=16,
        gold=[10],
    )
    world = _world(
        case,
        [
            _scalar_row(1, 0, (0, 1, 2, 3, 4)),
            _scalar_row(2, 4, (10, 7, 6, 10, 4)),
            _scalar_row(3, 10, (10,) * 5),
        ],
        10,
        mode="hybrid8",
    )
    score = verifier.verify(case, world, expected_mode="hybrid8")
    assert score == {
        "case_id": "2/9",
        "mode": "hybrid8",
        "choice": 10,
        "official_exact_match": True,
        "physically_feasible": True,
        "reward": 1,
        "resources": {
            "round_trips": 3,
            "seat_exchanges": 15,
            "scalar_disclosures": 15,
            "distinct_disclosed_starts": 12,
            "request_payload_bytes": 20,
            "response_payload_bytes": 22,
            "payload_bytes": 42,
            "suffix_exchanges": 0,
            "suffix_masks": 0,
            "suffix_bit_positions": 0,
        },
    }

    # Cursor mode on the same transcript keeps only the 7 frozen keys (no suffix_* keys).
    cursor_world = _world(case, world["rounds"], 10, mode="cursor")
    cursor_score = verifier.verify(case, cursor_world, expected_mode="cursor")
    assert set(cursor_score["resources"]) == {
        "round_trips",
        "seat_exchanges",
        "scalar_disclosures",
        "distinct_disclosed_starts",
        "request_payload_bytes",
        "response_payload_bytes",
        "payload_bytes",
    }


def test_hybrid8_resolves_on_round_8_without_entering_suffix():
    # Disagreement on rounds 1..7, unanimous on round 8 at tick 7.
    free = {
        "m1": [0, 2, 4, 6, 7],
        "m2": [1, 3, 5, 7],
        "m3": list(range(8)),
        "m4": list(range(8)),
        "m5": list(range(8)),
    }
    case = _case(free, horizon=16, gold=[7])
    rounds = [
        _scalar_row(
            tick + 1,
            tick,
            (tick + tick % 2, tick + (tick + 1) % 2, tick, tick, tick),
        )
        for tick in range(7)
    ]
    rounds.append(_scalar_row(8, 7, (7,) * 5))
    world = _world(case, rounds, 7, mode="hybrid8")
    score = verifier.verify(case, world)
    assert score["choice"] == 7
    assert score["reward"] == 1
    assert score["resources"]["round_trips"] == 8
    assert score["resources"]["suffix_exchanges"] == 0
    assert score["resources"]["suffix_masks"] == 0
    assert score["resources"]["suffix_bit_positions"] == 0

    # Appending a round-9 suffix after round-8 unanimity must be rejected.
    extra = _world(
        case,
        [*rounds, _suffix_row(9, 7, ("001",) * 5)],
        7,
        mode="hybrid8",
    )
    with pytest.raises(ValueError, match="continue after a terminal reply set"):
        verifier.verify(case, extra)


def test_hybrid8_suffix_respects_duration_and_day_boundaries():
    # horizon=40, slots_per_day=20, duration=2.
    # Day 0 valid starts: 0..18 (tick 19 crosses day boundary, so NOT feasible even if 19 & 20 are free).
    # Day 1 valid starts: 20..38 (tick 23 needs 24 which is busy, so 22 is the last start on day 1).
    # Alternating busy blocks step bounds 0 -> 2 -> 4 -> 6 -> 8 -> 10 -> 12 -> 14 -> 16 across 8 rounds.
    # At bound=16: ticks 19 and 20 are free for all seats, tick 21 is busy for m4 (disqualifying start 20),
    # ticks 16..18 are busy for m3, and ticks 22..23 are free for all seats -> earliest common start is 22.
    horizon = 40
    slots_per_day = 20
    members = {
        "m1": [0, 1, 4, 5, 8, 9, 12, 13] + list(range(24, 40)),
        "m2": [2, 3, 6, 7, 10, 11, 14, 15] + list(range(24, 40)),
        "m3": [16, 17, 18] + list(range(24, 40)),
        "m4": [21] + list(range(24, 40)),
        "m5": list(range(24, 40)),
    }
    case = {
        "schema": "seatlab.calendar-case/v1",
        "case_id": "2/10",
        "horizon": horizon,
        "slots_per_day": slots_per_day,
        "duration": 2,
        "members": members,
        "gold": [19, 22],
    }
    rounds = [
        _scalar_row(1, 0, (2, 0, 0, 0, 0)),
        _scalar_row(2, 2, (2, 4, 2, 2, 2)),
        _scalar_row(3, 4, (6, 4, 4, 4, 4)),
        _scalar_row(4, 6, (6, 8, 6, 6, 6)),
        _scalar_row(5, 8, (10, 8, 8, 8, 8)),
        _scalar_row(6, 10, (10, 12, 10, 10, 10)),
        _scalar_row(7, 12, (14, 12, 12, 12, 12)),
        _scalar_row(8, 14, (14, 16, 14, 14, 14)),
    ]
    # Bound 16, horizon 40 => 24 bits => 6 hex digits.
    # m1, m2, m5: 16,17,18, 20,21,22 -> bits {0,1,2, 4,5,6} -> 0x000077
    # m3 (busy 16,17,18): 20,21,22 -> bits {4,5,6} -> 0x000070
    # m4 (busy 21): 16,17,18, 22 -> bits {0,1,2, 6} -> 0x000047
    honest_masks = ("000077", "000077", "000070", "000047", "000077")
    world = _world(case, [*rounds, _suffix_row(9, 16, honest_masks)], 22, mode="hybrid8")
    score = verifier.verify(case, world)
    assert score["choice"] == 22
    assert score["physically_feasible"] is True
    assert score["reward"] == 1
    assert score["resources"]["suffix_bit_positions"] == 5 * (40 - 16)

    # Forged masks that illegally include cross-day start 19 (bit 3 => +0x8) must fail.
    forged_masks = ("00007f", "00007f", "000078", "00004f", "00007f")
    forged_world = _world(
        case, [*rounds, _suffix_row(9, 16, forged_masks)], 19, mode="hybrid8"
    )
    with pytest.raises(ValueError, match="wrong suffix mask"):
        verifier.verify(case, forged_world)


def test_rejects_scalar_continuity_and_scalar_lies_in_hybrid8():
    case, world = _nine_round_case_and_world(common_tick=13, horizon=20, gold=[13])
    # Round 1 bound=0 gives (0,1,0,0,0) -> next bound=1; m2 already disclosed 1,
    # so reporting 3 instead of 1 in round 2 (even with matching digests) must fail.
    mutated_rounds = list(world["rounds"])
    mutated_rounds[1] = _scalar_row(2, 1, (2, 3, 1, 1, 1))
    with pytest.raises(ValueError, match="wrong next start from m2 in round 2"):
        verifier.verify(case, _world(case, mutated_rounds, 13))

    # Skipping a scalar bound jump (bound=2 instead of max(r1 replies)=1) must fail.
    bad_jump = list(world["rounds"])
    bad_jump[1] = _scalar_row(2, 2, (2, 3, 2, 2, 2))
    with pytest.raises(ValueError, match="unexpected bound in native round 2"):
        verifier.verify(case, _world(case, bad_jump, 13))

    # Lying in round 8 (m1 reports 10 instead of 8) alters the round-9 bound and must fail.
    bad_r8 = list(world["rounds"])
    bad_r8[7] = _scalar_row(8, 7, (10, 7, 7, 7, 7))
    with pytest.raises(ValueError, match="wrong next start from m1 in round 8"):
        verifier.verify(case, _world(case, bad_r8, 13))


def test_rejects_suffix_mask_padding_width_and_case_violations():
    # horizon=21, bound=8 => 13 valid bits => width=4 hex digits; top 3 bits (bits 13..15) must be 0.
    case, world = _nine_round_case_and_world(common_tick=13, horizon=21, gold=[13])
    honest_row = world["rounds"][-1]
    honest_m1 = honest_row["replies"]["m1"]  # e.g. "0125"
    assert len(honest_m1) == 4 and honest_m1.startswith("0")

    bad_masks = [
        honest_m1.lstrip("0"),  # stripped leading zero
        "0" + honest_m1,  # too wide
        "0x" + honest_m1,  # hex prefix
        " " + honest_m1[1:],  # whitespace
        # Non-zero high padding bit (bit 13 = 0x2000) with matching digests:
        f"{(int(honest_m1, 16) | (1 << 13)):04x}",
        # Non-zero high padding bit (bit 15 = 0x8000):
        f"{(int(honest_m1, 16) | (1 << 15)):04x}",
        int(honest_m1, 16),  # integer instead of hex string
        None,  # null instead of hex string
    ]
    # Also ensure uppercase hex letters are explicitly tested on m2 ("012a" -> "012A").
    honest_m2 = honest_row["replies"]["m2"]
    assert any(c in "abcdef" for c in honest_m2)
    upper_world = _world(
        case,
        [
            *world["rounds"][:-1],
            _suffix_row(
                9,
                8,
                tuple(
                    honest_m2.upper() if r == "m2" else honest_row["replies"][r]
                    for r in ROLES
                ),
            ),
        ],
        13,
    )
    with pytest.raises(ValueError, match="invalid suffix mask from m2"):
        verifier.verify(case, upper_world)

    for bad in bad_masks:
        mutated = _world(
            case,
            [
                *world["rounds"][:-1],
                _suffix_row(
                    9,
                    8,
                    tuple(
                        bad if r == "m1" else honest_row["replies"][r] for r in ROLES
                    ),
                ),
            ],
            13,
        )
        with pytest.raises(ValueError, match="invalid suffix mask from m1"):
            verifier.verify(case, mutated)


def test_rejects_substantive_suffix_lies_even_with_valid_digests():
    case, world = _nine_round_case_and_world(common_tick=13, horizon=20, gold=[13])
    honest = world["rounds"][-1]["replies"]

    # 1. m3 omits a genuinely feasible start (13 -> bit 5) and everyone picks 16 instead.
    omitted_m3 = f"{(int(honest['m3'], 16) ^ (1 << 5)):03x}"
    omitted_world = _world(
        case,
        [
            *world["rounds"][:-1],
            _suffix_row(
                9, 8, tuple(omitted_m3 if r == "m3" else honest[r] for r in ROLES)
            ),
        ],
        16,
    )
    with pytest.raises(ValueError, match="wrong suffix mask from m3"):
        verifier.verify(case, omitted_world)

    # 2. m5 fabricates a busy start (tick 10 -> bit 2) even though choice remains 13.
    fabricated_m5 = f"{(int(honest['m5'], 16) | (1 << 2)):03x}"
    fabricated_world = _world(
        case,
        [
            *world["rounds"][:-1],
            _suffix_row(
                9, 8, tuple(fabricated_m5 if r == "m5" else honest[r] for r in ROLES)
            ),
        ],
        13,
    )
    with pytest.raises(ValueError, match="wrong suffix mask from m5"):
        verifier.verify(case, fabricated_world)

    # 3. Coordinator picks later common start (16) instead of smallest common bit (13).
    later_choice = {**world, "choice": 16}
    with pytest.raises(ValueError, match="choice does not match terminal replies"):
        verifier.verify(case, later_choice)

    # 4. Coordinator picks null when common bits are non-empty.
    null_choice = {**world, "choice": None}
    with pytest.raises(ValueError, match="choice does not match terminal replies"):
        verifier.verify(case, null_choice)


def test_rejects_digest_encoding_bound_and_terminal_rule_failures():
    case, world = _nine_round_case_and_world(common_tick=13, horizon=20, gold=[13])

    # Missing encoding field in round 9.
    no_enc_row = dict(world["rounds"][-1])
    no_enc_row.pop("encoding")
    with pytest.raises(ValueError, match="invalid native round 9"):
        verifier.verify(case, {**world, "rounds": [*world["rounds"][:-1], no_enc_row]})

    # Wrong encoding tag in round 9 (even with matching digests).
    bad_enc = _world(
        case,
        [
            *world["rounds"][:-1],
            _suffix_row(
                9,
                8,
                tuple(world["rounds"][-1]["replies"][r] for r in ROLES),
                encoding="suffix-bin",
            ),
        ],
        13,
    )
    with pytest.raises(ValueError, match="invalid suffix encoding"):
        verifier.verify(case, bad_enc)

    # Extra encoding field on scalar round 8 must be rejected (scalar wire untouched).
    extra_enc_r8 = {**world["rounds"][7], "encoding": "scalar"}
    with pytest.raises(ValueError, match="invalid native round 8"):
        verifier.verify(
            case,
            {
                **world,
                "rounds": [*world["rounds"][:7], extra_enc_r8, world["rounds"][8]],
            },
        )

    # Wrong bound in round 9 (e.g. 9 instead of max(r8 replies)=8) with recomputed masks & digests.
    bad_bound_masks = []
    for role in ROLES:
        busy = set(case["members"][role])
        bits = 0
        for start in range(9, 20):
            if start not in busy:
                bits |= 1 << (start - 9)
        bad_bound_masks.append(f"{bits:03x}")
    bad_bound = _world(
        case, [*world["rounds"][:-1], _suffix_row(9, 9, bad_bound_masks)], 13
    )
    with pytest.raises(ValueError, match="unexpected bound in native round 9"):
        verifier.verify(case, bad_bound)

    # Round 9 query_sha256 omits encoding (uses cursor query digest instead of suffix query digest).
    r9_bad_q = dict(world["rounds"][-1])
    r9_bad_q["query_sha256"] = _digest({"round": 9, "bound": 8})
    with pytest.raises(ValueError, match="query digest mismatch in round 9"):
        verifier.verify(case, {**world, "rounds": [*world["rounds"][:-1], r9_bad_q]})

    # Round 9 bundle_sha256 omits encoding.
    r9_bad_b = dict(world["rounds"][-1])
    r9_bad_b["bundle_sha256"] = _digest(
        {"round": 9, "bound": 8, "replies": r9_bad_b["replies"]}
    )
    with pytest.raises(ValueError, match="round bundle digest mismatch in round 9"):
        verifier.verify(
            case,
            {
                **world,
                "rounds": [*world["rounds"][:-1], r9_bad_b],
                "choice_view_sha256": r9_bad_b["bundle_sha256"],
            },
        )

    # choice_view_sha256 points to round 8 bundle instead of terminal round 9 bundle.
    stale_choice_ack = {
        **world,
        "choice_view_sha256": world["rounds"][7]["bundle_sha256"],
    }
    with pytest.raises(ValueError, match="choice acknowledgment"):
        verifier.verify(case, stale_choice_ack)

    # Stopping after 8 non-terminal scalar rounds without round 9 suffix.
    premature = _world(case, world["rounds"][:8], None)
    with pytest.raises(ValueError, match="precedes a terminal reply set"):
        verifier.verify(case, premature)

    # More than 9 rounds in hybrid8.
    too_many = _world(
        case, [*world["rounds"], _suffix_row(10, 9, ("000",) * 5)], 13
    )
    with pytest.raises(ValueError, match="invalid number of native rounds"):
        verifier.verify(case, too_many)

    # Initial view digest computed with mode="cursor" instead of mode="hybrid8".
    wrong_view_mode = _world(case, world["rounds"], 13, mode="hybrid8", view_mode="cursor")
    with pytest.raises(ValueError, match="initial acknowledgments"):
        verifier.verify(case, wrong_view_mode)

    # Cursor mode must reject a round-9 suffix row.
    cursor_with_suffix = _world(case, world["rounds"], 13, mode="cursor")
    with pytest.raises(ValueError, match="invalid native round 9"):
        verifier.verify(case, cursor_with_suffix)

    # expected_mode cross-check rejects mode mismatch.
    with pytest.raises(ValueError, match="completion record"):
        verifier.verify(case, world, expected_mode="cursor")


def test_main_enforces_case_mode_and_scores_hybrid8(tmp_path, monkeypatch):
    case, world = _nine_round_case_and_world(common_tick=13, horizon=20, gold=[13])
    private_case = tmp_path / "case.json"
    artifact = tmp_path / "world.calendar.json"
    reward = tmp_path / "verifier" / "reward.txt"
    monkeypatch.setattr(verifier, "CASE_PATH", private_case)
    monkeypatch.setattr(verifier, "WORLD_PATH", artifact)
    monkeypatch.setattr(verifier, "REWARD_PATH", reward)

    # Mode mismatch between case.json ("cursor") and world artifact ("hybrid8") must fail.
    private_case.write_text(json.dumps({"mode": "cursor", "case": case}))
    artifact.write_text(json.dumps(world))
    with pytest.raises(SystemExit, match="invalid interactive calendar evidence"):
        verifier.main()
    assert not reward.exists()

    # Matching hybrid8 mode verifies and writes reward 1.
    private_case.write_text(json.dumps({"mode": "hybrid8", "case": case}))
    verifier.main()
    assert reward.read_text() == "1\n"

    # Null choice in hybrid8 verifies and writes reward 0.
    null_case, null_world = _nine_round_case_and_world(
        common_tick=None, horizon=21, gold=[13]
    )
    private_case.write_text(json.dumps({"mode": "hybrid8", "case": null_case}))
    artifact.write_text(json.dumps(null_world))
    verifier.main()
    assert reward.read_text() == "0\n"
