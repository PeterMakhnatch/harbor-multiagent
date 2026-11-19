"""Launch the preregistered native two-model Dispatch Handoff collection.

Registered design (see protocol.md): seeds 2003 and 2017, condition
``reordered``, 8 shipments per world, 4 teams per seed. Seed 2003 runs
ss, ms, sm, mm; seed 2017 runs the reverse. Each trial is launched exactly once
with the stock Olam local Docker runtime. Failures are recorded, never retried,
replaced, or skipped. This file only launches; it does not analyze or score.

Output layout (OUT is a new private directory; never reuse it):
  OUT/plan.json                    read-only registered plan and SHA-256 manifest
  OUT/started.json                 read-only launch reservation marker
  OUT/cells/<cell_id>/             one folder per world attempt, <cell_id> = s<seed>-<team>
      trial.json                   stock native trial config
      harbor.log                   private launch log (not for publication)
      launch.json                  read-only record written before the launch
      receipt.json                 read-only exit receipt written after the launch
      native/<trial_name>/         stock native output (raw evidence, result.json)
  OUT/attempts.json                raw index of all 8 attempts (written once)

attempts.json keys:
  schema, plan_sha256, preflight, complete, attempts[8].
  Each attempt: position (1-8), cell_id, seed, team, roles
  {inventory, dispatch} in {script, first-party}, trial_path (relative to OUT,
  the native trial directory holding result.json and the seat dossiers),
  cell_path, receipt_path (relative to OUT), attempted (bool),
  returncode (int or null), timed_out (bool), launch_error (string or null).
  A null returncode with attempted true means timeout or launch error. The
  integrator should read raw evidence from OUT/<trial_path>; the exit code only
  reports process status and never decides whether a trial is measured.
"""

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
sys.path.insert(0, str(PARENT))

import claude_replay  # noqa: E402
import run_panel  # noqa: E402

PLAN_SCHEMA = "seatlab.native-model-plan/v1"
ATTEMPTS_SCHEMA = "seatlab.native-model-attempts/v1"
CONDITION = "reordered"
EPISODES = 8
SEEDS = (2003, 2017)
TEAMS = ("ss", "ms", "sm", "mm")
ROLE_POLICY = {"s": "script", "m": "first-party"}
IMPORT_PATH = "native_actor:NativeHandoffAgent"
LAUNCH_TIMEOUT_SECONDS = 420
ACTOR = HERE / "native_actor.py"
COLLECTOR = Path(__file__).resolve()
SOURCE_POLICY = "arrival"  # snapshot includes the seat adapter and every task file


def _order() -> list[tuple[int, str]]:
    return [
        (seed, team)
        for seed, teams in zip(SEEDS, (TEAMS, TEAMS[::-1]), strict=True)
        for team in teams
    ]


def _cell(seed: int, team: str) -> run_panel.Cell:
    return {"cell_id": f"s{seed}-{team}", "seed": seed, "condition": CONDITION}


def _roles(team: str) -> dict[str, str]:
    return {
        "inventory": ROLE_POLICY[team[0]],
        "dispatch": ROLE_POLICY[team[1]],
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _create(path: Path, value: object, mode: int = 0o400) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(run_panel._json(value))
        stream.flush()
        os.fsync(stream.fileno())


def _participants(team: str) -> list[dict[str, Any]]:
    roles = _roles(team)
    return [
        {
            "seat": seat,
            "model_name": None,
            "name": None,
            "import_path": IMPORT_PATH,
            "kwargs": {"policy": roles[seat]},
        }
        for seat in ("inventory", "dispatch")
    ]


def _plan(sources: dict[str, bytes], harbor: Path) -> dict[str, Any]:
    source_sha = {
        name: hashlib.sha256(content).hexdigest() for name, content in sources.items()
    }
    return {
        "schema": PLAN_SCHEMA,
        "seeds": list(SEEDS),
        "condition": CONDITION,
        "episodes": EPISODES,
        "teams": list(TEAMS),
        "import_path": IMPORT_PATH,
        "role_policies": {team: _roles(team) for team in TEAMS},
        "launch_timeout_seconds": LAUNCH_TIMEOUT_SECONDS,
        "retries": 0,
        "native": {
            "expected_version": run_panel.NATIVE_VERSION,
            "olam_base": run_panel.OLAM_BASE,
            "harbor_executable_sha256": _sha(harbor),
        },
        "sha256": {
            "actor": {"native-model/native_actor.py": _sha(ACTOR)},
            "collector": {"native-model/collect.py": _sha(COLLECTOR)},
            # Original task and adapter bytes, which are the upstream baseline
            # files the native panel snapshot uses.
            "task_and_adapter": source_sha,
        },
        "cells": [
            {
                "position": index,
                **_cell(seed, team),
                "team": team,
                "roles": _roles(team),
            }
            for index, (seed, team) in enumerate(_order(), start=1)
        ],
    }


def _verify_unchanged(plan: dict[str, Any], sources: dict[str, bytes]) -> None:
    sha = plan["sha256"]
    if (
        _sha(ACTOR) != sha["actor"]["native-model/native_actor.py"]
        or _sha(COLLECTOR) != sha["collector"]["native-model/collect.py"]
    ):
        raise ValueError("actor or collector changed after preregistration")
    for name, content in sources.items():
        if (run_panel.EXAMPLES_DIR / name).read_bytes() != content:
            raise ValueError(f"registered source changed: {name}")


def _env() -> dict[str, str]:
    env = run_panel._native_env(None)
    env["PYTHONPATH"] = os.pathsep.join((str(PARENT), str(HERE)))
    if any(
        part in key.upper() for key in env for part in ("API_KEY", "TOKEN", "SECRET")
    ):
        raise ValueError("credentials must not be present in the trial environment")
    return env


def _launch(
    out: Path,
    plan: dict[str, Any],
    sources: dict[str, bytes],
    harbor: Path,
    env: dict[str, str],
    position: int,
    seed: int,
    team: str,
) -> dict[str, Any]:
    cell = _cell(seed, team)
    directory = run_panel._cell_dir(out, cell)
    row: dict[str, Any] = {
        "position": position,
        "cell_id": cell["cell_id"],
        "seed": seed,
        "team": team,
        "roles": _roles(team),
        "trial_path": None,
        "cell_path": directory.relative_to(out).as_posix(),
        "receipt_path": (directory / "receipt.json").relative_to(out).as_posix(),
        "attempted": False,
        "returncode": None,
        "timed_out": False,
        "launch_error": None,
    }
    directory.mkdir(parents=True, mode=0o700)
    started = _now()
    code: int | None = None
    timed_out = False
    error: str | None = None
    try:
        _verify_unchanged(plan, sources)
        run_panel._prepare_cell(directory, plan, cell, sources)  # type: ignore[arg-type]
        config = run_panel._trial_config(out, {"policy": SOURCE_POLICY}, cell)  # type: ignore[arg-type]
        config["participants"] = _participants(team)
        trial = Path(config["trials_dir"]) / config["trial_name"]
        row["trial_path"] = trial.relative_to(out).as_posix()
        config_path = directory / "trial.json"
        run_panel._create_json(config_path, config)
        _create(
            directory / "launch.json",
            {"position": position, "cell_id": cell["cell_id"], "at": started},
        )
        row["attempted"] = True
        with (directory / "harbor.log").open("xb") as log:
            os.fchmod(log.fileno(), 0o600)
            try:
                code = subprocess.run(
                    [str(harbor), "trial", "start", "-c", str(config_path)],
                    cwd=directory,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                    check=False,
                ).returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                error = "launch_timeout"
    except (OSError, ValueError, KeyError) as exc:
        error = f"setup_{type(exc).__name__}"
    row.update(returncode=code, timed_out=timed_out, launch_error=error)
    _create(
        directory / "receipt.json",
        {
            "position": position,
            "cell_id": cell["cell_id"],
            "started_at": started,
            "ended_at": _now(),
            "attempted": row["attempted"],
            "returncode": code,
            "timed_out": timed_out,
            "launch_error": error,
        },
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="new scratch directory")
    parser.add_argument(
        "--harbor", type=Path, required=True, help="stock Olam Harbor executable"
    )
    args = parser.parse_args(argv)
    try:
        requested = args.out.expanduser()
        out = requested.resolve()
        if (
            out.exists()
            or requested.is_symlink()
            or out.is_relative_to(PARENT)
            or any(os.path.lexists(p / ".git") for p in (out, *out.parents))
        ):
            raise ValueError(
                "output must be new, outside the sources, and outside any git checkout"
            )
        if not ACTOR.is_file():
            raise ValueError("native_actor.py is missing")
        harbor = args.harbor.expanduser().resolve(strict=True)
        sources = run_panel._snapshot(SOURCE_POLICY)
        for helper in ("claude_replay.py", "capsule.py", "paired.py"):
            sources[f"seatlab/{helper}"] = (PARENT / helper).read_bytes()
        plan = _plan(sources, harbor)
        env = _env()
        run_panel._check_native(harbor, env)
        # Fail closed before reserving anything or generating. Print a code only.
        route = claude_replay._preflight(claude_replay._invoke)
        if route != "verified":
            parser.exit(2, f"collect.py: route preflight failed: {route}\n")
        out.mkdir(mode=0o700, parents=True)
        _create(out / "plan.json", plan)
        plan_sha = _sha(out / "plan.json")
        _create(out / "started.json", {"plan_sha256": plan_sha, "at": _now()})
        rows: list[dict[str, Any]] = []
        try:
            for position, (seed, team) in enumerate(_order(), start=1):
                rows.append(
                    _launch(out, plan, sources, harbor, env, position, seed, team)
                )
        finally:
            done = {row["cell_id"] for row in rows}
            for position, (seed, team) in enumerate(_order(), start=1):
                cell = _cell(seed, team)
                if cell["cell_id"] not in done:
                    rows.append(
                        {
                            "position": position,
                            "cell_id": cell["cell_id"],
                            "seed": seed,
                            "team": team,
                            "roles": _roles(team),
                            "trial_path": None,
                            "cell_path": None,
                            "receipt_path": None,
                            "attempted": False,
                            "returncode": None,
                            "timed_out": False,
                            "launch_error": "not_attempted",
                        }
                    )
            rows.sort(key=lambda row: row["position"])
            _create(
                out / "attempts.json",
                {
                    "schema": ATTEMPTS_SCHEMA,
                    "plan_sha256": plan_sha,
                    "preflight": route,
                    "complete": len(rows) == 8
                    and all(row["attempted"] for row in rows),
                    "attempts": rows,
                },
            )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"collect.py: {exc}\n")
    print(f"Attempts: {out / 'attempts.json'}")
    return 0 if all(row["returncode"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
