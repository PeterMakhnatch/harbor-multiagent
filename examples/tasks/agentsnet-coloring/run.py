from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial

TASK = Path(__file__).resolve().parent
REPO = TASK.parents[2]
SDK = REPO / "src/harbor"
MODES = ("oracle", "nop", "conflict", "deepseek")
SEATS = {"n0", "n1", "n2", "n3"}
METRICS = {"edge_fraction", "proper_coloring"}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", *args],
        capture_output=True,
        check=True,
        text=True,
        timeout=5,
    ).stdout.strip()


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _runtime(mode: str) -> dict[str, Any]:
    import harbor

    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    imported = {
        "harbor": Path(inspect.getfile(harbor)).resolve(),
        "Trial": Path(inspect.getfile(Trial)).resolve(),
        "TrialConfig": Path(inspect.getfile(TrialConfig)).resolve(),
    }
    if any(not path.is_relative_to(SDK) for path in imported.values()):
        raise RuntimeError(f"Harbor did not load from {SDK}: {imported}")
    declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        "mode": mode,
        "repo_root": str(REPO),
        "repo_revision": _git("HEAD"),
        "task_root": str(TASK),
        "sdk_root": str(SDK),
        "sdk_modules": {name: str(path) for name, path in imported.items()},
        "sdk_runtime_version": harbor.__version__,
        "sdk_source_version": declared["project"]["version"],
        "python": sys.executable,
        "trial_retries": 0,
        "model_call_concurrency": 1 if mode == "deepseek" else None,
        "transport_retries": 0 if mode == "deepseek" else None,
    }


def _config(mode: str, output: Path) -> TrialConfig:
    import yaml

    from harbor.agents.factory import AgentFactory
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.seats import check_launch

    data = yaml.safe_load((TASK / f"{mode}.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "agent" in data:
        raise ValueError("A native seat trial must not specify a top-level agent")
    if data.get("task") != {"path": TASK.relative_to(REPO).as_posix()}:
        raise ValueError("Trial config must reference this example task")
    data["task"] = {"path": TASK}
    data["trials_dir"] = output / "trials"
    config = TrialConfig.model_validate(data)
    task = Task(TASK)
    if set(task.config.agents) != SEATS or not all(
        seat.under_test for seat in task.config.agents.values()
    ):
        raise ValueError("The AgentsNet example requires 4 seats under test")
    check_launch(config, task)
    for participant in config.participants:
        AgentFactory.get_agent_class_from_config(participant)
    return config


def _result_summary(trial: Trial, result: TrialResult) -> dict[str, Any]:
    seats = result.seat_results or []
    rewards = result.verifier_result.rewards if result.verifier_result else None
    exceptions = result.recorded_exceptions()
    contexts = [seat.agent_result for seat in seats if seat.agent_result is not None]
    observed_costs = [c.cost_usd for c in contexts if c.cost_usd is not None]
    metadata = [c.metadata for c in contexts if c.metadata is not None]
    complete = not exceptions and METRICS.issubset(rewards or {})
    return {
        "status": "complete" if complete else "failed",
        "trial_dir": str(trial.paths.trial_dir),
        "result_path": str(trial.paths.result_path),
        "task_checksum": result.task_checksum,
        "verifier_rewards": rewards,
        "reward_valid": result.reward_valid,
        "seat_results": [seat.model_dump(mode="json") for seat in seats],
        "exception_types": [exc.exception_type for exc in exceptions],
        "started_model_calls": sum(m.get("started_calls", 0) for m in metadata),
        "observed_usage_calls": sum(m.get("observed_usage_calls", 0) for m in metadata),
        "observed_model_cost_usd": math.fsum(observed_costs)
        if observed_costs
        else None,
        "unknown_usage_calls": sum(m.get("unknown_usage_calls", 0) for m in metadata),
        "unknown_usage_reserve_usd": math.fsum(
            m.get("unknown_usage_reserve_usd", 0) for m in metadata
        ),
    }


async def _run(config: TrialConfig) -> dict[str, Any]:
    from harbor.trial.seats import SeatsTrial
    from harbor.trial.trial import Trial

    trial = await Trial.create(config)
    if not isinstance(trial, SeatsTrial):
        raise RuntimeError("Harbor did not create a native seat trial")
    return _result_summary(trial, await trial.run())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one native AgentsNet Coloring trial."
    )
    parser.add_argument("mode", choices=MODES)
    parser.add_argument(
        "output", type=Path, help="Fresh directory outside the Harbor repository"
    )
    parser.add_argument(
        "--check", action="store_true", help="Import and validate without a trial"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    checkout = Path(_git("--path-format=absolute", "--git-common-dir")).resolve().parent
    if output.is_relative_to(REPO) or output.is_relative_to(checkout):
        parser.error("The output directory must be outside the Harbor repository")
    output.mkdir(mode=0o700)
    sys.path[:0] = [str(REPO / "src"), str(TASK)]
    summary: dict[str, Any] = {"mode": args.mode}
    try:
        runtime = _runtime(args.mode)
        _write(output / "runtime.json", runtime)
        summary.update(runtime)
        config = _config(args.mode, output)
        if args.check:
            summary.update(
                status="checked", seats=[p.seat for p in config.participants]
            )
        else:
            summary.update(asyncio.run(_run(config)))
    except BaseException as exc:
        summary.update(status="failed", launcher_exception_type=type(exc).__name__)
        raise
    finally:
        _write(output / "summary.json", summary)
    print(output / "summary.json")
    return 0 if summary["status"] in {"checked", "complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
