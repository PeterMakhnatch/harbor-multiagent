import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple

from ..seat_handoffs.core import reject_constant, unique_object
from ..seat_handoffs.native_records import record_digest
from .plan import (
    MAX_CELLS,
    MAX_MANIFEST_BYTES,
    TASK_FILES,
    TICKET,
    load_manifest,
    parse_shard,
    task_files_digest,
)

CLAIM_SCHEMA = "harbor.shift-board.claim/v1"
CLAIM_MODE = 0o600
MAX_CONFIG_BYTES = 1 << 20
MAX_CLAIM_BYTES = 4096
MAX_TASK_FILES_BYTES = 128
MAX_ENTRIES = 4 * MAX_CELLS
MAX_PATH_CHARS = 4096
EXIT_FAILURE = 2
EXIT_NONE_LEFT = 3
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
PENDING = re.compile(r"\.(sb-[0-9a-f]{16})\.[0-9a-f]{16}\.tmp")
Record = dict[str, Any]


class Survey(NamedTuple):
    workspace: Path
    directory: int
    shard: str
    tickets: list[str]
    claims: dict[str, bytes]
    claimed: frozenset[str]
    started: frozenset[str]
    task_files: str

    def unclaimed(self) -> list[str]:
        return [
            ticket
            for ticket in self.tickets
            if ticket not in self.claimed and ticket not in self.started
        ]

    def counts(self) -> Record:
        return {
            "shard": self.shard,
            "assigned": len(self.tickets),
            "claimed": len(self.claimed),
            "started": len(self.started),
            "unclaimed": len(self.unclaimed()),
            "task_files": self.task_files,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


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
        _require(stat.S_ISREG(info.st_mode), f"{label} is not a regular file")
        _require(info.st_size <= limit, f"{label} exceeds {limit} bytes")
        if mode is not None:
            exact = stat.S_IMODE(info.st_mode) == mode
            _require(exact, f"{label} is not mode {mode:o}")
        data = handle.read(limit + 1)
    _require(len(data) <= limit, f"{label} exceeds {limit} bytes")
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
    _require(type(value) is dict, f"{label} is not a JSON object")
    return value


def _manifest(path: str) -> tuple[Record, str]:
    data = _read_at(None, path, MAX_MANIFEST_BYTES, "manifest")
    _require(data is not None, "manifest is missing")
    assert data is not None
    manifest = load_manifest(Path(path))
    _require(
        record_digest(_parse(data, "manifest")) == record_digest(manifest),
        "manifest changed while it was read",
    )
    return manifest, hashlib.sha256(data).hexdigest()


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
    _require(not stat.S_ISLNK(info.st_mode), "workspace is a symlink")
    _require(stat.S_ISDIR(info.st_mode), "workspace is not a directory")
    workspace = Path(name).resolve(strict=True)
    directory = _open_dir(stack, str(workspace), None, "workspace")
    _require(directory is not None, "workspace vanished")
    assert directory is not None
    opened = os.fstat(directory)
    _require(
        (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
        "workspace changed while it was opened",
    )
    return workspace, directory


def _entries(directory: int, label: str) -> set[str]:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            full = len(names) >= MAX_ENTRIES
            _require(not full, f"{label} has over {MAX_ENTRIES} entries")
            names.add(entry.name)
    return names


def _expected_config(cell: Record, team: object, trials: str, task: str) -> Record:
    env = {
        "BOARD_SEED": str(cell["seed"]),
        "BOARD_FIXTURE": cell["fixture"],
        "BOARD_RULE": cell["rule"],
        "BOARD_TICKET": cell["ticket"],
    }
    return {
        "task": {"path": task},
        "trial_name": cell["ticket"],
        "trials_dir": trials,
        "environment": {"type": "docker", "delete": True, "env": env},
        "participants": team,
    }


def _task_path(config: Record, label: str) -> str:
    task = config.get("task")
    _require(
        type(task) is dict and set(task) == {"path"} and type(task["path"]) is str,
        f"{label} task is not exactly one package path",
    )
    assert type(task) is dict
    path: str = task["path"]
    _require(
        0 < len(path) <= MAX_PATH_CHARS and os.path.isabs(path),
        f"{label} task path is not absolute",
    )
    return path


def _check_task_package(path: str) -> None:
    try:
        resolved = Path(path).resolve(strict=True)
        info = os.lstat(path)
    except OSError:
        raise ValueError("task package named by the configs is missing") from None
    _require(str(resolved) == path, "task package path in the configs is not canonical")
    _require(stat.S_ISDIR(info.st_mode), "task package in configs is not a directory")


def _check_configs(
    configs: int, cells: list[Record], teams: Record, trials: str
) -> tuple[dict[str, str], str]:
    names = {f"{cell['ticket']}.json" for cell in cells}
    found = _entries(configs, "configs")
    _require(
        found == names,
        f"configs hold {len(found - names)} entries outside and lack "
        f"{len(names - found)} tickets of this shard",
    )
    digests: dict[str, str] = {}
    tasks: set[str] = set()
    for cell in cells:
        label = f"configs/{cell['ticket']}.json"
        data = _read_at(configs, f"{cell['ticket']}.json", MAX_CONFIG_BYTES, label)
        _require(data is not None, f"{label} is missing")
        assert data is not None
        config = _parse(data, label)
        task = _task_path(config, label)
        tasks.add(task)
        expected = _expected_config(cell, teams[cell["team"]], trials, task)
        _require(
            record_digest(config) == record_digest(expected),
            f"{label} does not bind its ticket, team, world inputs and these trials",
        )
        digests[cell["ticket"]] = hashlib.sha256(data).hexdigest()
    _require(len(tasks) == 1, "configs name different task packages")
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


def _claim_bytes(ticket: str, manifest: str, config: str, shard: str) -> bytes:
    record = {
        "schema": CLAIM_SCHEMA,
        "ticket": ticket,
        "shard": shard,
        "manifest": "sha256:" + manifest,
        "config": "sha256:" + config,
    }
    text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True)
    data = (text + "\n").encode("ascii")
    assert len(data) <= MAX_CLAIM_BYTES
    return data


def _same_device(directory: int, workspace: int, label: str) -> None:
    _require(
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
        _require(name in expected, f"claims hold {name!r}, outside this shard")
        label = f"claim {name}"
        data = _read_at(claims, name, MAX_CLAIM_BYTES, label, CLAIM_MODE)
        _require(data is not None, f"{label} vanished")
        _require(
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


def survey(stack: ExitStack, manifest_path: str, shard_text: str, out: str) -> Survey:
    index, count = parse_shard(shard_text)
    manifest, manifest_sha = _manifest(manifest_path)
    cells = [
        cell
        for position, cell in enumerate(manifest["cells"])
        if position % count == index
    ]
    _require(len(cells) > 0, f"shard {index}/{count} selects no cells")
    tickets = [cell["ticket"] for cell in cells]
    assert all(TICKET.fullmatch(ticket) for ticket in tickets)
    workspace, directory = _workspace(stack, out)
    configs = _open_dir(stack, "configs", directory, "configs")
    _require(configs is not None, "workspace has no configs directory")
    assert configs is not None
    trials_dir = str(workspace / "trials")
    digests, task = _check_configs(configs, cells, manifest["teams"], trials_dir)
    shard = f"{index}/{count}"
    claims = {
        ticket: _claim_bytes(ticket, manifest_sha, digests[ticket], shard)
        for ticket in tickets
    }
    claims_dir = _open_dir(stack, "claims", directory, "claims")
    claimed = _valid_claims(claims_dir, directory, claims)
    trials = _open_dir(stack, "trials", directory, "trials")
    started = frozenset(
        ticket
        for ticket in tickets
        if ticket not in claimed and _exists(trials, ticket)
    )
    task_files = _task_files(directory, task, manifest["task"])
    return Survey(
        workspace, directory, shard, tickets, claims, claimed, started, task_files
    )


def _link_claim(claims: int, ticket: str, data: bytes) -> bool:
    assert TICKET.fullmatch(ticket) and len(data) <= MAX_CLAIM_BYTES
    pending = f".{ticket}.{secrets.token_hex(8)}.tmp"
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
    _require(claims is not None, "claims directory vanished")
    assert claims is not None
    _same_device(claims, workspace, "claims")
    return claims


def take(stack: ExitStack, found: Survey) -> str | None:
    candidates = found.unclaimed()
    if not candidates:
        return None
    _require(
        found.task_files == "match",
        f"task package is {found.task_files}; no ticket was claimed",
    )
    claims = _claims_dir(stack, found.directory)
    for ticket in candidates:
        if not _link_claim(claims, ticket, found.claims[ticket]):
            continue
        os.fsync(claims)
        with ExitStack() as local:
            trials = _open_dir(local, "trials", found.directory, "trials")
            _require(
                not _exists(trials, ticket),
                f"trial {ticket} started outside the worklist; its claim is kept",
            )
        return str(found.workspace / "configs" / f"{ticket}.json")
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python3 -BS -m examples.shift_board.worklist")
    root.add_argument("manifest")
    root.add_argument("--shard", required=True)
    root.add_argument("--out", required=True)
    root.add_argument("--take", action="store_true")
    return root


def run(args: argparse.Namespace) -> int:
    with ExitStack() as stack:
        found = survey(stack, args.manifest, args.shard, args.out)
        if not args.take:
            for ticket in found.unclaimed():
                print(found.workspace / "configs" / f"{ticket}.json")
            print(json.dumps(found.counts(), sort_keys=True), file=sys.stderr)
            return 0
        config = take(stack, found)
        if config is None:
            print(f"no unclaimed ticket in shard {found.shard}", file=sys.stderr)
            print(json.dumps(found.counts(), sort_keys=True), file=sys.stderr)
            return EXIT_NONE_LEFT
        print(config)
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
    except OSError as error:
        print(f"error: filesystem: {error.strerror or 'failed'}", file=sys.stderr)
    return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
