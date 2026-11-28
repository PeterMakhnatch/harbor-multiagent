from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from ...native_study import (
    MAX_TASK_BYTES,
    NATIVE_DEFAULTS,
    _task_file_sha,
    as_list,
    as_record,
    native,
    normalize,
    record_bytes,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ...seat_checkpoints.materialize import read_plain, sync_directory
from ...ship_inflight.materialize import publish
from ..decision_receipt.control_inputs import check_lock, lock_native
from ..exhibit_harness import fingerprint
from ..exhibit_prepare import CHECKOUT
from ..visibility import local_seat
from ..workbench.arm_runtime import inspect_record
from ..workbench.arm_sources import bounded_tree, inspect_source, require_unchanged
from . import participant
from .design import TIMINGS, WORLDS, check_plan

SCHEMA = "seatlab.dispatch-recall.prepared/v1"
TASK_NAME = "harbor/dispatch-recall"
TASK_DIR = "dispatch-recall"
ROLES = ("loader", "depot")
SERVER = "main"
COMPOSE = "environment/docker-compose.yaml"
TASK_CONFIG = "task.toml"
CONFIG = "config.json"
LOCK = "lock.json"
RECEIPT = "receipt.json"
FILES = (CONFIG, LOCK, RECEIPT)
PRIVATE_FILE = 0o600
WORLD_KEYS = ("RECALL_SEED", "RECALL_TRANSITION", "RECALL_TIMING")
VERIFIER_ENV = {"RECALL_RETAIN_CUT": "1"}
MARK = "RECALL"
SEAT_NETWORK = "none"
SCRIPTED = frozenset({"oracle", "nop"})
DECLARED = ("name", "import_path", "model_name")
PARTICIPANT_FIELDS = frozenset(
    {
        "seat",
        "name",
        "import_path",
        "model_name",
        "model_api_key_name",
        "model_api_format",
        "base_url",
        "skip_model_parsing",
        "kwargs",
        *NATIVE_DEFAULTS,
    }
)
FIXED_DEFAULTS = ("skills", "resume_trajectory", "extra_allowed_hosts", "mcp_servers")
CREDENTIAL = re.compile(r"key|token|secret|password|credential|auth", re.IGNORECASE)
MAX_KWARG_NODES = 256
SOURCES = (
    "examples/seatlab/dispatch_recall/__main__.py",
    "examples/seatlab/dispatch_recall/blind_reference.py",
    "examples/seatlab/dispatch_recall/collect.py",
    "examples/seatlab/dispatch_recall/design.py",
    "examples/seatlab/dispatch_recall/native.py",
    "examples/seatlab/dispatch_recall/owner_evidence.py",
    "examples/seatlab/dispatch_recall/participant.py",
    "examples/seatlab/dispatch_recall/prepare.py",
    "examples/seatlab/dispatch_recall/registry.py",
    "src/harbor/agents/base.py",
    "src/harbor/environments/base.py",
    "src/harbor/environments/docker/__init__.py",
    "src/harbor/environments/docker/docker.py",
    "src/harbor/models/agent/context.py",
    "src/harbor/models/trial/paths.py",
)
CLAIMS = {
    "run": "not_started",
    "plan_enrollment": "not_attested",
    "decision_owner_identity": "not_bound",
    "provider_identity": "not_bound",
    "owner_endpoints": "paths_checked_not_bound",
    "participant_independence": "not_attested",
    "case_isolation": "static_task_and_config_check_only",
    "fresh_role_state": "new_trial_directory_and_compose_project_only",
}
ORACLE_ROSTER = {
    "participants": [
        {"seat": "loader", "name": "oracle"},
        {"seat": "depot", "name": "oracle"},
    ]
}
UNSUPPORTED_TEAM = (
    "seats are offline (network_mode none): both roles need the host-side "
    f"{participant.IMPORT_PATH} adapter, or both roles a scripted oracle or "
    "nop control; in-seat installed agents and mixed teams are not supported"
)

assert SOURCES == tuple(sorted(set(SOURCES)))
assert set(FIXED_DEFAULTS) <= set(NATIVE_DEFAULTS)
assert "RECALL_RETAIN_CUT" not in WORLD_KEYS


def _digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(record_bytes(record)).hexdigest()


def source_digest(root: Path) -> str:
    outer = hashlib.sha256(b"seatlab.dispatch-recall.sources/v1\n")
    budget = MAX_TASK_BYTES
    for relative in SOURCES:
        checksum, size = _task_file_sha(root / relative, budget)
        budget -= size
        outer.update(f"{relative}\0{checksum}\n".encode("ascii"))
    assert 0 <= budget <= MAX_TASK_BYTES
    return "sha256:" + outer.hexdigest()


def _slot(plan: Any, job_id: Any) -> dict[str, Any]:
    check_plan(plan)
    require(type(job_id) is str, "job id must be text")
    matches = [job for job in plan["jobs"] if job["job_id"] == job_id]
    require(len(matches) == 1, "the plan holds no such assignment")
    job = matches[0]
    require(
        plan["seats"] == dict.fromkeys(ROLES, "free")
        and tuple(job["participants"]) == ROLES,
        "plan roles differ from a free loader and depot",
    )
    return job


def world_env(session: dict[str, Any]) -> dict[str, str]:
    seed = session["seed"]
    require(type(seed) is int and 0 <= seed < 2**63, "invalid assigned seed")
    require(session["transition"] in WORLDS, "invalid assigned transition")
    require(session["timing"] in TIMINGS, "invalid assigned timing")
    env = {
        "RECALL_SEED": str(seed),
        "RECALL_TRANSITION": session["transition"],
        "RECALL_TIMING": session["timing"],
    }
    assert tuple(env) == WORLD_KEYS
    return env


def _table(value: Any, label: str) -> dict[str, Any]:
    require(type(value) is dict, f"task.toml {label} is not a table")
    return value


def _task_config(root: Path) -> None:
    data, _ = read_plain(root / TASK_CONFIG)
    config = tomllib.loads(data.decode("utf-8"))
    environment = _table(config.get("environment", {}), "environment")
    verifier = _table(config.get("verifier", {}), "verifier")
    require(
        sorted(_table(environment.get("env", {}), "environment.env"))
        == sorted(WORLD_KEYS),
        "task world settings differ from the three RECALL settings",
    )
    require(
        sorted(_table(verifier.get("env", {}), "verifier.env")) == sorted(VERIFIER_ENV),
        "task verifier settings differ from RECALL_RETAIN_CUT",
    )
    agents = _table(config.get("agents", {}), "agents")
    require(tuple(agents) == ROLES, "task roles differ from loader and depot")
    require(
        all(
            _table(seat, "agent").get("under_test", True) is True
            for seat in agents.values()
        ),
        "a task role is not under test",
    )
    solution = _table(config.get("solution", {}), "solution")
    require(
        MARK not in json.dumps(solution.get("env", {})),
        "task solution env names a world setting",
    )


def _seat(role: str, seat: Any) -> None:
    require(type(seat) is dict, f"{role} seat is not a compose service")
    require(
        "environment" not in seat and "env_file" not in seat,
        f"{role} seat receives service environment",
    )
    require(MARK not in yaml.safe_dump(seat), f"{role} seat names a world setting")
    require(seat.get("network_mode") == SEAT_NETWORK, f"{role} seat is not offline")
    volumes = seat.get("volumes", [])
    require(
        type(volumes) is list
        and all(type(v) is str and v.endswith(":ro") for v in volumes),
        f"{role} seat mounts a writable volume",
    )


def world_scope(root: Path) -> dict[str, Any]:
    data, _ = read_plain(root / COMPOSE)
    document = yaml.safe_load(data.decode("utf-8"))
    services = document.get("services") if type(document) is dict else None
    require(
        type(services) is dict and set(services) == {SERVER, *ROLES},
        "compose services differ from main, loader and depot",
    )
    assert isinstance(services, dict)
    server = services[SERVER]
    env = server.get("environment") if type(server) is dict else None
    require(
        type(env) is dict and all(key in env for key in WORLD_KEYS),
        "main does not receive every world setting",
    )
    for role in ROLES:
        _seat(role, services[role])
    return {"service": SERVER, "keys": list(WORLD_KEYS), "seat_network": SEAT_NETWORK}


def _keys(value: Any) -> list[str]:
    pending = [value]
    found: list[str] = []
    for _ in range(MAX_KWARG_NODES):
        if not pending:
            return found
        item = pending.pop()
        if type(item) is dict:
            found.extend(str(key) for key in item)
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    require(not pending, "participant kwargs are too large")
    return found


def _member(harbor: SimpleNamespace, raw: Any, seat: str) -> dict[str, Any]:
    raw = as_record(raw, f"{seat} participant")
    require(raw.get("seat") == seat, "roster seats must be loader then depot")
    unknown = sorted(set(raw) - PARTICIPANT_FIELDS)
    require(not unknown, f"{seat} participant sets unsupported fields {unknown}")
    require(
        raw.get("name") is not None or raw.get("import_path") is not None,
        f"{seat} participant names no agent",
    )
    require(
        MARK not in record_bytes({"member": raw}).decode("ascii"),
        f"{seat} participant names a world setting",
    )
    base = raw.get("base_url")
    require(
        base is None or (type(base) is str and "@" not in base and "?" not in base),
        f"{seat} base_url may carry a credential",
    )
    require(
        not any(CREDENTIAL.search(key) for key in _keys(raw.get("kwargs", {}))),
        f"{seat} kwargs may carry a credential",
    )
    member = normalize(harbor, raw)
    changed = sorted(key for key in raw if member.get(key) != raw[key])
    require(not changed, f"native normalization changes {seat} fields {changed}")
    for field in FIXED_DEFAULTS:
        require(
            member[field] == NATIVE_DEFAULTS[field], f"{seat} participant sets {field}"
        )
    return member


def team_kind(team: list[dict[str, Any]]) -> str:
    if all(
        member.get("name") in SCRIPTED
        and "import_path" not in member
        and "model_name" not in member
        for member in team
    ):
        return "scripted_control"
    if all(
        member.get("import_path") == participant.IMPORT_PATH and "name" not in member
        for member in team
    ):
        return "host_adapter"
    raise ValueError(UNSUPPORTED_TEAM)


def _endpoint_shape(turn: str, raw: Any) -> str:
    require(type(raw) is str, f"{turn} owner endpoint is not text")
    path = Path(raw)
    require(
        path.is_absolute()
        and os.path.normpath(raw) == raw
        and local_seat._SOCKET_NAME.fullmatch(path.name) is not None
        and len(os.fsencode(raw)) <= local_seat._MAX_SOCKET_PATH_BYTES,
        f"{turn} owner endpoint is not a short canonical socket path",
    )
    return raw


def _endpoint(turn: str, raw: Any, places: tuple[Path, Path], live: bool) -> str:
    shaped = _endpoint_shape(turn, raw)
    path = Path(shaped)
    require(
        not any(path.is_relative_to(base) for base in (CHECKOUT, *places)),
        f"{turn} owner endpoint overlaps the checkout, task or output",
    )
    if not live:
        return shaped
    try:
        canonical = local_seat._socket_path(shaped)
    except local_seat._Fail as error:
        raise ValueError(f"{turn} owner endpoint: {error.message}") from None
    require(canonical == shaped, f"{turn} owner endpoint is not canonical")
    require(
        not os.path.lexists(path) or stat.S_ISSOCK(os.lstat(path).st_mode),
        f"{turn} owner endpoint exists and is not a socket",
    )
    return canonical


def owner_endpoints(
    harbor: SimpleNamespace,
    team: list[dict[str, Any]],
    places: tuple[Path, Path],
    live: bool,
) -> dict[str, str]:
    if team_kind(team) != "host_adapter":
        return {}
    claimed: dict[str, Any] = {}
    for member in team:
        sockets = as_record(member["kwargs"].get("owner_sockets"), "owner sockets")
        require(claimed.keys().isdisjoint(sockets), "roles share an owner turn")
        claimed.update(sockets)
    require(set(claimed) == set(participant.TURNS), "owner turns differ")
    endpoints = {
        turn: _endpoint(turn, claimed[turn], places, live) for turn in participant.TURNS
    }
    require(len(set(endpoints.values())) == len(endpoints), "owner endpoints repeat")
    require(
        participant.roster(harbor, endpoints) == team,
        "adapter participants differ from the recall roster",
    )
    return endpoints


def _team(harbor: SimpleNamespace, roster: Any) -> list[dict[str, Any]]:
    record = as_record(roster, "roster", frozenset({"participants"}))
    members = as_list(record["participants"], "roster participants", 2, 2)
    return [_member(harbor, raw, seat) for raw, seat in zip(members, ROLES)]


def _lock(
    harbor: SimpleNamespace,
    root: Path,
    request: dict[str, Any],
    identity: dict[str, str],
) -> tuple[dict[str, Any], Any]:
    from harbor.trial.seats import check_launch

    locking = lock_native()
    config = harbor.TrialConfig.model_validate(request)
    check_launch(config, harbor.Task(root))
    require(config.verifier.env == VERIFIER_ENV, "native config changes verifier env")
    resolved = SimpleNamespace(
        path=root, content_hash=None, resolved_git_commit_id=None
    )
    model = locking.build_trial_lock(trial_config=config, task_download_result=resolved)
    lock = check_lock(
        locking,
        json.loads(model.model_dump_json(exclude_none=True)),
        request,
        identity["digest"],
        request["trial_name"],
    )
    require(lock["verifier"].get("env") == VERIFIER_ENV, "lock changes verifier env")
    runtime = inspect_record(lock)
    source = inspect_source(root, runtime.task)
    require(
        source.seats == runtime.seats == tuple(sorted(ROLES)), "role roster differs"
    )
    require(source.lock_covers_roles, "native lock omits a declared role")
    return lock, source


def outputs(job_id: str) -> dict[str, str]:
    trial = f"trials/{job_id}"
    return {
        "trial": trial,
        "private_cut": f"{trial}/verifier/private-recall-cut.json",
        "reward": f"{trial}/verifier/reward.json",
        "verdict": f"{trial}/verifier/verdict.json",
        "diagnostics": f"{trial}/verifier/diagnostics.json",
        "lock": f"{trial}/lock.json",
        "result": f"{trial}/result.json",
    }


def _receipt(
    plan: dict[str, Any],
    job: dict[str, Any],
    team: list[dict[str, Any]],
    native_records: dict[str, Any],
) -> dict[str, Any]:
    root, identity, source, request, lock, endpoints = (
        native_records[key]
        for key in ("root", "identity", "source", "request", "lock", "endpoints")
    )
    kind = team_kind(team)
    return {
        "schema": SCHEMA,
        "status": "prepared_not_run",
        "executions": 0,
        "model_calls": 0,
        "provider_authenticated": False,
        "job_id": job["job_id"],
        "position": job["position"],
        "block": job["block"],
        "condition": dict(job["session"]),
        "plan_sha256": hashlib.sha256(record_bytes(plan)).hexdigest(),
        "participant_labels": dict(job["participants"]),
        "team": {
            "kind": kind,
            "scripted": kind == "scripted_control",
            "free_team_eligible": kind == "host_adapter",
            "seats": list(ROLES),
            "agents": {m["seat"]: {k: m[k] for k in DECLARED if k in m} for m in team},
            "owner_endpoints": endpoints,
        },
        "task_name": identity["name"],
        "native_digest": identity["digest"],
        "task_checksum": identity["checksum"],
        "task_tree_digest": task_files_digest(root, identity),
        "role_file_digests": source.roles,
        "role_instruction_digests": source.effective_roles,
        "source_digest": source_digest(CHECKOUT),
        "host_harness_digest": fingerprint(CHECKOUT),
        "world_env": request["environment"]["env"],
        "world_scope": world_scope(root),
        "verifier_env": dict(VERIFIER_ENV),
        "config": CONFIG,
        "lock": LOCK,
        "config_sha256": _digest(request),
        "lock_sha256": _digest(lock),
        "roster_sha256": _digest({"participants": team}),
        "trial_dir": f"trials/{job['job_id']}",
        "outputs": outputs(job["job_id"]),
        "claims": dict(CLAIMS),
    }


def _build(
    plan: dict[str, Any],
    job: dict[str, Any],
    root: Path,
    roster: Any,
    out: Path,
    live_endpoints: bool,
) -> dict[str, dict[str, Any]]:
    harbor = native()
    identity = task_identity(harbor, root, TASK_NAME)
    _task_config(root)
    world_scope(root)
    team = _team(harbor, roster)
    endpoints = owner_endpoints(harbor, team, (root, out), live_endpoints)
    env = world_env(job["session"])
    request = trial_request(harbor, root, out / "trials", job["job_id"], team, env)
    request["verifier"] = {"env": dict(VERIFIER_ENV)}
    lock, source = _lock(harbor, root, request, identity)
    receipt = _receipt(
        plan,
        job,
        team,
        {
            "root": root,
            "identity": identity,
            "source": source,
            "request": request,
            "lock": lock,
            "endpoints": endpoints,
        },
    )
    return {CONFIG: request, LOCK: lock, RECEIPT: receipt}


def _place(out: Path, root: Path) -> Path:
    require(isinstance(out, Path), "output must be a path")
    out = out.expanduser().absolute()
    require(out.name not in ("", ".", ".."), "output must name a directory")
    out = out.parent.resolve(strict=True) / out.name
    require(not out.is_relative_to(CHECKOUT), "output must be outside the checkout")
    require(not out.is_relative_to(root), "output must be outside the task")
    return out


def _inputs(
    plan: Any, job_id: Any, task: Path
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    job = _slot(plan, job_id)
    require(isinstance(task, Path), "task must be a path")
    root, stamps = bounded_tree(task.expanduser().absolute())
    require(root.name == TASK_DIR, f"task directory must be named {TASK_DIR}")
    require(
        not any(
            "__pycache__" in rel.split("/") or rel.endswith(".pyc") for rel in stamps
        ),
        "task tree holds Python bytecode; remove it before preparation",
    )
    return job, root, stamps


def records(
    plan: dict[str, Any],
    job_id: str,
    task: Path,
    roster: dict[str, Any],
    out: Path,
    *,
    live_endpoints: bool,
) -> dict[str, dict[str, Any]]:
    require(type(live_endpoints) is bool, "live_endpoints must be a bool")
    job, root, stamps = _inputs(plan, job_id, task)
    built = _build(plan, job, root, roster, _place(out, root), live_endpoints)
    require_unchanged(root, stamps)
    return built


def _publish(out: Path, built: dict[str, dict[str, Any]], rebuild: Any) -> None:
    require(tuple(built) == FILES, "unexpected prepared records")
    staged = Path(tempfile.mkdtemp(prefix=".dispatch-recall-prepare-", dir=out.parent))
    try:
        for name, record in built.items():
            write_exclusive(staged / name, record, PRIVATE_FILE)
        sync_directory(staged)
        require(
            record_bytes(rebuild()[RECEIPT]) == record_bytes(built[RECEIPT]),
            "task or sources changed during preparation",
        )
        publish(staged, out)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    sync_directory(out.parent)


def prepare(
    plan: dict[str, Any], job_id: str, task: Path, roster: dict[str, Any], out: Path
) -> dict[str, Any]:
    job, root, stamps = _inputs(plan, job_id, task)
    out = _place(out, root)
    require(not os.path.lexists(out), "output already exists")

    def rebuild() -> dict[str, dict[str, Any]]:
        again = _build(plan, job, root, roster, out, True)
        require_unchanged(root, stamps)
        return again

    built = rebuild()
    _publish(out, built, rebuild)
    receipt = built[RECEIPT]
    return {
        "status": "prepared_not_run",
        "job_id": receipt["job_id"],
        "files": list(FILES),
        "receipt_sha256": hashlib.sha256(record_bytes(receipt)).hexdigest(),
        "plan_sha256": receipt["plan_sha256"],
        "config_sha256": receipt["config_sha256"],
        "lock_sha256": receipt["lock_sha256"],
        "native_digest": receipt["native_digest"],
        "task_tree_digest": receipt["task_tree_digest"],
        "team_kind": receipt["team"]["kind"],
        "scripted": receipt["team"]["scripted"],
        "free_team_eligible": receipt["team"]["free_team_eligible"],
        "command": (
            "uv run --locked --no-dev python -B -m harbor.cli.main trial start "
            f"-c {shlex.quote(str(out / CONFIG))}"
        ),
        "executions": 0,
    }
