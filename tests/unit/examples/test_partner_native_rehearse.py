import asyncio
from collections import Counter

import pytest

from examples.seatlab.partner_native import rehearse
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
    assert asyncio.run(rehearse._episode(cell, team)) == (1, 10)
