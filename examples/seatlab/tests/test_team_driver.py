import ast
import importlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from runpy import run_path
from stat import S_IMODE
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from examples.seatlab import team_driver as driver
    from examples.seatlab import team_report
    from examples.seatlab.capsule import view_hash
    from examples.seatlab.evidence import _score
else:
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
        driver = importlib.import_module("team_driver")
        team_report = importlib.import_module("team_report")
        view_hash = importlib.import_module("capsule").view_hash
        _score = importlib.import_module("evidence")._score


def test_fixed_grid_actor_routes_and_turn_cap(tmp_path: Path) -> None:
    cells = driver._grid()
    assert [cell["cell_id"] for cell in cells] == [
        "s2001-fresh-SS",
        "s2001-fresh-SM",
        "s2001-fresh-MS",
        "s2001-fresh-MM",
        "s2001-reordered-SS",
        "s2001-reordered-SM",
        "s2001-reordered-MS",
        "s2001-reordered-MM",
        "s2003-reordered-MM",
        "s2003-reordered-MS",
        "s2003-reordered-SM",
        "s2003-reordered-SS",
        "s2003-fresh-MM",
        "s2003-fresh-MS",
        "s2003-fresh-SM",
        "s2003-fresh-SS",
    ]
    assert Counter(cell["pairing"] for cell in cells) == dict.fromkeys(
        ("SS", "SM", "MS", "MM"), 4
    )
    plan = driver._make_plan({})
    assert plan["episodes"] == 12
    assert (
        sum(driver._attempt(plan, cell)["reserved_model_turns"] for cell in cells) == 16
    )
    assert plan["max_model_turns"] == 16
    assert plan["per_turn_budget_usd"] == 0.30
    assert plan["max_model_budget_usd"] == 4.80
    script, model = driver._trial_config(tmp_path, cells[1])["participants"]
    assert script == {
        "seat": "inventory",
        "name": None,
        "import_path": "seat_agent:SeatAgent",
        "model_name": None,
        "kwargs": {"policy": "arrival"},
    }
    assert model == {
        "seat": "dispatch",
        "name": None,
        "import_path": "native_model_agent:NativeModelAgent",
        "model_name": "anthropic/claude-sonnet-5-5",
        "kwargs": {},
    }


def test_frozen_sources_cover_local_agent_imports_and_task_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "study"
    plan = driver._register(root)
    assert driver._load_plan(root) == plan
    assert S_IMODE(root.stat().st_mode) == 0o700
    assert S_IMODE((root / "plan.json").stat().st_mode) == 0o400
    modules = root / "sources" / "seatlab"
    local = {path.stem for path in driver.run_panel.PANEL_DIR.glob("*.py")}
    pending = {"seat_agent", "native_model_agent"}
    seen = set()
    while pending:
        name = pending.pop()
        seen.add(name)
        frozen = modules / f"{name}.py"
        body = frozen.read_bytes()
        assert body == (driver.run_panel.PANEL_DIR / frozen.name).read_bytes()
        assert plan["source_sha256"][f"seatlab/{frozen.name}"] == (
            driver.run_panel._sha256(body)
        )
        for node in ast.walk(ast.parse(body)):
            if isinstance(node, ast.Import) or (
                isinstance(node, ast.ImportFrom) and node.module is None
            ):
                pending.update({alias.name for alias in node.names} & (local - seen))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module in local
                and node.module not in seen
            ):
                pending.add(node.module)
    assert {f"seatlab/{name}.py" for name in seen} == set(driver._AGENT_MODULES)
    scorer = "tasks/dispatch-handoff/tests/score.py"
    assert (root / "sources" / scorer).read_bytes() == (
        driver.run_panel.EXAMPLES_DIR / scorer
    ).read_bytes()
    for cell in plan["cells"]:
        assert driver._checked_cell(root, plan, cell) is None
        directory = driver._cell_dir(root, cell)
        task = directory / "dispatch-handoff"
        assert S_IMODE(directory.stat().st_mode) == 0o700
        assert S_IMODE(task.stat().st_mode) == 0o755
        scenario = (task / driver.run_panel.SCENARIO).read_bytes()
        assert (
            driver.run_panel._sha256(scenario)
            == (plan["scenario_sha256"][cell["cell_id"]])
        )
    path = modules / "native_model_agent.py"
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"\n")
    path.chmod(0o400)
    with pytest.raises(ValueError, match="frozen native team source differs"):
        driver._load_plan(root)


def _write_trial(
    root: Path,
    cell: driver.Cell,
    *,
    dispatch_stage: str = "complete",
    grade_present: bool = True,
) -> None:
    config = driver._trial_config(root, cell)
    trial = driver._trial_dir(root, cell)
    trial.parent.mkdir(mode=0o700, exist_ok=True)
    trial.mkdir(mode=0o700)
    trial.chmod(0o777)  # Native Harbor does this for Docker bind mounts.
    task = Path(config["task"]["path"])
    world_type = run_path(str(task / "environment/seatworld.py"))["World"]
    world = world_type(json.loads((task / driver.run_panel.SCENARIO).read_bytes()))
    inventory = world.view("inventory")
    world.submit("inventory", {"rows": inventory["cases"]})
    dispatch = world.view("dispatch")
    world.submit(
        "dispatch",
        {
            "rows": [
                {
                    "id": case["id"],
                    "bay": max(
                        case["messages"][-1]["available"],
                        key=case["utilities"].__getitem__,
                    ),
                }
                for case in dispatch["cases"]
            ]
        },
    )
    dossier = world.evidence()
    views = {"inventory": view_hash(inventory), "dispatch": view_hash(dispatch)}
    dossier["observation_receipts"] = {
        role: {"ack_sha256": digest} for role, digest in views.items()
    }
    score = _score(dossier)
    timestamp = "2026-11-19T00:00:00Z"
    timing = {"started_at": timestamp, "finished_at": timestamp}
    seats = []
    for participant in config["participants"]:
        role = participant["seat"]
        if participant["model_name"] is None:
            info = {"name": "seatlab-dispatch-handoff", "version": "1.0.0"}
            usage: dict[str, Any] = {
                "n_input_tokens": 0,
                "n_cache_tokens": 0,
                "n_output_tokens": 0,
                "cost_usd": 0.0,
            }
            metadata = {
                "schema": "seatlab.seat/v1",
                "role": role,
                "policy": "arrival",
                "decision_source": "script",
                "model_turns": 0,
            }
        else:
            model = "claude-sonnet-5-5"
            complete = role != "dispatch" or dispatch_stage == "complete"
            info = {
                "name": "seatlab-first-party",
                "version": "1.0.0",
                "model_info": {"name": model, "provider": "anthropic"},
            }
            model_usage = {
                "n_input_tokens": 9,
                "n_cache_tokens": 2,
                "n_output_tokens": 4,
                "cost_usd": 0.05,
            }
            usage = {**model_usage, "model_usage": {driver.MODEL: model_usage}}
            metadata = {
                "schema": "seatlab.native-model-seat/v1",
                "role": role,
                "requested_model": model,
                "served_model": model,
                "model_turns": 1,
                "stage": "complete" if complete else dispatch_stage,
                "view_sha256": views[role] if complete else None,
                "telemetry": {
                    "input_tokens": 6,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                    "output_tokens": 4,
                    "duration_ms": 100,
                    "duration_api_ms": 60,
                    "estimated_cost_usd": 0.05,
                },
            }
        seats.append(
            {
                "seat": role,
                "under_test": True,
                "exception_info": None,
                "agent_info": info,
                "agent_execution": timing,
                "agent_result": {"metadata": metadata, **usage},
            }
        )
    native = {
        "schema_version": 1,
        "id": str(uuid4()),
        "config": config,
        "trial_name": trial.name,
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        **timing,
        "exception_info": None,
        "reward_valid": grade_present and dispatch_stage == "complete",
        "verifier_result": (
            {"rewards": {"reward": score["optimal"] / score["episodes"]}}
            if grade_present
            else None
        ),
        "seat_results": seats,
    }
    (trial / "result.json").write_bytes(driver._bytes(native))
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(driver._bytes(dossier))


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root = tmp_path / "study"
    harbor = tmp_path / "harbor"
    harbor.touch()
    state: dict[str, Any] = {
        "root": root,
        "harbor": harbor,
        "auth_status": "verified",
        "auth_calls": 0,
        "launched": [],
        "returncode": 0,
        "write_trial": lambda cell: _write_trial(root, cell),
    }
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/fake-test-docker.sock")
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "SEATLAB_FREE_MODEL",
        "SEATLAB_FREE_PROVIDER",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(key, "not-for-native-trials")

    def check_harbor(path: Path, env: dict[str, str]) -> None:
        assert path == harbor
        assert env["PYTHONPATH"] == str(root / "sources" / "seatlab")
        assert env["DOCKER_HOST"].startswith("unix:///")

    def preflight(invoke: object) -> str:
        assert invoke is driver.claude_replay._invoke
        assert (root / "plan.json").exists()
        state["auth_calls"] += 1
        return state["auth_status"]

    def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        directory = kwargs["cwd"]
        plan = driver._load_plan(root)
        cell = next(cell for cell in plan["cells"] if cell["cell_id"] == directory.name)
        assert command == [
            str(harbor),
            "trial",
            "start",
            "-c",
            str(directory / "config.json"),
        ]
        assert kwargs["env"]["PYTHONPATH"] == str(root / "sources" / "seatlab")
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["umask"] == 0o077
        assert "not-for-native-trials" not in kwargs["env"].values()
        assert (directory / "attempt.json").read_bytes() == driver._bytes(
            driver._attempt(plan, cell)
        )
        assert not (directory / "outcome.json").exists()
        state["launched"].append(cell["cell_id"])
        kwargs["stdout"].write(b"private native output\n")
        state["write_trial"](cell)
        return subprocess.CompletedProcess(command, state["returncode"])

    monkeypatch.setattr(driver.run_panel, "_check_native", check_harbor)
    monkeypatch.setattr(driver.claude_replay, "_preflight", preflight)
    monkeypatch.setattr(driver.subprocess, "run", launch)
    return state


def _result(root: Path) -> dict[str, Any]:
    return json.loads((root / "results.json").read_bytes())


def test_run_claims_whole_grid_once_and_collect_never_launches(
    runtime: dict[str, Any],
) -> None:
    root, harbor = runtime["root"], runtime["harbor"]
    assert driver.main(["run", "--out", str(root), "--harbor", str(harbor)]) == 0
    assert runtime["launched"] == [cell["cell_id"] for cell in driver._grid()]
    assert runtime["auth_calls"] == 1
    reservations = [
        json.loads(path.read_bytes()) for path in root.glob("cells/*/attempt.json")
    ]
    assert len(reservations) == 16
    assert sum(record["reserved_model_turns"] for record in reservations) == 16
    with pytest.raises(ValueError, match="already exists"):
        driver._run(root, harbor)
    before = (root / "results.json").read_bytes()
    assert driver.main(["collect", "--out", str(root)]) == 0
    assert (root / "results.json").read_bytes() == before
    assert runtime["auth_calls"] == 1
    assert len(runtime["launched"]) == 16
    assert str(root).encode() not in before
    assert b"private native output" not in before


def test_preflight_failure_has_no_fallback_or_cell_attempt(
    runtime: dict[str, Any],
) -> None:
    root, harbor = runtime["root"], runtime["harbor"]
    runtime["auth_status"] = "unverified_max_auth"
    assert not driver._run(root, harbor)
    report = _result(root)
    assert report["preflight"] == "unverified_max_auth"
    assert report["stop"] == {"reason": "preflight_failed", "cell_id": None}
    assert report["counts"]["not_attempted"] == 16
    assert runtime["auth_calls"] == 1
    assert not runtime["launched"]
    assert not tuple(root.glob("cells/*/attempt.json"))


def test_failed_launch_still_consumes_the_registered_attempt(
    runtime: dict[str, Any],
) -> None:
    root, harbor = runtime["root"], runtime["harbor"]

    def launch_failure(cell: driver.Cell) -> None:
        raise OSError("private native process failure")

    runtime["write_trial"] = launch_failure
    assert not driver._run(root, harbor)
    plan = driver._load_plan(root)
    cell = plan["cells"][0]
    directory = driver._cell_dir(root, cell)
    assert driver._outcome(directory / "outcome.json") == {
        "returncode": None,
        "launch_error": "native_launch_failed",
    }
    assert _result(root)["cells"][0]["status"] == "unmeasured"
    assert b"private native process failure" not in (root / "results.json").read_bytes()
    with pytest.raises(ValueError, match="already has an attempt"):
        driver._run_cell(root, plan, cell, harbor, {})
    assert len(runtime["launched"]) == 1


@pytest.mark.parametrize("fault", ["missing_grade", "model_invalid", "stop"])
def test_run_stops_before_opening_later_cells(
    runtime: dict[str, Any], fault: str
) -> None:
    root, harbor = runtime["root"], runtime["harbor"]

    def write_trial(cell: driver.Cell) -> None:
        if fault in ("missing_grade", "model_invalid") and cell["pairing"] == "SM":
            if fault == "model_invalid":
                _write_trial(root, cell, dispatch_stage="invalid_or_refused_decisions")
            else:
                _write_trial(root, cell, grade_present=False)
            return
        _write_trial(root, cell)
        if fault == "stop":
            (root / "STOP").touch()

    runtime["write_trial"] = write_trial
    assert not driver._run(root, harbor)
    report = _result(root)
    stopped_by_operator = fault == "stop"
    assert report["stop"] == {
        "reason": "stop_requested" if stopped_by_operator else "cell_not_measured",
        "cell_id": None if stopped_by_operator else "s2001-fresh-SM",
    }
    assert report["cells"][0]["status"] == "measured"
    assert (
        report["cells"][1]["status"]
        == {
            "stop": "not_attempted",
            "model_invalid": "invalid",
            "missing_grade": "unmeasured",
        }[fault]
    )
    if fault == "missing_grade":
        assert report["cells"][1]["error"] == "native_grade_missing"
    elif fault == "model_invalid":
        assert report["cells"][1]["error"] == "model_invalid_or_refused_decisions"
    assert report["cells"][2]["status"] == "not_attempted"
    assert len(runtime["launched"]) == (1 if stopped_by_operator else 2)


def test_outcome_does_not_create_or_veto_native_reward(tmp_path: Path) -> None:
    root = tmp_path / "study"
    plan = driver._register(root)
    cell, untouched = plan["cells"][:2]
    directory = driver._cell_dir(root, cell)
    attempt = directory / "attempt.json"
    outcome = directory / "outcome.json"
    assert team_report.inspect_cell(root, plan, cell)["status"] == "not_attempted"
    driver._seal(attempt, driver._bytes(driver._attempt(plan, cell)))
    driver._seal(outcome, driver._bytes({"returncode": 0, "launch_error": None}))
    assert team_report.inspect_cell(root, plan, cell)["status"] == "unmeasured"
    outcome.unlink()
    _write_trial(root, cell)
    row = team_report.inspect_cell(root, plan, cell)
    assert row["status"] == "measured"
    assert row["native_returncode"] is None
    assert row["metrics"] is not None
    assert row["metrics"]["optimal"] == 12
    driver._seal(outcome, driver._bytes({"returncode": 17, "launch_error": None}))
    row = team_report.inspect_cell(root, plan, cell)
    assert row["status"] == "measured"
    assert row["native_returncode"] == 17
    bare = driver._cell_dir(root, untouched) / "outcome.json"
    driver._seal(bare, driver._bytes({"returncode": 0, "launch_error": None}))
    assert team_report.inspect_cell(root, plan, untouched)["status"] == "invalid"


def test_raw_trial_paths_cannot_escape_private_registration(tmp_path: Path) -> None:
    root = tmp_path / "study"
    plan = driver._register(root)
    cell = plan["cells"][0]
    directory = driver._cell_dir(root, cell)
    trial = driver._trial_dir(root, cell)
    driver._seal(directory / "attempt.json", driver._bytes(driver._attempt(plan, cell)))
    trial.parent.mkdir(mode=0o700)
    extra = trial.parent / "unexpected"
    extra.mkdir()
    with pytest.raises(ValueError, match="unexpected native trial output"):
        driver._checked_cell(root, plan, cell)
    extra.rmdir()
    trial.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe native team registration path"):
        driver._checked_cell(root, plan, cell)
