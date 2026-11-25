import json
from fractions import Fraction
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.exhibit_pairs import analyze

_TASK = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = import_module(f"{_TASK}.cases").make_case
Session = import_module(f"{_TASK}.engine").Session
observation = import_module(f"{_TASK}.diagnostics").observation
case_commitment = import_module(f"{_TASK}.diagnostics").case_commitment

_METRICS = ("physical", "C", "P")


def _json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _pair(case: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return {
        "id": f"pair-{index}",
        "case_commitment": case_commitment(case),
        "left": f"left-{index}",
        "right": f"right-{index}",
    }


def _manifest(root: Path, pairs: list[dict[str, Any]], **overrides: object) -> Path:
    path = root / "manifest.json"
    _json(
        path,
        {
            "schema": "seatlab.exhibit-pairs/v1",
            "profile": "coupling",
            "arms": ["masked", "bundled"],
            "pairs": pairs,
            **overrides,
        },
    )
    return path


def _current(cards: list[dict[str, Any]], item: str, epoch: int) -> dict[str, Any]:
    return next(
        card
        for card in cards
        if card["key"] == item and card["start"] <= epoch <= card["end"]
    )


def _produce(
    case: dict[str, Any], mode: str, *, correct: bool = False, steps: int = 4
) -> dict[str, Any]:
    session = Session(case, mode)
    order = next(brief for brief in case["briefs"] if brief["id"] == case["realized"])
    registrar = [
        _current(case["registrar"], item, order["epoch"]) for item in order["items"]
    ]
    depot = [
        _current(case["depot"], card["value"], order["epoch"]) for card in registrar
    ]
    routes = {
        item: card["value"]
        if correct
        else next(bay for bay in case["bays"] if bay != card["value"])
        for item, card in zip(order["items"], depot, strict=True)
    }
    actions = [
        ("loader", {"op": "request", "slips": [order["id"]]}),
        (
            "registrar",
            {"op": "file", "ids": [c["id"] for c in registrar] if correct else []},
        ),
        ("depot", {"op": "file", "ids": [c["id"] for c in depot] if correct else []}),
        ("loader", {"op": "commit", "routes": routes}),
    ]
    for role, action in actions[:steps]:
        session.apply(role, action)
    return observation(session.snapshot())


def _publish(root: Path, directory: str, obs: dict[str, Any]) -> Path:
    folder = root / directory
    folder.mkdir()
    _json(folder / "observation.json", obs)
    if obs["status"] == "complete":
        _json(folder / "reward.json", {"reward": obs["reward"]})
    return folder


def _bounds(
    report: dict[str, Any], metric: str, low: Fraction | int, high: Fraction | int
) -> None:
    assert report["metrics"][metric]["difference_bounds"] == [
        str(Fraction(low)),
        str(Fraction(high)),
    ]


def _coverage(case: dict[str, Any]) -> tuple[Fraction, Fraction]:
    order = next(brief for brief in case["briefs"] if brief["id"] == case["realized"])
    chosen = set(order["items"])
    total = sum(brief["weight"] for brief in case["briefs"])
    complete = sum(
        brief["weight"] for brief in case["briefs"] if set(brief["items"]) <= chosen
    )
    items = sum(
        brief["weight"] * len(chosen.intersection(brief["items"]))
        for brief in case["briefs"]
    )
    return Fraction(complete, total), Fraction(items, 2 * total)


def test_all_missing_preserves_full_uncertainty(tmp_path: Path) -> None:
    report = analyze(_manifest(tmp_path, [_pair(make_case(17, "coupling"))]))
    for metric in _METRICS:
        _bounds(report, metric, -1, 1)


def test_verified_right_zero_missing_left(tmp_path: Path) -> None:
    case = make_case(18, "coupling")
    pair = _pair(case)
    _publish(tmp_path, pair["right"], _produce(case, "bundled"))
    report = analyze(_manifest(tmp_path, [pair]))
    for metric in _METRICS:
        _bounds(report, metric, -1, 0)


@pytest.mark.parametrize(
    "fault",
    [
        "reward_mismatch",
        "reward_missing",
        "different_case",
        "unknown_status",
        "wrong_mode",
    ],
)
def test_unverified_arm_remains_unknown(tmp_path: Path, fault: str) -> None:
    case = make_case(19, "coupling")
    pair = _pair(case)
    obs = _produce(
        make_case(20, "coupling") if fault == "different_case" else case,
        "linked" if fault == "wrong_mode" else "bundled",
    )
    if fault == "unknown_status":
        obs["status"] = "infrastructure_error"
        obs["reward"] = None
    folder = _publish(tmp_path, pair["right"], obs)
    if fault == "reward_mismatch":
        _json(folder / "reward.json", {"reward": 1})
    if fault == "reward_missing":
        (folder / "reward.json").unlink()
    report = analyze(_manifest(tmp_path, [pair]))
    for metric in _METRICS:
        _bounds(report, metric, -1, 1)


def test_incomplete_before_depot_is_not_verified_zero_coverage(tmp_path: Path) -> None:
    case = make_case(21, "coupling")
    pair = _pair(case)
    _publish(tmp_path, pair["left"], _produce(case, "masked", correct=True, steps=2))
    _publish(tmp_path, pair["right"], _produce(case, "bundled"))
    report = analyze(_manifest(tmp_path, [pair]))
    _bounds(report, "physical", 0, 0)
    _bounds(report, "C", -1, 0)
    _bounds(report, "P", -1, 0)


def test_incomplete_after_depot_has_observed_coverage(tmp_path: Path) -> None:
    case = make_case(22, "coupling")
    pair = _pair(case)
    _publish(tmp_path, pair["left"], _produce(case, "masked"))
    _publish(tmp_path, pair["right"], _produce(case, "bundled", correct=True, steps=3))
    complete, items = _coverage(case)
    report = analyze(_manifest(tmp_path, [pair]))
    _bounds(report, "physical", 0, 0)
    _bounds(report, "C", complete, complete)
    _bounds(report, "P", items, items)


def test_bounds_use_all_planned_pairs_not_complete_cases(tmp_path: Path) -> None:
    case = make_case(23, "coupling")
    pair = _pair(case)
    _publish(tmp_path, pair["left"], _produce(case, "masked"))
    _publish(tmp_path, pair["right"], _produce(case, "bundled", correct=True))
    complete, items = _coverage(case)
    roster = [pair, _pair(make_case(24, "coupling"), 1)]
    report = analyze(_manifest(tmp_path, roster))
    for metric, effect in zip(_METRICS, (Fraction(1), complete, items), strict=True):
        _bounds(report, metric, (effect - 1) / 2, (effect + 1) / 2)


@pytest.mark.parametrize(
    "duplicate", ["path", "reversed_paths", "commitment", "ticket", "same_arm"]
)
def test_invalid_roster_cannot_count_reused_units(
    tmp_path: Path, duplicate: str
) -> None:
    first = _pair(make_case(25, "coupling"))
    second = _pair(make_case(26, "coupling"), 1)
    if duplicate == "path":
        second["left"] = first["left"]
    elif duplicate == "reversed_paths":
        second["left"], second["right"] = first["right"], first["left"]
    elif duplicate == "commitment":
        second["case_commitment"] = first["case_commitment"]
    elif duplicate == "ticket":
        second["id"] = first["id"]
    else:
        second["right"] = second["left"]
    with pytest.raises(ValueError):
        analyze(_manifest(tmp_path, [first, second]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "unknown"},
        {"profile": "unknown"},
        {"arms": ["bundled", "masked"]},
        {"pairs": []},
        {"pairs": {}},
        {"unexpected": True},
    ],
)
def test_malformed_top_level_enrollment(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    pair = _pair(make_case(27, "coupling"))
    record = {
        "schema": "seatlab.exhibit-pairs/v1",
        "profile": "coupling",
        "arms": ["masked", "bundled"],
        "pairs": [pair],
    }
    record.update(overrides)
    path = tmp_path / "manifest.json"
    _json(path, record)
    with pytest.raises(ValueError):
        analyze(path)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "left-0/../other"])
def test_manifest_paths_must_be_private_relative_paths(
    tmp_path: Path, path: str
) -> None:
    pair = _pair(make_case(28, "coupling"))
    pair["right"] = path
    with pytest.raises(ValueError):
        analyze(_manifest(tmp_path, [pair]))
