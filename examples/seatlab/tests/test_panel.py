import importlib
import os
from pathlib import Path
from stat import S_IMODE
from typing import Any
from uuid import uuid4

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    panel = importlib.import_module("run_panel")


@pytest.fixture
def sources() -> dict[str, bytes]:
    return panel._snapshot("oracle")


def plan_for(sources: dict[str, bytes], *conditions: str) -> Any:
    hashes = {name: panel._sha256(content) for name, content in sources.items()}
    return panel._make_plan([17], list(conditions), 1, "oracle", hashes)


def write_dossier(
    out: Path, plan: Any, cell: Any, *, actual_seed: int = 17
) -> tuple[Path, dict[str, Any]]:
    config = panel._trial_config(out, plan, cell)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    scenario = panel._scenario(plan, cell)
    scenario["seed"] = actual_seed
    mask = ["A", "B"]
    reports = [{"revision": revision, "available": mask} for revision in (1, 2)]
    revisions = {
        "silent": [],
        "stale": [1],
        "fresh": [2],
        "reordered": [2, 1],
        "pooled": [],
    }
    panel._create_json(
        artifact,
        {
            "schema": "seatlab.world/v1",
            "settings": scenario,
            "complete": True,
            "cases": [
                {
                    "id": 0,
                    "utilities": {"A": 1, "B": 4, "C": 3, "D": 2},
                    "before": mask,
                    "after": mask,
                    "reports": None if cell["condition"] == "pooled" else reports,
                    "visible": [reports[i - 1] for i in revisions[cell["condition"]]],
                    "chosen": "B",
                }
            ],
        },
    )
    timestamp = "2026-10-02T00:00:00Z"
    result = {
        "schema_version": 1,
        "id": str(uuid4()),
        "config": config,
        "task_name": "harbor/dispatch-handoff",
        "trial_name": trial.name,
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        "task_checksum": "a" * 64,
        "started_at": timestamp,
        "finished_at": timestamp,
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 1.0}},
        "verifier_environment_mode": "shared",
        "reward_valid": True,
        "seat_results": [
            {
                "seat": participant["seat"],
                "under_test": True,
                "exception_info": None,
                "agent_info": {"name": participant["name"], "version": "1.0.0"},
                "agent_execution": {"started_at": timestamp, "finished_at": timestamp},
                "agent_result": {},
            }
            for participant in config["participants"]
        ],
    }
    return trial, result


def test_plan_is_canonical_and_native_configs_are_cell_scoped(
    tmp_path: Path, sources: dict[str, bytes]
) -> None:
    hashes = {name: panel._sha256(content) for name, content in sources.items()}
    assert panel.SEAT_ADAPTER not in hashes
    assert panel.SEAT_ADAPTER in panel._source_names("arrival")
    assert panel.SEAT_ADAPTER in panel._source_names("hosted")
    plan = panel._make_plan([29, 17], ["pooled", "fresh"], 12, "arrival", hashes)
    reordered = panel._make_plan([17, 29], ["fresh", "pooled"], 12, "arrival", hashes)
    assert panel._json(plan) == panel._json(reordered)
    assert [(cell["seed"], cell["condition"]) for cell in plan["cells"]] == [
        (17, "fresh"),
        (17, "pooled"),
        (29, "fresh"),
        (29, "pooled"),
    ]
    assert str(tmp_path) not in panel._json(plan)
    assert hashes[f"tasks/dispatch-handoff/{panel.SCENARIO}"] == panel._sha256(
        (panel.TASK_DIR / panel.SCENARIO).read_bytes()
    )
    names = set()
    for out in (tmp_path / "panel-a", tmp_path / "panel-b"):
        for cell in plan["cells"]:
            config = panel._trial_config(out, plan, cell)
            assert config["environment"]["type"] == "docker"
            assert config["trial_name"] not in names
            names.add(config["trial_name"])
            inventory, dispatch = config["participants"]
            assert dispatch["import_path"] == "seat_agent:SeatAgent"
            assert dispatch["kwargs"] == {"policy": "arrival"}
            assert inventory["name"] == (
                "nop" if cell["condition"] == "pooled" else None
            )


def test_stage_only_normalizes_the_public_clone_with_private_umask(
    tmp_path: Path, sources: dict[str, bytes]
) -> None:
    plan = plan_for(sources, "stale")
    cell = plan["cells"][0]
    original = panel.TASK_DIR / panel.SCENARIO
    original_bytes, original_mode = original.read_bytes(), original.stat().st_mode
    previous = os.umask(0o077)
    try:
        directory = panel._cell_dir(tmp_path, cell)
        directory.mkdir(parents=True)
        panel._prepare_cell(directory, plan, cell, sources)
    finally:
        os.umask(previous)
    task = directory / "dispatch-handoff"
    assert S_IMODE(directory.stat().st_mode) == 0o700
    assert S_IMODE(task.stat().st_mode) == 0o755
    for path in task.rglob("*"):
        assert S_IMODE(path.stat().st_mode) == (
            0o755 if path.is_dir() or path.suffix == ".sh" else 0o644
        )
    assert (original.read_bytes(), original.stat().st_mode) == (
        original_bytes,
        original_mode,
    )
    assert panel._read_object(task / panel.SCENARIO) == panel._scenario(plan, cell)


def test_collect_preserves_partial_attempt_and_never_restarts_it(
    tmp_path: Path, sources: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_for(sources, "stale", "fresh")
    plan_file = tmp_path / "plan.json"
    panel._create_json(plan_file, plan)
    directory = panel._cell_dir(tmp_path, plan["cells"][0])
    directory.mkdir(parents=True)
    log = directory / "harbor.log"
    log.write_text("interrupted", encoding="utf-8")
    receipt = directory / "receipt.json"
    panel._create_json(receipt, {"returncode": 0, "launch_error": None, "reward": 1})
    original = {path: path.read_bytes() for path in (plan_file, log, receipt)}

    def no_execution(*args: object, **kwargs: object) -> None:
        pytest.fail("collection must not invoke a subprocess")

    monkeypatch.setattr(panel.subprocess, "run", no_execution)
    monkeypatch.setattr(panel.asyncio, "create_subprocess_exec", no_execution)
    args = ["--out", str(tmp_path)]
    assert panel.main([*args, "--collect"]) == 1
    index = tmp_path / "runs.json"
    first = index.read_bytes()
    assert panel.main([*args, "--collect"]) == 1
    assert index.read_bytes() == first
    with pytest.raises(SystemExit) as exc:
        panel.main([*args, "--harbor", "/not-used"])
    assert exc.value.code == 1
    assert {path: path.read_bytes() for path in original} == original
    attempted, not_attempted = panel._read_object(index)["cells"]
    assert attempted["status"] == "unmeasured"
    assert attempted["returncode"] == 0
    assert attempted["evidence"] is None
    assert not_attempted["error"] == "not attempted"
    assert not panel._cell_dir(tmp_path, plan["cells"][1]).exists()


@pytest.mark.parametrize("mismatch", ["seed", "policy", "reward"])
def test_native_rejection_survives_a_success_receipt(
    tmp_path: Path, sources: dict[str, bytes], mismatch: str
) -> None:
    plan = plan_for(sources, "fresh")
    cell = plan["cells"][0]
    trial, native = write_dossier(
        tmp_path, plan, cell, actual_seed=29 if mismatch == "seed" else 17
    )
    if mismatch == "policy":
        native["config"]["participants"][0]["kwargs"] = {"policy": "arrival"}
    if mismatch == "reward":
        native["verifier_result"]["rewards"]["reward"] = 0.5
    panel._create_json(trial / "result.json", native)
    panel._create_json(
        panel._cell_dir(tmp_path, cell) / "receipt.json",
        {"returncode": 0, "launch_error": None},
    )
    run = panel._inspect_cell(tmp_path, plan, cell)
    assert run["status"] == "unmeasured"
    assert run["evidence"] is None
    assert run["returncode"] == 0
    assert run["error"] is not None
    assert mismatch in run["error"]


def test_valid_native_dossier_is_measured_without_a_success_receipt(
    tmp_path: Path, sources: dict[str, bytes]
) -> None:
    plan = plan_for(sources, "fresh")
    cell = plan["cells"][0]
    trial, native = write_dossier(tmp_path, plan, cell)
    panel._create_json(trial / "result.json", native)
    receipt = panel._cell_dir(tmp_path, cell) / "receipt.json"
    receipt.write_text("torn receipt", encoding="utf-8")
    run = panel._inspect_cell(tmp_path, plan, cell)
    assert run["status"] == "measured"
    assert run["receipt_error"]
    assert run["evidence"] is not None
    assert run["evidence"]["score"]["optimal"] == 1


def test_pairing_rejects_incompatible_latent_cases(
    tmp_path: Path, sources: dict[str, bytes]
) -> None:
    plan = plan_for(sources, "stale", "fresh")
    panel._create_json(tmp_path / "plan.json", plan)
    for cell in plan["cells"]:
        trial, native = write_dossier(tmp_path, plan, cell)
        panel._create_json(trial / "result.json", native)
        if cell["condition"] == "fresh":
            artifact = trial / "artifacts/logs/world/handoff.json"
            world = panel._read_object(artifact)
            world["cases"][0]["utilities"] = {"A": 2, "B": 4, "C": 3, "D": 1}
            artifact.write_text(panel._json(world), encoding="utf-8")
    assert not panel._collect(tmp_path)
    results = panel._read_object(tmp_path / "runs.json")
    assert results["measured"] == 0
    assert results["unmeasured"] == 2
    assert all("fingerprints differ" in run["error"] for run in results["cells"])
