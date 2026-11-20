import json
import os
import subprocess
import sys
from pathlib import Path
from stat import S_IMODE
from unittest.mock import Mock

import pytest

from examples.seatlab.committee import driver, schedule
from examples.seatlab.visibility import deepseek, spend


def test_plan_has_only_public_registered_cells(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert driver.main(["plan"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == {
        "schema": "seatlab.committee-plan/v1",
        "task_git_tree": schedule.TASK_TREE,
        "task_checksum": schedule.TASK_CHECKSUM,
        "roles": list(schedule.ROLES),
        "model": "deepseek/deepseek-flash",
        "cells": schedule.grid(),
    }


@pytest.mark.parametrize(
    "change", ["world_bytes", "extra_directory", "symlink", "mode"]
)
def test_stock_tree_check_precedes_native_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    task = tmp_path / "hidden_profile_unshared"
    environment = task / "environment"
    environment.mkdir(parents=True)
    world = environment / "world.py"
    world.write_bytes(b"stock world\n")
    monkeypatch.setattr(schedule, "TASK_TREE", driver._git_tree(task).hex())
    driver._check_task_tree(task)
    if change == "world_bytes":
        world.write_bytes(b"different world\n")
    elif change == "extra_directory":
        (environment / "new").mkdir()
    elif change == "symlink":
        world.rename(tmp_path / "original.py")
        world.symlink_to(tmp_path / "original.py")
    else:
        world.chmod(0o755)
    helpers = Mock()
    monkeypatch.setattr(driver, "_helpers", helpers)

    with pytest.raises(ValueError, match="stock_task_tree_mismatch"):
        driver._preflight(tmp_path / "harbor", task)
    helpers.assert_not_called()


def test_host_child_binds_before_loading_harbor_with_process_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.json"
    harbor = tmp_path / "harbor"
    stock = tmp_path / "hidden_profile_unshared"
    config.write_text(json.dumps({"task": {"path": str(stock)}}))
    sequence = []
    monkeypatch.setattr(
        sys, "argv", ["driver", "_host_trial", str(config), str(harbor)]
    )
    # Use an independent mapping because the child intentionally scrubs its env.
    monkeypatch.setattr(
        os,
        "environ",
        {
            "DEEPSEEK_API_KEY": "dummy-key",
            "SEATLAB_SPEND_DIR": str(tmp_path),
            "OPENAI_API_KEY": "do-not-forward",
            "UV_INDEX_SECRET_PASSWORD": "do-not-forward",
            "HARBOR_CONFIG_FILE": "do-not-forward",
        },
    )
    monkeypatch.setattr(deepseek, "_BOUND", False)
    monkeypatch.setattr(deepseek, "_KEY", None)

    def bind_spend() -> None:
        assert "DEEPSEEK_API_KEY" not in os.environ
        assert deepseek._key() == "dummy-key"
        assert os.environ.pop("SEATLAB_SPEND_DIR") == str(tmp_path)
        sequence.append("bind_spend")

    def validate_task(path: Path) -> None:
        assert path == stock
        assert not os.environ
        sequence.append("validate_task")

    def run_entrypoint(path: str, *, run_name: str) -> None:
        assert path == str(harbor) and run_name == "__main__"
        assert sys.argv == [str(harbor), "trial", "start", "-c", str(config)]
        assert not os.environ
        assert deepseek._key() == "dummy-key"
        sequence.append("harbor")

    monkeypatch.setattr(spend, "bind_host_spend_dir", bind_spend)
    monkeypatch.setattr(driver, "_check_task_tree", lambda path: None)
    monkeypatch.setattr(driver, "_check_runtime_task", validate_task)
    monkeypatch.setattr(driver.runpy, "run_path", run_entrypoint)

    assert driver.main() == 0
    assert sequence == ["bind_spend", "validate_task", "harbor"]


def test_launcher_uses_only_explicit_private_host_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = tmp_path / "cells" / "cell"
    directory.mkdir(parents=True)
    harbor = tmp_path / "venv/bin/harbor"
    python = harbor.with_name("python")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-key")
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "never-forward")
    monkeypatch.setenv("XDG_CONFIG_HOME", "never-forward")
    native_env = driver._native_env(
        {
            "PATH": "/usr/bin",
            "DOCKER_HOST": "unix:///local/docker.sock",
            "DOCKER_CONFIG": "never-forward",
            "DEEPSEEK_API_KEY": "never-forward",
        }
    )
    assert "DEEPSEEK_API_KEY" not in native_env
    assert "SEATLAB_SPEND_DIR" not in native_env
    assert "DOCKER_CONFIG" not in native_env

    def child(command, *, env, cwd, stdout, umask, **kwargs):
        assert command[0] == str(python)
        assert command[-3:] == [
            "_host_trial",
            str(directory / "config.json"),
            str(harbor),
        ]
        assert cwd == directory
        assert umask == 0o077
        assert env == {
            **native_env,
            "HOME": env["HOME"],
            "DEEPSEEK_API_KEY": "dummy-key",
            "SEATLAB_SPEND_DIR": str(tmp_path),
        }
        assert Path(env["HOME"]).parent == tmp_path
        assert S_IMODE(Path(env["HOME"]).stat().st_mode) == 0o700
        assert S_IMODE(os.fstat(stdout.fileno()).st_mode) == 0o600
        return subprocess.CompletedProcess(command, 0)

    run = Mock(side_effect=child)
    monkeypatch.setattr(driver.subprocess, "run", run)
    assert driver._launch(directory, harbor, python, native_env) == 0
    run.assert_called_once()


def test_zero_native_exit_without_seat_results_is_unreclaimable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task = tmp_path / "hidden_profile_unshared"
    task.mkdir()
    root = tmp_path / "out"
    cell = schedule.grid()[0]
    harbor = tmp_path / "harbor"
    python = tmp_path / "python"
    preflight = Mock(return_value=(harbor, python, {}))
    launch = Mock(return_value=0)
    monkeypatch.setattr(driver, "_preflight", preflight)
    monkeypatch.setattr(driver, "_launch", launch)
    args = [
        "run",
        "--out",
        str(root),
        "--cell",
        cell["cell_id"],
        "--task",
        str(task),
        "--harbor",
        str(harbor),
    ]
    assert driver.main(args) == 1
    assert driver.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "native_trial_incomplete" in captured.err
    assert "attempt_already_claimed_do_not_retry" in captured.err
    preflight.assert_called_once_with(harbor, task.resolve())
    directory = root / "cells" / cell["cell_id"]
    launch.assert_called_once_with(directory.resolve(), harbor, python, {})
    for path in (root, directory.parent, directory, directory / "trials"):
        assert S_IMODE(path.stat().st_mode) == 0o700
    for name in ("attempt.json", "config.json", "outcome.json"):
        assert S_IMODE((directory / name).stat().st_mode) == 0o400
    config = driver._json_object(directory / "config.json")
    assert config["task"] == {"path": str(task.resolve())}
    assert config["participants"] == [
        {
            "seat": role,
            "import_path": "examples.seatlab.committee.seat:CommitteeSeat",
            "model_name": "deepseek/deepseek-flash",
            "kwargs": {
                "role": role,
                "run_id": cell["cell_id"],
                "arm": cell["arm"],
                "map_index": cell["map_index"],
            },
        }
        for role in schedule.ROLES
    ]
    assert driver._json_object(directory / "attempt.json") == {
        "schema": "seatlab.committee-attempt/v1",
        **cell,
        "trial_name": "committee-" + cell["cell_id"],
        "task_git_tree": schedule.TASK_TREE,
    }
    assert driver._json_object(directory / "outcome.json") == {
        "schema": "seatlab.committee-launch/v1",
        "returncode": 0,
        "error": "native_trial_incomplete",
    }


def test_native_zero_reward_is_complete_only_with_four_returned_seats(
    tmp_path: Path,
) -> None:
    cell = schedule.grid()[0]
    config = driver._trial_config(tmp_path, tmp_path / "hidden_profile_unshared", cell)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    trial.mkdir(parents=True)
    seats = [
        {
            "seat": p["seat"],
            "under_test": True,
            "exception_info": None,
            "agent_result": {
                "metadata": {
                    "schema": "seatlab.committee-seat/v1",
                    "status": "complete",
                    **p["kwargs"],
                }
            },
        }
        for p in config["participants"]
    ]
    result = {
        "task_name": "crucible/epistemic-crucible-hidden_profile_unshared",
        "task_checksum": schedule.TASK_CHECKSUM,
        "trial_name": config["trial_name"],
        "finished_at": "2026-11-20T00:00:01Z",
        "exception_info": None,
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 0}},
        "seat_results": seats,
    }
    path = trial / "result.json"
    path.write_text(json.dumps(result))
    assert driver._completed(config)
    seats[0]["exception_info"] = {"exception_type": "SeatFailure"}
    path.write_text(json.dumps(result))
    assert not driver._completed(config)
    seats[0]["exception_info"] = None
    seats.pop()
    path.write_text(json.dumps(result))
    assert not driver._completed(config)
