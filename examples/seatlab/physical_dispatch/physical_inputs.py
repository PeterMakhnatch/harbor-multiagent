from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from ...native_study import native, record_bytes, task_files_digest, task_identity
from ..exhibit_capture import read_record
from ..exhibit_enroll import digest
from ..exhibit_pairs import DIR_FLAGS, require
from ..exhibit_prepare import CHECKOUT, ROLES
from ..workbench.arm_sources import bounded_tree, require_unchanged
from .certificate import native_pair
from .preflight import NAME, host_fingerprint
from .receiver_plan import CELLS, _canonical, _receipt, factor_inputs
from .stage import check_stage


def equal(left: Any, right: Any, message: str) -> None:
    require(_canonical(left) == _canonical(right), message)


def absolute_path(value: Any) -> Path:
    require(type(value) is str and "\x00" not in value, "invalid path")
    path = Path(value)
    require(
        path.is_absolute() and ".." not in path.parts and str(path) == value,
        "noncanonical path",
    )
    return path


def read_plan(plan: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    fd = os.open(plan, DIR_FLAGS)
    try:
        receipt, payload = read_record(fd, "receipt.json")
        records: dict[str, Any] = {
            cell: tuple(
                read_record(fd, f"{cell}.{kind}.json") for kind in ("config", "lock")
            )
            for cell in CELLS
        }
    finally:
        os.close(fd)
    return receipt, payload, records


def regenerate(
    harbor: Any,
    root: Path,
    trials: Path,
    identity: dict[str, str],
    seed: int,
    records: dict[str, Any],
    cases: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    endpoints: dict[str, str] = {}
    for cell in CELLS:
        request = records[cell][0][0]
        endpoint = absolute_path(request["participants"][0]["kwargs"]["owner_socket"])
        require(
            bool(endpoint.name) and len(os.fsencode(endpoint)) < 104, "invalid endpoint"
        )
        require(
            not any(
                endpoint.is_relative_to(directory)
                for directory in (CHECKOUT, root, trials.parent)
            ),
            "endpoint overlaps inputs",
        )
        endpoints[cell] = str(endpoint)
    require(len(set(endpoints.values())) == len(CELLS), "duplicate endpoint")
    expected, inputs, source = factor_inputs(
        harbor, root, trials, seed, identity, cases, endpoints
    )
    prepared = {}
    for cell in CELLS:
        config = expected[inputs[cell]["config"]]
        lock = expected[inputs[cell]["lock"]]
        for (_, observed), record in zip(records[cell], (config, lock), strict=True):
            require(observed == record_bytes(record), "prepared native input differs")
        prepared[cell] = (harbor.TrialConfig.model_validate(config), lock)
    return inputs, prepared, source


def check_result(
    raw: dict[str, Any],
    config: Any,
    identity: dict[str, str],
    task: Any,
    lock: dict[str, Any],
) -> TrialResult:
    require("id" in raw, "missing trial ID")
    result = TrialResult.model_validate(raw)
    equal(raw, result.model_dump(mode="json"), "noncanonical native result")
    equal(raw["config"], config.model_dump(mode="json"), "result config differs")
    require(
        result.trial_name == config.trial_name
        and result.trial_uri == (config.trials_dir / config.trial_name).as_uri()
        and result.task_name == identity["name"]
        and result.task_id == config.task.get_task_id()
        and result.task_checksum == identity["checksum"]
        and result.source == config.task.source
        and result.verifier_environment_mode == lock["verifier"]["environment_mode"],
        "result identity differs",
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
        "invalid result roster",
    )
    for seat in seats:
        info = seat.agent_info
        require(
            task.config.agents[seat.seat].under_test
            and seat.under_test
            and info is not None
            and info.name == "seatlab-physical-dispatch-receiver"
            and info.version == "1.0.0"
            and info.model_info is None,
            "unexpected receiver identity",
        )
    require(
        result.agent_info == seats[0].agent_info, "primary receiver identity differs"
    )
    return result


def load_inputs(
    plan: Path, cell: str, trial_fd: int
) -> tuple[TrialResult, dict[str, Any], dict[str, str], dict[str, str], str]:
    require(cell in CELLS, "unknown physical cell")
    receipt, payload, records = read_plan(plan)
    seed = receipt["seed"]
    require(type(seed) is int and 0 <= seed < 2**63, "invalid seed")
    base, cases = native_pair(seed)
    require(
        base["realized"] == min(brief["id"] for brief in base["briefs"]),
        "not a naturally focal draw",
    )
    request = records[cell][0][0]
    declared_root = absolute_path(request["task"]["path"])
    root, stamps = bounded_tree(declared_root)
    require(root == declared_root, "task root differs")
    trials = absolute_path(request["trials_dir"])
    require(trials.name == "trials", "invalid trial root")
    stage = check_stage(root)
    host = host_fingerprint(CHECKOUT)
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree = task_files_digest(root, identity)
    inputs, prepared, source = regenerate(
        harbor, root, trials, identity, seed, records, cases
    )
    expected = _receipt(seed, identity, tree, stage, base, cases, inputs, source)
    expected["host_source_digest"] = host
    equal(receipt, expected, "prepared receipt differs")
    task = harbor.Task(root)
    instructions = {role: task.seat_instruction(role) for role in ROLES}
    equal(
        {
            role: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for role, text in instructions.items()
        },
        source.effective_roles,
        "role instructions differ",
    )
    config, lock = prepared[cell]
    paths = TrialPaths(Path(config.trial_name))
    raw_config, config_bytes = read_record(trial_fd, paths.config_path.name)
    raw_lock, lock_bytes = read_record(trial_fd, paths.lock_path.name)
    raw_result, result_bytes = read_record(trial_fd, paths.result_path.name)
    equal(
        raw_config,
        config.model_dump(mode="json", exclude={"agent"}),
        "archive config differs",
    )
    equal(raw_lock, lock, "archive lock differs")
    result = check_result(raw_result, config, identity, task, lock)
    require_unchanged(root, stamps)
    require(host_fingerprint(CHECKOUT) == host, "host source changed")
    hashes = {
        "plan": digest(payload),
        "prepared_config": inputs[cell]["config_digest"],
        "prepared_lock": inputs[cell]["lock_digest"],
        "config": digest(config_bytes),
        "lock": digest(lock_bytes),
        "result": digest(result_bytes),
        "task_tree": tree,
        "native_task": identity["digest"],
        "stage": stage,
        "host_source": host,
    }
    return (
        result,
        cases[inputs[cell]["map"]],
        instructions,
        hashes,
        inputs[cell]["filing"],
    )
