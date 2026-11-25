from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from ...native_study import (
    MAX_TASK_BYTES,
    _task_file_sha,
    native,
    record_bytes,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ...seat_checkpoints.materialize import read_plain, sync_directory
from ...ship_inflight.materialize import publish
from ..exhibit_author_prepare import OWNER_ROLES, TURNS, future_endpoints, owner_team
from ..exhibit_bind import DEPOT_OWNER
from ..exhibit_harness import fingerprint
from ..exhibit_prepare import CHECKOUT, ROLES, locked_arm
from ..physical_dispatch import receiver_plan
from ..physical_dispatch.certificate import case_commitment, make_case
from ..physical_dispatch.preflight import _output_path
from ..visibility import local_seat
from ..workbench.arm_sources import bounded_tree, require_unchanged
from . import stage, world

TEAM_SCHEMA = "seatlab.exhibit-relay-joint.team-plan/v1"
RECEIVER_SCHEMA = "seatlab.exhibit-relay-joint.receiver-plan/v1"
TRIAL_NAME = "exhibit-relay-joint"
CONFIG = "config.json"
LOCK = "lock.json"
RECEIPT = "receipt.json"
PRIVATE_FILE = 0o600
FIXED_ENV = {
    "EXHIBIT_PROFILE": "fork",
    "EXHIBIT_MODE": "linked",
    "EXHIBIT_LAYOUT": "draw",
}
PRIVATE_KEYS = (
    "EXHIBIT_SEED",
    "EXHIBIT_REGISTRY_MAP",
    "EXHIBIT_BAY_MAP",
    "EXHIBIT_CHANNELS",
)
SERVER = "main"
COMPOSE = "environment/docker-compose.yaml"
VERIFIER_ENV = {"EXHIBIT_RETAIN_CUT": "1"}
RECEIVER = receiver_plan.RECEIVER
RECEIVER_FILING = "relevant"
RECEIVER_TURN = "loader_dispatch"
JOINT_SOURCES = (
    "examples/seatlab/joint_handoffs/prepare.py",
    "examples/seatlab/joint_handoffs/stage.py",
    "examples/seatlab/joint_handoffs/world.py",
    "examples/seatlab/physical_dispatch/certificate.py",
    "examples/seatlab/physical_dispatch/policies.py",
    "examples/seatlab/physical_dispatch/preflight.py",
    "examples/seatlab/physical_dispatch/receiver.py",
    "examples/seatlab/physical_dispatch/receiver_plan.py",
    "examples/seatlab/physical_dispatch/stage.py",
)
CLAIMS = {
    "holdout": "not_claimed",
    "provider_identity": "not_bound",
    "receiver_study_enrollment": "not_enrolled",
    "completed_results_audit": "not_performed",
}

assert JOINT_SOURCES == tuple(sorted(set(JOINT_SOURCES)))
assert set(FIXED_ENV).isdisjoint(PRIVATE_KEYS)
assert len(TURNS) == 4 and len(ROLES) == 3 and RECEIVER_TURN in TURNS

type Team = list[dict[str, Any]]


def joint_source_digest(root: Path) -> str:
    outer = hashlib.sha256(b"seatlab.exhibit-relay-joint.sources/v1\n")
    budget = MAX_TASK_BYTES
    for relative in JOINT_SOURCES:
        checksum, size = _task_file_sha(root / relative, budget)
        budget -= size
        outer.update(f"{relative}\0{checksum}\n".encode("ascii"))
    assert 0 <= budget <= MAX_TASK_BYTES
    return "sha256:" + outer.hexdigest()


def _arguments(seed: int, registry_map: str, bay_map: str, channels: str) -> None:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid fixed seed")
    require(
        type(registry_map) is str and registry_map in world.MAPS,
        "invalid registry map",
    )
    require(type(bay_map) is str and bay_map in world.MAPS, "invalid bay map")
    require(type(channels) is str and channels in world.CHANNELS, "invalid channels")


def world_env(
    seed: int, registry_map: str, bay_map: str, channels: str
) -> dict[str, str]:
    env = {
        **FIXED_ENV,
        "EXHIBIT_SEED": str(seed),
        "EXHIBIT_REGISTRY_MAP": registry_map,
        "EXHIBIT_BAY_MAP": bay_map,
        "EXHIBIT_CHANNELS": channels,
    }
    assert set(env) == {*FIXED_ENV, *PRIVATE_KEYS}
    return env


def natural_focal(base: dict[str, Any]) -> bool:
    return base["realized"] == min(brief["id"] for brief in base["briefs"])


def _draw(seed: int, registry_map: str, bay_map: str) -> tuple[bool, str]:
    base = make_case(seed, "fork", layout="draw")
    commitments = {
        (registry, bay): case_commitment(world.select(base, registry, bay))
        for registry in world.MAPS
        for bay in world.MAPS
    }
    require(
        len(set(commitments.values())) == len(world.MAPS) ** 2,
        "private record maps share a case commitment",
    )
    return natural_focal(base), commitments[(registry_map, bay_map)]


def server_only(root: Path) -> list[str]:
    data, _ = read_plain(root / COMPOSE)
    document = yaml.safe_load(data.decode("utf-8"))
    services = document.get("services") if type(document) is dict else None
    require(
        type(services) is dict and set(services) == {SERVER, *ROLES},
        "compose services differ from the server and three seats",
    )
    declared = cast(dict[str, Any], services)
    server = declared[SERVER]
    env = server.get("environment") if type(server) is dict else None
    keys = [*FIXED_ENV, *PRIVATE_KEYS]
    require(
        type(env) is dict and all(key in env for key in keys),
        "server does not receive every world setting",
    )
    for role in ROLES:
        seat = declared[role]
        require(
            type(seat) is dict and "environment" not in seat and "env_file" not in seat,
            f"{role} seat receives service environment",
        )
        require(
            not any(key in yaml.safe_dump(seat) for key in keys),
            f"{role} seat names a world setting",
        )
    return sorted(keys)


def _check_team(team: Team, agent: str, kwargs: dict[str, dict[str, Any]]) -> Team:
    require([member.get("seat") for member in team] == list(ROLES), "team differs")
    for member in team:
        require(
            member.get("import_path") == agent
            and member.get("kwargs") == kwargs[member["seat"]]
            and member.get("name") is None
            and member.get("model_name") is None
            and member.get("base_url") is None
            and not member.get("env"),
            "participant carries more than its fixed role binding",
        )
    shown = record_bytes({"participants": team})
    require(
        not any(key.encode("ascii") in shown for key in PRIVATE_KEYS),
        "participant config names a private world setting",
    )
    return team


def _native(
    root: Path, out: Path, env: dict[str, str], bind: Callable[[Any], Team]
) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, str]]:
    harbor = native()
    identity = task_identity(harbor, root, stage.TASK_NAME)
    team = bind(harbor)
    request = trial_request(harbor, root, out / "trials", TRIAL_NAME, team, env)
    request["verifier"] = {"env": dict(VERIFIER_ENV)}
    lock, arm = locked_arm(harbor, root, request, identity, TRIAL_NAME)
    require(arm.source.lock_covers_roles, "native lock omits a declared role")
    require(
        request["participants"] == team and request["trial_name"] == TRIAL_NAME,
        "native config changed the neutral team",
    )
    return request, lock, arm.source, identity


def _digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(record_bytes(record)).hexdigest()


def _build(
    root: Path,
    out: Path,
    seed: int,
    condition: tuple[str, str, str],
    bind: Callable[[Any], Team],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    registry_map, bay_map, channels = condition
    stage_digest = stage.check_stage(root)
    keys = server_only(root)
    focal, commitment = _draw(seed, registry_map, bay_map)
    env = world_env(seed, registry_map, bay_map, channels)
    request, lock, source, identity = _native(root, out, env, bind)
    receipt = {
        "status": "prepared_not_run",
        "native_admission": "accepted",
        "input_basis": "prospective_local_configs",
        "executions": 0,
        "model_calls": 0,
        "owner_contacted": False,
        "provider_authenticated": False,
        "provider_identities": [],
        "effect_estimated": False,
        "seed": seed,
        "natural_focal_draw": focal,
        "condition": {
            "registry_map": registry_map,
            "bay_map": bay_map,
            "channels": channels,
        },
        "case_commitment": commitment,
        "condition_scope": {"service": SERVER, "keys": keys},
        "task_name": identity["name"],
        "trial_name": TRIAL_NAME,
        "verifier_env": dict(VERIFIER_ENV),
        "native_digest": identity["digest"],
        "task_tree_digest": task_files_digest(root, identity),
        "stage_digest": stage_digest,
        "host_harness_digest": fingerprint(CHECKOUT),
        "joint_source_digest": joint_source_digest(CHECKOUT),
        "role_file_digests": source.roles,
        "role_instruction_digests": source.effective_roles,
        "native_lock_covers_roles": True,
        "config": CONFIG,
        "lock": LOCK,
        "config_digest": _digest(request),
        "lock_digest": _digest(lock),
    }
    return {CONFIG: request, LOCK: lock}, receipt


def prepare(
    task: Path,
    out: Path,
    seed: int,
    registry_map: str,
    bay_map: str,
    channels: str,
    supplied: dict[str, Path],
) -> dict[str, Any]:
    _arguments(seed, registry_map, bay_map, channels)
    require(
        type(supplied) is dict
        and all(isinstance(path, Path) for path in supplied.values()),
        "expected owner turn endpoint paths",
    )
    root, stamps = bounded_tree(task.expanduser().absolute())
    out = _output_path(out, root)
    endpoints, parents = future_endpoints(supplied, out)
    require(
        not any(Path(path).is_relative_to(root) for path in endpoints.values()),
        "owner endpoint overlaps the task",
    )
    kwargs = {
        role: {"role": role, "owner_sockets": [endpoints[t] for t in turns]}
        for role, turns in OWNER_ROLES.items()
    }
    records, receipt = _build(
        root,
        out,
        seed,
        (registry_map, bay_map, channels),
        lambda harbor: _check_team(owner_team(harbor, endpoints), DEPOT_OWNER, kwargs),
    )
    receipt = {
        "schema": TEAM_SCHEMA,
        "design": "autonomous_team",
        "fixed_prefix_eligible": False,
        "claims": {"draw": "any_stock_draw", **CLAIMS},
        **receipt,
        "team": {"roles": list(ROLES), "agent": DEPOT_OWNER, "autonomous": True},
        "owner_turns": list(TURNS),
        "owner_endpoints": endpoints,
    }
    _publish(
        out,
        {**records, RECEIPT: receipt},
        (root, stamps),
        lambda: future_endpoints(supplied, out) == (endpoints, parents),
    )
    return receipt


def receiver_endpoint(
    owner: Path, root: Path, out: Path
) -> tuple[str, tuple[int, int]]:
    require(
        isinstance(owner, Path)
        and owner.is_absolute()
        and ".." not in owner.parts
        and 0 < len(os.fsencode(owner)) < 104,
        "owner: expected a short absolute Unix path",
    )
    try:
        canonical = Path(local_seat._socket_path(str(owner)))
    except local_seat._Fail as error:
        raise ValueError(f"owner: {error.message}") from None
    require(
        not any(canonical.is_relative_to(place) for place in (CHECKOUT, root, out)),
        "owner endpoint overlaps checkout, task or output",
    )
    require(not os.path.lexists(canonical), "owner endpoint must be absent")
    info = canonical.parent.stat()
    return str(canonical), (info.st_dev, info.st_ino)


def prepare_receiver(
    task: Path,
    out: Path,
    seed: int,
    registry_map: str,
    bay_map: str,
    channels: str,
    owner: Path,
) -> dict[str, Any]:
    _arguments(seed, registry_map, bay_map, channels)
    require(
        natural_focal(make_case(seed, "fork", layout="draw")),
        "fixed seed is not a naturally focal draw",
    )
    root, stamps = bounded_tree(task.expanduser().absolute())
    out = _output_path(out, root)
    endpoint, parent = receiver_endpoint(owner, root, out)
    kwargs = {
        "loader": {"role": "loader", "owner_socket": endpoint},
        "registrar": {"role": "registrar"},
        "depot": {"role": "depot", "filing": RECEIVER_FILING},
    }
    records, receipt = _build(
        root,
        out,
        seed,
        (registry_map, bay_map, channels),
        lambda harbor: _check_team(
            receiver_plan._team(harbor, endpoint, RECEIVER_FILING), RECEIVER, kwargs
        ),
    )
    assert receipt["natural_focal_draw"] is True
    receipt = {
        "schema": RECEIVER_SCHEMA,
        "design": "fixed_prefix_receiver",
        "fixed_prefix_eligible": True,
        "claims": {"draw": "natural_focal_only", **CLAIMS},
        **receipt,
        "team": {
            "roles": list(ROLES),
            "agent": RECEIVER,
            "autonomous": False,
            "fixed": {
                "loader_request": "policies.nominate",
                "registrar": "policies.registrar",
                "depot": f"policies.depot:{RECEIVER_FILING}",
            },
        },
        "owner_turns": [RECEIVER_TURN],
        "owner_endpoints": {RECEIVER_TURN: endpoint},
    }
    _publish(
        out,
        {**records, RECEIPT: receipt},
        (root, stamps),
        lambda: receiver_endpoint(owner, root, out) == (endpoint, parent),
    )
    return receipt


def _recheck(
    receipt: dict[str, Any], pins: tuple[Path, Any], endpoints: Callable[[], bool]
) -> None:
    root, stamps = pins
    require_unchanged(root, stamps)
    require(stage.check_stage(root) == receipt["stage_digest"], "stage changed")
    identity = task_identity(native(), root, stage.TASK_NAME)
    require(identity["digest"] == receipt["native_digest"], "native task changed")
    require(
        task_files_digest(root, identity) == receipt["task_tree_digest"],
        "task tree changed",
    )
    require(
        fingerprint(CHECKOUT) == receipt["host_harness_digest"]
        and joint_source_digest(CHECKOUT) == receipt["joint_source_digest"],
        "host implementation changed",
    )
    require(endpoints(), "owner endpoints changed during preparation")
    require_unchanged(root, stamps)


def _publish(
    out: Path,
    records: dict[str, dict[str, Any]],
    pins: tuple[Path, Any],
    endpoints: Callable[[], bool],
) -> None:
    require(set(records) == {CONFIG, LOCK, RECEIPT}, "unexpected plan records")
    staged = Path(tempfile.mkdtemp(prefix=".exhibit-joint-plan-", dir=out.parent))
    try:
        for name, record in records.items():
            write_exclusive(staged / name, record, PRIVATE_FILE)
        sync_directory(staged)
        _recheck(records[RECEIPT], pins, endpoints)
        publish(staged, out)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    sync_directory(out.parent)
