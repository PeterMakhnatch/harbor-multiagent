import errno
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple

from .joint_reopen import study
from .native_study import (
    TASK_FILES,
    native,
    parse_shard,
    record_task_files,
    registration_request,
    require,
    task_files_digest,
    task_identity,
)
from .seat_handoffs.core import reject_constant, unique_object
from .seat_handoffs.native_records import record_digest
from .shift_board import plan
from .ship_handover import study as ship_handover

CLAIM_MODE = 0o600
MAX_CELLS = 2048
MAX_REGISTRATION_BYTES = 1 << 20
MAX_CONFIG_BYTES = 1 << 20
MAX_CLAIM_BYTES = 4096
MAX_TASK_FILES_BYTES = 128
MAX_ENTRIES = 4 * MAX_CELLS
MAX_PATH_CHARS = 4096
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
PENDING = re.compile(r"\.([a-z]{2}-[0-9a-f]{16})\.[0-9a-f]{16}\.tmp")
NOT_A_TRIAL_DIRECTORY = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP})
RESULT = "result.json"
Record = dict[str, Any]


class World(NamedTuple):
    schema: str
    label: str
    claim_schema: str
    task_name: str
    ticket: re.Pattern[str]
    max_cells: int
    load: Callable[[Path], Record]
    env: Callable[[Record], dict[str, str]]


WORLDS = {
    plan.SCHEMA: World(
        plan.SCHEMA,
        "manifest",
        "harbor.shift-board.claim/v1",
        plan.TASK_NAME,
        plan.TICKET,
        plan.MAX_CELLS,
        plan.load_manifest,
        plan.board_env,
    ),
    study.SCHEMA: World(
        study.SCHEMA,
        "registration",
        "harbor.joint-reopen.claim/v1",
        study.TASK_NAME,
        study.TICKET,
        study.MAX_CELLS,
        study.load_registration,
        study.world_env,
    ),
    ship_handover.SCHEMA: World(*ship_handover.WORLD),
    ship_handover.INFLIGHT_WORLD[0]: World(*ship_handover.INFLIGHT_WORLD),
}
assert all(world.max_cells <= MAX_CELLS for world in WORLDS.values())


class Survey(NamedTuple):
    workspace: Path
    directory: int
    world: World
    shard: str
    tickets: list[str]
    claims: dict[str, bytes]
    configs: dict[str, str]
    claimed: frozenset[str]
    present: frozenset[str]
    results: frozenset[str]
    task_path: str
    identity: Record
    task_files: str
    registration_sha256: str

    def unclaimed(self) -> list[str]:
        return [
            ticket
            for ticket in self.tickets
            if ticket not in self.claimed and ticket not in self.present
        ]

    def counts(self) -> Record:
        return {
            "shard": self.shard,
            "assigned": len(self.tickets),
            "claimed": len(self.claimed),
            "started": len(self.present - self.claimed),
            "unclaimed": len(self.unclaimed()),
            "task_files": self.task_files,
        }

    def status(self) -> Record:
        without_result = self.present - self.results
        status = {
            "task": self.identity["name"],
            "shard": self.shard,
            "task_files": self.task_files,
            "assigned": len(self.tickets),
            "claimed": len(self.claimed),
            "available": len(self.unclaimed()),
            "reserved_unstarted": len(self.claimed - self.present),
            "directory_without_result": len(without_result),
            "result_file_present": len(self.results),
            "started_outside_worklist": len(self.present - self.claimed),
        }
        parts = ("available", "reserved_unstarted", "directory_without_result")
        partition = sum(status[part] for part in parts) + len(self.results)
        assert partition == len(self.tickets)
        return status


def _read_at(
    directory: int | None, name: str, limit: int, label: str, mode: int | None = None
) -> bytes | None:
    assert name and limit > 0
    try:
        fd = os.open(name, PLAIN_FILE, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"{label} is not a plain file: {error.strerror}") from None
    with open(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode), f"{label} is not a regular file")
        require(info.st_size <= limit, f"{label} exceeds {limit} bytes")
        if mode is not None:
            exact = stat.S_IMODE(info.st_mode) == mode
            require(exact, f"{label} is not mode {mode:o}")
        data = handle.read(limit + 1)
    require(len(data) <= limit, f"{label} exceeds {limit} bytes")
    return data


def _parse(data: bytes, label: str) -> Record:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"{label} is not strict JSON") from None
    require(type(value) is dict, f"{label} is not a JSON object")
    return value


def _world(parsed: Record, expected_schema: str | None) -> World:
    schema = parsed.get("schema")
    if not isinstance(schema, str) or schema not in WORLDS:
        raise ValueError(f"registration schema is not one of {sorted(WORLDS)}")
    if expected_schema is not None and schema != expected_schema:
        raise ValueError("registration belongs to another study type")
    return WORLDS[schema]


def _registration(path: str, expected_schema: str | None) -> tuple[World, Record, str]:
    require(
        expected_schema is None or expected_schema in WORLDS,
        f"expected schema is not one of {sorted(WORLDS)}",
    )
    label = "registration" if expected_schema is None else WORLDS[expected_schema].label
    data = _read_at(None, path, MAX_REGISTRATION_BYTES, label)
    require(data is not None, f"{label} is missing")
    assert data is not None
    parsed = _parse(data, label)
    world = _world(parsed, expected_schema)
    registration = world.load(Path(path))
    require(
        record_digest(parsed) == record_digest(registration),
        f"{world.label} changed while it was read",
    )
    require(
        len(registration["cells"]) <= world.max_cells, f"{world.label} is too large"
    )
    return world, registration, hashlib.sha256(data).hexdigest()


def _open_dir(
    stack: ExitStack, name: str, directory: int | None, label: str
) -> int | None:
    try:
        fd = os.open(name, DIRECTORY, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        reason = error.strerror
        raise ValueError(f"{label} is not a plain directory: {reason}") from None
    stack.callback(os.close, fd)
    return fd


def _workspace(stack: ExitStack, out: str) -> tuple[Path, int]:
    name = out.rstrip("/") or "/"
    try:
        info = os.lstat(name)
    except FileNotFoundError:
        raise ValueError("workspace is missing") from None
    require(not stat.S_ISLNK(info.st_mode), "workspace is a symlink")
    require(stat.S_ISDIR(info.st_mode), "workspace is not a directory")
    workspace = Path(name).resolve(strict=True)
    directory = _open_dir(stack, str(workspace), None, "workspace")
    require(directory is not None, "workspace vanished")
    assert directory is not None
    opened = os.fstat(directory)
    require(
        (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
        "workspace changed while it was opened",
    )
    return workspace, directory


def _same_workspace(found: Survey) -> None:
    info = os.lstat(found.workspace)
    opened = os.fstat(found.directory)
    require(
        stat.S_ISDIR(info.st_mode)
        and (info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino),
        "workspace moved after the survey",
    )


def _entries(directory: int, label: str) -> set[str]:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            full = len(names) >= MAX_ENTRIES
            require(not full, f"{label} has over {MAX_ENTRIES} entries")
            names.add(entry.name)
    return names


def _task_path(config: Record, label: str) -> str:
    task = config.get("task")
    require(
        type(task) is dict and set(task) == {"path"} and type(task["path"]) is str,
        f"{label} task is not exactly one package path",
    )
    assert type(task) is dict
    path: str = task["path"]
    require(
        0 < len(path) <= MAX_PATH_CHARS and os.path.isabs(path),
        f"{label} task path is not absolute",
    )
    return path


def _trials_path(config: Record, label: str, workspace: Path, directory: int) -> str:
    trials = config.get("trials_dir")
    require(
        type(trials) is str
        and 0 < len(trials) <= MAX_PATH_CHARS
        and os.path.isabs(trials),
        f"{label} trials_dir is not absolute",
    )
    assert type(trials) is str
    require(
        os.path.normpath(trials) == trials
        and ".." not in trials.split("/")
        and os.path.basename(trials) == "trials",
        f"{label} trials_dir is not a plain <workspace>/trials path",
    )
    parent = os.path.dirname(trials)
    try:
        resolved = Path(parent).resolve(strict=True)
        info = os.stat(parent)
    except OSError:
        raise ValueError(f"{label} trials_dir names a missing workspace") from None
    opened = os.fstat(directory)
    require(
        resolved == workspace
        and (info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino),
        f"{label} trials_dir names another workspace",
    )
    return trials


def _check_task_package(path: str) -> None:
    try:
        resolved = Path(path).resolve(strict=True)
        info = os.lstat(path)
    except OSError:
        raise ValueError("task package named by the configs is missing") from None
    require(str(resolved) == path, "task package path in the configs is not canonical")
    require(stat.S_ISDIR(info.st_mode), "task package in configs is not a directory")


def _config_bytes(configs: int, ticket: str) -> bytes:
    label = f"configs/{ticket}.json"
    data = _read_at(configs, f"{ticket}.json", MAX_CONFIG_BYTES, label)
    require(data is not None, f"{label} is missing")
    assert data is not None
    return data


def _check_configs(
    world: World,
    configs: int,
    cells: list[Record],
    teams: Record,
    workspace: tuple[Path, int],
) -> tuple[dict[str, str], str]:
    names = {f"{cell['ticket']}.json" for cell in cells}
    found = _entries(configs, "configs")
    require(
        found == names,
        f"configs hold {len(found - names)} entries outside and lack "
        f"{len(names - found)} tickets of this shard",
    )
    digests: dict[str, str] = {}
    tasks: set[str] = set()
    trials_dirs: set[str] = set()
    for cell in cells:
        label = f"configs/{cell['ticket']}.json"
        data = _config_bytes(configs, cell["ticket"])
        config = _parse(data, label)
        task = _task_path(config, label)
        trials = _trials_path(config, label, *workspace)
        tasks.add(task)
        trials_dirs.add(trials)
        team, env = teams[cell["team"]], world.env(cell)
        expected = registration_request(
            Path(task), Path(trials), cell["ticket"], team, env
        )
        require(
            record_digest(config) == record_digest(expected),
            f"{label} does not bind its ticket, team, world inputs and these trials",
        )
        digests[cell["ticket"]] = hashlib.sha256(data).hexdigest()
    require(len(tasks) == 1, "configs name different task packages")
    require(len(trials_dirs) == 1, "configs name different trials directories")
    task = tasks.pop()
    _check_task_package(task)
    return digests, task


def _task_files(workspace: int, task: str, identity: Record) -> str:
    try:
        recorded = _read_at(workspace, TASK_FILES, MAX_TASK_FILES_BYTES, TASK_FILES)
    except (ValueError, OSError):
        return "task_files_unreadable"
    if recorded is None:
        return "task_files_missing"
    try:
        current = task_files_digest(Path(task), identity)
    except ValueError:
        return "task_files_changed"
    except OSError:
        return "task_files_unreadable"
    if recorded != f"{current}\n".encode("ascii"):
        return "task_files_changed"
    return "match"


def _claim_bytes(
    world: World, ticket: str, registration: str, config: str, shard: str
) -> bytes:
    record = {
        "schema": world.claim_schema,
        "ticket": ticket,
        "shard": shard,
        "manifest": "sha256:" + registration,
        "config": "sha256:" + config,
    }
    text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True)
    data = (text + "\n").encode("ascii")
    assert len(data) <= MAX_CLAIM_BYTES
    return data


def _same_device(directory: int, workspace: int, label: str) -> None:
    require(
        os.fstat(directory).st_dev == os.fstat(workspace).st_dev,
        f"{label} is on another filesystem than the workspace",
    )


def _valid_claims(
    claims: int | None, workspace: int, expected: dict[str, bytes]
) -> frozenset[str]:
    if claims is None:
        return frozenset()
    _same_device(claims, workspace, "claims")
    claimed: set[str] = set()
    for name in sorted(_entries(claims, "claims")):
        pending = PENDING.fullmatch(name)
        if pending is not None and pending[1] in expected:
            continue
        require(name in expected, f"claims hold {name!r}, outside this shard")
        label = f"claim {name}"
        data = _read_at(claims, name, MAX_CLAIM_BYTES, label, CLAIM_MODE)
        require(data is not None, f"{label} vanished")
        require(
            data == expected[name],
            f"{label} does not bind this manifest, shard and config; inspect it",
        )
        claimed.add(name)
    return frozenset(claimed)


def _exists(directory: int | None, name: str) -> bool:
    if directory is None:
        return False
    try:
        os.lstat(name, dir_fd=directory)
    except FileNotFoundError:
        return False
    return True


def _result_file(trials: int | None, ticket: str) -> bool:
    if trials is None:
        return False
    try:
        trial = os.open(ticket, DIRECTORY, dir_fd=trials)
    except OSError as error:
        if error.errno in NOT_A_TRIAL_DIRECTORY:
            return False
        raise
    try:
        info = os.lstat(RESULT, dir_fd=trial)
    except FileNotFoundError:
        return False
    finally:
        os.close(trial)
    return stat.S_ISREG(info.st_mode)


def _trial_present(workspace: int, ticket: str) -> bool:
    with ExitStack() as local:
        trials = _open_dir(local, "trials", workspace, "trials")
        return _exists(trials, ticket)


def survey(
    stack: ExitStack,
    registration_path: str,
    shard_text: str,
    out: str,
    *,
    expected_schema: str | None = None,
) -> Survey:
    index, count = parse_shard(shard_text)
    world, registration, registration_sha = _registration(
        registration_path, expected_schema
    )
    cells = [
        cell
        for position, cell in enumerate(registration["cells"])
        if position % count == index
    ]
    require(len(cells) > 0, f"shard {index}/{count} selects no cells")
    tickets = [cell["ticket"] for cell in cells]
    assert all(world.ticket.fullmatch(ticket) for ticket in tickets)
    workspace, directory = _workspace(stack, out)
    configs = _open_dir(stack, "configs", directory, "configs")
    require(configs is not None, "workspace has no configs directory")
    assert configs is not None
    teams = registration["teams"]
    digests, task = _check_configs(world, configs, cells, teams, (workspace, directory))
    shard = f"{index}/{count}"
    claims = {
        ticket: _claim_bytes(world, ticket, registration_sha, digests[ticket], shard)
        for ticket in tickets
    }
    claims_dir = _open_dir(stack, "claims", directory, "claims")
    claimed = _valid_claims(claims_dir, directory, claims)
    trials = _open_dir(stack, "trials", directory, "trials")
    present = frozenset(ticket for ticket in tickets if _exists(trials, ticket))
    results = frozenset(ticket for ticket in present if _result_file(trials, ticket))
    identity = registration["task"]
    return Survey(
        workspace=workspace,
        directory=directory,
        world=world,
        shard=shard,
        tickets=tickets,
        claims=claims,
        configs=digests,
        claimed=claimed,
        present=present,
        results=results,
        task_path=task,
        identity=identity,
        task_files=_task_files(directory, task, identity),
        registration_sha256=registration_sha,
    )


def _link_claim(claims: int, ticket: str, data: bytes) -> bool:
    assert len(data) <= MAX_CLAIM_BYTES
    pending = f".{ticket}.{secrets.token_hex(8)}.tmp"
    assert PENDING.fullmatch(pending)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(pending, flags, CLAIM_MODE, dir_fd=claims)
    try:
        with open(fd, "wb") as handle:
            os.fchmod(handle.fileno(), CLAIM_MODE)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(pending, ticket, src_dir_fd=claims, dst_dir_fd=claims)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(pending, dir_fd=claims)


def _claims_dir(stack: ExitStack, workspace: int) -> int:
    try:
        os.mkdir("claims", 0o700, dir_fd=workspace)
    except FileExistsError:
        pass
    claims = _open_dir(stack, "claims", workspace, "claims")
    require(claims is not None, "claims directory vanished")
    assert claims is not None
    _same_device(claims, workspace, "claims")
    return claims


def _require_bound(found: Survey) -> None:
    require(
        found.task_files == "match",
        f"task package is {found.task_files}; no ticket was claimed",
    )
    current = _task_files(found.directory, found.task_path, found.identity)
    require(current == "match", f"task package is {current}; no ticket was claimed")


def take(stack: ExitStack, found: Survey) -> str | None:
    candidates = found.unclaimed()
    if not candidates:
        return None
    _require_bound(found)
    _same_workspace(found)
    configs = _open_dir(stack, "configs", found.directory, "configs")
    require(configs is not None, "configs directory vanished")
    assert configs is not None
    claims = _claims_dir(stack, found.directory)
    for ticket in candidates:
        current = hashlib.sha256(_config_bytes(configs, ticket)).hexdigest()
        require(
            current == found.configs[ticket],
            f"configs/{ticket}.json changed after the survey; no ticket was claimed",
        )
        if _trial_present(found.directory, ticket):
            continue
        if not _link_claim(claims, ticket, found.claims[ticket]):
            continue
        os.fsync(claims)
        require(
            not _trial_present(found.directory, ticket),
            f"trial {ticket} started outside the worklist; its claim is kept",
        )
        return str(found.workspace / "configs" / f"{ticket}.json")
    return None


def _configs_unchanged(found: Survey) -> bool:
    with ExitStack() as local:
        configs = _open_dir(local, "configs", found.directory, "configs")
        require(configs is not None, "configs directory vanished")
        assert configs is not None
        current = {
            ticket: hashlib.sha256(_config_bytes(configs, ticket)).hexdigest()
            for ticket in found.tickets
        }
    return current == found.configs


def check_fingerprint(found: Survey) -> str:
    current = _task_files(found.directory, found.task_path, found.identity)
    if current == "match":
        return current
    require(
        current == "task_files_missing",
        f"task package is {current}; the existing {TASK_FILES} is kept",
    )
    _check_task_package(found.task_path)
    task_dir = Path(found.task_path)
    before = task_files_digest(task_dir, found.identity)
    try:
        harbor = native()
    except SystemExit as error:
        raise ValueError(str(error)) from None
    identity = task_identity(harbor, task_dir, found.identity["name"])
    require(identity == found.identity, f"task {task_dir} is not the registered task")
    after = task_files_digest(task_dir, found.identity)
    require(after == before, "task changed while it was checked")
    require(_configs_unchanged(found), "configs changed while the task was checked")
    _same_workspace(found)
    record_task_files(found.workspace / TASK_FILES, before)
    bound = _task_files(found.directory, found.task_path, found.identity)
    require(bound == "match", f"task package is {bound} after binding")
    return bound
