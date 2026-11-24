import importlib.util
import json
from hashlib import sha256
from operator import itemgetter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from examples.seatlab.dispatch_opportunity import describe, main

_TASK = Path(__file__).resolve().parents[2] / "tasks" / "dispatch-handoff"
_ARMS = ("fresh", "reordered")
_ALL_ARMS = ("silent", "stale", *_ARMS, "pooled")
_COUNT_KEYS = (
    "unchanged",
    "changed_same_best",
    "old_best_feasible_suboptimal",
    "old_best_infeasible",
)


def _load_native(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _TASK / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def native() -> tuple[ModuleType, ModuleType]:
    return (
        _load_native(
            "dispatch_opportunity_seatworld", Path("environment/seatworld.py")
        ),
        _load_native("dispatch_opportunity_score", Path("tests/score.py")),
    )


def _registration(
    seeds: tuple[int, ...], conditions: tuple[str, ...], episodes: int
) -> tuple[dict[str, Any], str]:
    plan = {"seeds": list(seeds), "conditions": list(conditions), "episodes": episodes}
    pin = sha256(repr(plan).encode()).hexdigest()
    return {"plan": plan}, pin


def _native_score(
    native: tuple[ModuleType, ModuleType],
    seed: int,
    episodes: int,
    arm: str,
    *,
    highest_revision: bool,
) -> dict[str, Any]:
    seatworld, grader = native
    world = seatworld.World(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": episodes,
            "condition": arm,
        }
    )
    world.submit("inventory", {"rows": world.view("inventory")["cases"]})
    choices = []
    for case in world.view("dispatch")["cases"]:
        reports = case["messages"]
        report = (
            max(reports, key=itemgetter("revision"))
            if highest_revision
            else reports[-1]
        )
        choices.append(
            {
                "id": case["id"],
                "bay": max(report["available"], key=case["utilities"].__getitem__),
            }
        )
    world.submit("dispatch", {"rows": choices})
    return grader.score(world.evidence())


def _check_report(
    native: tuple[ModuleType, ModuleType],
    seeds: tuple[int, ...],
    conditions: tuple[str, ...],
    episodes: int,
) -> dict[str, Any]:
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    optimality = feasibility = 0
    for seed in seeds:
        fresh = _native_score(native, seed, episodes, "fresh", highest_revision=True)
        assert fresh == _native_score(
            native, seed, episodes, "fresh", highest_revision=False
        )
        assert fresh == _native_score(
            native, seed, episodes, "reordered", highest_revision=True
        )
        assert fresh["optimal"] == fresh["feasible"] == episodes
        arrival = _native_score(
            native, seed, episodes, "reordered", highest_revision=False
        )
        assert arrival["changed"] == fresh["changed"] == episodes // 2
        for row in arrival["rows"]:
            assert row["feasible"] or not row["optimal"]
            assert row["changed"] or row["optimal"]
        lost_optimal = fresh["optimal"] - arrival["optimal"]
        lost_feasible = fresh["feasible"] - arrival["feasible"]
        assert 0 <= lost_feasible <= lost_optimal <= arrival["changed"]
        counts["unchanged"] += episodes - arrival["changed"]
        counts["changed_same_best"] += arrival["changed"] - lost_optimal
        counts["old_best_feasible_suboptimal"] += (
            arrival["feasible"] - arrival["optimal"]
        )
        counts["old_best_infeasible"] += lost_feasible
        optimality += lost_optimal
        feasibility += lost_feasible

    shipments = len(seeds) * episodes
    assert sum(counts.values()) == shipments
    registration, pin = _registration(seeds, conditions, episodes)
    report = describe(registration, pin, native[0].World)
    assert report == {
        "schema": "seatlab.dispatch-revision-opportunity/v1",
        "registration_sha256": pin,
        "assumption": "truthful_inventory_reports",
        "status": "distinguishable" if optimality else "indistinguishable",
        "missing_arms": [],
        "paired_teams": 2 * len(seeds),
        "unique_shipments": shipments,
        "counts": counts,
        "optimality_opportunities": optimality,
        "feasibility_opportunities": feasibility,
    }
    return report


@pytest.mark.parametrize(
    ("seeds", "conditions", "episodes"),
    [
        ((17, 29), _ALL_ARMS, 12),
        ((-29, -17), _ARMS, 9),
        ((-1, 17, 29), _ALL_ARMS, 1),
    ],
)
def test_native_scores_match_aggregate_opportunities(
    native: tuple[ModuleType, ModuleType],
    seeds: tuple[int, ...],
    conditions: tuple[str, ...],
    episodes: int,
) -> None:
    _check_report(native, seeds, conditions, episodes)


def test_archived_fresh_baseline_score(native: tuple[ModuleType, ModuleType]) -> None:
    score = _native_score(native, 17, 12, "fresh", highest_revision=True)
    assert (score["optimal"], score["episodes"]) == (12, 12)


@pytest.mark.parametrize(
    ("episodes", "expected_counts", "arrival_scores"),
    [(2, (1, 1, 0, 0), (2, 2)), (6, (3, 1, 1, 1), (4, 5))],
)
def test_native_grader_distinguishes_the_four_partitions(
    native: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    episodes: int,
    expected_counts: tuple[int, ...],
    arrival_scores: tuple[int, int],
) -> None:
    seatworld, _ = native
    stable = seatworld._Case((4, 3, 2, 1), ("A", "B"), ("A", "B"))
    changed_same_best = seatworld._Case((4, 3, 2, 1), ("A", "C"), ("A", "D"))
    changed_feasible = seatworld._Case((2, 3, 4, 1), ("A", "B"), ("B", "C"))
    changed_infeasible = seatworld._Case((4, 3, 2, 1), ("A", "B"), ("B", "C"))
    cases = (
        stable,
        changed_same_best,
        stable,
        changed_feasible,
        stable,
        changed_infeasible,
    )

    def case_for_fixture(_seed: int, episode: int) -> Any:
        return cases[episode]

    monkeypatch.setattr(seatworld, "_case", case_for_fixture)
    score = _native_score(native, 17, episodes, "reordered", highest_revision=False)
    assert (score["optimal"], score["feasible"]) == arrival_scores
    report = _check_report(native, (17,), _ARMS, episodes)
    assert report["counts"] == dict(zip(_COUNT_KEYS, expected_counts, strict=True))


@pytest.mark.parametrize(
    ("conditions", "missing"),
    [
        (("silent", "fresh", "pooled"), ["reordered"]),
        (("stale", "reordered"), ["fresh"]),
        (("silent", "pooled"), ["fresh", "reordered"]),
    ],
)
def test_missing_arms_have_no_world_or_counts(
    conditions: tuple[str, ...], missing: list[str]
) -> None:
    class UnusedWorld:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("Missing arms must not instantiate World")

    registration, pin = _registration((17, 29), conditions, 12)
    assert describe(registration, pin, UnusedWorld) == {
        "schema": "seatlab.dispatch-revision-opportunity/v1",
        "registration_sha256": pin,
        "assumption": "truthful_inventory_reports",
        "status": "missing_arms",
        "missing_arms": missing,
        "paired_teams": 0,
        "unique_shipments": None,
        "counts": None,
        "optimality_opportunities": None,
        "feasibility_opportunities": None,
    }


def test_native_first_attempt_controls_agree_with_the_input_diagnostic(
    native: tuple[ModuleType, ModuleType],
) -> None:
    saved = Path(__file__).resolve().parents[1] / "results/native-model-primary.json"
    cells = [
        cell
        for cell in json.loads(saved.read_bytes())["cells"]
        if cell["pairing"] == "SS"
    ]
    accepted = []
    for cell in cells:
        if cell["status"] != "measured":
            assert cell["metrics"] is None
            continue
        observed = cell["metrics"]
        n = observed["episodes"]
        assert observed["inventory_copy_fidelity"]["both_correct"] == n
        packet, pin = _registration((cell["seed"],), _ARMS, n)
        design = describe(packet, pin, native[0].World)
        predicted = (
            (n, n)
            if cell["condition"] == "fresh"
            else (
                n - design["optimality_opportunities"],
                n - design["feasibility_opportunities"],
            )
        )
        assert predicted == (observed["optimal"], observed["feasible"])
        accepted.append(cell["trial_id"])
    assert len(set(accepted)) == len(accepted) == 3


def test_cli_rejects_wrong_digest_without_publishing_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet = tmp_path / "registration.json"
    packet.write_text('{"registration":"untrusted"}')
    assert main(["design", str(packet), "--sha256", sha256(b"other").hexdigest()]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "SHA-256" in captured.err
