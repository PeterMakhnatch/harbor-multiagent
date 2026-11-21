"""Register, run once, and collect the fixed native Dispatch Handoff team study."""

import argparse
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING or __package__:
    from . import claude_replay, run_panel, team_report
else:
    import claude_replay
    import run_panel
    import team_report

PLAN_SCHEMA = "seatlab.native-team-plan/v1"
ATTEMPT_SCHEMA = "seatlab.native-team-attempt/v1"
MODEL = "anthropic/claude-sonnet-5-5"
EPISODES = 12
MAX_MODEL_TURNS = 16
PER_TURN_BUDGET_USD = 0.30
_TRIAL_TIMEOUT_SEC = 600
_CELL_FILES = {
    "config.json",
    "dispatch-handoff",
    "attempt.json",
    "outcome.json",
    "native.log",
    "trials",
}
_PAIRINGS = ("SS", "SM", "MS", "MM")
_ROLES = ("inventory", "dispatch")
# All SeatLab modules imported by the native agents, including lazy imports.
# The task snapshot also includes score.py, which evidence imports by file path.
_AGENT_MODULES = (
    "seatlab/seat_agent.py",
    "seatlab/native_model_agent.py",
    "seatlab/claude_replay.py",
    "seatlab/capsule.py",
    "seatlab/evidence.py",
    "seatlab/export_capsules.py",
    "seatlab/paired.py",
)
_STUDY_TOOLS = ("seatlab/team_driver.py", "seatlab/team_report.py")
_SOURCE_NAMES = frozenset(
    (*run_panel._source_names("arrival"), *_AGENT_MODULES, *_STUDY_TOOLS)
)
_PREFLIGHT_ERRORS = frozenset(
    {
        "native_preflight_failed",
        "managed_configuration",
        "cli_unavailable",
        "auth_timeout",
        "unverified_max_auth",
    }
)


class Cell(run_panel.Cell):
    pairing: str


class Plan(TypedDict):
    schema: str
    episodes: int
    model: str
    max_model_turns: int
    per_turn_budget_usd: float
    max_model_budget_usd: float
    native: dict[str, str]
    actors: dict[str, dict[str, Any]]
    source_sha256: dict[str, str]
    scenario_sha256: dict[str, str]
    cells: list[Cell]


def _grid() -> list[Cell]:
    return [
        {
            "cell_id": f"s{seed}-{condition}-{pairing}",
            "seed": seed,
            "condition": condition,
            "pairing": pairing,
        }
        for seed, conditions, pairings in (
            (2001, ("fresh", "reordered"), _PAIRINGS),
            (2003, ("reordered", "fresh"), _PAIRINGS[::-1]),
        )
        for condition in conditions
        for pairing in pairings
    ]


def _actor(kind: str) -> dict[str, Any]:
    if kind == "S":
        return {
            "name": None,
            "import_path": "seat_agent:SeatAgent",
            "model_name": None,
            "kwargs": {"policy": "arrival"},
        }
    if kind == "M":
        return {
            "name": None,
            "import_path": "native_model_agent:NativeModelAgent",
            "model_name": MODEL,
            "kwargs": {},
        }
    raise ValueError("unknown registered actor")


def _bytes(value: object) -> bytes:
    return run_panel._json(value).encode("utf-8")


def _panel_plan(source_sha256: dict[str, str]) -> run_panel.Plan:
    return run_panel._make_plan(
        [2001, 2003], ["fresh", "reordered"], EPISODES, "arrival", source_sha256
    )


def _make_plan(source_sha256: dict[str, str]) -> Plan:
    cells = _grid()
    if sum(cell["pairing"].count("M") for cell in cells) != MAX_MODEL_TURNS:
        raise ValueError("registered grid does not match the study turn cap")
    panel_plan = _panel_plan(source_sha256)
    return {
        "schema": PLAN_SCHEMA,
        "episodes": EPISODES,
        "model": MODEL,
        "max_model_turns": MAX_MODEL_TURNS,
        "per_turn_budget_usd": PER_TURN_BUDGET_USD,
        "max_model_budget_usd": 4.80,
        "native": panel_plan["native"],
        "actors": {kind: _actor(kind) for kind in ("S", "M")},
        "source_sha256": source_sha256,
        "scenario_sha256": {
            cell["cell_id"]: run_panel._sha256(
                _bytes(run_panel._scenario(panel_plan, cell))
            )
            for cell in cells
        },
        "cells": cells,
    }


def _snapshot() -> dict[str, bytes]:
    sources = run_panel._snapshot("arrival")
    sources.update(
        (name, (run_panel.EXAMPLES_DIR / name).read_bytes())
        for name in sorted(_SOURCE_NAMES - sources.keys())
    )
    return sources


def _cell_dir(root: Path, cell: Cell) -> Path:
    return root / "cells" / cell["cell_id"]


def _trial_name(root: Path, cell: Cell) -> str:
    digest = run_panel._sha256(os.fsencode(_cell_dir(root, cell)))[:24]
    return f"seatlab-team-{digest}"


def _trial_dir(root: Path, cell: Cell) -> Path:
    return _cell_dir(root, cell) / "trials" / _trial_name(root, cell)


def _trial_config(root: Path, cell: Cell) -> dict[str, Any]:
    directory = _cell_dir(root, cell)
    return {
        "schema_version": 1,
        "task": {
            "path": str(directory / "dispatch-handoff"),
            "git_url": None,
            "name": None,
        },
        "trial_name": _trial_name(root, cell),
        "trials_dir": str(directory / "trials"),
        # MM can require two sequential 90 s turn deadlines plus setup and commits.
        "agent_timeout_multiplier": 1.5,
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": [
            {"seat": role, **_actor(kind)}
            for role, kind in zip(_ROLES, cell["pairing"], strict=True)
        ],
    }


def _root(path: Path) -> Path:
    return claude_replay._output(path.expanduser(), run_panel.EXAMPLES_DIR)


def _check_path(
    path: Path, mode: int, *, directory: bool = False, native_mount: bool = False
) -> None:
    details = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    allowed_modes = (mode, 0o777) if native_mount else (mode,)
    if (
        not kind(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) not in allowed_modes
        or (not directory and details.st_nlink != 1)
    ):
        raise ValueError("unsafe native team registration path")


def _seal(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o400), "wb") as output:
        os.fchmod(output.fileno(), 0o400)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _read_sealed(path: Path) -> bytes:
    _check_path(path, 0o400)
    return path.read_bytes()


def _tree(root: Path, *, directory_mode: int | None = None) -> set[str]:
    """List regular files without traversing symbolic links or special files."""
    if directory_mode is not None:
        _check_path(root, directory_mode, directory=True)
    pending = [root]
    files = set()
    while pending:
        for path in pending.pop().iterdir():
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                if directory_mode is not None:
                    _check_path(path, directory_mode, directory=True)
                pending.append(path)
            elif stat.S_ISREG(mode):
                files.add(path.relative_to(root).as_posix())
            else:
                raise ValueError("unsafe entry in native team output")
    return files


def _check_sources(root: Path, hashes: dict[str, str]) -> None:
    source_root = root / "sources"
    if _tree(source_root, directory_mode=0o700) != _SOURCE_NAMES:
        raise ValueError("frozen native team source set differs from registration")
    for name, digest in hashes.items():
        if run_panel._sha256(_read_sealed(source_root / name)) != digest:
            raise ValueError("frozen native team source differs from registration")


def _load_plan(root: Path) -> Plan:
    _check_path(root, 0o700, directory=True)
    _check_path(root / "cells", 0o700, directory=True)
    raw = _read_sealed(root / "plan.json")
    document = claude_replay._json_object(raw)
    hashes = document.get("source_sha256")
    if not isinstance(hashes, dict) or hashes.keys() != _SOURCE_NAMES:
        raise ValueError("native team source set differs from registration")
    plan = _make_plan(cast(dict[str, str], hashes))
    if raw != _bytes(plan):
        raise ValueError("native team plan differs from the fixed study")
    _check_sources(root, plan["source_sha256"])
    # Collection runs these local reducers. Do not score with modified code.
    for name in (
        *_STUDY_TOOLS,
        *run_panel.CORE_TOOLS,
        run_panel._task_source("tests/score.py"),
        run_panel._task_source("environment/seatworld.py"),
        "seatlab/claude_replay.py",
        "seatlab/capsule.py",
        "seatlab/export_capsules.py",
    ):
        body = (run_panel.EXAMPLES_DIR / name).read_bytes()
        if run_panel._sha256(body) != hashes[name]:
            raise ValueError("local native team reducer differs from registration")
    return plan


def _register(root: Path) -> Plan:
    if os.path.lexists(root):
        raise ValueError("study output already exists; collect it without retrying")
    sources = _snapshot()
    plan = _make_plan({name: run_panel._sha256(body) for name, body in sources.items()})
    # mkdir is also the exclusive run claim. Nothing resumes an existing root.
    root.mkdir(mode=0o700)
    _check_path(root, 0o700, directory=True)
    (root / "cells").mkdir(mode=0o700)
    for name, body in sources.items():
        path = root / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _seal(path, body)
    # mkdir(parents=True) applies the requested mode only to the last component.
    for path in (root / "sources", *(root / "sources").rglob("*")):
        if path.is_dir():
            path.chmod(0o700)
    panel_plan = _panel_plan(plan["source_sha256"])
    for cell in plan["cells"]:
        directory = _cell_dir(root, cell)
        directory.mkdir(mode=0o700)
        run_panel._prepare_cell(directory, panel_plan, cell, sources)
        _seal(directory / "config.json", _bytes(_trial_config(root, cell)))
    _seal(root / "plan.json", _bytes(plan))
    return plan


def _attempt(plan: Plan, cell: Cell) -> dict[str, Any]:
    return {
        "schema": ATTEMPT_SCHEMA,
        "cell_id": cell["cell_id"],
        "reserved_model_turns": cell["pairing"].count("M"),
        "plan_sha256": run_panel._sha256(_bytes(plan)),
    }


def _outcome(path: Path) -> dict[str, Any]:
    raw = _read_sealed(path)
    record = claude_replay._json_object(raw)
    code, error = record.get("returncode"), record.get("launch_error")
    if (
        record.keys() != {"returncode", "launch_error"}
        or not (
            (type(code) is int and error is None)
            or (code is None and error == "native_launch_failed")
        )
        or raw != _bytes(record)
    ):
        raise ValueError("invalid native team process outcome")
    return record


def _checked_cell(root: Path, plan: Plan, cell: Cell) -> Path | None:
    if cell not in plan["cells"]:
        raise ValueError("cell is not registered for this study")
    directory = _cell_dir(root, cell)
    _check_path(root / "cells", 0o700, directory=True)
    _check_path(directory, 0o700, directory=True)
    if any(path.name not in _CELL_FILES for path in directory.iterdir()):
        raise ValueError("unexpected entry in native team cell")
    if _read_sealed(directory / "config.json") != _bytes(_trial_config(root, cell)):
        raise ValueError("native team config differs from registration")
    task = directory / "dispatch-handoff"
    if _tree(task, directory_mode=0o755) != set(run_panel.TASK_FILES):
        raise ValueError("native team task file set differs from registration")
    for name in run_panel.TASK_FILES:
        path = task / name
        _check_path(path, 0o755 if path.suffix == ".sh" else 0o644)
        expected = (
            plan["scenario_sha256"][cell["cell_id"]]
            if name == run_panel.SCENARIO
            else plan["source_sha256"][run_panel._task_source(name)]
        )
        if run_panel._sha256(path.read_bytes()) != expected:
            raise ValueError("native team task bytes differ from registration")

    marker = directory / "attempt.json"
    outcome = directory / "outcome.json"
    log = directory / "native.log"
    trial = _trial_dir(root, cell)
    trials = trial.parent
    if not os.path.lexists(marker):
        if any(os.path.lexists(path) for path in (outcome, log, trials)):
            raise ValueError("native output has no registered attempt")
        return None
    if _read_sealed(marker) != _bytes(_attempt(plan, cell)):
        raise ValueError("invalid native team attempt marker")
    if os.path.lexists(outcome):
        _outcome(outcome)
    if os.path.lexists(log):
        _check_path(log, 0o600)
    if os.path.lexists(trials):
        _check_path(trials, 0o700, directory=True)
        if any(entry != trial for entry in trials.iterdir()):
            raise ValueError("unexpected native trial output path")
    if os.path.lexists(trial):
        # Harbor chmods bind mount paths. The three private ancestors stay 0700.
        _check_path(trial, 0o700, directory=True, native_mount=True)
        _tree(trial)
    return trial


def _run_cell(
    root: Path, plan: Plan, cell: Cell, harbor: Path, env: dict[str, str]
) -> bool:
    if _checked_cell(root, plan, cell) is not None:
        raise ValueError("registered native team cell already has an attempt")
    if os.path.lexists(root / "STOP"):
        return False
    directory = _cell_dir(root, cell)
    _seal(directory / "attempt.json", _bytes(_attempt(plan, cell)))
    # Reserve the attempt before any subprocess operation, including failure.
    (directory / "trials").mkdir(mode=0o700)
    log_path = directory / "native.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(log_path, flags, 0o600), "wb") as log:
        os.fchmod(log.fileno(), 0o600)
        try:
            process = subprocess.run(
                [str(harbor), "trial", "start", "-c", str(directory / "config.json")],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=_TRIAL_TIMEOUT_SEC,
                umask=0o077,
            )
        except (OSError, subprocess.TimeoutExpired):
            record = {"returncode": None, "launch_error": "native_launch_failed"}
        else:
            record = {"returncode": process.returncode, "launch_error": None}
    _seal(directory / "outcome.json", _bytes(record))
    return True


def _stop(root: Path, reason: str, cell_id: str | None = None) -> None:
    _seal(root / "stop.json", _bytes({"reason": reason, "cell_id": cell_id}))


def _read_state(root: Path, cell_ids: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"preflight": None, "stop": None}
    path = root / "preflight.json"
    if os.path.lexists(path):
        value = claude_replay._json_object(_read_sealed(path))
        status = value.get("status")
        if not (
            value.keys() == {"status"}
            and isinstance(status, str)
            and (status == "verified" or status in _PREFLIGHT_ERRORS)
        ):
            raise ValueError("invalid native team preflight record")
        result["preflight"] = status
    path = root / "stop.json"
    if os.path.lexists(path):
        stop = claude_replay._json_object(_read_sealed(path))
        reason, cell_id = stop.get("reason"), stop.get("cell_id")
        if not (
            stop.keys() == {"reason", "cell_id"}
            and (
                (reason in ("stop_requested", "preflight_failed") and cell_id is None)
                or (
                    reason == "cell_not_measured"
                    and isinstance(cell_id, str)
                    and cell_id in cell_ids
                )
            )
        ):
            raise ValueError("invalid native team stop record")
        result["stop"] = stop
    return result


def _collect(root: Path) -> bool:
    report = team_report.collect(root)
    report.update(_read_state(root, {row["cell_id"] for row in report["cells"]}))
    body = _bytes(report)
    path = root / "results.json"
    if os.path.lexists(path):
        _check_path(path, 0o400)
    fd, temporary_name = tempfile.mkstemp(prefix=".report-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return report["complete"] is True


def _run(root: Path, harbor: Path) -> bool:
    if not harbor.is_absolute():
        raise ValueError("--harbor must be an absolute path")
    plan = _register(root)
    if os.path.lexists(root / "STOP"):
        _stop(root, "stop_requested")
        return _collect(root)
    try:
        harbor = harbor.resolve(strict=True)
        env = run_panel._native_env(None)
        env.update(
            PYTHONPATH=str(root / "sources" / "seatlab"),
            PYTHONNOUSERSITE="1",
            PYTHONSAFEPATH="1",
        )
        run_panel._check_native(harbor, env)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _seal(root / "preflight.json", _bytes({"status": "native_preflight_failed"}))
        _stop(root, "preflight_failed")
        return _collect(root)
    if os.path.lexists(root / "STOP"):
        _stop(root, "stop_requested")
        return _collect(root)
    status = claude_replay._preflight(claude_replay._invoke)
    _seal(root / "preflight.json", _bytes({"status": status}))
    if status != "verified":
        _stop(root, "preflight_failed")
        return _collect(root)

    seen_ids: set[str] = set()
    for cell in plan["cells"]:
        if os.path.lexists(root / "STOP"):
            _stop(root, "stop_requested")
            break
        try:
            _check_sources(root, plan["source_sha256"])
            if not _run_cell(root, plan, cell, harbor, env):
                _stop(root, "stop_requested")
                break
            row = team_report.inspect_cell(root, plan, cell)
        except (OSError, ValueError):
            _stop(root, "cell_not_measured", cell["cell_id"])
            break
        trial_id = row["trial_id"]
        if row["status"] != "measured" or trial_id is None or trial_id in seen_ids:
            _stop(root, "cell_not_measured", cell["cell_id"])
            break
        seen_ids.add(trial_id)
    return _collect(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="register and run a new study once")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--harbor", type=Path, required=True)
    collect = commands.add_parser(
        "collect", help="collect existing evidence without a model"
    )
    collect.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = _root(args.out)
        complete = _run(root, args.harbor) if args.command == "run" else _collect(root)
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
