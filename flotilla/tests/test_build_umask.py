import os
import stat
from pathlib import Path

from conftest import FLOTILLA, load

BUILD = load(FLOTILLA / "build_tasks.py")
SEED = 6873629991007281
HOST_ONLY_FILES = ("task.toml", "environment/task.json", "environment/docker-compose.yaml")


def build_under(umask: int, out: Path) -> list[Path]:
    previous = os.umask(umask)
    try:
        ledger = BUILD.build("ledger", [0], 2, "easy", SEED, ("solo", "talk"), out, policy="careful",
                             readable_names=True)
        handoff = BUILD.build("handoff", [0], 3, "canary", SEED, ("team",), out, readable_names=True)
    finally:
        os.umask(previous)
    return [*ledger, *handoff]


def payload_paths(task: Path) -> list[Path]:
    roots = [task / name for name in BUILD.CONTAINER_PAYLOADS]
    return [path for root in roots for path in (root, *sorted(root.rglob("*")))]


def payload_modes(out: Path, tasks: list[Path]) -> dict[str, int]:
    return {str(path.relative_to(out)): stat.S_IMODE(path.lstat().st_mode)
            for task in tasks for path in payload_paths(task)}


def assert_player_can_run_oracle(task: Path) -> None:
    executables = {task / "solution" / "solve.sh", task / "tests" / "test.sh"}
    for path in payload_paths(task):
        mode = path.lstat().st_mode
        needed = 0o005 if stat.S_ISDIR(mode) or path in executables else 0o004
        assert mode & needed == needed, (path, oct(mode))


def test_private_build_root_stays_private_and_oracle_payload_opens_to_player(tmp_path):
    private = tmp_path / "private"
    tasks = build_under(0o077, private)
    assert {(task / "solution" / "policy.json").exists() for task in tasks} == {True}
    payload = {path for task in tasks for path in payload_paths(task)}
    for path in (private, *sorted(private.rglob("*"))):
        if path in payload:
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            assert not mode & 0o077, (path, oct(mode))
    for task in tasks:
        assert_player_can_run_oracle(task)
        for path in (*(task / name for name in HOST_ONLY_FILES), *sorted((task / "instructions").glob("*.md"))):
            assert not path.lstat().st_mode & 0o077, path
    assert not (private / BUILD.MANIFEST).lstat().st_mode & 0o077
    public = tmp_path / "public"
    public_tasks = build_under(0o022, public)
    for task in public_tasks:
        assert_player_can_run_oracle(task)
    assert payload_modes(private, tasks) == payload_modes(public, public_tasks)
