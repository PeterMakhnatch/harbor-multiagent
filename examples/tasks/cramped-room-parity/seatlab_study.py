"""Register, run, and collect the fixed local SeatLab kitchen study."""

import argparse
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import tempfile
import tomllib
from collections import Counter
from datetime import UTC, datetime
from getpass import getuser
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

TASK = Path(__file__).resolve().parent
OVERLAYS = TASK.parents[1] / "configs/features"
ROLES = ("chef0", "chef1")
EPISODE_FIELDS = {
    "schema",
    "id",
    "case_sha256",
    "role",
    "native_trial_id",
    "closed",
    "frames",
    "episode_sha256",
}
TURNS = 38
CELLS = ("A-TT", "A-WT", "A-TW", "B-TW", "B-WT", "B-TT")
COMPARISONS = (
    ("A-TT", "A-WT"),
    ("A-TT", "A-TW"),
    ("B-TT", "B-WT"),
    ("B-TT", "B-TW"),
    ("A-TT", "B-TT"),
)
TASK_FILES = (
    "task.toml",
    "instructions/chef0.md",
    "instructions/chef1.md",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/kitchen-requirements.txt",
    "environment/kitchen_world.py",
    "environment/player/Dockerfile",
    "solution/policy.py",
    "solution/solve.sh",
    "tests/test.sh",
)
TAPE = "cramped-room-tape.override.yaml"
WAIT = {
    "WT": "cramped-room-wait.override.yaml",
    "TW": "cramped-room-chef1-wait.override.yaml",
}
OVERLAY_POLICIES = {
    TAPE: ("chef0", "tape"),
    WAIT["WT"]: ("chef0", "wait"),
    WAIT["TW"]: ("chef1", "wait"),
}
CAPSULE_FILES = (
    *(f"cramped-room-parity/{name}" for name in TASK_FILES),
    *(f"overlays/{name}" for name in OVERLAY_POLICIES),
    "seatlab-protocol.md",
)
WORDS = {
    (0, -1): "north",
    (0, 1): "south",
    (1, 0): "east",
    (-1, 0): "west",
    (0, 0): "stay",
    "interact": "interact",
}


def _canonical(value):
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
        + b"\n"
    )


def _write_json(path, value):
    with path.open("xb") as file:
        file.write(_canonical(value))


def _read_bytes(path):
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError(f"Expected a regular file without symlinks: {path}")
    return path.read_bytes()


def _read_json(path):
    value = json.loads(_read_bytes(path))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _digest(value):
    return sha256(value).hexdigest()


def _is_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("Expected a canonical UUID")
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("Expected a canonical UUID")
    return parsed


def _now():
    return datetime.now(UTC).isoformat()


def _new_path(path):
    path = path.expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output already exists: {path}")
    return path.parent.resolve(strict=True) / path.name


def _local_docker_env():
    env = os.environ.copy()
    host = env.get("DOCKER_HOST") if not env.get("DOCKER_CONTEXT") else None
    if not host:
        result = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            raise ValueError("Cannot determine the Docker context socket")
        host = json.loads(result.stdout)
    if not isinstance(host, str) or not host.startswith("unix:///"):
        raise ValueError("The study requires a local Docker Unix socket")
    for name in (
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "BUILDKIT_HOST",
    ):
        env.pop(name, None)
    env.update(DOCKER_HOST=host, BUILDX_BUILDER="default")
    return env


def _inventory(capsule):
    files = {
        path.relative_to(capsule).as_posix()
        for path in capsule.rglob("*")
        if not path.is_dir() or path.is_symlink()
    }
    if files != set(CAPSULE_FILES):
        raise ValueError("Unexpected files in the frozen task capsule")
    return {
        name: {
            "sha256": _digest(_read_bytes(capsule / name)),
            "mode": stat.S_IMODE((capsule / name).stat().st_mode),
        }
        for name in CAPSULE_FILES
    }


def _freeze(capsule):
    for name in CAPSULE_FILES:
        if name.startswith("overlays/"):
            source = OVERLAYS / Path(name).name
        else:
            source = TASK / name.removeprefix("cramped-room-parity/")
        _read_bytes(source)
        target = capsule / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    task = capsule / "cramped-room-parity"
    task_config = tomllib.loads(_read_bytes(task / "task.toml").decode("utf-8"))
    if task_config.get("agents") != {role: {"under_test": True} for role in ROLES}:
        raise ValueError("The task must have exactly two tested chef seats")
    for name, (role, policy) in OVERLAY_POLICIES.items():
        expected = (
            f"services:\n  {role}:\n    environment:\n      KITCHEN_POLICY: {policy}\n"
        )
        if _read_bytes(capsule / "overlays" / name) != expected.encode("ascii"):
            raise ValueError(f"Unexpected policy in {name}")
    # Load the reviewed checkout policy once, not executable code from a collected study.
    tape = runpy.run_path(str(task / "solution/policy.py"))["JOINT_ACTIONS"]
    if len(tape) != TURNS or any(len(actions) != len(ROLES) for actions in tape):
        raise ValueError("The tape must contain two actions on each of 38 turns")
    return _inventory(capsule), {
        role: [WORDS[actions[i]] for actions in tape] for i, role in enumerate(ROLES)
    }


def _comparison_id(source, target):
    return f"{source}-vs-{target}"


def _registrations(study_id):
    return [
        {"id": cell, "trial_name": f"seatlab-{study_id.hex}-{cell.lower()}"}
        for cell in CELLS
    ]


def _comparison_registrations():
    return [
        {"id": _comparison_id(s, t), "source": s, "target": t} for s, t in COMPARISONS
    ]


def _config(root, slot):
    arm = slot["id"][2:]
    overlays = [TAPE] + ([WAIT[arm]] if arm in WAIT else [])
    return {
        "schema_version": 1,
        "task": {"path": str(root / "capsule/cramped-room-parity")},
        "trials_dir": str(root / "trials"),
        "trial_name": slot["trial_name"],
        "participants": [{"seat": role, "name": "oracle"} for role in ROLES],
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "extra_docker_compose": [
                str(root / "capsule/overlays" / name) for name in overlays
            ],
        },
    }


def _assert_inputs(root, plan):
    files = _inventory(root / "capsule")
    if (
        files != plan["capsule_files"]
        or _digest(_canonical(files)) != plan["capsule_sha256"]
    ):
        raise ValueError("The frozen task capsule does not match the study plan")


def _argv(harbor, config_path):
    return [str(harbor), "trial", "start", "-c", str(config_path)]


def _run_cell(root, harbor, env, slot):
    cell = slot["id"]
    trial = root / "trials" / slot["trial_name"]
    config_path = root / "configs" / f"{cell}.json"
    config_sha = _digest(_canonical(_config(root, slot)))
    if (
        _digest(_read_bytes(config_path)) != config_sha
        or trial.exists()
        or trial.is_symlink()
    ):
        raise ValueError(f"Refusing changed config or existing trial for {cell}")
    process = root / "process"
    argv = _argv(harbor, config_path)
    _write_json(
        process / f"{cell}.launch.json",
        {"argv": argv, "config_sha256": config_sha, "started_at": _now()},
    )
    result = {"returncode": None, "native_result_sha256": None}
    try:
        with (
            (process / f"{cell}.stdout.log").open("xb") as stdout,
            (process / f"{cell}.stderr.log").open("xb") as stderr,
        ):
            result["returncode"] = subprocess.run(
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
            ).returncode
        try:
            result["native_result_sha256"] = _digest(_read_bytes(trial / "result.json"))
        except (OSError, ValueError) as exc:
            result["result_read_error"] = str(exc)
    except (OSError, KeyboardInterrupt) as exc:
        result["process_error"] = str(exc) or type(exc).__name__
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        _write_json(process / f"{cell}.result.json", {**result, "finished_at": _now()})


def run(harbor: Path, out: Path) -> int:
    """Write all six registrations before executing any Harbor trial."""
    if not harbor.is_absolute():
        raise ValueError("--harbor must be an absolute path to the Harbor executable")
    harbor = harbor.resolve(strict=True)
    if not harbor.is_file() or not os.access(harbor, os.X_OK):
        raise ValueError("--harbor must name an executable file")
    root = _new_path(out)
    env = _local_docker_env()
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for directory in ("configs", "process", "trials"):
        (root / directory).mkdir()
    files, tape = _freeze(root / "capsule")
    study_id = uuid4()
    slots = _registrations(study_id)
    plan = {
        "schema": "seatlab.kitchen-study-plan/v1",
        "study_id": str(study_id),
        "coordinator": getuser(),
        "registered_at": _now(),
        "harbor": str(harbor),
        "docker_host": env["DOCKER_HOST"],
        "capsule_files": files,
        "capsule_sha256": _digest(_canonical(files)),
        "cells": slots,
        "comparisons": _comparison_registrations(),
        "tape_actions": tape,
    }
    for slot in slots:
        _write_json(root / "configs" / f"{slot['id']}.json", _config(root, slot))
    _write_json(root / "plan.json", plan)
    native_ids = set()
    for slot in slots:
        _assert_inputs(root, plan)
        _run_cell(root, harbor, env, slot)
        native_id, _ = _inspect_native(root, plan, slot)
        if native_id is not None:
            native_ids.add(native_id)
    return 0 if len(native_ids) == len(CELLS) else 1


def _config_snapshot(value):
    """Drop inert defaults added by Harbor's config serializer."""
    if not isinstance(value, dict):
        raise ValueError("Expected a native trial configuration object")
    defaults = {
        "timeout_multiplier": 1.0,
        "cpu_enforcement_policy": "auto",
        "memory_enforcement_policy": "auto",
    }
    result = {}
    for key, item in value.items():
        if isinstance(item, dict):
            item = _config_snapshot(item)
        elif isinstance(item, list):
            item = [
                _config_snapshot(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        if (
            item is None
            or item is False
            or item in ([], {})
            or (key in defaults and item == defaults[key])
        ):
            continue
        result[key] = item
    return result


def _native_trial(root, plan, slot):
    cell = slot["id"]
    process = root / "process"
    if not (process / f"{cell}.launch.json").exists():
        return None, "not_launched"
    if not (process / f"{cell}.result.json").exists():
        return None, "process_unfinished"
    launch = _read_json(process / f"{cell}.launch.json")
    process_result = _read_json(process / f"{cell}.result.json")
    if (
        type(process_result.get("returncode")) is not int
        or process_result["returncode"] != 0
    ):
        return None, "process_failed"
    if process_result.get("native_result_sha256") is None:
        return None, "missing_native_result"
    expected = _config(root, slot)
    config_path = root / "configs" / f"{cell}.json"
    config_bytes = _canonical(expected)
    if (
        _read_bytes(config_path) != config_bytes
        or launch.get("argv") != _argv(plan["harbor"], config_path)
        or launch.get("config_sha256") != _digest(config_bytes)
    ):
        raise ValueError("The launch does not match the registration")
    trial = root / "trials" / slot["trial_name"]
    if not (trial / "result.json").exists():
        return None, "missing_native_result"
    result_bytes = _read_bytes(trial / "result.json")
    if _digest(result_bytes) != process_result["native_result_sha256"]:
        raise ValueError("The native result differs from the launch receipt")
    result = json.loads(result_bytes)
    resolved = _config_snapshot(result["config"])
    if resolved.pop("agent", None) not in (None, {"name": "oracle"}):
        raise ValueError("The trial config selects a primary agent")
    if (
        resolved != expected
        or result.get("trial_uri") != trial.as_uri()
        or _config_snapshot(_read_json(trial / "config.json")) != expected
        or result.get("trial_name") != slot["trial_name"]
        or result.get("task_id") != expected["task"]
        or result.get("source") is not None
    ):
        raise ValueError("The native trial does not match the registered config")
    lock = _read_json(trial / "lock.json")
    if (
        lock["task"].get("type") != "local"
        or lock["task"].get("path") != expected["task"]["path"]
    ):
        raise ValueError("The native lock has a different task")
    overlay_locks = [
        {
            "path": path,
            "digest": "sha256:"
            + plan["capsule_files"][f"overlays/{Path(path).name}"]["sha256"],
        }
        for path in expected["environment"]["extra_docker_compose"]
    ]
    if (
        lock.get("extra_docker_compose") != overlay_locks
        or lock.get("source_trial") is not None
    ):
        raise ValueError("The native lock has different trial inputs")
    seats = result["seat_results"]
    if (
        len(seats) != len(ROLES)
        or {seat["seat"] for seat in seats} != set(ROLES)
        or any(
            seat.get("under_test") is not True
            or seat["agent_info"].get("name") != "oracle"
            or seat["agent_info"].get("model_info") is not None
            for seat in seats
        )
    ):
        raise ValueError("The native result does not record two tested oracle seats")
    return str(_uuid(result["id"])), None


def _inspect_native(root, plan, slot):
    try:
        return _native_trial(root, plan, slot)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "invalid_native_registration"


def _export_cell(exporter, root, scratch, plan, slot, native_id):
    cell = slot["id"]
    episode_path = scratch / f"{cell}.jsonl"
    summary = exporter(root / "trials" / slot["trial_name"], episode_path)
    reward = summary["reward"]
    deliveries = summary["deliveries_by_seat"]
    hashes = summary["episode_sha256"]
    if (
        summary["native_trial_id"] != native_id
        or not _is_digest(summary["case_sha256"])
        or summary["episodes"] != len(ROLES)
        or summary["turns_per_role"] != TURNS
        or type(reward) not in (float, int)
        or reward not in (0, 1)
        or set(deliveries) != set(ROLES)
        or any(
            type(count) is not int or not 0 <= count <= TURNS
            for count in deliveries.values()
        )
        or set(hashes) != set(ROLES)
        or not all(_is_digest(value) for value in hashes.values())
    ):
        raise ValueError("Invalid native evidence summary")
    episodes = _read_bytes(episode_path).splitlines(keepends=True)
    if len(episodes) != len(ROLES):
        raise ValueError("The exporter did not write two episodes")
    for role, policy, line in zip(ROLES, cell[2:], episodes, strict=True):
        episode = json.loads(line)
        if (
            set(episode) != EPISODE_FIELDS
            or episode["schema"] != "seatlab.episode/v1"
            or episode["id"] != f"kitchen:{native_id}:{role}"
            or episode["role"] != role
            or episode["native_trial_id"] != native_id
            or episode["case_sha256"] != summary["case_sha256"]
            or episode["closed"] is not True
            or episode["episode_sha256"] != hashes[role]
            or line != _canonical(episode)
        ):
            raise ValueError("The episode does not match the native evidence summary")
        episode.pop("episode_sha256")
        if _digest(_canonical(episode).removesuffix(b"\n")) != hashes[role]:
            raise ValueError("The episode content does not match its digest")
        actions = [frame["observed"]["action"] for frame in episode["frames"]]
        expected = plan["tape_actions"][role] if policy == "T" else ["stay"] * TURNS
        if actions != expected:
            raise ValueError("The native actions do not match the registered policy")
    return {
        "id": cell,
        "status": "measured",
        "native_trial_id": native_id,
        "case_sha256": summary["case_sha256"],
        "reward": reward,
        "deliveries_by_seat": {role: deliveries[role] for role in ROLES},
        "episodes": len(ROLES),
        "turns_per_role": TURNS,
        "episode_sha256": {role: hashes[role] for role in ROLES},
        "episode_file": episode_path.name,
    }


def _missing(cell, reason, native_id=None):
    return {
        "id": cell,
        "status": "missing",
        "reason": reason,
        "native_trial_id": native_id,
    }


def _comparison(export, registration, cells):
    comparison_id = registration["id"]
    source = cells[registration["source"]]
    target = cells[registration["target"]]
    result = {
        **registration,
        "source_native_trial_id": source["native_trial_id"],
        "target_native_trial_id": target["native_trial_id"],
        "manifest": None,
    }
    cases = {
        cell["case_sha256"] for cell in (source, target) if cell["status"] == "measured"
    }
    if len(cases) != 1:
        return {**result, "reason": "case_mismatch" if cases else "no_trusted_case"}
    case = cases.pop()

    def side(cell, role):
        if cell["status"] == "missing":
            return None
        return {
            "native_trial_id": cell["native_trial_id"],
            "episode_sha256": cell["episode_sha256"][role],
        }

    manifest = f"{comparison_id}.json"
    _write_json(
        export / manifest,
        {
            "schema": "seatlab.episode-pairs/v1",
            "pairs": [
                {
                    "id": f"{comparison_id}-{role}",
                    "case_sha256": case,
                    "role": role,
                    "source": side(source, role),
                    "target": side(target, role),
                }
                for role in ROLES
            ],
        },
    )
    return {**result, "manifest": manifest}


def collect(out: Path, export: Path, *, exporter=None) -> None:
    """Reverify registered native evidence, leaving invalid and absent cells missing."""
    root = out.expanduser().resolve(strict=True)
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("--out must be a private study directory (mode 0700)")
    export = _new_path(export)
    if export.is_relative_to(root):
        raise ValueError("--export must be outside the private study directory")
    plan = _read_json(root / "plan.json")
    if plan.get("schema") != "seatlab.kitchen-study-plan/v1":
        raise ValueError("Invalid study plan schema")
    study_id = _uuid(plan.get("study_id"))
    slots = _registrations(study_id)
    comparisons = _comparison_registrations()
    if plan.get("cells") != slots or plan.get("comparisons") != comparisons:
        raise ValueError("The study plan does not match the fixed protocol")
    _assert_inputs(root, plan)
    if exporter is None:
        from seatlab_export import export_trial

        exporter = export_trial
    native_ids = {}
    cells = {}
    for slot in slots:
        cell = slot["id"]
        native_id, reason = _inspect_native(root, plan, slot)
        if native_id is None:
            cells[cell] = _missing(cell, reason)
        else:
            native_ids[cell] = native_id
    counts = Counter(native_ids.values())
    with tempfile.TemporaryDirectory(prefix=".collect-", dir=root) as scratch_path:
        scratch = Path(scratch_path)
        for slot in slots:
            cell = slot["id"]
            if cell not in native_ids:
                continue
            native_id = native_ids[cell]
            if counts[native_id] > 1:
                cells[cell] = _missing(cell, "duplicate_native_trial_id")
                continue
            try:
                cells[cell] = _export_cell(
                    exporter, root, scratch, plan, slot, native_id
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                cells[cell] = _missing(cell, "invalid_native_evidence", native_id)
        export.mkdir(mode=0o700)
        for cell in cells.values():
            if cell["status"] == "measured":
                name = cell["episode_file"]
                with (
                    (scratch / name).open("rb") as source,
                    (export / name).open("xb") as target,
                ):
                    shutil.copyfileobj(source, target)
        _write_json(
            export / "index.json",
            {
                "schema": "seatlab.kitchen-study/v1",
                "study_id": str(study_id),
                "capsule_sha256": plan["capsule_sha256"],
                "cells": [cells[cell] for cell in CELLS],
                "measured_trials": sum(
                    cell["status"] == "measured" for cell in cells.values()
                ),
                "comparisons": [
                    _comparison(export, item, cells) for item in comparisons
                ],
            },
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser(
        "run", help="Register and run the six local Harbor trials"
    )
    run_parser.add_argument("--harbor", required=True, type=Path)
    run_parser.add_argument("--out", required=True, type=Path)
    collect_parser = commands.add_parser(
        "collect", help="Reverify and export the registered trial evidence"
    )
    collect_parser.add_argument("--out", required=True, type=Path)
    collect_parser.add_argument("--export", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return run(args.harbor, args.out)
        collect(args.out, args.export)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
