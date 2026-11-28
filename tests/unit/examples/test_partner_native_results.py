import copy
import json
from typing import Any

import pytest

from examples.joint_reopen.collect import STATUSES
from examples.seatlab.partner_native.results import from_rows


@pytest.fixture
def matrix_fixture() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    names = ["second", "first"]
    manifest = {
        "names": names,
        "mode": "fixture",
        "start": 1,
        "registration_sha256": "a" * 64,
    }
    teams = {f"p{i:02}-{j:02}": [] for i in range(2) for j in range(2)}
    cells = [
        {
            "ticket": f"fixture-ticket-{pair}-{seed}",
            "team": pair,
            "seed": seed,
            "fixture": "none",
            "channel": "deliver",
        }
        for pair in teams
        for seed in range(1, 7)
    ]
    registration = {"teams": teams, "cells": cells}
    rows = [
        {
            **cell,
            "status": "missing",
            "observation": None,
            "native_id": None,
            "reason": None,
        }
        for cell in cells
    ]
    return manifest, registration, rows


def _usable(row: dict[str, Any], reward: int) -> None:
    row.update(status="usable", observation={"reward": reward})


def test_missing_fixture_identifies_no_point(matrix_fixture) -> None:
    manifest, registration, rows = matrix_fixture
    result = from_rows(manifest, registration, rows)
    assert result["names"] == ["second", "first"]
    assert result["mode"] == manifest["mode"]
    assert result["registration_sha256"] == manifest["registration_sha256"]
    assert result["schema"] == "seatlab.partner-native-matrix/v1"
    assert not result["complete"]
    for row in result["matrix"]:
        for cell in row:
            assert cell == {
                "registered": 6,
                "usable": 0,
                "wins": 0,
                "reward": None,
                "bounds": ["0", "1"],
                "statuses": {
                    status: 6 if status == "missing" else 0 for status in STATUSES
                },
            }
    for group in (result["home"], result["mixed"]):
        assert group["registered"] == 12
        assert group["statuses"]["missing"] == 12
        assert group["reward"] is None
        assert group["bounds"] == ["0", "1"]
    assert result["home_minus_mixed"] == {"reward": None, "bounds": ["-1", "1"]}


def test_partial_fixture_bounds_count_only_usable_rewards(matrix_fixture) -> None:
    manifest, registration, rows = matrix_fixture
    _usable(rows[0], 0)
    _usable(rows[1], 1)
    rows[2].update(
        status="native_error",
        reason="private error fixture",
        standing_reward=1,
        native_id="private UUID fixture",
        owner_evidence={"private role fixture": True},
    )
    rows[4]["status"] = "ambiguous"
    rows[5]["status"] = "duplicate"
    for row in rows[6:12]:
        _usable(row, 0)
    rows[12]["status"] = "incomplete"
    rows[13]["status"] = "invalid"
    for row in rows[18:24]:
        _usable(row, 1)
    result = from_rows(manifest, registration, list(reversed(rows)))
    assert not result["complete"]
    first = result["matrix"][0][0]
    assert first["usable"] == 2 and first["wins"] == 1
    assert first["statuses"] == {
        "usable": 2,
        "missing": 1,
        "incomplete": 0,
        "ambiguous": 1,
        "duplicate": 1,
        "native_error": 1,
        "invalid": 0,
    }
    assert first["bounds"] == ["1/6", "5/6"]
    assert first["reward"] is None
    assert result["matrix"][0][1]["reward"] == "0"
    assert result["matrix"][1][0]["bounds"] == ["0", "1"]
    assert result["matrix"][1][1]["reward"] == "1"
    assert result["home"]["usable"] == 8
    assert result["home"]["wins"] == 7
    assert result["home"]["bounds"] == ["7/12", "11/12"]
    assert result["mixed"]["usable"] == 6
    assert result["mixed"]["wins"] == 0
    assert result["mixed"]["bounds"] == ["0", "1/2"]
    assert result["home_minus_mixed"] == {
        "reward": None,
        "bounds": ["1/12", "11/12"],
    }
    encoded = json.dumps(result)
    for private in (
        "fixture-ticket",
        "private error",
        "private UUID",
        "private role",
        "seed",
    ):
        assert private not in encoded


def test_complete_fixture_exact_ordered_means(matrix_fixture) -> None:
    manifest, registration, rows = matrix_fixture
    wins = {"p00-00": 3, "p00-01": 1, "p01-00": 2, "p01-01": 5}
    for row in rows:
        _usable(row, int(row["seed"] <= wins[row["team"]]))
    result = from_rows(manifest, registration, rows)
    assert result["complete"]
    assert [[cell["reward"] for cell in row] for row in result["matrix"]] == [
        ["1/2", "1/6"],
        ["1/3", "5/6"],
    ]
    assert result["home"]["reward"] == "2/3"
    assert result["mixed"]["reward"] == "1/4"
    assert result["home_minus_mixed"] == {
        "reward": "5/12",
        "bounds": ["5/12", "5/12"],
    }


@pytest.mark.parametrize("reward", [True, False, 1.0, 0.0, "1", None, -1, 2])
def test_fixture_rejects_non_integer_binary_reward(matrix_fixture, reward) -> None:
    manifest, registration, rows = matrix_fixture
    rows[0].update(status="usable", observation={"reward": reward})
    with pytest.raises(ValueError, match="binary observation reward"):
        from_rows(manifest, registration, rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ticket", "unknown"),
        ("team", "p00-01"),
        ("seed", True),
        ("seed", 2),
        ("fixture", "east"),
        ("channel", "withhold"),
    ],
)
def test_fixture_rejects_mismatched_cell(matrix_fixture, field, value) -> None:
    manifest, registration, rows = matrix_fixture
    rows[0][field] = value
    with pytest.raises(ValueError, match="differs from a registered cell"):
        from_rows(manifest, registration, rows)


def test_fixture_rejects_missing_duplicate_and_unknown_rows(matrix_fixture) -> None:
    manifest, registration, rows = matrix_fixture
    with pytest.raises(ValueError, match="one row per registered ticket"):
        from_rows(manifest, registration, rows[:-1])
    with pytest.raises(ValueError, match="repeats"):
        from_rows(manifest, registration, [rows[0], *rows[:-1]])
    rows[0]["status"] = "unrecognized"
    with pytest.raises(ValueError, match="unknown status"):
        from_rows(manifest, registration, rows)


def test_fixture_rejects_duplicate_cases_even_with_distinct_tickets(
    matrix_fixture,
) -> None:
    manifest, registration, rows = copy.deepcopy(matrix_fixture)
    registration["cells"][1]["seed"] = registration["cells"][0]["seed"]
    rows[1]["seed"] = rows[0]["seed"]
    with pytest.raises(ValueError, match="six distinct cases per pair"):
        from_rows(manifest, registration, rows)
