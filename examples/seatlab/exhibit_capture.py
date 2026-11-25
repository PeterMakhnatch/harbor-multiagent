from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import native, record_bytes, task_identity
from .decision_receipt.control_inputs import lock_native
from .exhibit_bind import fixed_request
from .exhibit_enroll import TASK_MODULE, digest, task_snapshot
from .exhibit_pairs import (
    DIR_FLAGS,
    FILE_FLAGS,
    open_folder,
    reject_constant,
    require,
    unique_object,
    validate_observation,
)
from .exhibit_plan import load_plan
from .exhibit_prepare import NAME, ROLES, TASK
from .exhibit_wire import encode_request, render_turn, request_stamp
from .workbench.arm_sources import inspect_source, require_unchanged

SCHEMA = "seatlab.exhibit-capture/v1"
LIMIT = 1 << 20


def read_record(fd: int, name: str) -> tuple[dict[str, Any], bytes]:
    with os.fdopen(os.open(name, FILE_FLAGS, dir_fd=fd), "rb") as handle:
        original = os.fstat(handle.fileno())
        require(
            stat.S_ISREG(original.st_mode) and original.st_size <= LIMIT,
            "invalid artifact file",
        )
        data = handle.read(LIMIT + 1)
        final = os.fstat(handle.fileno())
        require(
            len(data) == original.st_size <= LIMIT
            and (original.st_size, original.st_mtime_ns, original.st_ctime_ns)
            == (final.st_size, final.st_mtime_ns, final.st_ctime_ns),
            "artifact changed",
        )
    raw = json.loads(
        data, object_pairs_hook=unique_object, parse_constant=reject_constant
    )
    require(type(raw) is dict, "invalid artifact record")
    return raw, data


def select(
    plan: Path, ticket: str, arm: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest, schedule, receipt = load_plan(plan)
    require(re.fullmatch(r"pair-[0-9]{4}", ticket) is not None, "invalid ticket")
    require(
        arm in ("masked", "bundled") and manifest["profile"] == "coupling",
        "invalid arm",
    )
    pair = next((row for row in manifest["pairs"] if row["id"] == ticket), None)
    case = next((row for row in schedule["cases"] if row["id"] == ticket), None)
    if pair is None or case is None:
        raise ValueError("unknown ticket")
    side = "left" if arm == manifest["arms"][0] else "right"
    require(pair[side] == f"outcomes/{ticket}/{arm}", "noncanonical outcome leaf")
    return pair, case, receipt


def check_config(
    raw: dict[str, Any], trial: Path, root: Path, case: dict[str, Any], arm: str
):
    harbor = native()
    config = harbor.TrialConfig.model_validate(raw)
    require(
        config.model_dump(mode="json", exclude={"agent"}) == raw,
        "noncanonical trial config",
    )
    require(
        config.trial_name == trial.name
        and config.trials_dir.is_absolute()
        and config.trials_dir.resolve(strict=True) == trial.parent,
        "trial path mismatch",
    )
    members = config.participants
    require(
        tuple(member.seat for member in members) == ROLES, "participant roster mismatch"
    )
    sockets = members[2].kwargs.get("owner_sockets")
    require(
        type(sockets) is list and len(sockets) == 1 and type(sockets[0]) is str,
        "invalid declared depot endpoint",
    )
    socket = Path(sockets[0])
    require(
        socket.is_absolute()
        and len(os.fsencode(socket)) <= 103
        and str(socket) == sockets[0]
        and ".." not in socket.parts
        and re.fullmatch(r"[^/\x00]+", socket.name) is not None,
        "invalid declared depot endpoint",
    )
    require(trial.name == case["id"] and root == TASK, "unregistered trial or task")
    expected = harbor.TrialConfig.model_validate(
        fixed_request(harbor, case, arm, socket, config.trials_dir)
    )
    require(
        raw == expected.model_dump(mode="json", exclude={"agent"}),
        "trial config differs from fixed-prefix design",
    )
    return config


def check_native(
    fd: int, trial: Path, case: dict[str, Any], arm: str, enrolled: dict[str, Any]
):
    from harbor.models.trial.paths import TrialPaths
    from harbor.models.trial.result import TrialResult

    paths = TrialPaths(trial)
    tree, root, stamps = task_snapshot()
    require(tree == enrolled["local_task_tree_digest"], "task differs from enrollment")
    raw, config_bytes = read_record(fd, paths.config_path.name)
    config = check_config(raw, trial, root, case, arm)
    lock, lock_bytes = read_record(fd, paths.lock_path.name)
    locking = lock_native()
    resolved = SimpleNamespace(
        path=root, content_hash=None, resolved_git_commit_id=None
    )
    expected = locking.build_trial_lock(
        trial_config=config, task_download_result=resolved
    )
    require(
        lock == json.loads(expected.model_dump_json(exclude_none=True)),
        "native lock mismatch",
    )
    source = inspect_source(root, lock["task"])
    require(set(source.seats) == set(ROLES), "effective instruction roster mismatch")
    identity = task_identity(native(), root, NAME)
    require(source.native_digest == identity["digest"], "native task digest mismatch")
    require_unchanged(root, stamps)
    raw_result, result_bytes = read_record(fd, paths.result_path.name)
    result = TrialResult.model_validate(raw_result)
    require(
        raw_result.get("config") == config.model_dump(mode="json")
        and result.trial_name == trial.name
        and result.trial_uri == trial.as_uri()
        and result.task_name == identity["name"]
        and result.task_id == config.task.get_task_id()
        and result.task_checksum == identity["checksum"],
        "trial result identity mismatch",
    )
    require(
        result.started_at is not None
        and result.finished_at is not None
        and result.finished_at >= result.started_at
        and result.verifier is not None,
        "trial not finished with verifier",
    )
    seats = result.seat_results or []
    require(
        len(seats) == 3 and {seat.seat for seat in seats} == set(ROLES),
        "result roster mismatch",
    )
    for seat in seats:
        info = seat.agent_info
        name = "seatlab-exhibit-relay-owner" if seat.seat == "depot" else "oracle"
        require(
            seat.under_test
            and info is not None
            and info.name == name
            and info.model_info is None,
            "declared seat agent mismatch",
        )
    hashes = {
        "config": digest(config_bytes),
        "lock": digest(lock_bytes),
        "result": digest(result_bytes),
    }
    return result, paths.verifier_dir.name, hashes, source


def confirm_prefix(
    folder: int,
    raw: dict[str, Any],
    result,
    arm: str,
    commitment: str,
    instruction: str,
) -> tuple[str, dict[str, Any]]:
    cut, data = read_record(folder, "private-exhibit-cut.json")
    diagnostics = import_module(f"{TASK_MODULE}.diagnostics")
    measured = diagnostics.observation(cut)
    require(
        measured == raw
        and cut["mode"] == arm
        and measured["case_commitment"] == commitment,
        "private event cut and verifier observation differ",
    )
    case = cut["case"]
    actions = [row["action"] for row in cut["events"]]
    require(len(actions) >= 3, "no accepted depot action")
    expected_briefs = sorted(brief["id"] for brief in case["briefs"])
    require(
        actions[0] == {"op": "request", "slips": expected_briefs},
        "noncanonical loader prefix",
    )
    required = {
        card["id"]
        for brief in case["briefs"]
        for card in case["registrar"]
        if card["key"] in brief["items"]
        and card["start"] <= brief["epoch"] <= card["end"]
    }
    require(
        len(required) == 4 and actions[1] == {"op": "file", "ids": sorted(required)},
        "noncanonical registrar prefix",
    )
    seat = next(s for s in result.seat_results if s.seat == "depot")
    metadata = seat.agent_result.metadata if seat.agent_result is not None else None
    if not isinstance(metadata, dict):
        raise TypeError("external depot has no action record")
    require(
        metadata.get("role") == "depot" and metadata.get("phase") == "finished",
        "external depot did not finish",
    )
    engine = import_module(f"{TASK_MODULE}.engine")
    replay = engine.Session(case, arm)
    for role, action in zip(("loader", "registrar"), actions[:2], strict=True):
        replay.apply(role, action)
    view = json.loads(json.dumps(replay.view("depot"), sort_keys=True))
    expected_request = request_stamp(
        encode_request(render_turn(instruction, view, None))
    )
    require(
        metadata.get("accepted_requests") == [expected_request],
        "depot owner request differs from the registered view",
    )
    decisions = metadata["accepted_actions"]
    require(
        type(decisions) is list and len(decisions) == 1,
        "expected one reported depot action",
    )
    decision = decisions[0]
    require(
        isinstance(decision, dict)
        and decision.keys() == {"op", "ids"}
        and decision["op"] == "file"
        and isinstance(decision["ids"], list)
        and sorted(decision["ids"]) == actions[2]["ids"],
        "depot declarations differ",
    )
    return digest(data), expected_request


def check_verifier(
    folder: int, result, arm: str, commitment: str, instruction_digest: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, data = read_record(folder, "observation.json")
    outcome = validate_observation(raw, folder, "coupling", arm, commitment)
    require(
        outcome.status in ("complete", "incomplete"),
        "invalid observation identity or status",
    )
    require(
        outcome.status == "complete" or outcome.C is not None,
        "no post-depot observation",
    )
    instruction = native().Task(TASK).seat_instruction("depot")
    require(
        isinstance(instruction, str)
        and digest(instruction.encode()) == "sha256:" + instruction_digest,
        "depot instruction has changed",
    )
    private_digest, depot_request = confirm_prefix(
        folder, raw, result, arm, commitment, instruction
    )
    files = {"observation.json": data}
    if outcome.status == "complete":
        reward, payload = read_record(folder, "reward.json")
        require(
            result.reward_valid
            and result.verifier_result is not None
            and result.verifier_result.rewards == reward
            and not result.recorded_exceptions(),
            "invalid native complete reward",
        )
        files["reward.json"] = payload
    else:
        require(
            not result.reward_valid
            and (result.verifier_result is None or not result.verifier_result.rewards),
            "unexpected native reward",
        )
    for name, payload in files.items():
        require(read_record(folder, name)[1] == payload, "verifier artifact changed")
    return {
        "observation_status": outcome.status,
        "stage": raw["handoff"]["stage"],
        "fixed_prefix_verified": True,
        "private_cut_digest": private_digest,
        "depot_owner_request": depot_request,
    }, files


def mkdir_private(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = open_folder(parent, (name,))
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise ValueError("outcome directory not private")
    return fd


def rename_exclusive(parent: int, stage: str, leaf: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        flag = 4
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        flag = 1
    else:
        raise ValueError("atomic exclusive rename unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if function(parent, os.fsencode(stage), parent, os.fsencode(leaf), flag) != 0:
        code = ctypes.get_errno()
        raise OSError(code or errno.EIO, "atomic outcome publication failed")


def publish(plan: Path, ticket: str, arm: str, files: dict[str, bytes]) -> None:
    root = os.open(plan, DIR_FLAGS)
    try:
        outcomes = mkdir_private(root, "outcomes")
        try:
            parent = mkdir_private(outcomes, ticket)
            try:
                publish_leaf(parent, plan / "outcomes" / ticket, arm, files)
                os.fsync(outcomes)
            finally:
                os.close(parent)
        finally:
            os.close(outcomes)
        os.fsync(root)
    finally:
        os.close(root)


def publish_leaf(
    parent: int, location: Path, arm: str, files: dict[str, bytes]
) -> None:
    stage = Path(tempfile.mkdtemp(prefix=".capture-", dir=location))
    try:
        fd = open_folder(parent, (stage.name,))
        try:
            for name, payload in files.items():
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                with os.fdopen(os.open(name, flags, 0o600, dir_fd=fd), "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.fsync(fd)
        finally:
            os.close(fd)
        rename_exclusive(parent, stage.name, arm)
        os.fsync(parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def capture(plan: Path, ticket: str, arm: str, trial: Path) -> dict[str, Any]:
    report = {
        "schema": SCHEMA,
        "status": "invalid",
        "evidence": "declared-local/artifact-bound",
        "operator_authenticated": False,
        "provider_authenticated": False,
    }
    try:
        plan = plan.absolute()
        pair, case, enrolled = select(plan, ticket, arm)
        require(
            not os.path.lexists(plan / "outcomes" / ticket / arm),
            "outcome already enrolled",
        )
        trial = trial.absolute()
        require(not trial.is_symlink(), "unexpected trial symlink")
        trial = trial.resolve(strict=True)
        require(
            not trial.is_relative_to(plan.resolve(strict=True)),
            "invalid trial location",
        )
        fd = os.open(trial, DIR_FLAGS)
        try:
            result, verifier, hashes, source = check_native(
                fd, trial, case, arm, enrolled
            )
            folder = open_folder(fd, (verifier,))
            try:
                status, files = check_verifier(
                    folder,
                    result,
                    arm,
                    pair["case_commitment"],
                    source.effective_roles["depot"],
                )
            finally:
                os.close(folder)
        finally:
            os.close(fd)
        report.update(
            status,
            status="admitted",
            case_commitment=pair["case_commitment"],
            enrollment_status=enrolled["status"],
            arm=arm,
            manifest_digest=enrolled["manifest_digest"],
            local_task_tree_digest=enrolled["local_task_tree_digest"],
            native_task_digest=source.native_digest,
            host_harness_digest=enrolled["host_harness_digest"],
            role_instruction_digests=source.effective_roles,
            artifact_digests=hashes
            | {name: digest(data) for name, data in files.items()},
        )
        files["receipt.json"] = record_bytes(report)
        require(
            select(plan, ticket, arm) == (pair, case, enrolled), "enrollment changed"
        )
        publish(plan, ticket, arm, files)
        return report
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ImportError,
        RecursionError,
        OverflowError,
    ):
        return {
            "schema": SCHEMA,
            "status": "invalid",
            "evidence": "no admission",
            "operator_authenticated": False,
            "provider_authenticated": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Admit declared local Exhibit Relay evidence; never launch"
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("ticket")
    parser.add_argument("arm", choices=("masked", "bundled"))
    parser.add_argument("trial", type=Path)
    args = parser.parse_args()
    report = capture(args.plan, args.ticket, args.arm, args.trial)
    print(json.dumps(report, sort_keys=True))
    return int(report["status"] != "admitted")


if __name__ == "__main__":
    raise SystemExit(main())
