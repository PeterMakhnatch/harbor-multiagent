from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...native_study import (
    native,
    require,
    task_files_digest,
    task_identity,
    trial_request,
)
from ..exhibit_author_prepare import TURNS, future_endpoints, owner_team
from ..exhibit_prepare import CHECKOUT, ROLES, locked_arm
from ..visibility import local_seat
from ..workbench.arm_sources import bounded_tree, require_unchanged
from .certificate import case_commitment, native_pair
from .preflight import (
    MAPS,
    NAME,
    SETTING,
    _digest,
    _output_path,
    _publish,
    host_fingerprint,
)
from .stage import check_stage
from .team_sources import extension_digest

SCHEMA = "seatlab.exhibit-relay-physical.free-team-preflight/v1"
VERIFIER_ENV = {"EXHIBIT_RETAIN_CUT": "1"}


def _arguments(seed: int, map_name: str) -> None:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid fixed seed")
    require(type(map_name) is str and map_name in MAPS, "invalid bay map")


def _place(output: Path, root: Path) -> Path:
    out = output.expanduser().absolute()
    require(out.name not in ("", ".", "..") and ".." not in out.parts, "invalid output")
    out = out.parent.resolve() / out.name
    require(not out.is_relative_to(CHECKOUT), "output must be outside checkout")
    require(not out.is_relative_to(root), "output must be outside task")
    return out


def _declared(paths: dict[str, str], root: Path, out: Path) -> dict[str, str]:
    require(
        type(paths) is dict and paths.keys() == set(TURNS),
        "expected four owner turn endpoints",
    )
    declared: dict[str, str] = {}
    for turn in TURNS:
        raw = paths[turn]
        require(type(raw) is str and "\x00" not in raw, f"{turn}: invalid endpoint")
        path = Path(raw)
        require(
            path.is_absolute()
            and ".." not in path.parts
            and str(path) == raw
            and 0 < len(os.fsencode(raw)) <= local_seat._MAX_SOCKET_PATH_BYTES
            and local_seat._SOCKET_NAME.fullmatch(path.name) is not None,
            f"{turn}: expected a canonical short absolute Unix path",
        )
        require(
            not any(path.is_relative_to(place) for place in (CHECKOUT, root, out)),
            f"{turn}: endpoint overlaps checkout, task or output",
        )
        declared[turn] = raw
    require(
        len({raw.casefold() for raw in declared.values()}) == len(TURNS),
        "owner turn endpoints must be distinct",
    )
    return declared


def _build(
    root: Path, out: Path, seed: int, map_name: str, paths: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    stage_digest = check_stage(root)
    endpoints = _declared(paths, root, out)
    host_digest = host_fingerprint(CHECKOUT)
    team_digest = extension_digest(CHECKOUT)
    require(
        type(team_digest) is str and bool(team_digest), "invalid team source digest"
    )
    base, worlds = native_pair(seed)
    require(set(worlds) == set(MAPS), "unexpected native case maps")
    commitments = {name: case_commitment(case) for name, case in worlds.items()}
    require(
        len(set(commitments.values())) == len(MAPS), "private maps share a commitment"
    )
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree_digest = task_files_digest(root, identity)
    ticket = f"exhibit-physical-team-{seed}-{map_name}"
    env = {
        "EXHIBIT_SEED": str(seed),
        "EXHIBIT_PROFILE": "fork",
        "EXHIBIT_MODE": "linked",
        "EXHIBIT_LAYOUT": "draw",
        SETTING: map_name,
    }
    team = owner_team(harbor, endpoints)
    request = trial_request(harbor, root, out / "trials", ticket, team, env)
    request["verifier"] = {"env": dict(VERIFIER_ENV)}
    lock, arm = locked_arm(harbor, root, request, identity, ticket)
    source = arm.source
    receipt = {
        "schema": SCHEMA,
        "status": "prepared_not_enrolled",
        "evidence_label": "future_free_team",
        "excluded_uses": ["paired_control", "holdout", "result"],
        "native_admission": "accepted",
        "input_basis": "prospective_local_configs",
        "executions": 0,
        "effect_estimated": False,
        "provider_authenticated": False,
        "provider_identities": [],
        "owner_contacted": False,
        "seed": seed,
        "map": map_name,
        "natural_focal_draw": base["realized"]
        == min(brief["id"] for brief in base["briefs"]),
        "case_commitment": commitments[map_name],
        "task_name": identity["name"],
        "trial_name": ticket,
        "profile": "fork",
        "mode": "linked",
        "layout": "draw",
        "verifier_env": dict(VERIFIER_ENV),
        "owner_turns": ["loader_request", "registrar", "depot", "loader_dispatch"],
        "owner_endpoints": endpoints,
        "native_digest": identity["digest"],
        "task_tree_digest": tree_digest,
        "host_source_digest": host_digest,
        "team_source_digest": team_digest,
        "stage_digest": stage_digest,
        "role_count": len(ROLES),
        "role_file_digests": source.roles,
        "role_instruction_digests": source.effective_roles,
        "native_lock_covers_roles": source.lock_covers_roles,
        "role_binding": "native_and_independent"
        if source.lock_covers_roles
        else "independent_only",
        "config": "config.json",
        "lock": "lock.json",
        "config_digest": _digest(request),
        "lock_digest": _digest(lock),
    }
    records = {"config.json": request, "lock.json": lock, "receipt.json": receipt}
    return records, identity


def build_records(
    task: Path, output: Path, seed: int, map_name: str, paths: dict[str, str]
) -> dict[str, dict[str, Any]]:
    _arguments(seed, map_name)
    root, stamps = bounded_tree(task.expanduser().absolute())
    out = _place(output, root)
    records, _ = _build(root, out, seed, map_name, paths)
    require_unchanged(root, stamps)
    return records


def prepare_team(
    task: Path, output: Path, seed: int, map_name: str, supplied: dict[str, Path]
) -> dict[str, Any]:
    _arguments(seed, map_name)
    require(
        type(supplied) is dict and all(isinstance(p, Path) for p in supplied.values()),
        "expected owner turn endpoint paths",
    )
    root, stamps = bounded_tree(task.expanduser().absolute())
    out = _output_path(output, root)
    endpoints, parents = future_endpoints(supplied, out)
    records, identity = _build(root, out, seed, map_name, endpoints)
    receipt = records["receipt.json"]
    require(
        task_identity(native(), root, NAME) == identity, "native task identity changed"
    )
    require(
        extension_digest(CHECKOUT) == receipt["team_source_digest"],
        "team implementation changed",
    )
    require(
        future_endpoints(supplied, out) == (endpoints, parents),
        "owner endpoints changed during preparation",
    )
    tree_digest, stage_digest = receipt["task_tree_digest"], receipt["stage_digest"]
    _publish(out, records, root, stamps, identity, tree_digest, stage_digest)
    return receipt
