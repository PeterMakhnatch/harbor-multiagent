from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...native_study import native, normalize, require, task_files_digest, trial_request
from ..exhibit_owner import _identity
from ..exhibit_prepare import CHECKOUT
from ..visibility import local_seat
from ..workbench.arm_sources import bounded_tree
from .certificate import case_commitment, native_pair
from .preflight import (
    MAPS,
    NAME,
    ROLES,
    SETTING,
    _digest,
    _output_path,
    _publish,
    host_fingerprint,
    locked_arm,
    task_identity,
)
from .stage import check_stage

FILINGS = ("other", "relevant")
CELLS = tuple(f"{map_name}-{filing}" for map_name in MAPS for filing in FILINGS)
RECEIVER = "examples.seatlab.physical_dispatch.receiver:DispatchReceiver"


def _endpoints(
    supplied: dict[str, str], root: Path, out: Path
) -> tuple[dict[str, str], dict[str, tuple[int, int, int, int]]]:
    require(
        type(supplied) is dict and supplied.keys() == set(CELLS),
        "expected four receiver endpoints",
    )
    paths: dict[str, str] = {}
    pins: dict[str, tuple[int, int, int, int]] = {}
    aliases: set[tuple[int, int, str]] = set()
    sockets: set[tuple[int, int]] = set()
    for cell in CELLS:
        raw = supplied[cell]
        require(type(raw) is str and "\x00" not in raw, f"{cell}: invalid socket path")
        path = Path(raw)
        require(
            path.is_absolute()
            and ".." not in path.parts
            and 0 < len(os.fsencode(raw)) < 104,
            f"{cell}: expected a short absolute Unix path",
        )
        try:
            canonical = Path(local_seat._socket_path(raw))
        except local_seat._Fail as error:
            raise ValueError(f"{cell}: {error.message}") from None
        require(
            not any(
                canonical.is_relative_to(directory)
                for directory in (CHECKOUT, root, out)
            ),
            f"{cell}: endpoint overlaps checkout, task or output",
        )
        parent = canonical.parent.stat()
        alias = (parent.st_dev, parent.st_ino, canonical.name.casefold())
        pin = _identity(str(canonical))
        require(
            alias not in aliases and pin not in sockets,
            "receiver endpoints must be distinct",
        )
        aliases.add(alias)
        sockets.add(pin)
        paths[cell] = str(canonical)
        pins[cell] = (parent.st_dev, parent.st_ino, *pin)
    require(len(set(paths.values())) == len(CELLS), "receiver path alias")
    return paths, pins


def _kwargs(role: str, endpoint: str, filing: str) -> dict[str, str]:
    require(role in ROLES and filing in FILINGS, "invalid role or filing")
    if role == "loader":
        return {"role": role, "owner_socket": endpoint}
    if role == "depot":
        return {"role": role, "filing": filing}
    return {"role": role}


def _team(harbor: SimpleNamespace, endpoint: str, filing: str) -> list[dict[str, Any]]:
    team = []
    for role in ROLES:
        kwargs = _kwargs(role, endpoint, filing)
        member = normalize(
            harbor, {"seat": role, "import_path": RECEIVER, "kwargs": kwargs}
        )
        require(
            member.get("seat") == role
            and member.get("import_path") == RECEIVER
            and member.get("kwargs") == kwargs
            and member.get("name") is None
            and member.get("model_name") is None,
            "native participant changed receiver binding",
        )
        team.append(member)
    return team


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _neutral(
    record: dict[str, Any],
    *,
    config: bool,
    ticket: str,
    env: dict[str, str],
    endpoint: str,
    filing: str,
    team: list[dict[str, Any]],
) -> bytes:
    rest = copy.deepcopy(record)
    if config:
        require(rest.pop("trial_name") == ticket, "unexpected trial name")
    else:
        require("trial_name" not in rest, "unexpected native lock trial name")
    actual_env = rest["environment"]["env"]
    require(_canonical(actual_env) == _canonical(env), "unexpected native environment")
    actual_env[SETTING] = "<map>"
    members = rest["participants"]
    require(_canonical(members) == _canonical(team), "unexpected native participants")
    for role, member in zip(ROLES, members, strict=True):
        require(
            member["seat"] == role
            and member["import_path"] == RECEIVER
            and member["kwargs"] == _kwargs(role, endpoint, filing),
            "unexpected role binding",
        )
        if role == "loader":
            member["kwargs"]["owner_socket"] = "<endpoint>"
        elif role == "depot":
            member["kwargs"]["filing"] = "<filing>"
    return _canonical(rest)


def prepare_receivers(
    task: Path, out: Path, seed: int, endpoints: dict[str, str]
) -> dict[str, Any]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid fixed seed")
    root, stamps = bounded_tree(task.expanduser().absolute())
    stage_digest = check_stage(root)
    out = _output_path(out, root)
    host_digest = host_fingerprint(CHECKOUT)
    paths, pins = _endpoints(endpoints, root, out)
    base, cases = native_pair(seed)
    require(
        base["realized"] == min(brief["id"] for brief in base["briefs"]),
        "fixed seed is not a naturally focal draw",
    )
    require(set(cases) == set(MAPS), "unexpected native case maps")
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree_digest = task_files_digest(root, identity)
    records: dict[str, dict[str, Any]] = {}
    inputs: dict[str, Any] = {}
    neutral_configs: set[bytes] = set()
    neutral_locks: set[bytes] = set()
    sources = []
    for map_name in MAPS:
        for filing in FILINGS:
            cell = f"{map_name}-{filing}"
            ticket = f"exhibit-physical-receiver-{seed}-{cell}"
            env = {
                "EXHIBIT_SEED": str(seed),
                "EXHIBIT_PROFILE": "fork",
                "EXHIBIT_MODE": "linked",
                "EXHIBIT_LAYOUT": "draw",
                SETTING: map_name,
            }
            team = _team(harbor, paths[cell], filing)
            request = trial_request(harbor, root, out / "trials", ticket, team, env)
            lock, arm = locked_arm(harbor, root, request, identity, ticket)
            for config, record, neutral in (
                (True, request, neutral_configs),
                (False, lock, neutral_locks),
            ):
                neutral.add(
                    _neutral(
                        record,
                        config=config,
                        ticket=ticket,
                        env=env,
                        endpoint=paths[cell],
                        filing=filing,
                        team=team,
                    )
                )
            sources.append(arm.source)
            config_name, lock_name = f"{cell}.config.json", f"{cell}.lock.json"
            records[config_name], records[lock_name] = request, lock
            inputs[cell] = {
                "trial_name": ticket,
                "map": map_name,
                "filing": filing,
                "case_commitment": case_commitment(cases[map_name]),
                "config": config_name,
                "lock": lock_name,
                "config_digest": _digest(request),
                "lock_digest": _digest(lock),
            }
    require(
        len(inputs) == len(CELLS) == 4 and len(records) == 8,
        "incomplete receiver matrix",
    )
    require(
        len(neutral_configs) == len(neutral_locks) == 1,
        "native inputs differ outside declared factors",
    )
    source = sources[0]
    require(
        all(
            item.roles == source.roles
            and item.effective_roles == source.effective_roles
            and item.lock_covers_roles == source.lock_covers_roles
            for item in sources
        ),
        "role provenance differs",
    )
    receipt = _receipt(
        seed, identity, tree_digest, stage_digest, base, cases, inputs, source
    )
    receipt["host_source_digest"] = host_digest
    records["receipt.json"] = receipt
    require(
        _endpoints(endpoints, root, out) == (paths, pins),
        "receiver endpoints changed during preparation",
    )
    _publish(out, records, root, stamps, identity, tree_digest, stage_digest)
    return receipt


def _receipt(
    seed: int,
    identity: dict[str, str],
    tree_digest: str,
    stage_digest: str,
    base: Any,
    cases: dict[str, Any],
    inputs: dict[str, Any],
    source: Any,
) -> dict[str, Any]:
    return {
        "schema": "seatlab.exhibit-relay-physical.receiver-preflight/v1",
        "status": "prepared_not_enrolled",
        "native_admission": "accepted",
        "input_basis": "prospective_local_configs",
        "executions": 0,
        "effect_estimated": False,
        "provider_authenticated": False,
        "provider_identities": [],
        "endpoint_caveat": "Distinct local sockets do not establish provider identity or independence.",
        "seed": seed,
        "natural_focal_draw": True,
        "task_name": identity["name"],
        "profile": "fork",
        "mode": "linked",
        "layout": "draw",
        "base_case_commitment": case_commitment(base),
        "case_commitments": {name: case_commitment(cases[name]) for name in MAPS},
        "native_digest": identity["digest"],
        "task_tree_digest": tree_digest,
        "stage_digest": stage_digest,
        "role_count": len(ROLES),
        "role_file_digests": source.roles,
        "role_instruction_digests": source.effective_roles,
        "native_lock_covers_roles": source.lock_covers_roles,
        "role_binding": "native_and_independent"
        if source.lock_covers_roles
        else "independent_only",
        "receiver_import_path": RECEIVER,
        "role_interfaces": {
            "loader": {
                "request": "policies.nominate",
                "dispatch": "seatlab.seat-turn/v1",
            },
            "registrar": {"registrar": "policies.registrar"},
            "depot": {"depot": "policies.depot"},
        },
        "comparison": {
            "status": "isolated",
            "varied": [
                "trial_name",
                SETTING,
                "loader.kwargs.owner_socket",
                "depot.kwargs.filing",
            ],
        },
        "arms": inputs,
    }
