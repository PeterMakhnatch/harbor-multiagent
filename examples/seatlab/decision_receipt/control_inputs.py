import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

from ...native_evidence import same
from ...native_study import (
    DIGEST,
    as_record,
    as_text,
    check_task,
    native,
    normalize,
    record_bytes,
    registration_request,
    require,
    task_identity,
    trial_request,
)
from ...seat_checkpoints.materialize import dir_mode, read_plain, sync_directory
from ...ship_inflight import inflight_verify as trusted
from ...ship_inflight import materialize as inflight
from ...ship_inflight.controls import ORACLE, POLICY, ROLES
from . import materialize as decision

SCHEMA = "harbor.ship-decision-receipt.control-enrollment/v1"
EMIT_SCHEMA = "harbor.ship-decision-receipt.control-inputs/v1"
KIND = "scripted-calibration-plan"
CONTROL_MODULE = "examples.seatlab.decision_receipt.control_seat"
CONTROL_IMPORT = CONTROL_MODULE + ":DecisionReceiptControl"
OUTGOING_POLICY = "original"
CHANNEL = "deliver"
FATES = ("settles", "fails")
CITES = (False, True)
HARNESS: tuple[tuple[str, Path], ...] = (
    ("control_seat.py", decision.PACKAGE_DIR / "control_seat.py"),
    ("control_client.py", decision.PACKAGE_DIR / "control_client.py"),
)
NATIVE_CAVEAT = (
    "task digest and checksum are what the installed native Packager and Task"
    " computed at emit; another packager revision may hash another file set."
    " tree binds all 16 task files with their modes and is rechecked at load."
    " locks are the TrialLock v2 records native build_trial_lock made at emit;"
    " load checks their form and their bond to the configs, not a live run."
    " load refuses another harbor version or packager, lock or trial config source."
)
ENROLLMENT = "enrollment.json"
CONFIGS = "configs"
TRIALS = "trials"
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
STAGE_PREFIX = ".decision-controls-stage-"
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
ENROLLMENT_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "task",
        "task_path",
        "tree",
        "native",
        "harness",
        "trials",
        "cases",
        "configs",
        "locks",
    }
)
NATIVE_SOURCES = ("packager_sha256", "lock_sha256", "trial_config_sha256")
NATIVE_FIELDS = frozenset({"harbor", *NATIVE_SOURCES, "caveat"})
WORKSPACE_ENTRIES = frozenset({CONFIGS, ENROLLMENT, TRIALS})

assert ROLES == ("outgoing", "relief")
assert set(FATES) == set(trusted.FATES) and CHANNEL in trusted.CHANNELS
assert OUTGOING_POLICY == "original"
assert __package__ is not None and CONTROL_MODULE == __package__ + ".control_seat"
assert HARNESS[0][0] == CONTROL_MODULE.rpartition(".")[2] + ".py"
assert all(path.parent == decision.PACKAGE_DIR for _, path in HARNESS)


class Enrollment(NamedTuple):
    task: dict[str, str]
    tree: str
    requests: dict[str, dict[str, Any]]
    pin: str
    trials: Path
    locks: dict[str, str]
    client_sha256: str


class NativeDrift(ValueError):
    pass


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def lock_digest(lock: object) -> str:
    record = as_record(lock, "lock")
    try:
        text = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("lock is not a plain JSON object") from None
    return sha256(text.encode("ascii"))


def ticket(fate: str, cite: bool) -> str:
    assert fate in FATES and type(cite) is bool
    return f"dr-{fate}-{'cited' if cite else 'plain'}"


def cases() -> list[dict[str, Any]]:
    rows = [
        {"ticket": ticket(fate, cite), "fate": fate, "cite": cite}
        for fate in FATES
        for cite in CITES
    ]
    assert len({row["ticket"] for row in rows}) == len(rows) == 4
    return rows


def team(cite: bool) -> list[dict[str, Any]]:
    assert type(cite) is bool
    outgoing = {
        "name": ORACLE,
        "skip_model_parsing": False,
        "skills": [],
        "resume_trajectory": False,
        "extra_allowed_hosts": [],
        "kwargs": {},
        "env": {POLICY: OUTGOING_POLICY},
        "mcp_servers": [],
        "seat": "outgoing",
    }
    relief = {
        "import_path": CONTROL_IMPORT,
        "skip_model_parsing": False,
        "skills": [],
        "resume_trajectory": False,
        "extra_allowed_hosts": [],
        "kwargs": {"cite": cite},
        "mcp_servers": [],
        "seat": "relief",
    }
    return [outgoing, relief]


def world_env(fate: str) -> dict[str, str]:
    assert fate in FATES
    return {"SH_FATE": fate, "SH_CHANNEL": CHANNEL}


def _without_cite(request: dict[str, Any]) -> tuple[dict[str, Any], object]:
    rest = copy.deepcopy(request)
    rest.pop("trial_name")
    relief = [member for member in rest["participants"] if member["seat"] == "relief"]
    require(len(relief) == 1, "a request lacks one relief participant")
    require(set(relief[0]["kwargs"]) == {"cite"}, "relief kwargs are not only cite")
    return rest, relief[0]["kwargs"].pop("cite")


def check_pairs(requests: dict[str, dict[str, Any]]) -> None:
    tickets = [case["ticket"] for case in cases()]
    require(sorted(requests) == sorted(tickets), "request tickets differ")
    for fate in FATES:
        plain, plain_cite = _without_cite(requests[ticket(fate, False)])
        cited, cited_cite = _without_cite(requests[ticket(fate, True)])
        require(plain == cited, f"the {fate} pair differs beyond relief cite")
        require(
            plain_cite is False and cited_cite is True,
            f"the {fate} pair does not compare cite false with true",
        )
        require(plain["environment"]["env"] == world_env(fate), f"{fate} world env")


def plan(task_dir: Path, trials: Path) -> dict[str, dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    for case in cases():
        members = team(case["cite"])
        env = world_env(case["fate"])
        name = case["ticket"]
        requests[name] = registration_request(task_dir, trials, name, members, env)
    check_pairs(requests)
    return requests


def native_requests(
    harbor: SimpleNamespace, task_dir: Path, trials: Path
) -> dict[str, dict[str, Any]]:
    planned = plan(task_dir, trials)
    requests: dict[str, dict[str, Any]] = {}
    for case in cases():
        members = team(case["cite"])
        normalized = [normalize(harbor, member) for member in members]
        require(normalized == members, f"native normalization changes {case['ticket']}")
        request = trial_request(
            harbor, task_dir, trials, case["ticket"], members, world_env(case["fate"])
        )
        require(
            record_bytes(request) == record_bytes(planned[case["ticket"]]),
            f"native request {case['ticket']} differs from the plan",
        )
        requests[case["ticket"]] = request
    return requests


def lock_native() -> SimpleNamespace:
    try:
        from harbor.models.job.lock import TrialLock, build_trial_lock
    except ImportError as error:
        raise SystemExit(
            f"control locks need the installed Harbor fork: {error}"
        ) from error
    return SimpleNamespace(TrialLock=TrialLock, build_trial_lock=build_trial_lock)


def check_lock(
    locking: SimpleNamespace,
    value: object,
    request: dict[str, Any],
    digest: str,
    name: str,
) -> dict[str, Any]:
    lock = as_record(value, f"lock {name}")
    try:
        model = locking.TrialLock.model_validate(lock)
    except ValueError:
        raise ValueError(f"lock {name} is not a native TrialLock") from None
    dumped = json.loads(model.model_dump_json(exclude_none=True))
    require(
        record_bytes(dumped) == record_bytes(lock),
        f"lock {name} is not a native TrialLock dump",
    )
    version = lock["schema_version"]
    require(type(version) is int and version == 2, f"lock {name} is not version 2")
    task = as_record(lock["task"], f"lock {name} task")
    path = request["task"]["path"]
    require(
        task.get("type") == "local"
        and same(task.get("digest"), digest)
        and same(task.get("path"), path)
        and task.get("name") == os.path.basename(path),
        f"lock {name} task is not the enrolled local task",
    )
    require(lock.get("source_trial") is None, f"lock {name} derives from a trial")
    require(lock["install_only"] is False, f"lock {name} is install-only")
    environment = as_record(lock["environment"], f"lock {name} environment")
    wanted = request["environment"]
    require(
        same(environment.get("env"), wanted["env"])
        and environment.get("type") == wanted["type"],
        f"lock {name} world is not the config world",
    )
    require(
        same(lock.get("participants"), request["participants"]),
        f"lock {name} participants are not the config team",
    )
    verifier = as_record(lock["verifier"], f"lock {name} verifier")
    require("environment_mode" in verifier, f"lock {name} lacks the verifier mode")
    return lock


def native_locks(
    harbor: SimpleNamespace,
    locking: SimpleNamespace,
    task_dir: Path,
    requests: dict[str, dict[str, Any]],
    digest: str,
) -> dict[str, dict[str, Any]]:
    resolved = SimpleNamespace(
        path=task_dir, content_hash=None, resolved_git_commit_id=None
    )
    locks: dict[str, dict[str, Any]] = {}
    for name, request in requests.items():
        config = harbor.TrialConfig.model_validate(request)
        lock = locking.build_trial_lock(
            trial_config=config, task_download_result=resolved
        )
        raw = json.loads(lock.model_dump_json(exclude_none=True))
        locks[name] = check_lock(locking, raw, request, digest, name)
    assert list(locks) == list(requests)
    return locks


def task_tree(task_dir: Path) -> str:
    files = decision.compose(decision.load_base())
    composed = {relative: sha256(data) for relative, data in files.items()}
    expected = decision.tree_digest(decision.FINAL_MODES, composed)
    inflight.check_tree(task_dir, list(files))
    for relative in ("", *inflight.STOCK_DIRS):
        require(
            dir_mode(task_dir / relative) == inflight.DIR_MODE,
            f"task directory {relative or '.'} mode differs from the composition",
        )
    found: dict[str, str] = {}
    for relative in sorted(files):
        data, mode = read_plain(task_dir / relative)
        require(data == files[relative], f"task file {relative} differs")
        require(
            mode == decision.FINAL_MODES[relative], f"task file {relative} mode differs"
        )
        found[relative] = sha256(data)
    tree = decision.tree_digest(decision.FINAL_MODES, found)
    require(tree == expected, "task tree differs from the composition")
    return tree


def harness() -> dict[str, str]:
    pins: dict[str, str] = {}
    for name, path in HARNESS:
        data, _ = read_plain(path)
        require(data.endswith(b"\n") and data.strip() != b"", f"{name} is not text")
        pins[name] = sha256(data)
    assert len(pins) == len(HARNESS)
    return pins


def _source_digest(module: str, label: str) -> str:
    source = getattr(sys.modules[module], "__file__", None)
    if not source:
        raise ValueError(f"native {label} has no source path")
    data, _ = read_plain(Path(source))
    return sha256(data)


def native_scope(harbor: SimpleNamespace, locking: SimpleNamespace) -> dict[str, str]:
    version = metadata.version("harbor")
    require(version != "", "native harbor version is unknown")
    return {
        "harbor": version,
        "packager_sha256": _source_digest(harbor.Packager.__module__, "packager"),
        "lock_sha256": _source_digest(locking.build_trial_lock.__module__, "lock"),
        "trial_config_sha256": _source_digest(
            harbor.TrialConfig.__module__, "trial config"
        ),
        "caveat": NATIVE_CAVEAT,
    }


def _absolute(text: object, label: str) -> Path:
    require(
        isinstance(text, str)
        and text.startswith("/")
        and os.path.normpath(text) == text,
        f"{label} must be a normalized absolute path",
    )
    path = Path(str(text))
    require(PLAIN_NAME.fullmatch(path.name) is not None, f"{label} needs a plain name")
    return path


def task_path(text: str) -> Path:
    path = _absolute(text, "TASK")
    require(path.resolve(strict=True) == path, "TASK must be spelled without symlinks")
    dir_mode(path)
    require(
        not path.is_relative_to(decision.CHECKOUT), "TASK must lie outside the checkout"
    )
    return path


def workspace_path(text: str, task_dir: Path) -> Path:
    out = _absolute(text, "OUT")
    parent = out.parent
    require(
        parent.resolve(strict=True) == parent,
        "OUT parent must be spelled without symlinks",
    )
    dir_mode(parent)
    require(not os.path.lexists(out), f"{out} already exists")
    require(
        not out.is_relative_to(decision.CHECKOUT), "OUT must lie outside the checkout"
    )
    require(not out.is_relative_to(task_dir), "OUT must lie outside TASK")
    return out


def _write(path: Path, data: bytes, written: list[Path]) -> None:
    fd = os.open(path, inflight.CREATE_FLAGS, PRIVATE_FILE)
    written.append(path)
    with open(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), PRIVATE_FILE)


def publish(out: Path, configs: dict[str, bytes], enrollment: bytes) -> None:
    assert len(configs) == 4 and out.parent.is_absolute()
    stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=out.parent))
    folder = stage / CONFIGS
    written: list[Path] = []
    published = False
    try:
        require(dir_mode(stage) == PRIVATE_DIR, "staging directory is not private")
        os.mkdir(folder, PRIVATE_DIR)
        os.chmod(folder, PRIVATE_DIR)
        for name, data in configs.items():
            _write(folder / f"{name}.json", data, written)
        _write(stage / ENROLLMENT, enrollment, written)
        sync_directory(folder)
        sync_directory(stage)
        inflight.publish(stage, out)
        published = True
    finally:
        if not published:
            for path in reversed(written):
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            with suppress(OSError):
                folder.rmdir()
            with suppress(OSError):
                stage.rmdir()
    sync_directory(out.parent)


def emit(task_dir: Path, out: Path) -> dict[str, Any]:
    harbor = native()
    locking = lock_native()
    tree = task_tree(task_dir)
    identity = task_identity(harbor, task_dir, decision.TASK_NAME)
    trials = out / TRIALS
    requests = native_requests(harbor, task_dir, trials)
    locks = native_locks(harbor, locking, task_dir, requests, identity["digest"])
    require(task_tree(task_dir) == tree, "TASK changed while native hashed it")
    configs = {name: record_bytes(request) for name, request in requests.items()}
    enrollment = {
        "schema": SCHEMA,
        "kind": KIND,
        "task": identity,
        "task_path": str(task_dir),
        "tree": tree,
        "native": native_scope(harbor, locking),
        "harness": harness(),
        "trials": str(trials),
        "cases": cases(),
        "configs": {name: sha256(data) for name, data in configs.items()},
        "locks": locks,
    }
    data = record_bytes(enrollment)
    publish(out, configs, data)
    return {
        "schema": EMIT_SCHEMA,
        "kind": KIND,
        "workspace": str(out),
        "enrollment": sha256(data),
        "task": identity,
        "tree": tree,
        "harness": enrollment["harness"],
        "trials": str(trials),
        "cases": cases(),
        "configs": {name: str(out / CONFIGS / f"{name}.json") for name in configs},
    }


def _parse(raw: bytes, label: str) -> object:
    value = json.loads(raw.decode("ascii"))
    require(record_bytes(value) == raw, f"{label} is not in canonical form")
    return value


def _private_file(path: Path) -> bytes:
    data, mode = read_plain(path)
    require(mode == PRIVATE_FILE, f"{path.name} is not private")
    return data


def _workspace(workspace: Path) -> Path:
    require(isinstance(workspace, Path), "workspace must be a Path")
    root = _absolute(str(workspace), "workspace")
    require(
        root.resolve(strict=True) == root, "workspace must be spelled without symlinks"
    )
    require(dir_mode(root) == PRIVATE_DIR, "workspace is not private")
    return root


def _check_native(value: object, current: dict[str, str]) -> None:
    scope = as_record(value, "enrollment native", NATIVE_FIELDS)
    require(
        isinstance(scope["harbor"], str) and scope["harbor"] != "", "native version"
    )
    for field in NATIVE_SOURCES:
        as_text(scope[field], f"native {field}", DIGEST)
    require(scope["caveat"] == NATIVE_CAVEAT, "enrollment native caveat differs")
    assert set(current) == NATIVE_FIELDS
    drifted = sorted(field for field in NATIVE_FIELDS if scope[field] != current[field])
    if drifted:
        raise NativeDrift(
            f"installed native harbor drifted since enrollment: {', '.join(drifted)}"
        )


def _check_layout(root: Path, planned: dict[str, dict[str, Any]]) -> None:
    entries = set(os.listdir(root))
    require(
        {CONFIGS, ENROLLMENT} <= entries <= WORKSPACE_ENTRIES,
        "workspace entries differ from the enrollment layout",
    )
    require(dir_mode(root / CONFIGS) == PRIVATE_DIR, "configs is not private")
    names = sorted(os.listdir(root / CONFIGS))
    require(names == sorted(f"{name}.json" for name in planned), "configs differ")


def _check_configs(
    root: Path, planned: dict[str, dict[str, Any]], pinned: object
) -> dict[str, dict[str, Any]]:
    digests = as_record(pinned, "enrollment configs", frozenset(planned))
    requests: dict[str, dict[str, Any]] = {}
    for name, request in planned.items():
        data = _private_file(root / CONFIGS / f"{name}.json")
        require(sha256(data) == digests[name], f"config {name} differs from enrollment")
        require(data == record_bytes(request), f"config {name} differs from the plan")
        requests[name] = as_record(_parse(data, f"config {name}"), f"config {name}")
    check_pairs(requests)
    return requests


def _check_locks(
    locking: SimpleNamespace,
    pinned: object,
    requests: dict[str, dict[str, Any]],
    digest: str,
) -> dict[str, str]:
    locks = as_record(pinned, "enrollment locks", frozenset(requests))
    hashes: dict[str, str] = {}
    for name, request in requests.items():
        lock = check_lock(locking, locks[name], request, digest, name)
        hashes[name] = lock_digest(lock)
    assert list(hashes) == list(requests)
    return hashes


def load(task: Path, workspace: Path, expected: object) -> Enrollment:
    pin = as_text(expected, "held enrollment digest", DIGEST)
    require(isinstance(task, Path), "task must be a Path")
    task_dir = task_path(str(task))
    locking = lock_native()
    root = _workspace(workspace)
    raw = _private_file(root / ENROLLMENT)
    require(sha256(raw) == pin, "enrollment differs from the held digest")
    record = as_record(_parse(raw, "enrollment"), "enrollment", ENROLLMENT_FIELDS)
    require(record["schema"] == SCHEMA, "enrollment schema differs")
    require(record["kind"] == KIND, "enrollment kind differs")
    identity = check_task(record["task"], decision.TASK_NAME)
    require(record["task_path"] == str(task_dir), "enrollment binds another TASK")
    trials = root / TRIALS
    require(record["trials"] == str(trials), "enrollment binds another trials path")
    require(
        same(record["cases"], cases()),
        "enrollment cases differ from the four controls",
    )
    _check_native(record["native"], native_scope(native(), locking))
    sources = harness()
    require(
        same(record["harness"], sources), "control harness differs from the enrollment"
    )
    tree = task_tree(task_dir)
    require(record["tree"] == tree, "TASK tree differs from the enrollment")
    planned = plan(task_dir, trials)
    _check_layout(root, planned)
    requests = _check_configs(root, planned, record["configs"])
    locks = _check_locks(locking, record["locks"], requests, identity["digest"])
    return Enrollment(
        task={field: identity[field] for field in ("name", "digest", "checksum")},
        tree=tree,
        requests=requests,
        pin=pin,
        trials=trials,
        locks=locks,
        client_sha256=sources["control_client.py"].removeprefix("sha256:"),
    )


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: control_inputs TASK OUT\n")
        return 2
    try:
        task_dir = task_path(argv[0])
        record = emit(task_dir, workspace_path(argv[1], task_dir))
    except (ValueError, OSError) as error:
        sys.stderr.write(f"control_inputs: {error}\n")
        return 1
    sys.stdout.write(json.dumps(record, indent=2, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
