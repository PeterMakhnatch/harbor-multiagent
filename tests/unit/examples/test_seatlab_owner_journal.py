import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from examples.seatlab.workbench.generation_receipts import (
    MAX_LINE_BYTES,
    MAX_OWNER_BYTES,
    OWNER_KEYS,
    _owners,
    owner,
)
from examples.seatlab.workbench.owner_journal import OwnerJournal
from examples.seatlab.workbench.seat_stream import Malformed

ROOT = Path(__file__).resolve().parents[3]
FILE = "owners.jsonl"
REFUSED = Malformed
TRIAL = "0b6f4c1e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"
OTHER = "5e0d7c2b-8a41-4f3e-b6c9-1d2e3f4a5b6c"
RETRY = "a9c8b7d6-e5f4-4a3b-8c2d-1e0f9a8b7c6d"
SECRET = "sk-or-v1-" + "5f" * 32
WORKER = """
import sys
from pathlib import Path
from examples.seatlab.workbench.owner_journal import OwnerJournal
directory, trial, seat, prefix, count = sys.argv[1:]
journal = OwnerJournal.bind(Path(directory))
sys.stdin.readline()
for index in range(int(count)):
    journal.append(trial, seat, f"{prefix}-{index}")
"""


def private(tmp_path: Path, name: str = "owners") -> Path:
    directory = tmp_path.resolve() / name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def seed(directory: Path, data: bytes, mode: int = 0o600) -> Path:
    path = directory / FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    path.chmod(mode)
    return path


def rows(path: Path) -> list[dict[str, str]]:
    data = path.read_bytes()
    assert data.endswith(b"\n")
    return [json.loads(line) for line in data[:-1].split(b"\n")]


def refuses(directory: Path) -> None:
    with pytest.raises(REFUSED):
        OwnerJournal.bind(directory).append(TRIAL, "planner", "gen-new")


def test_two_processes_append_every_row_whole(tmp_path):
    directory = private(tmp_path)
    count = 300
    plans = [(TRIAL, "planner", "gen-a"), (OTHER, "coder", "gen-b")]
    workers = [
        subprocess.Popen(
            [sys.executable, "-BS", "-c", WORKER, str(directory), *plan, str(count)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for plan in plans
    ]
    for worker in workers:
        worker.stdin.write(b"go\n")
        worker.stdin.flush()
    for worker in workers:
        _, error = worker.communicate(timeout=120)
        assert worker.returncode == 0, error.decode(errors="replace")

    found = rows(directory / FILE)
    assert len(found) == 2 * count
    for trial, seat, prefix in plans:
        expected = [owner(trial, seat, f"{prefix}-{index}") for index in range(count)]
        assert [row for row in found if row["trial"] == trial] == expected
    owners, total, repeats = _owners(str(directory / FILE))
    assert (len(owners), total, repeats) == (2 * count, 2 * count, 0)


def test_repeat_counts_once_and_rebind_keeps_prior_rows(tmp_path):
    directory = private(tmp_path)
    path = directory / FILE
    OwnerJournal.bind(directory).append(TRIAL, "planner", "gen-1")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()

    journal = OwnerJournal.bind(directory)
    journal.append(TRIAL, "planner", "gen-1")
    journal.append(TRIAL, "coder", "gen-2")

    assert path.read_bytes().startswith(before)
    assert rows(path) == [
        owner(TRIAL, "planner", "gen-1"),
        owner(TRIAL, "planner", "gen-1"),
        owner(TRIAL, "coder", "gen-2"),
    ]
    owners, total, repeats = _owners(str(path))
    assert (len(owners), total, repeats) == (2, 3, 1)


@pytest.mark.parametrize(
    "second",
    [(TRIAL, "coder"), (RETRY, "planner"), (RETRY, "coder")],
    ids=["other-seat", "retry-uuid", "retry-uuid-other-seat"],
)
def test_conflicting_claims_both_survive_and_reader_refuses(tmp_path, second):
    directory = private(tmp_path)
    journal = OwnerJournal.bind(directory)
    journal.append(TRIAL, "planner", "gen-shared")
    journal.append(*second, "gen-shared")

    assert rows(directory / FILE) == [
        owner(TRIAL, "planner", "gen-shared"),
        owner(*second, "gen-shared"),
    ]
    with pytest.raises(Malformed):
        _owners(str(directory / FILE))


@pytest.mark.parametrize(
    "ids",
    [
        (TRIAL, "planner", SECRET + " " + SECRET),
        (TRIAL, "planner", SECRET + '\n{"choices":[]}'),
        (TRIAL, "planner", SECRET * 4),
        (TRIAL, "planner", ""),
        (SECRET, "planner", "gen-ok"),
        (TRIAL, "../" + SECRET, "gen-ok"),
    ],
    ids=["space", "newline-body", "too-long", "empty", "trial", "seat"],
)
def test_only_hashed_owner_rows_persist(tmp_path, ids):
    directory = private(tmp_path)
    path = directory / FILE
    journal = OwnerJournal.bind(directory)
    journal.append(TRIAL, "planner", SECRET)
    before = path.read_bytes()

    with pytest.raises(REFUSED) as refused:
        journal.append(*ids)

    assert SECRET not in str(refused.value)
    assert str(directory) not in str(refused.value)
    assert path.read_bytes() == before
    assert [set(row) for row in rows(path)] == [OWNER_KEYS]
    for item in directory.iterdir():
        assert SECRET.encode() not in item.read_bytes()


@pytest.mark.parametrize("tamper", ["replace", "hardlink", "symlink"])
def test_bound_writer_refuses_swapped_destination(tmp_path, tamper):
    directory = private(tmp_path)
    path = directory / FILE
    journal = OwnerJournal.bind(directory)
    journal.append(TRIAL, "planner", "gen-1")
    original = path.read_bytes()
    elsewhere = private(tmp_path, "elsewhere")
    victim = elsewhere / "victim.jsonl"
    if tamper == "replace":
        staged = seed(elsewhere, original).rename(directory / "staged")
        os.replace(staged, path)
        victim = path
    elif tamper == "hardlink":
        os.link(path, victim)
    else:
        path.rename(victim)
        path.symlink_to(victim)

    with pytest.raises(REFUSED):
        journal.append(TRIAL, "planner", "gen-2")
    assert victim.read_bytes() == original


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_bind_refuses_linked_destination(tmp_path, link):
    directory = private(tmp_path)
    target = seed(private(tmp_path, "elsewhere"), b"")
    if link == "hardlink":
        os.link(target, directory / FILE)
    else:
        (directory / FILE).symlink_to(target)

    refuses(directory)
    assert target.read_bytes() == b""


def full_file() -> bytes:
    base = json.dumps(owner(TRIAL, "planner", "gen-fill"))
    width = MAX_LINE_BYTES - 1024
    whole, rest = divmod(MAX_OWNER_BYTES, width + 1)
    assert rest - 1 >= len(base)

    def padded(length: int) -> bytes:
        return (base[:-1] + " " * (length - len(base)) + "}\n").encode()

    data = padded(width) * whole + padded(rest - 1)
    assert len(data) == MAX_OWNER_BYTES
    return data


@pytest.mark.parametrize("state", ["torn-tail", "at-limit"])
def test_torn_or_full_file_is_never_extended(tmp_path, state):
    directory = private(tmp_path)
    if state == "torn-tail":
        complete = json.dumps(owner(TRIAL, "planner", "gen-1")) + "\n"
        data = (complete + complete[:20]).encode()
    else:
        data = full_file()
        assert len(_owners(str(seed(private(tmp_path, "check"), data)))[0]) == 1
    path = seed(directory, data)

    refuses(directory)
    assert path.read_bytes() == data


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o711, 0o777])
def test_public_directory_is_refused(tmp_path, mode):
    directory = private(tmp_path)
    directory.chmod(mode)

    refuses(directory)
    assert not (directory / FILE).exists()


def test_symlinked_directory_is_refused(tmp_path):
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(private(tmp_path), target_is_directory=True)

    refuses(alias)
    assert not (alias / FILE).exists()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_public_file_is_refused_without_chmod(tmp_path, mode):
    directory = private(tmp_path)
    data = (json.dumps(owner(TRIAL, "planner", "gen-1")) + "\n").encode()
    path = seed(directory, data, mode)

    refuses(directory)
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert path.read_bytes() == data
