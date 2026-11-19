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


def _case(free, *, horizon=16, slots_per_day=None, duration=1, gold=()):
    return {
        "schema": "seatlab.calendar-case/v1",
        "case_id": "2/1",
        "horizon": horizon,
        "slots_per_day": horizon if slots_per_day is None else slots_per_day,
        "duration": duration,
        "members": {
            role: sorted(set(range(horizon)).difference(free[role])) for role in ROLES
        },
        "gold": list(gold),
    }


def _row(number, bound, replies):
    query = {"round": number, "bound": bound}
    bundle = {**query, "replies": dict(zip(ROLES, replies, strict=True))}
    return {**bundle, "query_sha256": _digest(query), "bundle_sha256": _digest(bundle)}


def _world(case, rounds, choice):
    return {
        "schema": "seatlab.calendar-interactive-world/v1",
        "case_id": case["case_id"],
        "mode": "cursor",
        "initial_view_sha256": {
            role: _digest(
                {
                    "role": role,
                    "mode": "cursor",
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


@pytest.fixture
def disagreement():
    case = _case(
        {
            "m1": [0, 3, 10],
            "m2": [1, 7, 10],
            "m3": [2, 6, 10],
            "m4": [3, 10],
            "m5": [4, 8, 10],
        },
        gold=[10],
    )
    return case, _world(
        case,
        [
            _row(1, 0, (0, 1, 2, 3, 4)),
            _row(2, 4, (10, 7, 6, 10, 4)),
            _row(3, 10, (10,) * 5),
        ],
        10,
    )


def test_scores_five_way_disagreement_and_counts_repeated_disclosures(disagreement):
    case, world = disagreement
    assert verifier.verify(case, world) == {
        "case_id": "2/1",
        "mode": "cursor",
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
        },
    }

    case["gold"] = [9]
    result = verifier.verify(case, world)
    assert result["official_exact_match"] is False
    assert result["physically_feasible"] is True
    assert result["reward"] == 0


def test_first_round_exhaustion_is_a_completed_null_choice():
    free = dict.fromkeys(ROLES, [2])
    free["m1"] = []
    case = _case(free, gold=[2])
    world = _world(case, [_row(1, 0, (None, 2, 2, 2, 2))], None)
    score = verifier.verify(case, world)
    assert score["choice"] is None
    assert score["physically_feasible"] is None
    assert score["official_exact_match"] is False
    assert score["reward"] == 0
    assert score["resources"] == {
        "round_trips": 1,
        "seat_exchanges": 5,
        "scalar_disclosures": 4,
        "distinct_disclosed_starts": 4,
        "request_payload_bytes": 5,
        "response_payload_bytes": 8,
        "payload_bytes": 13,
    }

    world["choice"] = 2
    with pytest.raises(ValueError, match="choice does not match"):
        verifier.verify(case, world)


def test_end_of_day_is_not_a_free_cross_day_start():
    case = _case(
        dict.fromkeys(ROLES, [9, 10, 18, 19]),
        horizon=20,
        slots_per_day=10,
        duration=2,
        gold=[9, 18],
    )
    world = _world(case, [_row(1, 0, (18,) * 5)], 18)
    assert verifier.verify(case, world)["reward"] == 1
    forged = _world(case, [_row(1, 0, (9,) * 5)], 9)
    with pytest.raises(ValueError, match="wrong next start"):
        verifier.verify(case, forged)


def test_accepts_720_rounds_and_final_legal_bound():
    horizon = 720
    free = dict.fromkeys(ROLES, range(horizon))
    free["m1"] = range(0, horizon, 2)
    free["m2"] = range(1, horizon, 2)
    case = _case(free, horizon=horizon)
    rounds = [
        _row(
            tick + 1,
            tick,
            (tick + tick % 2, tick + (tick + 1) % 2, tick, tick, tick),
        )
        for tick in range(horizon - 1)
    ]
    rounds.append(_row(720, 719, (None, 719, 719, 719, 719)))
    world = _world(case, rounds, None)
    score = verifier.verify(case, world)
    assert score["choice"] is None
    assert score["resources"]["round_trips"] == 720
    assert score["resources"]["seat_exchanges"] == 3600
    assert score["resources"]["scalar_disclosures"] == 3599
    assert score["resources"]["distinct_disclosed_starts"] == 2880

    # Even a larger whole-day case cannot allow more than 720 native rounds.
    larger_case = _case(dict.fromkeys(ROLES, ()), horizon=horizon + 1)
    too_many = _world(larger_case, [*rounds, _row(721, 720, (None,) * 5)], None)
    with pytest.raises(ValueError, match="number of native rounds"):
        verifier.verify(larger_case, too_many)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda w: w.pop("choice"), "completion record"),
        (lambda w: w.update(mode="first8"), "completion record"),
        (lambda w: w.update(acknowledged=1), "completion record"),
        (lambda w: w.update(case_id="2/2"), "another case"),
        (lambda w: w["initial_view_sha256"].pop("m5"), "initial acknowledgments"),
        (
            lambda w: w["initial_view_sha256"].update(m1="0" * 64),
            "initial acknowledgments",
        ),
        (lambda w: w.update(rounds=[]), "number of native rounds"),
        (lambda w: w["rounds"][0].update(round=True), "invalid native round 1"),
        (lambda w: w["rounds"][1].update(round=1), "invalid native round 2"),
        (lambda w: w["rounds"][0].update(bound=False), "unexpected bound"),
        (lambda w: w["rounds"][0]["replies"].update(m6=0), "invalid reply roles"),
        (lambda w: w["rounds"][0]["replies"].pop("m5"), "invalid reply roles"),
        (lambda w: w["rounds"][0]["replies"].update(m2=True), "invalid reply tick"),
        (
            lambda w: w["rounds"][0].update(query_sha256="0" * 64),
            "query digest mismatch",
        ),
        (
            lambda w: w["rounds"][1].update(bundle_sha256="0" * 64),
            "bundle digest mismatch",
        ),
        (
            lambda w: w.update(choice_view_sha256=w["rounds"][1]["bundle_sha256"]),
            "choice acknowledgment",
        ),
        (lambda w: w.update(choice=10.0), "invalid native choice"),
        (lambda w: w.update(choice=None), "choice does not match"),
    ],
)
def test_rejects_wrong_shapes_digests_and_choice(disagreement, mutate, message):
    case, world = disagreement
    mutate(world)
    with pytest.raises(ValueError, match=message):
        verifier.verify(case, world)


@pytest.mark.parametrize(
    ("bound", "replies", "error"),
    [
        (5, (10, 7, 6, 10, 8), "unexpected bound"),
        (4, (10, 7, None, 10, 4), "wrong next start from m3"),
        (4, (10, 7, 6, 10, 8), "wrong next start from m5"),
        (4, (10, 7, 6, 3, 4), "wrong next start from m4"),
        (16, (None,) * 5, "unexpected bound"),
    ],
)
def test_rejects_wrong_round_values_even_with_matching_digests(
    disagreement, bound, replies, error
):
    case, world = disagreement
    world["rounds"][1] = _row(2, bound, replies)
    with pytest.raises(ValueError, match=error):
        verifier.verify(case, world)


def test_rejects_early_choice_and_rounds_after_termination(disagreement):
    case, world = disagreement
    rounds = world["rounds"]
    premature = _world(case, rounds[:2], None)
    with pytest.raises(ValueError, match="precedes a terminal reply set"):
        verifier.verify(case, premature)

    continued = _world(case, [*rounds, _row(4, 11, (None,) * 5)], None)
    with pytest.raises(ValueError, match="continue after a terminal reply set"):
        verifier.verify(case, continued)

    empty = _case(dict.fromkeys(ROLES, ()))
    continued = _world(empty, [_row(1, 0, (None,) * 5), _row(2, 1, (None,) * 5)], None)
    with pytest.raises(ValueError, match="continue after a terminal reply set"):
        verifier.verify(empty, continued)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(horizon=0),
        lambda c: c.update(duration=True),
        lambda c: c.update(slots_per_day=7),
        lambda c: c["members"].update(m2=[0, 0]),
        lambda c: c["members"].pop("m3"),
        lambda c: c.update(gold=[False]),
    ],
)
def test_rejects_malformed_private_case(disagreement, mutate):
    case, world = disagreement
    mutate(case)
    with pytest.raises(ValueError, match="invalid private calendar case"):
        verifier.verify(case, world)


def test_native_command_writes_reward_only_after_verified_action(
    disagreement, tmp_path, monkeypatch
):
    case, world = disagreement
    private_case = tmp_path / "case.json"
    artifact = tmp_path / "world.calendar.json"
    reward = tmp_path / "verifier" / "reward.txt"
    monkeypatch.setattr(verifier, "CASE_PATH", private_case)
    monkeypatch.setattr(verifier, "WORLD_PATH", artifact)
    monkeypatch.setattr(verifier, "REWARD_PATH", reward)
    private_case.write_text(json.dumps({"mode": "cursor", "case": case}))

    with pytest.raises(SystemExit, match="invalid interactive calendar evidence"):
        verifier.main()
    assert not reward.exists()

    # JSON duplicate keys must not silently normalize into an accepted decision.
    artifact.write_text(json.dumps(world)[:-1] + ', "choice": 10}')
    with pytest.raises(SystemExit, match="duplicate JSON field"):
        verifier.main()
    assert not reward.exists()

    artifact.write_text(json.dumps(world))
    verifier.main()
    assert reward.read_text() == "1\n"

    artifact.write_text(json.dumps({**world, "acknowledged": False}))
    with pytest.raises(SystemExit, match="invalid interactive completion record"):
        verifier.main()
    assert not reward.exists()

    empty_case = _case(dict.fromkeys(ROLES, ()))
    no_start = _world(empty_case, [_row(1, 0, (None,) * 5)], None)
    private_case.write_text(json.dumps({"mode": "cursor", "case": empty_case}))
    artifact.write_text(json.dumps(no_start))
    verifier.main()
    assert reward.read_text() == "0\n"
