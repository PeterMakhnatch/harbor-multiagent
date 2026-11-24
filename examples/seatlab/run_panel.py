"""Launch a preregistered local Docker panel for Dispatch Handoff."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

if __package__:
    from . import dispatch_plan
    from .evidence import TrialEvidence, read_trial
else:
    import dispatch_plan
    from evidence import TrialEvidence, read_trial

PANEL_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PANEL_DIR.parent
TASK_DIR = EXAMPLES_DIR / "tasks/dispatch-handoff"
CONDITIONS = dispatch_plan.CONDITIONS
POLICIES = dispatch_plan.POLICIES
NATIVE_VERSION = "0.26.0"
OLAM_BASE = "6aeb609e31fd77e5f89ce3bcb018db8045520fb7"
PLAN_SCHEMA = dispatch_plan.PLAN_SCHEMA
RUNS_SCHEMA = "seatlab.panel-runs/v1"
SCENARIO = "environment/scenario.json"
TASK_FILES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/player/Dockerfile",
    SCENARIO,
    "environment/seatworld.py",
    "environment/serve.py",
    "instructions/dispatch.md",
    "instructions/inventory.md",
    "solution/solve.sh",
    "tests/score.py",
    "tests/test.sh",
)
SEAT_ADAPTER = "seatlab/seat_agent.py"
CORE_TOOLS = (
    "seatlab/run_panel.py",
    "seatlab/evidence.py",
    "seatlab/dispatch_plan.py",
)
DESIGN_FLAGS = ("seeds", "conditions", "episodes", "jobs", "policy")


class Cell(TypedDict):
    cell_id: str
    seed: int
    condition: str


class Plan(TypedDict):
    schema: str
    native: dict[str, str]
    policy: str
    hosted_route: dict[str, str] | None
    seeds: list[int]
    conditions: list[str]
    episodes: int
    source_sha256: dict[str, str]
    cells: list[Cell]


class Run(Cell):
    status: Literal["measured", "unmeasured"]
    trial_path: str
    evidence: TrialEvidence | None
    error: str | None
    returncode: int | None
    launch_error: str | None
    receipt_error: str | None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return value


def _create_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as output:
        output.write(_json(value))


def _task_source(path: str) -> str:
    return f"tasks/dispatch-handoff/{path}"


def _source_names(policy: str) -> tuple[str, ...]:
    adapter = () if policy == "oracle" else (SEAT_ADAPTER,)
    return (*CORE_TOOLS, *adapter, *map(_task_source, TASK_FILES))


def _snapshot(policy: str) -> dict[str, bytes]:
    return {name: (EXAMPLES_DIR / name).read_bytes() for name in _source_names(policy)}


def _make_plan(
    seeds: list[int],
    conditions: list[str],
    episodes: int,
    policy: str,
    source_sha256: dict[str, str],
    hosted_route: dict[str, str] | None = None,
) -> Plan:
    if not seeds or any(
        type(seed) is not int or not -(2**63) <= seed < 2**63 for seed in seeds
    ):
        raise ValueError("seeds must be signed 64-bit integers")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must not repeat")
    if not conditions or any(condition not in CONDITIONS for condition in conditions):
        raise ValueError(f"use one or more conditions from {','.join(CONDITIONS)}")
    if len(set(conditions)) != len(conditions):
        raise ValueError("conditions must not repeat")
    if len(seeds) * len(conditions) > dispatch_plan.MAX_CELLS:
        raise ValueError(dispatch_plan.TOO_MANY_CELLS)
    if type(episodes) is not int or not 1 <= episodes <= 256:
        raise ValueError("episodes must be between 1 and 256")
    if policy not in POLICIES:
        raise ValueError(f"use a policy from {','.join(POLICIES)}")
    if (policy == "hosted") != (hosted_route is not None):
        raise ValueError("only hosted policy requires a registered free hosted route")
    seeds = sorted(seeds)
    conditions = [condition for condition in CONDITIONS if condition in conditions]
    return {
        "schema": PLAN_SCHEMA,
        "native": {"expected_version": NATIVE_VERSION, "olam_base": OLAM_BASE},
        "policy": policy,
        "hosted_route": hosted_route,
        "seeds": seeds,
        "conditions": conditions,
        "episodes": episodes,
        "source_sha256": source_sha256,
        "cells": [
            {
                "cell_id": f"seed-{seed}--{condition}",
                "seed": seed,
                "condition": condition,
            }
            for seed in seeds
            for condition in conditions
        ],
    }


def _check_checkout(plan: Plan) -> None:
    if plan["source_sha256"].keys() != set(_source_names(plan["policy"])):
        raise ValueError("invalid source checksum manifest")
    if plan["native"] != {"expected_version": NATIVE_VERSION, "olam_base": OLAM_BASE}:
        raise ValueError("panel plan targets a different native version")


def _check_sources(plan: Plan, sources: dict[str, bytes]) -> None:
    registered = plan["source_sha256"]
    if sources.keys() != registered.keys():
        raise ValueError("source snapshot does not match the registered manifest")
    for name, digest in registered.items():
        if _sha256(sources[name]) != digest:
            raise ValueError(f"registered source changed since registration: {name}")


def _load_plan(out: Path) -> Plan:
    plan = cast(Plan, dispatch_plan.normalize(_read_object(out / "plan.json")))
    _check_checkout(plan)
    _check_sources(plan, _snapshot(plan["policy"]))
    return plan


def _pinned(path: Path, pin: str) -> tuple[dict[str, Any], Plan, dict[str, bytes]]:
    registration = dispatch_plan.read_pinned(path, pin)
    plan = cast(Plan, registration["plan"])
    _check_checkout(plan)
    sources = _snapshot(plan["policy"])
    _check_sources(plan, sources)
    return registration, plan, sources


def _create_private(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o400), "wb") as output:
        os.fchmod(output.fileno(), 0o400)
        output.write(content)


def _report(registration: dict[str, Any], pin: str) -> None:
    plan = registration["plan"]
    route = plan["hosted_route"]
    via = f" via {route['model']} ({route['provider']})" if route else ""
    print(f"SHA-256: {pin}")
    print(f"Policy: {plan['policy']}{via}")
    print(
        f"Cells: {len(plan['cells'])} = {len(plan['seeds'])} seeds"
        f" x {len(plan['conditions'])} conditions;"
        f" episodes: {plan['episodes']}; jobs: {registration['jobs']}"
    )
    for name, count in sorted(registration["workload"].items()):
        print(f"{name}: {count}")
    if route:
        print("The hosted route is a label only: availability and price are unchecked.")


def _participants(policy: str, condition: str, episodes: int) -> list[dict[str, Any]]:
    participants = []
    for seat in ("inventory", "dispatch"):
        if seat == "inventory" and condition == "pooled":
            participant = {"name": "nop", "import_path": None, "kwargs": {}}
        elif policy == "oracle":
            participant = {"name": "oracle", "import_path": None, "kwargs": {}}
        else:
            kwargs: dict[str, Any] = {"policy": policy}
            if policy == "hosted":
                kwargs["expected_episodes"] = episodes
            participant = {
                "name": None,
                "import_path": "seat_agent:SeatAgent",
                "kwargs": kwargs,
            }
        participants.append({"seat": seat, "model_name": None, **participant})
    return participants


def _cell_dir(out: Path, cell: Cell) -> Path:
    return out / "cells" / cell["cell_id"]


def _trial_config(out: Path, plan: Plan, cell: Cell) -> dict[str, Any]:
    directory = _cell_dir(out, cell)
    # Harbor derives the Compose project name from the native trial name.
    unique = _sha256(os.fsencode(directory))[:24]
    return {
        "schema_version": 1,
        "task": {
            "path": str(directory / "dispatch-handoff"),
            "git_url": None,
            "name": None,
        },
        "trial_name": f"seatlab-{unique}",
        "trials_dir": str(directory / "native"),
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": _participants(
            plan["policy"], cell["condition"], plan["episodes"]
        ),
    }


def _scenario(plan: Plan, cell: Cell) -> dict[str, Any]:
    return {
        "schema": "seatlab.scenario/v1",
        "seed": cell["seed"],
        "condition": cell["condition"],
        "episodes": plan["episodes"],
    }


def _prepare_cell(
    directory: Path, plan: Plan, cell: Cell, sources: dict[str, bytes]
) -> None:
    task = directory / "dispatch-handoff"
    for name in TASK_FILES:
        path = task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == SCENARIO:
            _create_json(path, _scenario(plan, cell))
        else:
            path.write_bytes(sources[_task_source(name)])
        path.chmod(0o755 if path.suffix == ".sh" else 0o644)
    # Native copies these public task inputs into non-root containers.
    task.chmod(0o755)
    for subdirectory in task.rglob("*"):
        if subdirectory.is_dir():
            subdirectory.chmod(0o755)


def _config_matches(actual: object, expected: object) -> bool:
    """Allow native config defaults in addition to the registered inputs."""
    if expected == {}:
        return actual == {}
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            isinstance(key, str)
            and key in actual
            and _config_matches(cast(dict[str, Any], actual)[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _config_matches(value, target)
                for value, target in zip(actual, expected, strict=True)
            )
        )
    return type(actual) is type(expected) and actual == expected


def _inspect_cell(out: Path, plan: Plan, cell: Cell) -> Run:
    directory = _cell_dir(out, cell)
    config = _trial_config(out, plan, cell)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    run: Run = {
        **cell,
        "status": "unmeasured",
        "trial_path": trial.relative_to(out).as_posix(),
        "evidence": None,
        "error": None,
        "returncode": None,
        "launch_error": None,
        "receipt_error": None,
    }
    try:
        evidence = read_trial(trial)
        if (
            evidence["seed"] != cell["seed"]
            or evidence["condition"] != cell["condition"]
            or evidence["score"]["episodes"] != plan["episodes"]
        ):
            raise ValueError(
                "native dossier does not match the planned seed, condition, or episode count"
            )
        native = _read_object(trial / "result.json")
        if native.get("trial_name") != config["trial_name"] or not _config_matches(
            native.get("config"), config
        ):
            raise ValueError(
                "native trial configuration does not match the planned cell or policy"
            )
        if (route := plan["hosted_route"]) is not None:
            for seat, report in evidence["seats"].items():
                if seat == "inventory" and cell["condition"] == "pooled":
                    continue
                if report["model_info"] != {
                    "name": route["model"],
                    "provider": "openrouter",
                }:
                    raise ValueError(
                        "native hosted model differs from the registered route"
                    )
    except FileNotFoundError as error:
        if trial.exists() and error.filename:
            run["error"] = (
                f"native dossier is incomplete: {Path(error.filename).name} is missing"
            )
        else:
            run["error"] = (
                "native trial output is missing"
                if directory.exists()
                else "not attempted"
            )
    except (OSError, ValueError) as error:
        run["error"] = str(error)
    else:
        run["status"] = "measured"
        run["evidence"] = evidence

    # A receipt reports process status only. It cannot supply or veto a measurement.
    receipt_path = directory / "receipt.json"
    if receipt_path.exists():
        try:
            receipt = _read_object(receipt_path)
            code, message = receipt.get("returncode"), receipt.get("launch_error")
            exited = type(code) is int and message is None
            failed = code is None and isinstance(message, str) and bool(message)
            if not (exited or failed):
                raise ValueError(
                    "expected a native process exit code or a launch error"
                )
            run["returncode"] = code
            run["launch_error"] = message
        except (OSError, ValueError) as error:
            run["receipt_error"] = str(error)
    return run


def _write_index(out: Path, plan: Plan, runs: dict[str, Run]) -> bool:
    ids: dict[str, int] = defaultdict(int)
    fingerprints: dict[int, set[str]] = defaultdict(set)
    for run in runs.values():
        if (evidence := run["evidence"]) is not None:
            ids[evidence["trial_id"]] += 1
            fingerprints[run["seed"]].add(evidence["fingerprint"])
    rows = []
    for cell in plan["cells"]:
        row = runs[cell["cell_id"]].copy()
        if (evidence := row["evidence"]) is not None:
            if ids[evidence["trial_id"]] > 1:
                row["error"] = "duplicate native trial UUID"
            elif len(fingerprints[row["seed"]]) > 1:
                row["error"] = (
                    "latent scenario fingerprints differ for this paired seed"
                )
            if row["error"]:
                row["evidence"] = None
                row["status"] = "unmeasured"
        rows.append(row)
    measured = sum(row["status"] == "measured" for row in rows)
    payload = _json(
        {
            "schema": RUNS_SCHEMA,
            "planned": len(rows),
            "measured": measured,
            "unmeasured": len(rows) - measured,
            "cells": rows,
        }
    )
    with tempfile.NamedTemporaryFile(
        mode="w", dir=out, prefix=".runs-", encoding="utf-8", delete=False
    ) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(payload)
            temporary.close()
            path.replace(out / "runs.json")
        finally:
            path.unlink(missing_ok=True)
    return measured == len(rows)


@contextmanager
def _index_lock(out: Path) -> Iterator[None]:
    with (out / ".index.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "this panel is already being launched or collected"
            ) from error
        yield


def _capture(command: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"{Path(command[0]).name} could not confirm its local configuration"
        )
    return result.stdout.strip()


def _native_env(route: dict[str, str] | None) -> dict[str, str]:
    allowed = (
        "HOME",
        "LOGNAME",
        "PATH",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    host, context = os.environ.get("DOCKER_HOST"), os.environ.get("DOCKER_CONTEXT")
    if context or not host:
        command = ["docker", "context", "inspect"]
        if context:
            command.append(context)
        command.extend(["--format", "{{.Endpoints.docker.Host}}"])
        host = _capture(command, env)
    if not host.startswith("unix:///") or any(char in host for char in "\r\n"):
        raise ValueError("local Docker requires a Unix socket, not a remote endpoint")
    if route is not None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("hosted policy requires OPENROUTER_API_KEY on the host")
        env.update(
            OPENROUTER_API_KEY=key,
            SEATLAB_FREE_MODEL=route["model"],
            SEATLAB_FREE_PROVIDER=route["provider"],
        )
    env.update(
        COMPOSE_BAKE="false",
        DOCKER_HOST=host,
        HARBOR_TELEMETRY="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(PANEL_DIR),
    )
    return env


def _check_native(harbor: Path, env: dict[str, str]) -> None:
    if _capture([str(harbor), "--version"], env) != NATIVE_VERSION:
        raise ValueError(f"use the Olam Harbor executable at version {NATIVE_VERSION}")
    schema = json.loads(_capture([str(harbor), "trial", "schema"], env))
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or "participants" not in properties:
        raise ValueError("the selected Harbor executable has no native seat support")


async def _run_cell(
    out: Path,
    plan: Plan,
    cell: Cell,
    sources: dict[str, bytes],
    harbor: Path,
    env: dict[str, str],
) -> None:
    directory = _cell_dir(out, cell)
    directory.mkdir(parents=True)
    try:
        _prepare_cell(directory, plan, cell, sources)
        config = directory / "trial.json"
        _create_json(config, _trial_config(out, plan, cell))
        adapter = "seatlab/seat_agent.py"
        if (
            plan["policy"] != "oracle"
            and (EXAMPLES_DIR / adapter).read_bytes() != sources[adapter]
        ):
            raise ValueError("seat_agent.py changed after preregistration")
        with (directory / "harbor.log").open("xb") as log:
            process = await asyncio.create_subprocess_exec(
                str(harbor),
                "trial",
                "start",
                "-c",
                str(config),
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            returncode = await process.wait()
    except (OSError, ValueError) as error:
        _create_json(
            directory / "receipt.json", {"returncode": None, "launch_error": str(error)}
        )
    else:
        _create_json(
            directory / "receipt.json", {"returncode": returncode, "launch_error": None}
        )


async def _launch(
    out: Path,
    plan: Plan,
    sources: dict[str, bytes],
    harbor: Path,
    env: dict[str, str],
    jobs: int,
) -> bool:
    _check_sources(plan, sources)
    runs = {cell["cell_id"]: _inspect_cell(out, plan, cell) for cell in plan["cells"]}
    complete = _write_index(out, plan, runs)
    pending = iter(plan["cells"])

    async def worker() -> None:
        nonlocal complete
        for cell in pending:
            await _run_cell(out, plan, cell, sources, harbor, env)
            runs[cell["cell_id"]] = _inspect_cell(out, plan, cell)
            complete = _write_index(out, plan, runs)

    async with asyncio.TaskGroup() as group:
        for _ in range(jobs):
            group.create_task(worker())
    return complete


def _collect(out: Path) -> bool:
    plan = _load_plan(out)
    with _index_lock(out):
        runs = {
            cell["cell_id"]: _inspect_cell(out, plan, cell) for cell in plan["cells"]
        }
        return _write_index(out, plan, runs)


def _new_output(requested: Path) -> Path:
    out = requested.resolve()
    if out.exists() or requested.is_symlink():
        raise ValueError(
            "output already exists; use --collect or select a new output path"
        )
    if out.is_relative_to(PANEL_DIR) or out.is_relative_to(TASK_DIR):
        raise ValueError("output must not be inside the panel or task source folder")
    return out


def _requested_plan(args: argparse.Namespace) -> tuple[Plan, dict[str, bytes]]:
    policy = args.policy or "oracle"
    route = (
        dispatch_plan.hosted_route(
            os.environ.get("SEATLAB_FREE_MODEL", ""),
            os.environ.get("SEATLAB_FREE_PROVIDER", ""),
        )
        if policy == "hosted"
        else None
    )
    seeds = (args.seeds if args.seeds is not None else "17,29").split(",")
    conditions = (
        [condition.strip() for condition in args.conditions.split(",")]
        if args.conditions is not None
        else list(CONDITIONS)
    )
    sources = _snapshot(policy)
    plan = _make_plan(
        [int(seed) for seed in seeds],
        conditions,
        args.episodes if args.episodes is not None else 12,
        policy,
        {name: _sha256(content) for name, content in sources.items()},
        route,
    )
    return plan, sources


def _plan_only(out: Path, plan: Plan, jobs: int) -> None:
    registration = dispatch_plan.seal(plan, jobs)
    content = dispatch_plan.encode(registration)
    out.mkdir(mode=0o700, parents=True)
    out.chmod(0o700)
    path = out / "registration.json"
    _create_private(path, content)
    print(f"Registered: {path}")
    _report(registration, _sha256(content))
    print("No trials ran: no Harbor, Docker, or provider was contacted.")


def _check_registered_route(route: dict[str, str] | None) -> None:
    labels = {"SEATLAB_FREE_MODEL": "model", "SEATLAB_FREE_PROVIDER": "provider"}
    if route is not None and any(
        os.environ.get(name, route[key]) != route[key] for name, key in labels.items()
    ):
        raise ValueError(
            "SEATLAB_FREE_MODEL or SEATLAB_FREE_PROVIDER differs from the registration"
        )


def _start(
    out: Path,
    plan: Plan,
    sources: dict[str, bytes],
    harbor: Path,
    jobs: int,
    registration: bytes | None,
) -> bool:
    harbor = harbor.expanduser().resolve(strict=True)
    env = _native_env(plan["hosted_route"])
    _check_native(harbor, {k: v for k, v in env.items() if k != "OPENROUTER_API_KEY"})
    out.mkdir(mode=0o700, parents=True)
    with _index_lock(out):
        if registration is not None:
            _create_private(out / "registration.json", registration)
        plan_path = out / "plan.json"
        _create_json(plan_path, plan)
        plan_path.chmod(0o400)
        return asyncio.run(_launch(out, plan, sources, harbor, env, jobs))


def _from_plan(path: Path, pin: str, requested_out: Path, harbor: Path) -> bool:
    registration, plan, sources = _pinned(path, pin)
    _check_registered_route(plan["hosted_route"])
    content = dispatch_plan.encode(registration)
    if _sha256(content) != pin:
        raise ValueError("registration does not re-encode to its pinned bytes")
    out = _new_output(requested_out)
    return _start(out, plan, sources, harbor, registration["jobs"], content)


def _check_usage(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    design = any(getattr(args, name) is not None for name in DESIGN_FLAGS)
    pinned = args.check_plan is not None or args.from_plan is not None
    if args.collect:
        if design or args.harbor is not None or args.sha256 is not None:
            parser.error("--collect only takes --out")
    elif args.plan_only:
        if args.harbor is not None or args.sha256 is not None:
            parser.error("--plan-only takes --out and design flags only")
    elif args.check_plan is not None:
        if design or args.harbor is not None or args.out is not None:
            parser.error("--check-plan only takes --sha256")
    elif args.from_plan is not None:
        if design:
            parser.error("--from-plan uses the registered design; omit design flags")
    elif args.sha256 is not None:
        parser.error("--sha256 only applies to --check-plan or --from-plan")
    if pinned and args.sha256 is None:
        parser.error("--sha256 is required with a registration")
    if args.check_plan is None and args.out is None:
        parser.error("--out is required")
    launch = not (args.collect or args.plan_only or args.check_plan is not None)
    if launch and args.harbor is None:
        parser.error("--harbor is required for a launch")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Use a new output path for each launch. "
            "Collect again from the original path without starting any jobs. "
            "Register with --plan-only, review with --check-plan, "
            "then launch the pinned registration with --from-plan."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="new panel or registration directory, or the panel to --collect",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--collect",
        action="store_true",
        help="rebuild runs.json from native dossiers; launch nothing",
    )
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help="write registration.json into a new --out directory; launch nothing",
    )
    mode.add_argument(
        "--check-plan",
        type=Path,
        metavar="REGISTRATION",
        help="validate a pinned registration against current sources; launch nothing",
    )
    mode.add_argument(
        "--from-plan",
        type=Path,
        metavar="REGISTRATION",
        help="launch a pinned registration into a new --out directory",
    )
    parser.add_argument(
        "--sha256", metavar="PIN", help="SHA-256 of the registration file bytes"
    )
    parser.add_argument(
        "--harbor", type=Path, help="path to the Olam Harbor executable for launches"
    )
    parser.add_argument(
        "--seeds", default=None, help="comma-separated seeds (default: 17,29)"
    )
    parser.add_argument(
        "--conditions",
        help=f"comma-separated conditions (default: {','.join(CONDITIONS)})",
    )
    parser.add_argument(
        "--episodes", type=int, help="shipments per trial (default: 12; range: 1–256)"
    )
    parser.add_argument(
        "--jobs", type=int, choices=(1, 2), help="concurrent trials (default: 1)"
    )
    parser.add_argument(
        "--policy",
        choices=POLICIES,
        help="seat policy (default: oracle); hosted requires the frozen free OpenRouter route",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _check_usage(parser, args)
    try:
        if args.check_plan is not None:
            registration, _, _ = _pinned(
                cast(Path, args.check_plan).expanduser(), args.sha256
            )
            _report(registration, args.sha256)
            print("Valid against the current sources and native plan. No trials ran.")
            return 0
        requested_out = cast(Path, args.out).expanduser()
        if args.collect:
            out = requested_out.resolve()
            complete = _collect(out)
        elif args.from_plan is not None:
            out = requested_out.resolve()
            complete = _from_plan(
                cast(Path, args.from_plan).expanduser(),
                args.sha256,
                requested_out,
                cast(Path, args.harbor),
            )
        else:
            out = _new_output(requested_out)
            plan, sources = _requested_plan(args)
            if args.plan_only:
                _plan_only(out, plan, args.jobs or 1)
                return 0
            complete = _start(
                out, plan, sources, cast(Path, args.harbor), args.jobs or 1, None
            )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    print(
        f"{'Complete' if complete else 'Unmeasured cells remain'}: {out / 'runs.json'}"
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
