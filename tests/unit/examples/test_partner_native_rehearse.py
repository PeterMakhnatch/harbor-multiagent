import asyncio
from collections import Counter
from copy import deepcopy

import pytest

from examples.joint_reopen.observation import validate_observation
from examples.seatlab.partner_native import plan, rehearse
from examples.seatlab.partner_switch.engine import native


@pytest.mark.parametrize("mode", ["native-rosters", "unknown", None])
def test_rejects_other_modes_before_reading_registration(mode):
    with pytest.raises(ValueError, match="scripted controls only"):
        rehearse.run({"mode": mode}, {})


def test_four_pairings_share_six_calendars_not_iid():
    seeds = native.balanced_seeds(19)
    pairs = ("p00-00", "p00-01", "p01-00", "p01-01")
    cells = [
        {"team": pair, "seed": seed, "fixture": "both", "channel": "deliver"}
        for seed in seeds
        for pair in reversed(pairs)
    ]
    registration = {"teams": dict.fromkeys(pairs), "cells": cells}
    grouped = rehearse._registered_pairs(registration)
    assert [[group[0]["team"] for group in row] for row in grouped] == [
        ["p00-00", "p00-01"],
        ["p01-00", "p01-01"],
    ]
    assert Counter(
        cell["seed"] for row in grouped for group in row for cell in group
    ) == {seed: 4 for seed in seeds}
    assert "not IID samples" in rehearse.SCOPE
    cells[0]["seed"] = max(seeds) + 1
    with pytest.raises(ValueError, match="same six distinct calendars"):
        rehearse._registered_pairs(registration)


def test_one_native_world_runs_both_external_scripted_roles():
    seed = 19
    active = "".join(sorted(native.permutation(seed)[1:4]))
    team = [
        {"seat": role, "model_name": f"seatlab-switch/forward-{active}"}
        for role in ("east", "west")
    ]
    cell = {"seed": seed, "fixture": "both", "channel": "deliver"}
    observation, count = asyncio.run(rehearse._episode(cell, team))
    assert count == 10
    assert validate_observation(observation, seed, "both", "deliver") == observation
    assert observation["reward"] == 1
    assert observation["repaired_invalid_incumbent"] is True


def test_rehearsal_diagnoses_the_registered_scripted_worlds():
    manifest, registration = plan.build(19, None, "different")
    summary = rehearse.run(manifest, registration)
    assert summary["worlds"] == 24
    assert summary["decisions"] == 240
    assert summary["native_trials_started"] == summary["model_calls"] == 0
    assert summary["matrix"] == [["1", "1/2"], ["1/2", "1"]]
    assert summary["matched_prediction"] is True
    diagnosis = summary["diagnosis"]
    assert diagnosis["source"] == "scripted_native_worlds"
    assert diagnosis["registration_sha256"] == manifest["registration_sha256"]
    assert diagnosis["registered_worlds"] == diagnosis["observed_worlds"] == 24
    assert diagnosis["complete"] is True


def test_offsetting_rehearsal_requires_calendar_and_contact_predictions():
    manifest, registration = plan.build(19, None, "offsetting")
    summary = rehearse.run(manifest, registration)
    matrix = summary["matrix"]
    diagnosis = summary["diagnosis"]
    assert matrix == [["1/2", "1/2"], ["1/2", "1/2"]]
    assert summary["matched_prediction"] is True
    assert diagnosis["observed_worlds"] == 24
    for index, contrast in enumerate(diagnosis["contrasts"]):
        changed = deepcopy(diagnosis)
        replacement = (0, 0, 3, 3) if contrast["role"] == "east" else (3, 3, 0, 0)
        keys = ("original_only", "replacement_only", "both_win", "both_lose")
        changed["contrasts"][index].update(zip(keys, replacement, strict=True))
        assert not rehearse._offsetting_prediction(matrix, changed)
    for field, old, new in (
        ("outcomes", "split_vote", "same_unavailable"),
        ("receipt_before_vote", "both", "east_only"),
    ):
        changed = deepcopy(diagnosis)
        counts = changed["matrix"][0][0][field]
        counts[old] -= 1
        counts[new] += 1
        assert not rehearse._offsetting_prediction(matrix, changed)
