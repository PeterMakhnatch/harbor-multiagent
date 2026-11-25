from __future__ import annotations

import hashlib
import os
from importlib import import_module
from pathlib import Path
from typing import Any

from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from ..native_study import native, task_files_digest, task_identity, trial_request
from .exhibit_author_prepare import ARMS, OWNER_ROLES, REUSE_RULE, TURNS, owner_team
from .exhibit_authored import WORLD, canonical
from .exhibit_capture import read_record
from .exhibit_enroll import LAYOUTS, PROFILE, digest
from .exhibit_harness import fingerprint
from .exhibit_pairs import DIR_FLAGS, require
from .exhibit_prepare import CHECKOUT, NAME, ROLES, TASK, locked_arm
from .workbench.arm_runtime import inspect_record
from .workbench.arm_sources import bounded_tree, require_unchanged
from .workbench.isolate import Arm, compare


def check_scope(receipt: dict[str, Any], arm: str) -> tuple[int, str]:
    expected = {
        "schema": "seatlab.exhibit-author-preflight/v1",
        "status": "prepared_not_enrolled",
        "development_only": True,
        "fixed_prefix_eligible": False,
        "native_admission": "accepted",
        "executions": 0,
        "provider_authenticated": False,
        "provider_identities": [],
        "effect_estimated": False,
        "profile": PROFILE,
        "role_count": len(ROLES),
        "role_binding": "native_and_independent",
        "native_lock_covers_roles": True,
        "owner_turns": list(TURNS),
        "owner_rebinding_rule": REUSE_RULE,
    }
    require(
        canonical({k: receipt[k] for k in expected}) == canonical(expected),
        "invalid prepared plan scope",
    )
    seed, layout = receipt["seed"], receipt["layout"]
    require(
        type(seed) is int and 0 <= seed < 2**63 and layout in LAYOUTS,
        "invalid prepared case",
    )
    require(arm in ARMS and set(receipt["arms"]) == set(ARMS), "invalid prepared arms")
    require(
        receipt["host_harness_digest"] == fingerprint(CHECKOUT),
        "host harness differs from plan",
    )
    return seed, layout


def absolute_path(value: Any) -> Path:
    require(type(value) is str and "\x00" not in value, "invalid recorded path")
    path = Path(value)
    require(
        path.is_absolute() and ".." not in path.parts and str(path) == value,
        "noncanonical recorded path",
    )
    return path


def endpoints(request: dict[str, Any]) -> dict[str, str]:
    members = request["participants"]
    require(
        type(members) is list and len(members) == len(ROLES),
        "invalid participant roster",
    )
    paths = {}
    for role, member in zip(ROLES, members, strict=True):
        turns = OWNER_ROLES[role]
        sockets = member["kwargs"]["owner_sockets"]
        require(
            member["seat"] == role
            and type(sockets) is list
            and len(sockets) == len(turns),
            "invalid owner bindings",
        )
        for turn, socket in zip(turns, sockets, strict=True):
            path = absolute_path(socket)
            require(
                bool(path.name) and len(os.fsencode(path)) < 104,
                "invalid owner endpoint",
            )
            paths[turn] = socket
    require(
        len({path.casefold() for path in paths.values()}) == len(TURNS),
        "duplicate owner endpoints",
    )
    return paths


def prepared_arm(
    fd: int,
    receipt: dict[str, Any],
    mode: str,
    root: Path,
    harbor: Any,
    identity: dict[str, str],
) -> tuple[Any, dict[str, Any], Arm, dict[str, str], dict[str, str]]:
    seed, layout = receipt["seed"], receipt["layout"]
    ticket = f"dev-exhibit-author-{seed}-{layout}-{mode}"
    config_name, lock_name = f"{mode}.config.json", f"{mode}.lock.json"
    request, config_bytes = read_record(fd, config_name)
    lock, lock_bytes = read_record(fd, lock_name)
    hashes = {"config_digest": digest(config_bytes), "lock_digest": digest(lock_bytes)}
    require(
        receipt["arms"][mode]
        == {"trial_name": ticket, "config": config_name, "lock": lock_name, **hashes},
        "plan input binding differs",
    )
    paths = endpoints(request)
    task_path = absolute_path(request["task"]["path"])
    trials = absolute_path(request["trials_dir"])
    require(
        task_path.parts[-3:] == ("examples", "tasks", root.name)
        and trials.name == "trials",
        "invalid historical task or trial root",
    )
    team = owner_team(harbor, paths)
    env = {
        "EXHIBIT_PROFILE": PROFILE,
        "EXHIBIT_LAYOUT": layout,
        "EXHIBIT_SEED": str(seed),
        "EXHIBIT_MODE": mode,
    }
    expected = trial_request(harbor, root, trials, ticket, team, env)
    expected["verifier"] = {"env": {"EXHIBIT_RETAIN_CUT": "1"}}
    current_lock, measured = locked_arm(harbor, root, expected, identity, ticket)
    expected["task"]["path"] = str(task_path)
    current_lock["task"]["path"] = str(task_path)
    require(
        canonical(request) == canonical(expected), "prepared config differs from design"
    )
    require(
        canonical(lock) == canonical(current_lock),
        "prepared lock differs from current native lock",
    )
    source = measured.source
    require(
        source.lock_covers_roles
        and source.roles == receipt["role_file_digests"]
        and source.effective_roles == receipt["role_instruction_digests"],
        "role instructions differ from plan",
    )
    config = harbor.TrialConfig.model_validate(expected)
    return config, lock, Arm(source, inspect_record(lock)), paths, hashes


def check_result(
    raw: dict[str, Any],
    config: Any,
    identity: dict[str, str],
    task: Any,
    lock: dict[str, Any],
) -> TrialResult:
    require("id" in raw, "trial id missing")
    result = TrialResult.model_validate(raw)
    require(
        canonical(result.model_dump(mode="json")) == canonical(raw),
        "noncanonical native result",
    )
    require(
        canonical(raw["config"]) == canonical(config.model_dump(mode="json"))
        and result.trial_name == config.trial_name
        and result.trial_uri == (config.trials_dir / config.trial_name).as_uri()
        and result.task_name == identity["name"]
        and result.task_id == config.task.get_task_id()
        and result.task_checksum == identity["checksum"]
        and result.source == config.task.source
        and result.verifier_environment_mode == lock["verifier"]["environment_mode"],
        "native result identity mismatch",
    )
    start, finish = result.started_at, result.finished_at
    require(
        start is not None
        and finish is not None
        and start.utcoffset() is not None
        and finish.utcoffset() is not None
        and start <= finish
        and result.verifier is not None,
        "trial not finished with verifier",
    )
    seats = result.seat_results or []
    require(
        len(seats) == len(ROLES)
        and {seat.seat for seat in seats} == set(ROLES)
        and result.step_results is None
        and result.agent_result is None,
        "invalid native result roster",
    )
    for seat in seats:
        info = seat.agent_info
        require(
            task.config.agents[seat.seat].under_test
            and seat.under_test
            and info is not None
            and info.name == "seatlab-exhibit-relay-owner"
            and info.version == "1.0.0"
            and info.model_info is None,
            "native seat differs from owner design",
        )
    require(
        result.agent_info == seats[0].agent_info,
        "native primary agent differs from owner design",
    )
    return result


def load_inputs(
    plan: Path, arm: str, trial_fd: int
) -> tuple[TrialResult, dict[str, Any], dict[str, str], dict[str, str]]:
    harbor = native()
    root, stamps = bounded_tree(TASK)
    identity = task_identity(harbor, root, NAME)
    plan_fd = os.open(plan, DIR_FLAGS)
    try:
        receipt, receipt_bytes = read_record(plan_fd, "receipt.json")
        seed, layout = check_scope(receipt, arm)
        require(
            identity["digest"] == receipt["native_digest"]
            and task_files_digest(root, identity) == receipt["task_tree_digest"],
            "task tree differs from plan",
        )
        prepared = {
            mode: prepared_arm(plan_fd, receipt, mode, root, harbor, identity)
            for mode in ARMS
        }
    finally:
        os.close(plan_fd)
    left, right = (prepared[mode] for mode in ARMS)
    require(
        left[3] == right[3]
        and left[0].task == right[0].task
        and left[0].trials_dir == right[0].trials_dir,
        "prepared arm origins differ",
    )
    comparison = compare(left[2], right[2], "world_setting", "EXHIBIT_MODE", None)
    require(
        comparison["status"] == "isolated"
        and comparison["comparison"] == receipt["comparison"]
        and comparison["caveat"] == receipt["comparison_caveat"],
        "prepared arm isolation differs",
    )
    config, lock, measured, _, pins = prepared[arm]
    paths = TrialPaths(Path(config.trial_name))
    raw_config, config_bytes = read_record(trial_fd, paths.config_path.name)
    raw_lock, lock_bytes = read_record(trial_fd, paths.lock_path.name)
    require(
        canonical(raw_config)
        == canonical(config.model_dump(mode="json", exclude={"agent"})),
        "archive config differs from plan",
    )
    require(canonical(raw_lock) == canonical(lock), "archive lock differs from plan")
    raw_result, result_bytes = read_record(trial_fd, paths.result_path.name)
    task = harbor.Task(root)
    result = check_result(raw_result, config, identity, task, lock)
    instructions = {role: task.seat_instruction(role) for role in ROLES}
    require(
        {
            role: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for role, text in instructions.items()
        }
        == measured.source.effective_roles,
        "effective instructions changed",
    )
    case = import_module(f"{WORLD}.cases").make_case(seed, PROFILE, layout=layout)
    require_unchanged(root, stamps)
    require(
        fingerprint(CHECKOUT) == receipt["host_harness_digest"], "host harness changed"
    )
    hashes = {
        "plan": digest(receipt_bytes),
        "prepared_config": pins["config_digest"],
        "prepared_lock": pins["lock_digest"],
        "config": digest(config_bytes),
        "lock": digest(lock_bytes),
        "result": digest(result_bytes),
        "task_tree": receipt["task_tree_digest"],
        "native_task": identity["digest"],
        "host_harness": receipt["host_harness_digest"],
    }
    return result, case, instructions, hashes
